#!/usr/bin/env python3
"""Tests v0.5.0 : correctifs P0, cycle hybride, jalons, SessionStart, caches et UI sobre."""

import hashlib
import json
import os
from pathlib import Path
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
from metrics.store import update_session_metadata  # noqa: E402


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
                {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T11:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "edit_file"}]},
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

            # 0) hooks.json désactive SessionStart dans un groupe dédié tout en gardant PreInvocation actif
            hooks_cfg = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))
            self.assertIs(hooks_cfg["gravity-metrics-session"].get("enabled"), False)
            self.assertNotIn("PreInvocation", hooks_cfg["gravity-metrics-session"])
            self.assertIn("SessionStart", hooks_cfg["gravity-metrics-session"])
            self.assertIs(hooks_cfg["gravity-metrics-command"].get("enabled"), True)
            self.assertNotIn("SessionStart", hooks_cfg["gravity-metrics-command"])
            self.assertIn("PreInvocation", hooks_cfg["gravity-metrics-command"])

            # 1) SessionStart hook (appel direct) : stdin invalide -> {} sans crash, stdin valide -> session_meta.json sans toucher ws
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
            self.assertEqual(recs[0]["current_phase"], "Clôture / Enregistrée")
            self.assertEqual([m["id"] for m in recs[0]["milestones"]["phases"]], ["production", "cloture"])

    def test_architect_archive_auto_archive_and_bridge_milestones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "Atelier"
            ws.mkdir()

            cid = "arch0001-1111-2222-3333-444455556666"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-23T14:00:00Z", "content": "/gravity-architect spec ux-v05"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T14:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": [{"name": "write_file"}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T14:02:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "git push origin dev"}}]},
                ],
            )

            # /gravity-architect archive <slug> reste un passthrough silencieux sans archivage prématuré
            tpath = brain / cid / ".system_generated" / "logs" / "transcript.jsonl"
            out_ok = _run_py_hook(
                PRE_HOOK,
                json.dumps({"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "lastUserInput": "/gravity-architect archive mon-lot"}),
                store_dir=store,
                brain_dir=brain,
            )
            self.assertEqual(out_ok, {})
            self.assertEqual(load_history(store / "data" / "history.jsonl"), [])
            self.assertFalse((store / "reports").exists())

            # Archivage explicite en fin de cycle conserve les 4 jalons dont Clôture / Enregistrée
            saved_rec, recs, _ = archive_conversation(
                cid,
                store_dir=store,
                history_path=store / "data" / "history.jsonl",
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                label="mon-lot",
                project="Atelier",
            )
            self.assertEqual(len(recs), 1)
            self.assertEqual(saved_rec["label"], "mon-lot")
            self.assertEqual(saved_rec["project"], "Atelier")
            self.assertEqual([m["id"] for m in saved_rec["milestones"]["phases"]], ["conception", "production", "verification", "cloture"])
            self.assertEqual(saved_rec["current_phase"], "Clôture / Enregistrée")

    def test_ignore_unignore_and_update_session_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"
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
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-23T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 150, "tool_calls": [{"name": "edit_file"}]},
                ],
            )
            archive_conversation(
                cid_arch,
                brain_dir=brain,
                store_dir=store,
                history_path=hist_path,
                catalog_path=CATALOG_PATH,
                label="init-run",
                project="ProjAlpha",
                note="initial note",
            )
            cost_before = load_history(hist_path)[0]["cost_usd"]

            self.assertEqual(len(list_pending_sessions(brain, hist_path, store_dir=store)), 1)
            ignore_session(cid_pend, reason="bruit", store_dir=store)
            self.assertEqual(len(list_pending_sessions(brain, hist_path, store_dir=store)), 0)
            self.assertEqual(load_ignored_sessions(store)[cid_pend]["reason"], "bruit")
            unignore_session(cid_pend, store_dir=store)
            self.assertEqual(len(list_pending_sessions(brain, hist_path, store_dir=store)), 1)

            updated, _ = update_session_metadata(
                "init-run",
                label="renamed-run",
                project="ProjBeta",
                note="updated note",
                store_dir=store,
                history_path=hist_path,
            )
            self.assertEqual(updated["label"], "renamed-run")
            self.assertEqual(updated["project"], "ProjBeta")
            self.assertEqual(updated["note"], "updated note")
            self.assertEqual(updated["cost_usd"], cost_before)

            # Migration SPI-1 : dashboard_provider ignore/unignore/update-meta & fast unchanged poll (< 50 ms)
            from metrics import record_session_meta

            scripts_dir = str(PLUGIN_ROOT / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import dashboard_provider

            record_session_meta(cid_pend, cwd=str(root / "ProjBeta"), project="ProjBeta", store_dir=store)
            view_0 = dashboard_provider.get_metrics_view(m_home=store, brain_dir=brain)
            self.assertEqual(len(view_0["pending_sessions"]), 1)
            self.assertEqual(len(view_0["ignored_sessions"]), 0)

            res_ign = dashboard_provider.run_action(
                "ignore", {"target": cid_pend, "reason": "bruit-spi1"}, m_home=store, brain_dir=brain
            )
            self.assertTrue(res_ign["ok"])
            view_1 = dashboard_provider.get_metrics_view(m_home=store, brain_dir=brain)
            self.assertEqual(len(view_1["pending_sessions"]), 0)
            self.assertEqual(len(view_1["ignored_sessions"]), 1)
            self.assertEqual(view_1["ignored_sessions"][0]["ignored_reason"], "bruit-spi1")

            res_unign = dashboard_provider.run_action(
                "unignore", {"target": cid_pend}, m_home=store, brain_dir=brain
            )
            self.assertTrue(res_unign["ok"])
            res_meta = dashboard_provider.run_action(
                "update-meta",
                {"target": "renamed-run", "label": "renamed-spi1", "project": "ProjGamma", "note": "spi1 note"},
                m_home=store,
                brain_dir=brain,
            )
            self.assertTrue(res_meta["ok"])
            self.assertEqual(res_meta["record"]["label"], "renamed-spi1")
            self.assertEqual(res_meta["record"]["cost_usd"], cost_before)

            view_2 = dashboard_provider.get_metrics_view(m_home=store, brain_dir=brain)
            sig = view_2["signature"]
            t0 = time.perf_counter()
            fast_res = dashboard_provider.get_metrics_view(since_signature=sig, m_home=store, brain_dir=brain)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            self.assertFalse(fast_res["changed"])
            self.assertTrue(fast_res["unchanged"])
            self.assertEqual(fast_res["signature"], sig)
            self.assertLess(dt_ms, 50.0)

    def test_current_turn_start_step_and_tolerant_history_upsert(self) -> None:
        from metrics.history import to_session_record, upsert_session
        from metrics.lifecycle import (
            current_turn_start_step_in_transcript,
            next_step_index_in_transcript,
        )

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            self.assertEqual(current_turn_start_step_in_transcript(None), 0)
            self.assertEqual(current_turn_start_step_in_transcript(brain / "missing.jsonl"), 0)

            # Nouveau tour déjà flushé suivi d'un SYSTEM_MESSAGE -> retourne 0 (et next_step=2 intact)
            t1 = _write_transcript(
                brain / "t1",
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "/gravity-architect spec s1"},
                    {"step_index": 1, "type": "SYSTEM_MESSAGE", "status": "DONE", "content": "caveman"},
                ],
            )
            self.assertEqual(current_turn_start_step_in_transcript(t1), 0)
            self.assertEqual(next_step_index_in_transcript(t1), 2)

            # Reprise de conversation : USER_INPUT 72 non suivi d'un PLANNER_RESPONSE + SYSTEM_MESSAGE 73 -> 72
            t2 = _write_transcript(
                brain / "t2",
                [
                    {"step_index": 70, "type": "USER_INPUT", "status": "DONE", "content": "Avant"},
                    {"step_index": 71, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                    {"step_index": 72, "type": "USER_INPUT", "status": "DONE", "content": "/gravity-architect handoff"},
                    {"step_index": 73, "type": "SYSTEM_MESSAGE", "status": "DONE", "content": "hook"},
                ],
            )
            self.assertEqual(current_turn_start_step_in_transcript(t2), 72)

            # Tour déjà suivi d'un PLANNER_RESPONSE -> repli sur next_step_index_in_transcript
            t3 = _write_transcript(
                brain / "t3",
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Tour 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                    {"step_index": 2, "type": "SYSTEM_MESSAGE", "status": "DONE", "content": "post"},
                ],
            )
            self.assertEqual(current_turn_start_step_in_transcript(t3), 3)

            # find_archived_session & upsert_session tolérance ±2 steps + champs root_last_step_index/root_llm_calls
            cid_h = "h0001111-2222-3333-4444-555566667777"
            _write_transcript(
                brain / cid_h,
                [
                    {"step_index": 72, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "/gravity-architect spec x"},
                    {"step_index": 73, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 74, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3000, "cache_read_tokens": 0, "output_tokens": 150, "tool_calls": []},
                ],
            )
            m_h = collect_run_metrics(cid_h, brain, CATALOG_PATH, gravity_window=True)
            rec_new = to_session_record(m_h, label="new-lbl")
            self.assertEqual(rec_new["status"], "archived")
            self.assertTrue(rec_new["archived_at"])
            self.assertEqual(rec_new["window_start_step"], 72)
            self.assertEqual(rec_new["root_last_step_index"], 74)
            self.assertEqual(rec_new["root_llm_calls"], 2)

            old_rec = dict(rec_new)
            old_rec["label"] = "old-lbl"
            old_rec["window_start_step"] = 73
            self.assertIsNotNone(find_archived_session([old_rec], cid_h, 72))
            self.assertIsNone(find_archived_session([old_rec], cid_h, 76))
            merged = upsert_session([old_rec], rec_new)
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["label"], "new-lbl")
            self.assertEqual(merged[0]["window_start_step"], 72)

            # Exports publics, badge "archivé" et tableau terminal/pending
            import metrics as metrics_pkg

            self.assertIs(metrics_pkg.record_conversation, archive_conversation)
            self.assertIn("current_turn_start_step_in_transcript", metrics_pkg.__all__)
            self.assertIn("record_conversation", metrics_pkg.__all__)
            self.assertEqual(metrics_pkg.render_terminal_table([rec_new]).count("archivé"), 2)
            self.assertIn("Statut   ", metrics_pkg.render_terminal_table([rec_new]))
            self.assertIn("cli.py archive <id>", metrics_pkg.render_pending_table([{"conversation_id": cid_h, "updated_at": "2026-11-25T10:00:00Z", "llm_calls": 2, "window_mode": "gravity_session", "title": "t"}]))
            self.assertIn("tout est archivé", metrics_pkg.render_pending_markdown([]))

    def test_expected_prompt_anchoring_and_cli_from_step(self) -> None:
        from metrics.lifecycle import current_turn_start_step_in_transcript

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"
            cid = "4de96631-1111-2222-3333-444455556666"
            prompt_txt = "/gravity-architect wave-1 audit"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 52, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "Ancien tour"},
                    {"step_index": 53, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:05Z", "model": "gemini-4-neon", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                    {
                        "step_index": 54,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:01:00Z",
                        "content": f"<ADDITIONAL_METADATA>meta</ADDITIONAL_METADATA>\n<USER_REQUEST>\n{prompt_txt}\n</USER_REQUEST>",
                    },
                    {"step_index": 55, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                    {"step_index": 132, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-25T10:02:00Z", "content": "reactive wakeup"},
                ],
            )
            # Sans expected_prompt ou prompt absent -> repli exact sur max_step + 1 (133)
            self.assertEqual(current_turn_start_step_in_transcript(tpath), 133)
            self.assertEqual(current_turn_start_step_in_transcript(tpath, expected_prompt="/gravity-architect absent"), 133)
            # Avec expected_prompt -> ancre sur step 54 malgré PLANNER_RESPONSE + SYSTEM_MESSAGE intermédiaires
            self.assertEqual(current_turn_start_step_in_transcript(tpath, expected_prompt=prompt_txt), 54)

            # CLI record --from-step 54 --force et rejet des combinaisons invalides
            cli_script = PLUGIN_ROOT / "scripts" / "cli.py"
            common = ["--brain-dir", str(brain), "--store", str(store), "--history", str(hist_path), "--json"]

            proc_neg = subprocess.run(
                [sys.executable, "-B", str(cli_script), "record", cid, "--from-step", "-1", *common],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(proc_neg.returncode, 0)

            proc_both = subprocess.run(
                [sys.executable, "-B", str(cli_script), "record", cid, "--from-step", "54", "--full", *common],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(proc_both.returncode, 0)

            proc_ok = subprocess.run(
                [sys.executable, "-B", str(cli_script), "record", cid, "--from-step", "54", "--force", *common],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0, msg=proc_ok.stderr or proc_ok.stdout)
            payload_ok = json.loads(proc_ok.stdout)
            self.assertTrue(payload_ok["ok"])
            self.assertEqual(payload_ok["record"]["window_start_step"], 54)
            self.assertEqual(payload_ok["record"]["input_tokens"], 12_000)

    def test_remove_orphan_purge_state_if_exists_and_cli_flags(self) -> None:
        import metrics as metrics_pkg
        from metrics import (
            apply_remove,
            purge_lifecycle_state,
            record_session_meta,
            remove_session_meta,
            start_active_capture,
        )

        self.assertIn("remove_session_meta", metrics_pkg.__all__)
        self.assertIn("purge_lifecycle_state", metrics_pkg.__all__)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"
            cli_script = PLUGIN_ROOT / "scripts" / "cli.py"
            cid_1 = "orphan11-1111-4111-8111-111111111111"
            cid_2 = "orphan22-2222-4222-8222-222222222222"

            # 1) remove_session_meta & purge_lifecycle_state atomiques
            record_session_meta(cid_1, cwd=str(root / "Proj"), project="Proj", store_dir=store)
            self.assertTrue(remove_session_meta(cid_1, store_dir=store))
            self.assertFalse(remove_session_meta(cid_1, store_dir=store))
            self.assertNotIn(cid_1, load_session_meta(store))

            record_session_meta(cid_1, cwd=str(root / "Proj"), project="Proj", store_dir=store)
            start_active_capture(cid_1, start_step=0, label="orphan-slug", store_dir=store)
            ignore_session(cid_1, reason="stale", store_dir=store)
            reports_dir = store / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "orphan-slug.json").write_text("{}", encoding="utf-8")
            (reports_dir / "orphan-slug.md").write_text("# orphan\n", encoding="utf-8")

            # Mode strict par défaut sur historique vide -> ValueError
            with self.assertRaises(ValueError):
                apply_remove("orphan-slug", store_dir=store, history_path=hist_path)

            # CLI remove strict échoue sans --if-exists / --purge-state
            proc_strict = subprocess.run(
                [sys.executable, "-B", str(cli_script), "remove", "orphan-slug", "--store", str(store), "--history", str(hist_path), "--json"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertNotEqual(proc_strict.returncode, 0)

            # apply_remove avec purge_state=True & purge_reports=True nettoie orphelins lifecycle + rapports
            removed_orp, rem_orp = apply_remove(
                "orphan-slug",
                store_dir=store,
                history_path=hist_path,
                conversation_id=cid_1,
                purge_reports=True,
                purge_state=True,
                if_exists=True,
            )
            self.assertEqual(rem_orp, 0)
            self.assertFalse(removed_orp["already_absent"])
            self.assertEqual(
                removed_orp["cleaned_sources"],
                {"history": False, "reports": True, "active_captures": True, "ignored": True, "session_meta": True},
            )
            self.assertFalse((reports_dir / "orphan-slug.json").exists())
            self.assertFalse((reports_dir / "orphan-slug.md").exists())
            self.assertEqual(load_active_captures(store), {})
            self.assertEqual(load_ignored_sessions(store), {})
            self.assertEqual(load_session_meta(store), {})

            # Second appel idempotent (if_exists=True) -> already_absent=True
            removed_idemp, _ = apply_remove(
                "orphan-slug",
                store_dir=store,
                history_path=hist_path,
                conversation_id=cid_1,
                purge_reports=True,
                purge_state=True,
                if_exists=True,
            )
            self.assertTrue(removed_idemp["already_absent"])
            self.assertFalse(any(removed_idemp["cleaned_sources"].values()))

            # 2) CLI --purge-state & --if-exists sur préfixe d'UUID orphelin
            record_session_meta(cid_2, cwd=str(root / "Proj2"), project="Proj2", store_dir=store)
            ignore_session(cid_2, reason="orphan-cli", store_dir=store)
            proc_purge = subprocess.run(
                [sys.executable, "-B", str(cli_script), "remove", cid_2[:8], "--store", str(store), "--history", str(hist_path), "--purge-state", "--json"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(proc_purge.returncode, 0, msg=proc_purge.stderr or proc_purge.stdout)
            out_purge = json.loads(proc_purge.stdout)
            self.assertTrue(out_purge["ok"])
            self.assertFalse(out_purge["already_absent"])
            self.assertTrue(out_purge["cleaned_sources"]["ignored"])
            self.assertTrue(out_purge["cleaned_sources"]["session_meta"])

            proc_if_exists = subprocess.run(
                [sys.executable, "-B", str(cli_script), "remove", cid_2[:8], "--store", str(store), "--history", str(hist_path), "--if-exists", "--json"],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(proc_if_exists.returncode, 0, msg=proc_if_exists.stderr or proc_if_exists.stdout)
            out_ie = json.loads(proc_if_exists.stdout)
            self.assertTrue(out_ie["ok"])
            self.assertTrue(out_ie["already_absent"])
            self.assertEqual(purge_lifecycle_state(cid_2, store_dir=store), {"active_captures": False, "ignored": False, "session_meta": False})


if __name__ == "__main__":
    unittest.main()
