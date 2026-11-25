#!/usr/bin/env python3
"""Tests unitaires isolés : clôture fin de cycle, --update-sealed et filtre anti-bruit pending."""

import json
import os
from pathlib import Path
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
            self.assertEqual(m["milestones"]["current_phase"], "Clôture / Enregistrée")
            self.assertEqual(m["milestones"]["phases"][-1]["status"], "active")

    def test_update_sealed_cli_and_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"

            cid = "u2000001-1111-2222-3333-444455556666"
            steps_v1 = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:00:00Z", "content": "/gravity-architect spec sealed"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": [{"name": "edit_file"}]},
            ]
            _write_transcript(brain / cid, steps_v1)

            # 1) Archivage initial (zéro lecture live bridge.db -> seuls les tokens natifs 12_000)
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
                    "--store",
                    str(store),
                ],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_init.returncode, 0, proc_init.stderr)
            recs1 = load_history(hist_path)
            self.assertEqual(len(recs1), 1)
            self.assertEqual(recs1[0]["input_tokens"], 12_000)

            # 2) Activité finale (push) : sans --update-sealed, l'archive reste immuable
            steps_v2 = steps_v1 + [
                {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:05:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 250, "tool_calls": [{"name": "run_command", "args": {"CommandLine": "git push origin dev"}}]},
            ]
            _write_transcript(brain / cid, steps_v2)

            proc_imm = _run_py(
                CLI_SCRIPT,
                ["archive", cid, "--brain-dir", str(brain), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_imm.returncode, 0, proc_imm.stderr)
            self.assertIn("déjà enregistrée", proc_imm.stdout)
            self.assertIn("--update", proc_imm.stdout)
            self.assertEqual(load_history(hist_path)[0]["input_tokens"], 12_000)

            # 3) --update-sealed rafraîchit directement la fenêtre scellée
            m_live = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True, store_dir=store)
            self.assertEqual(m_live["bridge_pending_jobs"], [])

            proc_upd = _run_py(
                CLI_SCRIPT,
                ["archive", cid, "--update-sealed", "--brain-dir", str(brain), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_upd.returncode, 0, proc_upd.stderr)
            recs_upd = load_history(hist_path)
            self.assertEqual(len(recs_upd), 1)
            self.assertEqual(recs_upd[0]["label"], "sealed-lot")
            self.assertEqual(recs_upd[0]["note"], "Note initiale")
            self.assertEqual(recs_upd[0]["input_tokens"], 20_000)

            # 4) cli.py add --update-sealed met à jour les métriques en préservant label & note
            steps_v3 = steps_v2 + [
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:06:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
            ]
            _write_transcript(brain / cid, steps_v3)
            proc_add_upd = _run_py(
                CLI_SCRIPT,
                ["add", cid, "--gravity-window", "--update-sealed", "--brain-dir", str(brain), "--store", str(store)],
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(proc_add_upd.returncode, 0, proc_add_upd.stderr)
            recs_add = load_history(hist_path)
            self.assertEqual(len(recs_add), 1)
            self.assertEqual(recs_add[0]["label"], "sealed-lot")
            self.assertEqual(recs_add[0]["input_tokens"], 23_000)

            # Vérifier directement que record_session_metrics ne bloque jamais sur des champs bridge_pending_jobs hérités
            m_stub = dict(m_live)
            m_stub["bridge_pending_jobs"] = [{"thread_id": "t", "to_workspace": "w", "status": "dispatched"}]
            rec_no_raise, _ = record_session_metrics(m_stub, store_dir=store, history_path=hist_path, update_sealed=True, force=False)
            self.assertEqual(rec_no_raise["label"], "sealed-lot")

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
            wt_snap = root / "gravity-worktrees" / "gravity-metrics-w1"
            wt_snap.mkdir(parents=True)
            wt_posix = root / "repo" / "worktrees" / "wt-posix"
            wt_posix.mkdir(parents=True)
            real_ws = root / "RealProject"
            real_ws.mkdir()

            for idx, wt_cwd in enumerate(
                [
                    str(wt_linux),
                    str(wt_linux.parent),
                    str(wt_snap),
                    str(wt_snap.parent),
                    str(wt_posix),
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

    def test_pending_no_phantom_within_two_steps_and_update_sealed_reuses_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"

            cid = "r2000001-1111-2222-3333-444455556666"
            steps = [
                {"step_index": 70, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "Chat libre"},
                {"step_index": 71, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:10Z", "model": "gemini-4-neon", "input_tokens": 50_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                {"step_index": 72, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:01:00Z", "content": "/gravity-architect spec r2-lot"},
                {"step_index": 73, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-25T10:01:01Z", "content": "hook msg"},
                {"step_index": 74, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "edit_file", "args": {"Path": "plugin/metrics/store.py"}}]},
            ]
            _write_transcript(brain / cid, steps)

            # Enregistré avec ancien off-by-one window_start_step=73 (capture active purgée ensuite)
            saved1, _, _ = archive_conversation(
                cid,
                store_dir=store,
                history_path=hist_path,
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                label="r2-old-label",
                note="Note R2",
                project="ProjR2",
                from_step=73,
            )
            self.assertEqual(saved1["window_start_step"], 73)
            self.assertTrue((store / "reports" / "r2-old-label.json").is_file())

            # list_pending_sessions ré-ancre à step 72 -> abs(73 - 72) == 1 <= 2 -> zéro fantôme !
            self.assertEqual(list_pending_sessions(brain, hist_path, store_dir=store), [])

            # Suite de la session (step 75) + update_sealed=True sans active_capture ni label explicite :
            # réutilise la fenêtre existante (73), préserve label/project/note, zéro doublon !
            steps_more = steps + [
                {"step_index": 75, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:02:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "run_command", "args": {"CommandLine": "git -C ../gravity-metrics push origin dev"}}]},
            ]
            _write_transcript(brain / cid, steps_more)

            saved2, recs2, _ = archive_conversation(
                cid,
                store_dir=store,
                history_path=hist_path,
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                update_sealed=True,
            )
            self.assertEqual(len(recs2), 1)
            self.assertEqual(saved2["label"], "r2-old-label")
            self.assertEqual(saved2["project"], "ProjR2")
            self.assertEqual(saved2["note"], "Note R2")
            self.assertEqual(saved2["window_start_step"], 73)
            self.assertEqual(saved2["input_tokens"], 20_000)

            # Renommage lors d'un update_sealed=True purge l'ancien slug orphelin
            saved3, recs3, _ = archive_conversation(
                cid,
                store_dir=store,
                history_path=hist_path,
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                label="r2-new-label",
                update_sealed=True,
                from_step=72,
            )
            self.assertEqual(len(recs3), 1)
            self.assertEqual(saved3["label"], "r2-new-label")
            self.assertEqual(saved3["note"], "Note R2")
            self.assertFalse((store / "reports" / "r2-old-label.json").exists())
            self.assertFalse((store / "reports" / "r2-old-label.md").exists())
            self.assertTrue((store / "reports" / "r2-new-label.json").is_file())

            # Une nouvelle phase distante (après marqueur clôture phase 1, step 78, abs(72 - 78) == 6 > 2) DOIT apparaître dans pending
            from metrics import METRICS_MARKER

            steps_next_phase = steps_more + [
                {"step_index": 76, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:03:00Z", "content": "/gravity-metrics stop r2-new-label"},
                {"step_index": 77, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-25T10:03:01Z", "content": METRICS_MARKER},
                {"step_index": 78, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:10:00Z", "content": "/gravity-workflow phase suivante"},
                {"step_index": 79, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:10:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "args": {"Path": "plugin/metrics/history.py"}}]},
            ]
            _write_transcript(brain / cid, steps_next_phase)
            pend_next = list_pending_sessions(brain, hist_path, store_dir=store)
            self.assertEqual([p["conversation_id"] for p in pend_next], [cid])
            self.assertEqual(pend_next[0]["window_start_step"], 78)


if __name__ == "__main__":
    unittest.main()
