#!/usr/bin/env python3
"""Tests v0.5.0 : correctifs P0, cycle hybride, jalons, SessionStart, caches et UI sobre."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
PRE_HOOK = PLUGIN_ROOT / "hooks" / "pre_invocation_metrics.py"
START_HOOK = PLUGIN_ROOT / "hooks" / "session_start_metrics.py"
SERVER_SCRIPT = PLUGIN_ROOT / "mcp" / "server.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    METRICS_MARKER,
    archive_conversation,
    collect_run_metrics,
    find_archived_session,
    ignore_session,
    list_pending_sessions,
    load_active_captures,
    load_history,
    load_ignored_sessions,
    load_session_meta,
    read_gravity_profile_summary,
    record_session_metrics,
    resolve_gravity_window,
    unignore_session,
)
from mcp.ui.markdown_view import MARKDOWN_VIEW_JS  # noqa: E402


def _write_transcript(conv_dir: Path, steps: list) -> Path:
    conv_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    tpath = logs_dir / "transcript.jsonl"
    with tpath.open("w", encoding="utf-8", newline="\n") as fh:
        for obj in steps:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return tpath


def _run_py_hook(script: Path, stdin_text: str, *, store_dir: Path, brain_dir: Path, extra_env: dict = None) -> dict:
    env = os.environ.copy()
    env["GRAVITY_METRICS_HOME"] = str(store_dir)
    env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(script)],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"Hook {script.name} rc={proc.returncode} stderr={proc.stderr}")
    return json.loads(proc.stdout)


def _call_mcp_stdio(requests: list, store_dir: Path, brain_dir: Path, extra_env: dict = None) -> list:
    env = os.environ.copy()
    env["GRAVITY_METRICS_HOME"] = str(store_dir)
    env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    init_reqs = [
        {"jsonrpc": "2.0", "id": 1000, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in init_reqs + requests) + "\n"
    proc = subprocess.run(
        [sys.executable, "-B", str(SERVER_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"MCP server rc={proc.returncode} stderr={proc.stderr}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("id") == 1000:
            continue
        out.append(obj)
    return out


class TestLifecycleAndUxV050(unittest.TestCase):
    def test_full_session_dedup_and_post_capture_pending_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"

            cid_full = "f0001111-2222-3333-4444-555566667777"
            steps_v1 = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T10:00:00Z", "content": "/gravity run initial"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 50_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T10:01:00Z", "content": "/gravity-metrics run-full --full"},
                {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-23T10:01:01Z", "content": METRICS_MARKER},
            ]
            _write_transcript(brain / cid_full, steps_v1)

            m_full = collect_run_metrics(cid_full, brain, CATALOG_PATH, gravity_window=False)
            rec_full, _ = record_session_metrics(m_full, store_dir=store, history_path=hist_path, label="run-full")
            self.assertIsNone(rec_full.get("window_start_step"))

            # Ajout de nouveaux steps dans la conversation --full
            steps_v2 = steps_v1 + [
                {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T10:05:00Z", "content": "/gravity-workflow fast fix"},
                {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T10:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": []},
            ]
            _write_transcript(brain / cid_full, steps_v2)

            # Ne doit pas réapparaître dans pending ni créer de doublon sans --force
            pending = list_pending_sessions(brain, hist_path, store_dir=store)
            self.assertEqual([p["conversation_id"] for p in pending], [])
            recs_loaded = load_history(hist_path)
            self.assertIsNotNone(find_archived_session(recs_loaded, cid_full, window_start_step=4))
            m_retry = collect_run_metrics(cid_full, brain, CATALOG_PATH, gravity_window=True)
            rec_retry, _ = record_session_metrics(m_retry, store_dir=store, history_path=hist_path, label="dup-try")
            self.assertTrue(rec_retry.get("already_archived"))
            self.assertEqual(len(load_history(hist_path)), 1)

            # Cas 2 : session fenêtrée avec nouvelle activité post-capture -> réapparaît dans pending !
            cid_win = "w0001111-2222-3333-4444-555566667777"
            win_steps_1 = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T11:00:00Z", "content": "/gravity phase 1"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T11:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T11:01:00Z", "content": "/gravity-metrics stop phase-1"},
                {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-23T11:01:01Z", "content": METRICS_MARKER},
            ]
            _write_transcript(brain / cid_win, win_steps_1)
            m_p1 = collect_run_metrics(cid_win, brain, CATALOG_PATH, gravity_window=True)
            record_session_metrics(m_p1, store_dir=store, history_path=hist_path, label="phase-1")

            win_steps_2 = win_steps_1 + [
                {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T11:05:00Z", "content": "/gravity-workflow phase 2"},
                {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T11:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
            ]
            tpath_win_2 = _write_transcript(brain / cid_win, win_steps_2)

            resolved = resolve_gravity_window(tpath_win_2, current_prompt_is_metrics=False)
            self.assertEqual(resolved["mode"], "gravity_session")
            self.assertEqual(resolved["start_step_index"], 4)
            pending2 = list_pending_sessions(brain, hist_path, store_dir=store)
            self.assertEqual([p["conversation_id"] for p in pending2], [cid_win])

    def test_workflow_profile_precedence_centralized_over_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "MonWorkspace"
            (ws / ".agents" / "gravity").mkdir(parents=True)
            (ws / ".agents" / "gravity" / "profile.json").write_text(
                json.dumps({"mode": "low", "pace": "fast", "verification": "unsafe"}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GRAVITY_WORKFLOW_HOME": str(root / "wf_empty")}, clear=False):
                self.assertEqual(read_gravity_profile_summary(ws), "low · fast · unsafe")

            (ws / ".agents" / "gravity-workflow").mkdir(parents=True)
            (ws / ".agents" / "gravity-workflow" / "profile.json").write_text(
                json.dumps({"mode": "high", "pace": "slow", "verification": "safe"}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GRAVITY_WORKFLOW_HOME": str(root / "wf_empty")}, clear=False):
                self.assertEqual(read_gravity_profile_summary(ws), "high · slow · safe")

            wf_home = root / "gravity_home" / "workflow"
            h_digest = hashlib.sha256(str(ws.resolve()).encode("utf-8")).hexdigest()[:16]
            prof_dir = wf_home / "workspaces" / h_digest
            prof_dir.mkdir(parents=True)
            (prof_dir / "profile.json").write_text(
                json.dumps({"mode": "evo", "current_level": "medium", "pace": "slow", "verification": "safe"}),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GRAVITY_WORKFLOW_HOME": str(wf_home)}, clear=False):
                self.assertEqual(read_gravity_profile_summary(ws), "evo→medium · slow · safe")

    def test_session_start_hook_and_hybrid_start_stop_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetHybrid"
            ws.mkdir()
            cid = "a1b2c3d4-1111-2222-3333-444455556666"

            # 1) SessionStart hook : stdin invalide -> {} sans crash, stdin valide -> session_meta.json sans toucher ws
            res_bad = _run_py_hook(START_HOOK, "{broken-json", store_dir=store, brain_dir=brain)
            self.assertEqual(res_bad, {})
            res_ok = _run_py_hook(
                START_HOOK,
                json.dumps({"conversationId": cid, "cwd": str(ws)}),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(res_ok, {})
            self.assertEqual(list(ws.iterdir()), [])
            meta = load_session_meta(store)
            self.assertIn(cid, meta)
            self.assertEqual(meta[cid]["project"], "ProjetHybrid")

            # 2) /gravity-metrics start mon-jalon -> active_captures.json + terminate (0 token)
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T12:00:00Z", "content": "Chat initial"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T12:00:05Z", "model": "gemini-4-neon", "input_tokens": 900_000, "cache_read_tokens": 0, "output_tokens": 5_000, "tool_calls": []},
                ],
            )
            out_start = _run_py_hook(
                PRE_HOOK,
                json.dumps({"conversationId": cid, "transcriptPath": str(brain / cid / ".system_generated" / "logs" / "transcript.jsonl"), "cwd": str(ws), "lastUserInput": "/gravity-metrics start mon-jalon"}),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(out_start.get("terminationBehavior"), "terminate")
            caps = load_active_captures(store)
            self.assertEqual(caps[cid]["label"], "mon-jalon")
            self.assertEqual(caps[cid]["start_step"], 2)

            # 3) Activité pendant la capture -> visible "is_active" dans pending avec project="ProjetHybrid" via session_meta.json
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T12:00:00Z", "content": "Chat initial"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T12:00:05Z", "model": "gemini-4-neon", "input_tokens": 900_000, "cache_read_tokens": 0, "output_tokens": 5_000, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T12:01:00Z", "content": "Implémenter module X"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T12:01:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 25_000, "cache_read_tokens": 0, "output_tokens": 800, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "git push origin dev"}}]},
                ],
            )
            pend = list_pending_sessions(brain, store / "data" / "history.jsonl", store_dir=store)
            self.assertEqual(len(pend), 1)
            self.assertTrue(pend[0]["is_active"])
            self.assertEqual(pend[0]["project"], "ProjetHybrid")
            self.assertEqual(pend[0]["window_start_step"], 2)

            # 4) /gravity-metrics finish -> scelle strictement [2..end], retire active_captures.json et exclut gemini-4-neon pré-start
            out_stop = _run_py_hook(
                PRE_HOOK,
                json.dumps({"conversationId": cid, "transcriptPath": str(brain / cid / ".system_generated" / "logs" / "transcript.jsonl"), "cwd": str(ws), "lastUserInput": "/gravity-metrics finish"}),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertNotIn("terminationBehavior", out_stop)
            self.assertEqual(load_active_captures(store), {})
            recs = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["label"], "mon-jalon")
            self.assertEqual(recs[0]["input_tokens"], 25_000)
            self.assertEqual(recs[0]["project"], "ProjetHybrid")
            self.assertEqual(recs[0]["current_phase"], "Clôture / Archivé")
            self.assertEqual([m["id"] for m in recs[0]["milestones"]["phases"]], ["production", "cloture"])

    def test_architect_archive_auto_archive_and_bridge_milestones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            bridge_home = root / "bridge"
            db_path = bridge_home / "bridge.db"
            ws = root / "Atelier"
            ws.mkdir()

            cid = "arch0001-1111-2222-3333-444455556666"
            child_cid = "arch0002-1111-2222-3333-444455556666"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T14:00:00Z", "content": "/gravity-architect spec ux-v05"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T14:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": [{"name": "mcp_gravity-bridge_bridge_send_message"}]},
                    {"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-23T14:00:11Z", "content": json.dumps({"thread_id": "thr-arch", "to_workspace": "gravity-metrics"})},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T14:02:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "git push origin dev"}}]},
                ],
            )
            _write_transcript(
                brain / child_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T14:00:20Z", "content": "[GRAVITY-BRIDGE | thread=thr-arch]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T14:01:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 30_000, "cache_read_tokens": 0, "output_tokens": 1_000, "tool_calls": []},
                ],
            )
            bridge_home.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(db_path))
            con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)")
            con.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, action TEXT, target_conversation_id TEXT, status TEXT, created_at TEXT)")
            con.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("thr-arch", "gravity-studio", "gravity-metrics", "handoff", "open", child_cid, cid, "2026-11-23T14:00:15Z"))
            con.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)", ("job-arch", "thr-arch", "gravity-metrics", "new_conversation", child_cid, "claimed", "2026-11-23T14:00:15Z"))
            con.commit()
            con.close()

            # Job Bridge ouvert -> /gravity-architect archive passe en passthrough silencieux sans archiver
            tpath = brain / cid / ".system_generated" / "logs" / "transcript.jsonl"
            out_blocked = _run_py_hook(
                PRE_HOOK,
                json.dumps({"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "lastUserInput": "/gravity-architect archive mon-lot"}),
                store_dir=store,
                brain_dir=brain,
                extra_env={"GRAVITY_BRIDGE_HOME": str(bridge_home)},
            )
            self.assertEqual(out_blocked, {})
            self.assertEqual(load_history(store / "data" / "history.jsonl"), [])

            # Clôture job Bridge -> /gravity-architect archive <slug> archive automatiquement avec 4 jalons
            con = sqlite3.connect(str(db_path))
            con.execute("UPDATE jobs SET status = 'completed' WHERE id = 'job-arch'")
            con.commit()
            con.close()
            out_ok = _run_py_hook(
                PRE_HOOK,
                json.dumps({"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "lastUserInput": "/gravity-architect archive mon-lot"}),
                store_dir=store,
                brain_dir=brain,
                extra_env={"GRAVITY_BRIDGE_HOME": str(bridge_home)},
            )
            self.assertEqual(out_ok, {})
            recs = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["label"], "mon-lot")
            self.assertEqual(recs[0]["project"], "Atelier")
            self.assertEqual([m["id"] for m in recs[0]["milestones"]["phases"]], ["conception", "production", "verification", "cloture"])
            self.assertEqual(recs[0]["current_phase"], "Clôture / Archivé")

    def test_mcp_ignore_meta_edit_and_fast_unchanged_poll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjMeta"
            ws.mkdir()
            cid_arch = "m0001111-2222-3333-4444-555566667777"
            cid_pend = "m0002222-2222-3333-4444-555566667777"

            _write_transcript(
                brain / cid_arch,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T08:00:00Z", "content": "/gravity run"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T08:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / cid_pend,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T09:00:00Z", "content": "/gravity other"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 150, "tool_calls": []},
                ],
            )
            archive_conversation(
                cid_arch,
                brain_dir=brain,
                store_dir=store,
                history_path=store / "data" / "history.jsonl",
                catalog_path=CATALOG_PATH,
                label="init-run",
                project="ProjAlpha",
                note="initial note",
            )
            cost_before = load_history(store / "data" / "history.jsonl")[0]["cost_usd"]

            resps = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_ignore_session", "arguments": {"conversation_id": cid_pend, "reason": "bruit"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "metrics_unignore_session", "arguments": {"conversation_id": cid_pend}}},
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "metrics_update_session_meta", "arguments": {"target": "init-run", "label": "renamed-run", "project": "ProjBeta", "note": "updated note"}}},
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                ],
                store,
                brain,
            )
            by_id = {r["id"]: r["result"]["structuredContent"] for r in resps}
            self.assertEqual(len(by_id[1]["pending_sessions"]), 1)
            self.assertEqual(len(by_id[2]["pending_sessions"]), 0)
            self.assertEqual(len(by_id[2]["ignored_sessions"]), 1)
            self.assertTrue(by_id[2]["ignored_sessions"][0]["ignored"])
            self.assertEqual(by_id[2]["ignored_sessions"][0]["ignored_reason"], "bruit")
            self.assertEqual(len(by_id[3]["pending_sessions"]), 0)
            self.assertEqual(len(by_id[3]["ignored_sessions"]), 1)
            self.assertEqual(len(by_id[4]["pending_sessions"]), 1)
            self.assertEqual(len(by_id[4]["ignored_sessions"]), 0)
            self.assertEqual(by_id[5]["updated"]["label"], "renamed-run")
            self.assertEqual(by_id[5]["updated"]["project"], "ProjBeta")
            self.assertEqual(by_id[5]["updated"]["note"], "updated note")
            self.assertEqual(by_id[5]["updated"]["cost_usd"], cost_before)

            sig = by_id[6]["signature"]
            self.assertIn("hist_sig", by_id[6])
            self.assertIn("pending_sig", by_id[6])
            self.assertIn("audits_sig", by_id[6])

            # Test in-process de _fast_unchanged_signature (< 5 ms sur poll inchangé)
            import mcp.server as mcp_server  # noqa: E402

            with mock.patch.dict(
                os.environ,
                {"GRAVITY_METRICS_HOME": str(store), "ANTIGRAVITY_BRAIN_DIR": str(brain)},
                clear=False,
            ):
                mcp_server._handle_metrics_get_state({})
                t0 = time.perf_counter()
                fast_res = mcp_server._handle_metrics_get_state({"since_signature": sig})
                dt_ms = (time.perf_counter() - t0) * 1000.0
            self.assertFalse(fast_res["structuredContent"]["changed"])
            self.assertEqual(fast_res["structuredContent"]["signature"], sig)
            self.assertLess(dt_ms, 50.0)

    def test_markdown_view_no_loop_on_gt_entities(self) -> None:
        js = MARKDOWN_VIEW_JS
        self.assertIn("isQuoteLine", js)
        self.assertIn("stripQuotePrefix", js)
        self.assertIn("md-callout", js)
        node_bin = shutil.which("node")
        if not node_bin:
            return
        harness = (
            "function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
            + js
            + "\nconst out = renderSafeMarkdown('> citation simple\\n> [!WARNING]\\n> Alerte critique\\n\\n# Titre 1\\n## Titre 2\\n- item 1\\n  - sub 1');\n"
            + "if (!out.includes('blockquote') || !out.includes('md-callout-warning') || !out.includes('<h1>') || !out.includes('<ul>')) process.exit(2);\n"
            + "const loopCheck = renderSafeMarkdown('&gt; deja echappe');\n"
            + "if (!loopCheck.includes('blockquote')) process.exit(3);\n"
        )
        proc = subprocess.run(
            [node_bin, "-e", harness],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
