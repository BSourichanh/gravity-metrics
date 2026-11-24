#!/usr/bin/env python3
"""Tests unitaires isolés : clôture fin de cycle, --update-sealed et filtre anti-bruit pending."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
CLI_SCRIPT = PLUGIN_ROOT / "scripts" / "cli.py"
PRE_HOOK = PLUGIN_ROOT / "hooks" / "pre_invocation_metrics.py"
START_HOOK = PLUGIN_ROOT / "hooks" / "session_start_metrics.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    PendingBridgeJobsError,
    archive_conversation,
    collect_run_metrics,
    list_pending_sessions,
    load_history,
    load_session_meta,
    parse_architect_archive_prompt,
    record_session_metrics,
    start_active_capture,
)


def _write_transcript(conv_dir: Path, steps: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    with tpath.open("w", encoding="utf-8", newline="\n") as fh:
        for obj in steps:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return tpath


def _run_py(script: Path, args: list, *, stdin_text: str = "", store_dir: Path, brain_dir: Path, extra_env: dict = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GRAVITY_METRICS_HOME"] = str(store_dir)
    env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-B", str(script), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def _create_bridge_db(db_path: Path, threads: list, jobs: list) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
        "msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
        "action TEXT, direction TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, status TEXT, created_at TEXT)"
    )
    for row in threads:
        con.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    for row in jobs:
        con.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    con.commit()
    con.close()


class TestClosureAndPendingHygiene(unittest.TestCase):
    def test_pre_invocation_architect_archive_does_not_write_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "Atelier"
            ws.mkdir()
            cid = "c1000001-1111-2222-3333-444455556666"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "/gravity-architect spec lot-x"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "write_file"}]},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T10:02:00Z", "content": "/gravity-architect archive lot-x"},
                ],
            )
            proc = _run_py(
                PRE_HOOK,
                [],
                stdin_text=json.dumps(
                    {
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "lastUserInput": "/gravity-architect archive lot-x",
                    }
                ),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout), {})
            self.assertFalse((store / "data" / "history.jsonl").exists())
            self.assertFalse((store / "reports").exists())
            self.assertEqual(parse_architect_archive_prompt("/gravity-architect archive lot-x"), "lot-x")

            m = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True, store_dir=store)
            self.assertEqual(m["milestones"]["current_phase"], "Clôture / Archivé")
            self.assertEqual(m["milestones"]["phases"][-1]["status"], "active")

    def test_update_sealed_cli_and_pending_bridge_jobs_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            bridge_db = root / "bridge" / "bridge.db"
            hist_path = store / "data" / "history.jsonl"

            cid = "u2000001-1111-2222-3333-444455556666"
            child_cid = "u2000002-1111-2222-3333-444455556666"
            steps_v1 = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:00:00Z", "content": "/gravity-architect spec sealed"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": [{"name": "edit_file"}]},
            ]
            _write_transcript(brain / cid, steps_v1)
            _write_transcript(
                brain / child_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:00:20Z", "content": "[GRAVITY-BRIDGE | thread=thr-seal]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            _create_bridge_db(
                bridge_db,
                [("thr-seal", "gravity-studio", "gravity-metrics", "handoff", "done", child_cid, cid, "2026-11-24T11:00:15Z")],
                [("job-seal", "thr-seal", "gravity-metrics", "new_conversation", "outbound", child_cid, cid, "completed", "2026-11-24T11:00:15Z")],
            )

            # 1) Archivage initial
            proc_init = _run_py(
                CLI_SCRIPT,
                [
                    "archive",
                    cid,
                    "-l",
                    "sealed-lot",
                    "-n",
                    "Note initiale",
                    "--brain-dir",
                    str(brain),
                    "--bridge-db",
                    str(bridge_db),
                    "--store",
                    str(store),
                ],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_init.returncode, 0, proc_init.stderr)
            recs1 = load_history(hist_path)
            self.assertEqual(len(recs1), 1)
            self.assertEqual(recs1[0]["input_tokens"], 17_000)

            # 2) Activité finale (push) : sans --update-sealed, l'archive reste immuable
            steps_v2 = steps_v1 + [
                {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:05:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 250, "tool_calls": [{"name": "run_command", "args": {"CommandLine": "git push origin dev"}}]},
            ]
            _write_transcript(brain / cid, steps_v2)

            proc_imm = _run_py(
                CLI_SCRIPT,
                ["archive", cid, "--brain-dir", str(brain), "--bridge-db", str(bridge_db), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_imm.returncode, 0, proc_imm.stderr)
            self.assertIn("déjà archivée", proc_imm.stdout)
            self.assertEqual(load_history(hist_path)[0]["input_tokens"], 17_000)

            # 3) --update-sealed avec job Bridge actif -> DOIT lever PendingBridgeJobsError (rc=1, zéro écriture)
            con = sqlite3.connect(str(bridge_db))
            con.execute("UPDATE jobs SET status = 'dispatched' WHERE id = 'job-seal'")
            con.commit()
            con.close()

            proc_blk = _run_py(
                CLI_SCRIPT,
                ["archive", cid, "--update-sealed", "--brain-dir", str(brain), "--bridge-db", str(bridge_db), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_blk.returncode, 1)
            self.assertIn("Archivage bloqué", proc_blk.stderr)
            self.assertEqual(load_history(hist_path)[0]["input_tokens"], 17_000)

            m_blk = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True, bridge_db_path=bridge_db, store_dir=store)
            with self.assertRaises(PendingBridgeJobsError):
                record_session_metrics(m_blk, store_dir=store, history_path=hist_path, update_sealed=True, force=False)

            # 4) Clôture du job Bridge -> cli.py archive --update-sealed met à jour l'archive en préservant label & note
            con = sqlite3.connect(str(bridge_db))
            con.execute("UPDATE jobs SET status = 'completed' WHERE id = 'job-seal'")
            con.commit()
            con.close()

            proc_upd = _run_py(
                CLI_SCRIPT,
                ["archive", cid, "--update-sealed", "--brain-dir", str(brain), "--bridge-db", str(bridge_db), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_upd.returncode, 0, proc_upd.stderr)
            recs_upd = load_history(hist_path)
            self.assertEqual(len(recs_upd), 1)
            self.assertEqual(recs_upd[0]["label"], "sealed-lot")
            self.assertEqual(recs_upd[0]["note"], "Note initiale")
            self.assertEqual(recs_upd[0]["input_tokens"], 25_000)

            # 5) cli.py add --update-sealed fonctionne aussi et met à jour les métriques
            steps_v3 = steps_v2 + [
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:06:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
            ]
            _write_transcript(brain / cid, steps_v3)
            proc_add_upd = _run_py(
                CLI_SCRIPT,
                ["add", cid, "--gravity-window", "--update-sealed", "--brain-dir", str(brain), "--bridge-db", str(bridge_db), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_add_upd.returncode, 0, proc_add_upd.stderr)
            recs_add = load_history(hist_path)
            self.assertEqual(len(recs_add), 1)
            self.assertEqual(recs_add[0]["label"], "sealed-lot")
            self.assertEqual(recs_add[0]["input_tokens"], 28_000)

    def test_pending_anti_noise_filter_and_cli_all_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"

            cid_noise_dash = "n1000001-1111-2222-3333-444455556666"
            cid_noise_doc = "n1000002-1111-2222-3333-444455556666"
            cid_work_edit = "w1000001-1111-2222-3333-444455556666"
            cid_work_wf = "w1000002-1111-2222-3333-444455556666"
            cid_active_cap = "w1000003-1111-2222-3333-444455556666"

            _write_transcript(
                brain / cid_noise_dash,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:00:00Z", "content": "/gravity dashboard"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "call_mcp_tool"}]},
                ],
            )
            _write_transcript(
                brain / cid_noise_doc,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:01:00Z", "content": "/gravity doctor"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:01:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 80, "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 doctor.py"}}]},
                ],
            )
            _write_transcript(
                brain / cid_work_edit,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:02:00Z", "content": "/gravity-workflow fix bug"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "replace_file_content"}]},
                ],
            )
            _write_transcript(
                brain / cid_work_wf,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:03:00Z", "content": "/gravity-workflow evals"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:03:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "swarm"}]},
                ],
            )
            _write_transcript(
                brain / cid_active_cap,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:04:00Z", "content": "Session avec capture active sans outil"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:04:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            start_active_capture(cid_active_cap, start_step=0, label="cap-live", store_dir=store)

            default_ids = {p["conversation_id"] for p in list_pending_sessions(brain, hist_path, store_dir=store)}
            self.assertEqual(default_ids, {cid_work_edit, cid_work_wf, cid_active_cap})

            all_ids = {p["conversation_id"] for p in list_pending_sessions(brain, hist_path, store_dir=store, include_noise=True)}
            self.assertEqual(all_ids, {cid_noise_dash, cid_noise_doc, cid_work_edit, cid_work_wf, cid_active_cap})

            proc_def = _run_py(
                CLI_SCRIPT,
                ["pending", "--brain-dir", str(brain), "--store", str(store), "--json"],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_def.returncode, 0, proc_def.stderr)
            self.assertEqual({p["conversation_id"] for p in json.loads(proc_def.stdout)}, default_ids)

            proc_all = _run_py(
                CLI_SCRIPT,
                ["pending", "--all", "--brain-dir", str(brain), "--store", str(store), "--json"],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_all.returncode, 0, proc_all.stderr)
            self.assertEqual({p["conversation_id"] for p in json.loads(proc_all.stdout)}, all_ids)

    def test_session_start_ignores_subagent_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"

            wt_linux = root / ".gemini" / "antigravity" / "worktrees" / "subagent-1"
            wt_linux.mkdir(parents=True)
            real_ws = root / "RealProject"
            real_ws.mkdir()

            for idx, wt_cwd in enumerate(
                [
                    str(wt_linux),
                    str(wt_linux.parent),
                    r"C:\Users\dev\.gemini\antigravity\worktrees\wt-win",
                    r"D:\repo\worktrees\wt-branch",
                ],
                start=1,
            ):
                proc = _run_py(
                    START_HOOK,
                    [],
                    stdin_text=json.dumps({"conversationId": f"wt-cid-{idx}", "cwd": wt_cwd}),
                    store_dir=store,
                    brain_dir=brain,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout), {})

            self.assertEqual(load_session_meta(store), {})

            proc_real = _run_py(
                START_HOOK,
                [],
                stdin_text=json.dumps({"conversationId": "real-cid-1", "cwd": str(real_ws)}),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_real.returncode, 0, proc_real.stderr)
            self.assertEqual(json.loads(proc_real.stdout), {})
            meta = load_session_meta(store)
            self.assertIn("real-cid-1", meta)
            self.assertEqual(meta["real-cid-1"]["project"], "RealProject")


if __name__ == "__main__":
    unittest.main()
