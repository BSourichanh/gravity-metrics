#!/usr/bin/env python3
"""Tests unitaires et d'intégrité pour Gravity Metrics (100 % stdlib)."""

import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    METRICS_MARKER,
    classify_agent,
    collect_run_metrics,
    compute_call_cost,
    default_store_dir,
    discover_agent_tree,
    list_gravity_conversations,
    load_history,
    load_pricing_catalog,
    parse_agent_transcript,
    read_gravity_profile_summary,
    record_session_metrics,
    remove_session,
    render_metrics_markdown,
    render_session_detail_markdown,
    resolve_gravity_window,
    resolve_model_pricing,
    save_history,
    slugify_label,
    suggest_auto_label,
    to_session_record,
    upsert_session,
)

CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
CLI_SCRIPT = PLUGIN_ROOT / "scripts" / "cli.py"


def _write_subagent_meta(
    parent_dir: Path,
    child_id: str,
    type_name: str,
    role: str,
    spawn_step: int = 2,
    state: str = "SUBAGENT_STATE_DONE",
    workspace_uris: list = None,
) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversationId": child_id,
        "subagentDescriptor": {"typeName": type_name, "role": role},
        "state": state,
        "spawnStepIndex": spawn_step,
        "workspaceUris": workspace_uris or [],
    }
    (sub_dir / f"{child_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(item if isinstance(item, str) else json.dumps(item) for item in lines) + "\n"
    tpath.write_text(raw, encoding="utf-8")
    return tpath


class TestCoreAndParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_pricing_catalog(CATALOG_PATH)

    def test_classify_agent_prioritizes_type_name(self) -> None:
        self.assertEqual(classify_agent("teamwork_explorer", "Workspace Auditor"), "discovery")
        self.assertEqual(classify_agent("teamwork_auditor", "Final Forensic Auditor"), "verification")
        self.assertEqual(classify_agent("teamwork_worker", "Builder"), "build")
        self.assertEqual(classify_agent("teamwork_test_writer", "E2E Test Writer"), "testing")
        self.assertEqual(classify_agent("unknown_type", "Security Reviewer"), "verification")
        self.assertEqual(classify_agent("research", "Security and Hooks Auditor"), "verification")
        self.assertEqual(classify_agent("research", "Codebase Explorer"), "discovery")
        self.assertEqual(classify_agent("research", "Reader"), "discovery")
        self.assertEqual(classify_agent("planner", "Arch"), "orchestration")
        self.assertEqual(classify_agent("builder", "Impl"), "build")
        self.assertEqual(classify_agent("reviewer", "Audit"), "verification")

    def test_pricing_long_context_tier_flash_flat_and_fallback(self) -> None:
        p_pro, is_fb = resolve_model_pricing("gemini-3.1-pro-preview", self.catalog, "2026_nov_standard")
        self.assertFalse(is_fb)
        c_low = compute_call_cost(100_000, 50_000, 10_000, p_pro)
        self.assertAlmostEqual(c_low["total_usd"], 0.2 + 0.01 + 0.12, places=9)

        c_high = compute_call_cost(100_000, 150_000, 10_000, p_pro)
        self.assertAlmostEqual(c_high["total_usd"], 0.4 + 0.06 + 0.18, places=9)

        p_flash, _ = resolve_model_pricing("gemini-3.8-flash-tiered", self.catalog, "2026_nov_standard")
        c_flash = compute_call_cost(100_000, 150_000, 10_000, p_flash)
        self.assertAlmostEqual(c_flash["total_usd"], 0.075 + 0.01125 + 0.0375, places=9)

        _, is_fb_u = resolve_model_pricing("unknown-model-z", self.catalog, "2026_nov_standard")
        self.assertTrue(is_fb_u)

    def test_transcript_parsing_rewinds_gaps_malformed_and_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = _write_transcript(
                Path(tmp) / "ag1",
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T00:00:00Z"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T00:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                        "tool_calls": [{"name": "read_file"}],
                    },
                    "{corrupt_json_line",
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T00:08:30Z",
                        "model": "mystery-model",
                        "input_tokens": 500,
                        "cache_read_tokens": 900,
                        "output_tokens": 50,
                        "tool_calls": [],
                    },
                    {"step_index": 2, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-21T00:08:40Z"},
                ],
            )
            parsed = parse_agent_transcript(tpath, self.catalog, "2026_nov_standard", active_gap_cap_s=120.0)
            self.assertEqual(parsed["malformed_lines"], 1)
            self.assertEqual(parsed["llm_calls"], 2)
            self.assertEqual(parsed["rewind_calls"], 1)
            self.assertEqual(parsed["error_steps"], 0)
            self.assertEqual(parsed["running_status_steps"], 1)
            self.assertEqual(parsed["unmatched_models"], ["mystery-model"])
            self.assertFalse(parsed["clean_termination"])
            self.assertAlmostEqual(parsed["active_s"], 140.0, places=2)
            self.assertAlmostEqual(parsed["idle_gap_s"], 380.0, places=2)

    def test_tree_cycles_and_gravity_conversation_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            runs_file = Path(tmp) / "runs.jsonl"
            runs_file.write_text(
                json.dumps({"session_id": "grav-run-id"})
                + "\n"
                + json.dumps({"conversation_id": "grav-conv-id"})
                + "\n",
                encoding="utf-8",
            )

            _write_transcript(
                brain / "grav-slash",
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T01:00:00Z",
                        "content": "<USER_REQUEST>\n/gravity audit workspace\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T01:00:05Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                        "tool_calls": [],
                    },
                ],
            )
            _write_subagent_meta(brain / "grav-slash", "sub-1", "teamwork_worker", "Builder")
            _write_subagent_meta(brain / "sub-1", "grav-slash", "root", "root")
            _write_transcript(
                brain / "sub-1",
                [
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T01:00:02Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 500,
                        "cache_read_tokens": 0,
                        "output_tokens": 50,
                        "tool_calls": [],
                    }
                ],
            )

            _write_transcript(
                brain / "grav-run-id",
                [{"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Build feature X"}],
            )
            _write_transcript(
                brain / "grav-conv-id",
                [{"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Fix feature Y"}],
            )
            _write_transcript(
                brain / "personal-chat",
                [{"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Recherche de stage"}],
            )

            tree = discover_agent_tree("grav-slash", brain)
            self.assertEqual(len(tree), 2)

            filtered = list_gravity_conversations(brain, include_all=False, runs_jsonl_paths=[runs_file])
            ids_filtered = {c["conversation_id"] for c in filtered}
            self.assertEqual(ids_filtered, {"grav-slash", "grav-run-id", "grav-conv-id"})

            all_convs = list_gravity_conversations(brain, include_all=True, runs_jsonl_paths=[runs_file])
            ids_all = {c["conversation_id"] for c in all_convs}
            self.assertEqual(ids_all, {"grav-slash", "grav-run-id", "grav-conv-id", "personal-chat"})


class TestGravityWindowAndProfile(unittest.TestCase):
    def test_gravity_window_excludes_pre_gravity_and_metrics_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "conv-win-1"
            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:00:00Z",
                        "content": "<USER_REQUEST>\nDiscussion préalable sans gravity\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:00:10Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 500_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 10_000,
                        "tool_calls": [{"name": "invoke_subagent"}],
                    },
                    {
                        "step_index": 2,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:05:00Z",
                        "content": "<USER_REQUEST>\n/gravity init workspace\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 3,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:05:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 100_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 10_000,
                        "tool_calls": [{"name": "invoke_subagent"}],
                    },
                    {
                        "step_index": 4,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:06:00Z",
                        "content": "<USER_REQUEST>\n/gravity fast implémente auth\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 5,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:06:20Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 200_000,
                        "cache_read_tokens": 400_000,
                        "output_tokens": 20_000,
                        "tool_calls": [],
                    },
                    {
                        "step_index": 6,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:10:00Z",
                        "content": "<USER_REQUEST>\n/gravity-metrics\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 7,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:10:05Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 900_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 50_000,
                        "tool_calls": [],
                    },
                ],
            )
            # Sous-agent avant /gravity (doit être exclu)
            _write_subagent_meta(brain / root_id, "sub-before", "research", "PreChat", spawn_step=1)
            _write_transcript(
                brain / "sub-before",
                [
                    {
                        "step_index": 0,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:00:12Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 300_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 10_000,
                        "tool_calls": [],
                    }
                ],
            )
            # Sous-agent pendant /gravity (doit être inclus)
            _write_subagent_meta(brain / root_id, "sub-inside", "teamwork_worker", "Builder", spawn_step=3)
            _write_transcript(
                brain / "sub-inside",
                [
                    {
                        "step_index": 0,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:05:12Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 100_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 10_000,
                        "tool_calls": [],
                    }
                ],
            )
            # Sous-agent après /gravity-metrics (doit être exclu)
            _write_subagent_meta(brain / root_id, "sub-after", "teamwork_reviewer", "PostAudit", spawn_step=7)
            _write_transcript(
                brain / "sub-after",
                [
                    {
                        "step_index": 0,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T10:10:06Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 300_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 10_000,
                        "tool_calls": [],
                    }
                ],
            )

            m = collect_run_metrics(
                root_id,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                project="mon-projet",
                gravity_profile="evo→medium · slow · safe",
            )
            s = m["summary"]
            self.assertEqual(m["window"]["mode"], "gravity_session")
            self.assertEqual(m["window"]["start_step_index"], 2)
            self.assertEqual(m["window"]["end_step_index"], 6)
            self.assertEqual(m["window"]["excluded_before_steps"], 2)
            self.assertEqual(m["window"]["excluded_after_steps"], 2)
            self.assertEqual(s["total_agents"], 2)
            self.assertEqual(s["total_llm_calls"], 3)
            self.assertEqual(s["input_tokens"], 400_000)
            self.assertEqual(s["cache_read_tokens"], 400_000)
            self.assertEqual(s["output_tokens"], 40_000)
            self.assertNotIn("gemini-4-neon", m["by_model"])
            self.assertEqual(m["project"], "mon-projet")
            self.assertEqual(m["gravity_profile"], "evo→medium · slow · safe")

    def test_multiple_gravity_sessions_in_same_chat_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            cid = "conv-multi"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:00:00Z", "content": "/gravity run un"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T11:01:01Z", "content": METRICS_MARKER},
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:05:00Z", "content": "/gravity run deux"},
                    {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            # Fin de la 2e session (hook avant flush du 2e /gravity-metrics)
            m2 = collect_run_metrics(
                cid,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                current_prompt_is_metrics=True,
                current_prompt="/gravity-metrics",
            )
            self.assertEqual(m2["window"]["mode"], "gravity_session")
            self.assertEqual(m2["window"]["start_step_index"], 4)
            self.assertEqual(m2["summary"]["input_tokens"], 2000)

            # Deux entrées dans history.jsonl pour la même conversation (window_start_step 0 vs 4)
            rec1 = {
                "label": "run-1",
                "conversation_id": cid,
                "window_start_step": 0,
                "cost_usd": 1.0,
            }
            rec2 = to_session_record(m2, label="run-2")
            records = upsert_session([rec1], rec2)
            self.assertEqual(len(records), 2)
            md = render_metrics_markdown(records)
            self.assertIn("run-1", md)
            self.assertIn("run-2", md)

            # Repli sans /gravity préalable
            _write_transcript(
                brain / "no-grav",
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Aide-moi sur Python"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            win_fb = resolve_gravity_window(brain / "no-grav" / ".system_generated" / "logs" / "transcript.jsonl", current_prompt_is_metrics=True)
            self.assertEqual(win_fb["mode"], "full_conversation")
            self.assertFalse(win_fb["has_gravity_anchor"])

    def test_handoff_turn_is_excluded_from_subsequent_capture_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            cid = "conv-handoff"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:00:00Z", "content": "/gravity tache 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T11:01:01Z", "content": METRICS_MARKER},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:01:02Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 300, "cache_read_tokens": 0, "output_tokens": 30, "tool_calls": [{"name": "open_metrics_dashboard"}]},
                    {"step_index": 5, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-21T11:01:03Z", "content": "ok"},
                    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:01:04Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 250, "cache_read_tokens": 0, "output_tokens": 20, "tool_calls": []},
                ],
            )
            # Un second /gravity-metrics immédiat ne doit PAS compter le tour open_metrics_dashboard
            win_empty = resolve_gravity_window(
                tpath,
                current_prompt_is_metrics=True,
                current_prompt="/gravity-metrics",
            )
            self.assertTrue(win_empty["window_empty"])
            m_empty = collect_run_metrics(
                cid,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                current_prompt_is_metrics=True,
                current_prompt="/gravity-metrics",
            )
            self.assertEqual(m_empty["summary"]["total_llm_calls"], 0)
            self.assertEqual(m_empty["window"]["excluded_before_steps"], 7)

            # Un tour normal suivant repart proprement au USER_INPUT suivant
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:00:00Z", "content": "/gravity tache 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T11:01:01Z", "content": METRICS_MARKER},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:01:02Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 300, "cache_read_tokens": 0, "output_tokens": 30, "tool_calls": [{"name": "open_metrics_dashboard"}]},
                    {"step_index": 5, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-21T11:01:03Z", "content": "ok"},
                    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:01:04Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 250, "cache_read_tokens": 0, "output_tokens": 20, "tool_calls": []},
                    {"step_index": 7, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:05:00Z", "content": "Ajoute un test"},
                    {"step_index": 8, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:05:05Z", "model": "gemini-4-neon", "input_tokens": 4000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            m_next = collect_run_metrics(
                cid,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                current_prompt_is_metrics=True,
                current_prompt="/gravity-metrics",
            )
            self.assertEqual(m_next["window"]["mode"], "since_last_metrics")
            self.assertEqual(m_next["window"]["start_step_index"], 7)
            self.assertEqual(m_next["summary"]["total_llm_calls"], 1)
            self.assertEqual(m_next["summary"]["input_tokens"], 4000)

    def test_read_gravity_profile_summary_and_auto_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "MyApp"
            ws.mkdir()
            self.assertEqual(read_gravity_profile_summary(ws), "")

            pfile = ws / ".agents" / "gravity" / "profile.json"
            pfile.parent.mkdir(parents=True)
            pfile.write_text(
                json.dumps({"mode": "evo", "current_level": "high", "pace": "fast", "verification": "safe"}),
                encoding="utf-8",
            )
            self.assertEqual(read_gravity_profile_summary(ws), "evo→high · fast · safe")

            pfile.write_text(
                json.dumps({"mode": "ultra", "current_level": "ultra", "pace": "slow", "verification": "unsafe"}),
                encoding="utf-8",
            )
            self.assertEqual(read_gravity_profile_summary(ws), "ultra · slow · unsafe")

        lbl1 = suggest_auto_label("MyApp", "/gravity high fast ajoute le module auth", "2026-11-21T19:00:00Z", [])
        self.assertEqual(lbl1, "myapp-ajoute-module-auth")
        lbl2 = suggest_auto_label("MyApp", "/gravity high fast ajoute le module auth", "2026-11-21T19:00:00Z", [lbl1])
        self.assertEqual(lbl2, "myapp-ajoute-module-auth-2")

        with tempfile.TemporaryDirectory() as tmp2:
            brain2 = Path(tmp2) / "brain"
            cid2 = "conv-bare-gravity"
            tpath2 = _write_transcript(
                brain2 / cid2,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T21:18:00Z", "content": "<USER_REQUEST>\n/gravity \n</USER_REQUEST>"},
                    {"step_index": 1, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T21:20:00Z", "content": "<USER_REQUEST>\noui\n</USER_REQUEST>"},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T21:22:00Z", "content": "<USER_REQUEST>\nJe veux un audit complet de gravity sur les hooks\n</USER_REQUEST>"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T21:22:10Z", "model": "gemini-4-neon", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            win2 = resolve_gravity_window(tpath2, current_prompt_is_metrics=True)
            self.assertIn("audit complet", win2["first_prompt"].lower())
            auto_lbl = suggest_auto_label("Gravity", win2["first_prompt"], "2026-11-21T21:18:00Z", [])
            self.assertEqual(auto_lbl, "gravity-audit-complet-hooks")

    def test_gravity_workflow_window_and_label_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            cid = "conv-gw-1"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T09:00:00Z", "content": "Chat hors workflow"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T09:00:05Z", "model": "gemini-4-neon", "input_tokens": 250_000, "cache_read_tokens": 0, "output_tokens": 2_000, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T09:01:00Z", "content": "<USER_REQUEST>\n/gravity-workflow auto corrige bug auth\n</USER_REQUEST>"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T09:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 30_000, "cache_read_tokens": 70_000, "output_tokens": 1_500, "tool_calls": []},
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T09:02:00Z", "content": "/gravity-metrics"},
                ],
            )
            m = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True, project="MyApp")
            self.assertEqual(m["window"]["mode"], "gravity_session")
            self.assertEqual(m["window"]["start_step_index"], 2)
            self.assertEqual(m["window"]["end_step_index"], 4)
            self.assertEqual(m["summary"]["input_tokens"], 30_000)
            self.assertIn("gravity", m["summary"]["features_used"])
            self.assertNotIn("gemini-4-neon", m["by_model"])

            lbl = suggest_auto_label("MyApp", m["window"]["first_prompt"], "2026-11-22T09:01:00Z", [])
            self.assertEqual(lbl, "myapp-corrige-bug-auth")

            lbl_ns = suggest_auto_label(
                "MyApp",
                "/gravity-workflow:gravity-workflow high fast ajoute tests",
                "2026-11-22T09:05:00Z",
                [],
            )
            self.assertEqual(lbl_ns, "myapp-ajoute-tests")

    def test_default_store_dir_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            grav_home = root / "grav-home"
            explicit = root / "explicit-store"

            with mock.patch.object(Path, "home", return_value=fake_home):
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(default_store_dir(), fake_home / ".gemini" / "gravity" / "metrics")
                with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=True):
                    self.assertEqual(default_store_dir(), grav_home / "metrics")
                with mock.patch.dict(
                    os.environ,
                    {
                        "GRAVITY_HOME": str(grav_home),
                        "GRAVITY_METRICS_HOME": str(explicit),
                    },
                    clear=True,
                ):
                    self.assertEqual(default_store_dir(), explicit)


class TestHistoryAndCLI(unittest.TestCase):
    def test_slugify_label(self) -> None:
        self.assertEqual(slugify_label("v0.1 Prototype / Test!"), "v0.1-prototype-test")
        self.assertEqual(slugify_label("   ", fallback="session-abcd"), "session-abcd")

    def test_history_lifecycle_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hpath = Path(tmp) / "history.jsonl"
            rec1 = {
                "label": "v0.1-run",
                "conversation_id": "11111111-aaaa-bbbb-cccc-000000000001",
                "note": "Session initiale",
                "project": "Gravity",
                "gravity_profile": "evo→medium · slow · safe",
                "started_at": "2026-11-20T10:00:00Z",
                "wall_clock_s": 600,
                "primary_model": "gemini-3.8-flash-tiered",
                "agents": 10,
                "llm_calls": 100,
                "input_tokens": 100000,
                "cache_read_tokens": 900000,
                "output_tokens": 50000,
                "cache_hit_rate_pct": 90.0,
                "cost_usd": 10.0,
                "category_share_pct": {"orchestration": 20.0, "verification": 50.0, "build": 30.0},
                "duplicate_spawns": 2,
                "rewind_calls": 1,
                "interrupted_agents": 0,
            }
            rec2 = dict(rec1)
            rec2.update(
                {
                    "label": "v0.2-run",
                    "conversation_id": "22222222-aaaa-bbbb-cccc-000000000002",
                    "note": "Suite",
                    "project": "Gravity",
                    "cost_usd": 6.0,
                    "duplicate_spawns": 0,
                }
            )

            records = upsert_session([], rec1)
            records = upsert_session(records, rec2)
            self.assertEqual(len(records), 2)

            save_history(hpath, records)
            loaded = load_history(hpath)
            self.assertEqual(len(loaded), 2)

            md = render_metrics_markdown(loaded)
            self.assertIn("v0.1-run", md)
            self.assertIn("v0.2-run", md)
            self.assertIn("Gravity", md)
            self.assertIn("$16.0000", md)
            self.assertNotIn(".html", md)

            loaded = remove_session(loaded, "v0.1-run")
            self.assertEqual(len(loaded), 1)

    def test_record_session_metrics_harmonizes_project_casing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            history_path = store / "data" / "history.jsonl"
            brain = root / "brain"
            for cid, lbl in (("1111aaaa-0000-0000-0000-000000000001", "s1"), ("2222bbbb-0000-0000-0000-000000000002", "s2")):
                _write_transcript(
                    brain / cid,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T10:00:00Z", "content": "/gravity t"},
                        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T10:00:10Z", "model": "gemini-4-neon", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    ],
                )
            m1 = collect_run_metrics("1111aaaa-0000-0000-0000-000000000001", brain, CATALOG_PATH, label="s1", project="gravity")
            rec1, _ = record_session_metrics(m1, store_dir=store, history_path=history_path, label="s1")
            self.assertEqual(rec1["project"], "gravity")

            m2 = collect_run_metrics("2222bbbb-0000-0000-0000-000000000002", brain, CATALOG_PATH, label="s2", project="Gravity")
            rec2, _ = record_session_metrics(m2, store_dir=store, history_path=history_path, label="s2")
            self.assertEqual(rec2["project"], "gravity")

    def test_verification_ratio_na_without_build_or_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "verif-only-root"
            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T10:00:00Z", "content": "/gravity audit"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T10:00:10Z", "model": "gemini-4-neon", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "invoke_subagent"}]},
                ],
            )
            _write_subagent_meta(brain / root_id, "sub-aud", "research", "Security Auditor", spawn_step=1)
            _write_transcript(
                brain / "sub-aud",
                [
                    {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T10:00:15Z", "model": "gemini-4-neon", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            m = collect_run_metrics(root_id, brain, CATALOG_PATH, label="verif-only")
            self.assertIsNone(m["efficiency_and_waste"]["verification_to_build_cost_ratio"])
            det_md = render_session_detail_markdown(m)
            self.assertIn("N/A (sans Build/Test)", det_md)

    def test_cli_end_to_end_add_list_inspect_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            hist = root / "out" / "data" / "history.jsonl"

            cid1 = "aaaa1111-0000-0000-0000-000000000001"
            cid2 = "bbbb2222-0000-0000-0000-000000000002"
            _write_transcript(
                brain / cid1,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T02:00:00Z",
                        "content": "/gravity run 1",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T02:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 100_000,
                        "cache_read_tokens": 400_000,
                        "output_tokens": 10_000,
                        "tool_calls": [],
                    },
                ],
            )
            _write_transcript(
                brain / cid2,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T03:00:00Z",
                        "content": "/gravity run 2",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T03:00:05Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 50_000,
                        "cache_read_tokens": 200_000,
                        "output_tokens": 5_000,
                        "tool_calls": [],
                    },
                ],
            )

            def run_cli(*cli_args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    [sys.executable, "-B", str(CLI_SCRIPT), *cli_args],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )

            r1 = run_cli("add", cid1, "-l", "run-1", "-n", "init", "--brain-dir", str(brain), "--history", str(hist))
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = run_cli("add", "--latest", "-l", "run-2", "-n", "opti", "--brain-dir", str(brain), "--history", str(hist))
            self.assertEqual(r2.returncode, 0, r2.stderr)

            out_root = root / "out"
            self.assertTrue(hist.is_file())
            self.assertTrue((out_root / "METRICS.md").is_file())
            self.assertFalse((out_root / "dashboard.html").exists())

            rep_dir = out_root / "reports"
            for slug in ("run-1", "run-2"):
                self.assertTrue((rep_dir / f"{slug}.json").is_file())
                self.assertTrue((rep_dir / f"{slug}.md").is_file())
                self.assertFalse((rep_dir / f"{slug}.html").exists())

            md_txt = (out_root / "METRICS.md").read_text(encoding="utf-8")
            self.assertIn("reports/run-1.md", md_txt)
            self.assertNotIn(".html", md_txt)
            self.assertIn("`gemini-3.8-flash-tiered`", md_txt)

            r_list = run_cli("list", "--history", str(hist))
            self.assertEqual(r_list.returncode, 0, r_list.stderr)
            self.assertIn("run-1", r_list.stdout)
            self.assertIn("run-2", r_list.stdout)

            r_insp = run_cli(
                "inspect", "run-2", "--brain-dir", str(root / "missing-brain"), "--history", str(hist)
            )
            self.assertEqual(r_insp.returncode, 0, r_insp.stderr)
            self.assertIn("Inspection Gravity [run-2]", r_insp.stdout)

            r_rm = run_cli("remove", "run-1", "--history", str(hist))
            self.assertEqual(r_rm.returncode, 0, r_rm.stderr)
            self.assertEqual(len(load_history(hist)), 1)
            self.assertTrue((rep_dir / "run-1.json").is_file())

            r_rm2 = run_cli("remove", "run-2", "--purge-reports", "--history", str(hist))
            self.assertEqual(r_rm2.returncode, 0, r_rm2.stderr)
            self.assertFalse((rep_dir / "run-2.json").exists())
            self.assertFalse((rep_dir / "run-2.md").exists())

    def test_execution_mode_detection_swarm_teamwork_and_single(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp)

            _write_transcript(
                brain / "swarm-run",
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "/swarm go"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T04:00:00Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                        "tool_calls": [{"name": "swarm"}],
                    },
                    {
                        "step_index": 2,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T04:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 500,
                        "cache_read_tokens": 500,
                        "output_tokens": 50,
                        "tool_calls": [],
                    },
                ],
            )
            m_swarm = collect_run_metrics("swarm-run", brain, CATALOG_PATH, label="swarm-test")
            self.assertEqual(m_swarm["summary"]["execution_mode"], "swarm")
            self.assertIn("swarm", m_swarm["summary"]["features_used"])
            rec_swarm = to_session_record(m_swarm)
            md_swarm = render_metrics_markdown([rec_swarm])
            self.assertIn("`gemini-4-neon` (1)", md_swarm)
            self.assertIn("`gemini-3.8-flash-tiered` (1)", md_swarm)

            _write_transcript(
                brain / "single-run",
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": '<USER_REQUEST>\nAnalyse le blueprint Gravity sans lancer "/gravity"\n</USER_REQUEST>',
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T05:00:00Z",
                        "model": "gemini-3.8-flash-high",
                        "input_tokens": 200,
                        "cache_read_tokens": 0,
                        "output_tokens": 20,
                        "tool_calls": [],
                    },
                ],
            )
            m_single = collect_run_metrics("single-run", brain, CATALOG_PATH, label="single-test")
            self.assertEqual(m_single["summary"]["execution_mode"], "single")
            self.assertEqual(m_single["summary"]["features_used"], ["single"])

            _write_transcript(
                brain / "gravity-run",
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "<USER_REQUEST>\n/gravity status\n</USER_REQUEST>",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T06:00:00Z",
                        "model": "gemini-3.8-flash-high",
                        "input_tokens": 200,
                        "cache_read_tokens": 0,
                        "output_tokens": 20,
                        "tool_calls": [],
                    },
                ],
            )
            m_grav = collect_run_metrics("gravity-run", brain, CATALOG_PATH, label="grav-test")
            self.assertIn("gravity", m_grav["summary"]["features_used"])

    def test_markdown_sanitization_and_negative_projection_delta(self) -> None:
        rec = {
            "label": "feat|pipe\nline",
            "conversation_id": "33333333-aaaa-bbbb-cccc-000000000003",
            "note": "note|pipe\n2",
            "project": "proj|name",
            "started_at": "2026-11-21T10:00:00Z",
            "wall_clock_s": 120,
            "primary_model": "gemini-3.8-flash-tiered",
            "agents": 1,
            "llm_calls": 2,
            "input_tokens": 1000,
            "cache_read_tokens": 0,
            "output_tokens": 100,
            "cache_hit_rate_pct": 0.0,
            "cost_usd": 1.25,
            "cost_2027_usd": 0.50,
            "category_share_pct": {"orchestration": 100.0},
            "duplicate_spawns": 0,
            "rewind_calls": 0,
            "interrupted_agents": 0,
        }
        md = render_metrics_markdown([rec])
        self.assertIn("feat/pipe line", md)
        self.assertIn("proj/name", md)
        self.assertIn("note/pipe 2", md)
        table_rows = [ln for ln in md.splitlines() if ln.startswith("| `2026-11-21")]
        self.assertEqual(len(table_rows), 1)
        self.assertEqual(table_rows[0].count("|"), 14)

        detail_metrics = {
            "label": "neg-delta",
            "root_conversation_id": "44444444-aaaa-bbbb-cccc-000000000004",
            "pricing_profile": "2026_nov_standard",
            "secondary_pricing_profile": "2027_standard",
            "summary": {
                "first_ts": "2026-11-21T10:00:00Z",
                "last_ts": "2026-11-21T10:01:00Z",
                "wall_clock_s": 60.0,
                "parallelism_factor": 1.0,
                "sum_agent_active_s": 60.0,
                "total_agents": 1,
                "max_tree_depth": 0,
                "total_llm_calls": 1,
                "total_tool_calls": 0,
                "total_error_steps": 0,
                "error_rate_pct": 0.0,
                "total_user_inputs": 1,
                "input_tokens": 100,
                "cache_read_tokens": 0,
                "output_tokens": 10,
                "total_tokens": 110,
                "cache_hit_rate_pct": 0.0,
                "cost_usd": {
                    "input_usd": 0.5,
                    "cache_read_usd": 0.0,
                    "output_usd": 0.75,
                    "total_usd": 1.25,
                    "without_cache_usd": 1.25,
                    "cache_savings_usd": 0.0,
                },
                "cost_usd_projection": {"total_usd": 0.50},
            },
            "by_category": {},
            "by_model": {},
            "by_phase": {
                "Phase|1\nSub": {
                    "first_ts": "2026-11-21T10:00:00Z",
                    "last_ts": "2026-11-21T10:01:00Z",
                    "wall_span_s": 60.0,
                    "agents": 1,
                    "llm_calls": 1,
                    "cost_usd": {"total_usd": 1.25},
                    "cost_share_pct": 100.0,
                }
            },
            "agents": [
                {
                    "conversation_id": "44444444-aaaa-bbbb-cccc-000000000004",
                    "role": "Role|A\nB",
                    "category": "orchestration",
                    "llm_calls": 1,
                    "max_context_tokens": 100,
                    "input_tokens": 100,
                    "cache_read_tokens": 0,
                    "output_tokens": 10,
                    "cost_usd": {"total_usd": 1.25},
                    "clean_termination": True,
                }
            ],
            "efficiency_and_waste": {
                "verification_to_build_cost_ratio": 0.0,
                "duplicate_role_spawns": {"duplicate_agent_count": 0, "duplicate_calls": 0, "duplicate_cost_usd": 0.0, "groups": []},
                "rewind_waste": {"rewind_calls": 0, "rewind_cost_usd": 0.0},
                "interrupted_agents": {"count": 0, "total_llm_calls": 0, "total_cost_usd": 0.0},
                "context_bloat": {"max_context_tokens_overall": 100},
            },
            "tool_usage": {"total_calls": 0, "by_tool": {}, "top_errors": []},
            "data_quality": {"unmatched_models": [], "missing_transcripts": [], "malformed_lines": 0},
        }
        det_md = render_session_detail_markdown(detail_metrics)
        self.assertIn("-$0.7500", det_md)
        self.assertNotIn("+-", det_md)
        self.assertIn("Phase/1 Sub", det_md)
        self.assertIn("Role/A B", det_md)


class TestReconciliationAndCompliance(unittest.TestCase):
    def test_multi_agent_tree_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "rec-root"
            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T12:00:00Z", "content": "/teamwork build app"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T12:00:10Z", "model": "gemini-4-neon", "input_tokens": 10_000, "cache_read_tokens": 40_000, "output_tokens": 1_000, "tool_calls": [{"name": "invoke_subagent"}]},
                ],
            )
            _write_subagent_meta(brain / root_id, "sub-exp", "teamwork_explorer", "Explorer", spawn_step=1)
            _write_transcript(
                brain / "sub-exp",
                [
                    {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T12:00:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 80_000, "output_tokens": 2_000, "tool_calls": []},
                ],
            )
            _write_subagent_meta(brain / root_id, "sub-bld", "teamwork_worker", "Builder", spawn_step=1)
            _write_transcript(
                brain / "sub-bld",
                [
                    {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T12:00:25Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 30_000, "cache_read_tokens": 70_000, "output_tokens": 3_000, "tool_calls": []},
                ],
            )
            _write_subagent_meta(brain / "sub-bld", "sub-rev", "teamwork_reviewer", "Reviewer", spawn_step=0)
            _write_transcript(
                brain / "sub-rev",
                [
                    {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T12:00:35Z", "model": "gemini-4-hydrogen-tiered-high", "input_tokens": 15_000, "cache_read_tokens": 50_000, "output_tokens": 1_500, "tool_calls": []},
                ],
            )

            data = collect_run_metrics(root_id, brain, CATALOG_PATH, label="reconcile-test")
            s = data["summary"]
            self.assertEqual(s["total_agents"], 4)
            self.assertEqual(s["max_tree_depth"], 2)
            tot = s["cost_usd"]["total_usd"]

            self.assertAlmostEqual(sum(a["cost_usd"]["total_usd"] for a in data["agents"]), tot, delta=1e-4)
            self.assertAlmostEqual(sum(v["cost_usd"]["total_usd"] for v in data["by_model"].values()), tot, delta=1e-4)
            self.assertAlmostEqual(sum(v["cost_usd"]["total_usd"] for v in data["by_category"].values()), tot, delta=1e-4)
            self.assertAlmostEqual(sum(v["cost_usd"]["total_usd"] for v in data["by_phase"].values()), tot, delta=1e-4)
            self.assertEqual(sum(v["agents"] for v in data["by_category"].values()), 4)
            self.assertEqual(sum(v["agents"] for v in data["by_phase"].values()), 4)

    def test_stdlib_only_and_no_forbidden_jargon(self) -> None:
        stdlib_names = set(getattr(sys, "stdlib_module_names", set()))
        allowed = stdlib_names | {"_shared", "metrics", "dashboard_provider", "cli", "validate", "install"}
        forbidden_re = re.compile(r"\bMod" + r"ule\s+0?\d\b", re.IGNORECASE)
        forbidden_term_re = re.compile(r"\bbench" + r"marks?\b", re.IGNORECASE)

        py_files = [
            p
            for p in PROJECT_ROOT.rglob("*.py")
            if "__pycache__" not in p.parts and ".git" not in p.parts
        ]
        self.assertGreaterEqual(len(py_files), 10)
        for py_path in py_files:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods = [node.module.split(".")[0]]
                for mod in mods:
                    self.assertIn(mod, allowed, f"Dépendance non-stdlib {mod!r} dans {py_path}")

        for fpath in PROJECT_ROOT.rglob("*"):
            if fpath.is_file() and fpath.suffix.lower() in (".py", ".md", ".json", ".html", ".jsonl", ".svg"):
                if "__pycache__" in fpath.parts or ".git" in fpath.parts:
                    continue
                txt = fpath.read_text(encoding="utf-8")
                self.assertIsNone(forbidden_re.search(txt), f"Jargon interdit trouvé dans {fpath}")
                self.assertIsNone(
                    forbidden_term_re.search(txt),
                    f"Terme hors périmètre trouvé dans {fpath}",
                )

    def test_validate_module_contract_strict_and_rejection(self) -> None:
        validate_script = PLUGIN_ROOT / "scripts" / "validate.py"
        with tempfile.TemporaryDirectory() as tmp:
            plugin_copy = Path(tmp) / "plugin"
            shutil.copytree(PLUGIN_ROOT, plugin_copy)

            proc_ok = subprocess.run(
                [sys.executable, "-B", str(validate_script), "--plugin-root", str(plugin_copy), "--strict"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0, proc_ok.stdout + proc_ok.stderr)

            module_json = plugin_copy / "config" / "module.json"
            bad_contract = json.loads(module_json.read_text(encoding="utf-8"))
            bad_contract["schema_version"] = 2
            module_json.write_text(json.dumps(bad_contract), encoding="utf-8")

            proc_bad = subprocess.run(
                [sys.executable, "-B", str(validate_script), "--plugin-root", str(plugin_copy), "--strict"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(proc_bad.returncode, 0)
            self.assertIn("schema_version", proc_bad.stdout)

    def test_centralized_workflow_profile_slug_hash_t3_p25(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "My Very Long Workspace Name With Spaces & Caps!"
            ws.mkdir()
            grav_home = root / "gravity_home"
            resolved = ws.resolve()
            slug = re.sub(r"[^a-z0-9]+", "-", resolved.name.lower()).strip("-")[:32] or "workspace"
            digest8 = hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8")).hexdigest()[:8]
            prof_file = grav_home / "workflow" / "workspaces" / f"{slug}-{digest8}" / "profile.json"
            prof_file.parent.mkdir(parents=True)
            prof_file.write_text(json.dumps({"mode": "deep"}), encoding="utf-8")

            with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=False):
                os.environ.pop("GRAVITY_WORKFLOW_HOME", None)
                self.assertEqual(read_gravity_profile_summary(ws), "deep")

                prof_file.write_text(
                    json.dumps({"mode": "evo", "current_level": "high", "pace": "fast", "verification": "safe"}),
                    encoding="utf-8",
                )
                self.assertEqual(read_gravity_profile_summary(ws), "evo→high · fast · safe")

    def test_split_collector_dev_anomalies_cold_cache_and_zero_bridge(self) -> None:
        from metrics import collector as col_mod

        for attr in ("METRICS_MARKER", "METRICS_OPEN_MARKER", "slugify_label", "collect_run_metrics", "resolve_gravity_window", "suggest_auto_label"):
            self.assertTrue(hasattr(col_mod, attr), f"Missing collector facade attribute: {attr}")

        for fname in ("collector.py", "prompt_classifier.py", "transcript_index.py", "agent_tree.py", "milestone_analyzer.py"):
            src = (PLUGIN_ROOT / "metrics" / fname).read_text(encoding="utf-8")
            self.assertNotIn("bridge_links", src, f"Unexpected bridge_links reference in {fname}")

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            cid = "dev-feat-0001-0000-0000-000000000001"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:00:00Z", "content": "/gravity-workflow deep fix"},
                    {
                        "step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:05Z",
                        "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 200,
                        "thinking": "Reasoning trace", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}],
                    },
                    {"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-26T10:00:08Z", "content": "Command exited with code 2\nCommand output:\nfailed"},
                    {"step_index": 3, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-26T10:00:09Z", "content": "Showing lines 1-5\nCommand exited with code 9"},
                    {"step_index": 4, "type": "GENERIC", "status": "INVALID", "created_at": "2026-11-26T10:00:10Z", "content": "Bad tool payload"},
                    {
                        "step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:12Z",
                        "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 9000, "output_tokens": 50,
                        "tool_calls": [{"name": "run_command", "args": {"CommandLine": f"python3 plugin/scripts/cli.py record {cid[:8]}"}}],
                    },
                ],
            )
            m = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True, project="gravity-metrics")
            s = m["summary"]
            self.assertEqual(s["total_invalid_steps"], 1)
            self.assertEqual(s["total_shell_nonzero_steps"], 1)
            self.assertEqual(s["total_anomalies"], 2)
            self.assertEqual(s["thinking_calls"], 1)
            self.assertEqual(m["efficiency_and_waste"]["cold_cache_waste"]["calls"], 1)
            self.assertEqual(m["efficiency_and_waste"]["cold_cache_waste"]["input_tokens"], 20_000)
            self.assertTrue(m["agents"][0]["clean_termination"])
            self.assertIn("gravity-metrics", m["by_workspace"])

    def test_locks_fail_closed_timeout_posix_and_simulated_windows(self) -> None:
        import importlib
        import types
        from metrics.history import history_lock
        from metrics.lifecycle import state_file_lock

        file_lock_mod = importlib.import_module("_shared.file_lock")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hpath = root / "history.jsonl"
            hlock = hpath.with_suffix(".lock")
            spath = root / "session_meta.json"
            slock = spath.with_suffix(".lock")

            if os.name != "nt":
                import fcntl

                with hlock.open("a+b") as holder:
                    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        with self.assertRaises(TimeoutError):
                            with history_lock(hpath, timeout_s=0.05):
                                pass
                    finally:
                        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

                with slock.open("a+b") as holder:
                    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        with self.assertRaises(TimeoutError):
                            with state_file_lock(spath, timeout_s=0.05):
                                pass
                    finally:
                        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt

                for lock_file, lock_ctx in (
                    (hlock, lambda: history_lock(hpath, timeout_s=0.05)),
                    (slock, lambda: state_file_lock(spath, timeout_s=0.05)),
                ):
                    with lock_file.open("a+b") as holder:
                        holder.seek(0, os.SEEK_END)
                        if holder.tell() == 0:
                            holder.write(b"\x00")
                            holder.flush()
                        holder.seek(0)
                        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 1)
                        try:
                            with self.assertRaises(TimeoutError):
                                with lock_ctx():
                                    pass
                        finally:
                            holder.seek(0)
                            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 1)

            fake_msvcrt = types.SimpleNamespace(
                LK_NBLCK=1,
                LK_UNLCK=0,
                locking=mock.Mock(side_effect=OSError("win lock busy")),
            )
            fake_win_os = types.SimpleNamespace(
                name="nt",
                fspath=os.fspath,
                path=os.path,
                SEEK_END=os.SEEK_END,
            )
            with mock.patch.object(file_lock_mod, "os", fake_win_os), mock.patch.object(
                file_lock_mod, "msvcrt", fake_msvcrt
            ):
                with self.assertRaises(TimeoutError):
                    with history_lock(hpath, timeout_s=0.05):
                        pass
                with self.assertRaises(TimeoutError):
                    with state_file_lock(spath, timeout_s=0.05):
                        pass

            sym_lock = root / "sym.lock"
            sym_lock.symlink_to(hlock)
            with self.assertRaises(ValueError):
                with history_lock(root / "sym.jsonl", timeout_s=0.05):
                    pass

    def test_atomic_lf_writes_and_symlink_rejection(self) -> None:
        from metrics.lifecycle import record_session_meta, start_active_capture
        from metrics.store import regenerate_views, write_session_details

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            hist = store / "data" / "history.jsonl"
            rec = {
                "schema_version": "2.1.0",
                "status": "archived",
                "added_at": "2026-11-26T10:00:00Z",
                "archived_at": "2026-11-26T10:00:00Z",
                "label": "lf-test",
                "detail_slug": "lf-test",
                "conversation_id": "11111111-1111-4111-8111-111111111111",
                "note": "line1\r\nline2",
                "project": "gravity-metrics",
                "pricing_profile": "2026_nov_standard",
                "started_at": "2026-11-26T10:00:00Z",
                "ended_at": "2026-11-26T10:01:00Z",
                "wall_clock_s": 60.0,
                "primary_model": "gemini-3.8-flash-tiered",
                "models": {},
                "agents": 1,
                "max_depth": 0,
                "llm_calls": 1,
                "tool_calls": 0,
                "user_inputs": 1,
                "error_steps": 0,
                "error_rate_pct": 0.0,
                "input_tokens": 100,
                "cache_read_tokens": 0,
                "output_tokens": 10,
                "total_tokens": 110,
                "cache_hit_rate_pct": 0.0,
                "max_context_tokens": 100,
                "cost_usd": 0.001,
                "cost_input_usd": 0.0005,
                "cost_cache_usd": 0.0,
                "cost_output_usd": 0.0005,
                "cache_savings_usd": 0.0,
                "cost_2027_usd": 0.002,
                "category_share_pct": {},
                "duplicate_spawns": 0,
                "rewind_calls": 0,
                "interrupted_agents": 0,
            }
            save_history(hist, [rec])
            record_session_meta("11111111-1111-4111-8111-111111111111", cwd=str(root), project="gravity-metrics", store_dir=store)
            start_active_capture("11111111-1111-4111-8111-111111111111", start_step=0, label="lf-test", store_dir=store)
            regenerate_views(store, [rec])
            sample_metrics = {
                "schema_version": "2.1.0",
                "label": "lf-test",
                "root_conversation_id": "11111111-1111-4111-8111-111111111111",
                "project": "gravity-metrics",
                "pricing_profile": "2026_nov_standard",
                "secondary_pricing_profile": "2027_standard",
                "summary": {
                    "total_agents": 1, "max_tree_depth": 0, "total_llm_calls": 1, "total_tool_calls": 0,
                    "total_user_inputs": 1, "total_error_steps": 0, "error_rate_pct": 0.0,
                    "input_tokens": 100, "cache_read_tokens": 0, "output_tokens": 10, "total_tokens": 110,
                    "cache_hit_rate_pct": 0.0, "first_ts": "2026-11-26T10:00:00Z", "last_ts": "2026-11-26T10:01:00Z",
                    "wall_clock_s": 60.0, "sum_agent_active_s": 60.0, "parallelism_factor": 1.0,
                    "cost_usd": {"total_usd": 0.001, "input_usd": 0.0005, "cache_read_usd": 0.0, "output_usd": 0.0005, "without_cache_usd": 0.001, "cache_savings_usd": 0.0},
                    "cost_usd_projection": {"total_usd": 0.002},
                },
                "by_category": {}, "by_model": {}, "by_phase": {},
                "tool_usage": {"total_calls": 0, "by_tool": {}, "top_errors": []},
                "efficiency_and_waste": {
                    "verification_to_build_cost_ratio": 0.0,
                    "duplicate_role_spawns": {"duplicate_agent_count": 0, "duplicate_calls": 0, "duplicate_cost_usd": 0.0, "groups": []},
                    "rewind_waste": {"rewind_calls": 0, "rewind_cost_usd": 0.0},
                    "interrupted_agents": {"count": 0, "total_llm_calls": 0, "total_cost_usd": 0.0},
                    "context_bloat": {"max_context_tokens_overall": 100, "calls_over_100k_ctx": 0, "calls_over_128k_ctx": 0, "calls_over_200k_ctx": 0},
                },
                "agents": [],
                "data_quality": {"unmatched_models": [], "missing_transcripts": [], "malformed_lines": 0},
            }
            write_session_details(store, "lf-test", sample_metrics)

            for fpath in (
                hist,
                store / "session_meta.json",
                store / "active_captures.json",
                store / "METRICS.md",
                store / "reports" / "lf-test.json",
                store / "reports" / "lf-test.md",
            ):
                raw = fpath.read_bytes()
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {fpath}")
                self.assertNotIn(b"\r", raw, f"CR/CRLF found in {fpath}")

            sym_hist = root / "sym_history.jsonl"
            sym_hist.symlink_to(hist)
            with self.assertRaises(ValueError):
                save_history(sym_hist, [rec])

    def test_spi3_signal_emission_slug_normalization_and_window(self) -> None:
        import importlib

        signal_bus = importlib.import_module("_shared.signal_bus")
        expected_keys = {
            "avg_error_rate_pct",
            "avg_cache_hit_rate_pct",
            "max_context_tokens",
            "recent_duplicate_spawns",
            "recent_rewind_calls",
            "recent_interrupted_agents",
            "recent_bridge_sessions",
            "high_output_cost_ratio",
        }

        def _make_metrics(cid: str, project: str, err: float, cache: float, ctx: int, dup: int, rw: int, intr: int, cost: float, out_cost: float, ts_end: str) -> dict:
            return {
                "schema_version": "2.1.0",
                "label": f"lbl-{cid[:8]}",
                "root_conversation_id": cid,
                "project": project,
                "pricing_profile": "2026_nov_standard",
                "secondary_pricing_profile": "2027_standard",
                "window": {"mode": "full_conversation", "start_step_index": None, "first_prompt": "run"},
                "summary": {
                    "total_agents": 1, "max_tree_depth": 0, "total_llm_calls": 2, "total_tool_calls": 2,
                    "total_user_inputs": 1, "total_error_steps": 0, "error_rate_pct": err,
                    "input_tokens": 1000, "cache_read_tokens": 1000, "output_tokens": 100, "total_tokens": 2100,
                    "cache_hit_rate_pct": cache, "first_ts": "2026-11-26T10:00:00Z", "last_ts": ts_end,
                    "wall_clock_s": 10.0, "sum_agent_active_s": 10.0, "parallelism_factor": 1.0,
                    "cost_usd": {"total_usd": cost, "input_usd": cost - out_cost, "cache_read_usd": 0.0, "output_usd": out_cost, "without_cache_usd": cost, "cache_savings_usd": 0.0},
                    "cost_usd_projection": {"total_usd": cost},
                },
                "by_category": {}, "by_model": {}, "by_phase": {}, "by_workspace": {},
                "tool_usage": {"total_calls": 2, "by_tool": {}, "top_errors": []},
                "bridge_pending_jobs": [{"thread_id": "legacy-ignored"}],
                "efficiency_and_waste": {
                    "verification_to_build_cost_ratio": 0.0,
                    "duplicate_role_spawns": {"duplicate_agent_count": dup, "duplicate_calls": 0, "duplicate_cost_usd": 0.0, "groups": []},
                    "rewind_waste": {"rewind_calls": rw, "rewind_cost_usd": 0.0},
                    "interrupted_agents": {"count": intr, "total_llm_calls": 0, "total_cost_usd": 0.0},
                    "context_bloat": {"max_context_tokens_overall": ctx, "calls_over_100k_ctx": 0, "calls_over_128k_ctx": 0, "calls_over_200k_ctx": 0},
                },
                "agents": [],
                "data_quality": {"unmatched_models": [], "missing_transcripts": [], "malformed_lines": 0},
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grav_home = root / "grav_home"
            store = grav_home / "metrics"
            hist = store / "data" / "history.jsonl"

            with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=False):
                os.environ.pop("GRAVITY_METRICS_HOME", None)
                for idx in range(6):
                    cid = f"sig0000{idx}-1111-4111-8111-111111111111"
                    m = _make_metrics(
                        cid, "My.Project_v2!", err=float(idx * 2), cache=50.0 + idx,
                        ctx=10_000 * (idx + 1), dup=idx, rw=1, intr=1 if idx >= 4 else 0,
                        cost=1.0, out_cost=0.25, ts_end=f"2026-11-26T10:0{idx}:00Z",
                    )
                    record_session_metrics(m, store_dir=store, history_path=hist, label=f"s-{idx}")

                sig_path = grav_home / "signals" / "session-metrics" / "my-project-v2.json"
                self.assertTrue(sig_path.is_file(), f"Missing SPI-3 signal file: {sig_path}")
                sig = signal_bus.read_signal("session-metrics", "my-project-v2", gravity_home=grav_home)
                self.assertIsNotNone(sig)
                self.assertEqual(sig["schema_version"], 1)
                self.assertEqual(sig["topic"], "session-metrics")
                self.assertEqual(sig["producer"], "metrics")
                self.assertEqual(sig["sample_count"], 5)
                self.assertEqual(sig["window_size"], 5)
                payload = sig["payload"]
                self.assertEqual(set(payload.keys()), expected_keys)
                self.assertEqual(payload["avg_error_rate_pct"], 6.0)
                self.assertEqual(payload["avg_cache_hit_rate_pct"], 53.0)
                self.assertEqual(payload["max_context_tokens"], 60_000)
                self.assertEqual(payload["recent_duplicate_spawns"], 15)
                self.assertEqual(payload["recent_rewind_calls"], 5)
                self.assertEqual(payload["recent_interrupted_agents"], 2)
                self.assertEqual(payload["recent_bridge_sessions"], 0)
                self.assertAlmostEqual(payload["high_output_cost_ratio"], 0.25, places=4)

                # Empty project -> "global"; punctuation-only project -> "default"
                record_session_metrics(
                    _make_metrics("sig-glob-1111-4111-8111-111111111111", "", 1.0, 80.0, 5000, 0, 0, 0, 0.5, 0.1, "2026-11-26T11:00:00Z"),
                    store_dir=store, history_path=hist, label="glob-run",
                )
                self.assertIsNotNone(signal_bus.read_signal("session-metrics", "global", gravity_home=grav_home))

                record_session_metrics(
                    _make_metrics("sig-dflt-1111-4111-8111-111111111111", "___...", 1.0, 80.0, 5000, 0, 0, 0, 0.5, 0.1, "2026-11-26T11:05:00Z"),
                    store_dir=store, history_path=hist, label="dflt-run",
                )
                self.assertIsNotNone(signal_bus.read_signal("session-metrics", "default", gravity_home=grav_home))

    def test_dev_history_tolerance_lifecycle_worktree_and_update_sealed(self) -> None:
        from metrics.history import find_archived_session
        from metrics.lifecycle import (
            current_turn_start_step_in_transcript,
            load_session_meta,
            next_step_index_in_transcript,
            record_session_meta,
            start_active_capture,
        )
        from metrics.store import archive_conversation

        cid = "tol00001-1111-4111-8111-111111111111"
        base_rec = {"conversation_id": cid, "status": "archived", "window_start_step": 10, "label": "tol-1"}
        self.assertIsNotNone(find_archived_session([base_rec], cid, 12))
        self.assertIsNone(find_archived_session([base_rec], cid, 13))
        up = upsert_session([base_rec], {**base_rec, "window_start_step": 11, "label": "tol-1"})
        self.assertEqual(len(up), 1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist = store / "data" / "history.jsonl"

            self.assertEqual(record_session_meta(cid, cwd="/tmp/.worktrees/wt-1", store_dir=store), {})
            record_session_meta(cid, cwd=str(root / "gravity-metrics"), store_dir=store)
            self.assertIn(cid, load_session_meta(store))

            c1 = start_active_capture(cid, start_step=2, label="first", source="auto", store_dir=store, overwrite=False)
            c2 = start_active_capture(cid, start_step=4, label="second", source="manual", store_dir=store, overwrite=False)
            self.assertEqual(c1["label"], "first")
            self.assertEqual(c2["label"], "first")
            c3 = start_active_capture(cid, start_step=4, label="second", source="manual", store_dir=store, overwrite=True)
            self.assertEqual(c3["label"], "second")

            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:00:00Z", "content": "/gravity-workflow run"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:01:00Z", "content": "<USER_REQUEST>next turn</USER_REQUEST>"},
                ],
            )
            self.assertEqual(next_step_index_in_transcript(tpath), 3)
            self.assertEqual(current_turn_start_step_in_transcript(tpath, expected_prompt="next turn"), 2)

            archive_conversation(
                cid, store_dir=store, history_path=hist, brain_dir=brain,
                catalog_path=CATALOG_PATH, label="sealed-v1", full_conversation=True,
            )
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:00:00Z", "content": "/gravity-workflow run"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 100},
                ],
            )
            updated_rec, all_recs, _ = archive_conversation(
                cid, store_dir=store, history_path=hist, brain_dir=brain,
                catalog_path=CATALOG_PATH, label="sealed-v1", update_sealed=True, self_archiving=True,
            )
            self.assertEqual(len(all_recs), 1)
            self.assertEqual(updated_rec["input_tokens"], 2000)

    def test_technical_signals_and_pricing_catalog_aliases(self) -> None:
        from metrics.technical_signals import aggregate_technical_signals, extract_technical_signals

        steps = [
            {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:00:00Z", "content": "Go"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:01Z", "input_tokens": 50_000, "cache_read_tokens": 0, "thinking": "a" * 40},
            {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:03Z", "input_tokens": 20_000, "cache_read_tokens": 0, "thinking": [{"text": "b" * 20}]},
            {"step_index": 3, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:15:00Z", "content": "/compact"},
            {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:15:10Z", "input_tokens": 5_000, "cache_read_tokens": 0},
        ]
        sig = extract_technical_signals(steps)
        self.assertEqual(sig["thinking_calls"], 2)
        self.assertEqual(sig["thinking_est_tokens"], 15)
        self.assertEqual(sig["compaction_count"], 2)
        self.assertEqual(sig["latency_p50_ms"], 2000)
        self.assertEqual(sig["latency_p95_ms"], 10000)
        agg = aggregate_technical_signals([{"thinking_calls": 1, "thinking_chars": 8, "thinking_est_tokens": 2, "compaction_count": 1, "_latency_samples_ms": [100, 300]}])
        self.assertEqual(agg["latency_samples_count"], 2)
        self.assertEqual(agg["latency_p50_ms"], 100)

        catalog = load_pricing_catalog(CATALOG_PATH)
        p_neon_26, fb_neon_26 = resolve_model_pricing("gemini-pro-orchestrator-neon", catalog, "2026_nov_standard")
        self.assertFalse(fb_neon_26)
        self.assertEqual((p_neon_26["input"], p_neon_26["cache_read"], p_neon_26["output"]), (3.0, 0.15, 15.0))
        p_agy_26, fb_agy_26 = resolve_model_pricing("gemini-flash-worker-agy-high", catalog, "2026_nov_standard")
        self.assertFalse(fb_agy_26)
        self.assertEqual((p_agy_26["input"], p_agy_26["cache_read"], p_agy_26["output"]), (0.75, 0.075, 3.75))
        p_agy_27, fb_agy_27 = resolve_model_pricing("gemini-flash-worker-agy", catalog, "2027_standard")
        self.assertFalse(fb_agy_27)
        self.assertEqual((p_agy_27["input"], p_agy_27["cache_read"], p_agy_27["output"]), (1.5, 0.15, 7.5))

    def test_resolve_detail_slug_and_apply_remove_lifecycle_multi_window(self) -> None:
        from metrics.history import resolve_session_index
        from metrics.lifecycle import (
            ignore_session,
            load_active_captures,
            load_ignored_sessions,
            load_session_meta,
            record_session_meta,
            start_active_capture,
        )
        from metrics.store import apply_remove, purge_session_details, update_session_metadata

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            hist = store / "data" / "history.jsonl"
            cid = "d0001111-2222-3333-4444-555566667777"
            cid2 = "d0002222-2222-3333-4444-555566667777"

            rec_w1 = {
                "label": "Run Alpha Phase 1",
                "detail_slug": "run-alpha-phase-1",
                "conversation_id": cid,
                "window_start_step": 0,
                "status": "archived",
                "project": "Gravity",
                "cost_usd": 1.0,
            }
            rec_w2 = {
                "label": "Run Alpha Phase 2",
                "detail_slug": "run-alpha-phase-2",
                "conversation_id": cid,
                "window_start_step": 10,
                "status": "archived",
                "project": "Gravity",
                "cost_usd": 2.0,
            }
            rec_other = {
                "label": "Run Beta",
                "detail_slug": "run-beta",
                "conversation_id": cid2,
                "window_start_step": 0,
                "status": "archived",
                "project": "Gravity",
                "cost_usd": 0.5,
            }
            save_history(hist, [rec_w1, rec_w2, rec_other])

            # detail_slug != label : exact et case-insensitive, plus ambiguïté préservée
            recs = load_history(hist)
            self.assertEqual(resolve_session_index(recs, "run-alpha-phase-1"), 0)
            self.assertEqual(resolve_session_index(recs, "RUN-ALPHA-PHASE-2"), 1)
            with self.assertRaises(ValueError):
                resolve_session_index(recs, "d000")

            # update_session_metadata via detail_slug
            updated_meta, _ = update_session_metadata(
                "run-alpha-phase-1",
                note="maj via slug",
                store_dir=store,
                history_path=hist,
            )
            self.assertEqual(updated_meta["note"], "maj via slug")

            # Confinement purge_session_details : rejette la traversée ../
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            self.assertFalse(purge_session_details(store, {"detail_slug": "../outside"}))
            self.assertTrue(outside.is_file())

            # Prépare états lifecycle pour cid (multi-fenêtres)
            record_session_meta(cid, cwd=str(root / "Gravity"), project="Gravity", store_dir=store)
            start_active_capture(cid, start_step=20, label="next", store_dir=store)
            ignore_session(cid, reason="test", store_dir=store)

            # Suppression fenêtre 1 via detail_slug : fenêtre 2 reste -> lifecycle préservé
            removed_1, rem_1 = apply_remove(
                "run-alpha-phase-1", store_dir=store, history_path=hist, purge_reports=True,
            )
            self.assertEqual(removed_1["conversation_id"], cid)
            self.assertEqual(rem_1, 2)
            self.assertFalse(removed_1["already_absent"])
            self.assertEqual(
                removed_1["cleaned_sources"],
                {"history": True, "reports": False, "active_captures": False, "ignored": False, "session_meta": False},
            )
            self.assertIn(cid, load_session_meta(store))
            self.assertIn(cid, load_active_captures(store))
            self.assertIn(cid, load_ignored_sessions(store))

            # Suppression fenêtre 2 (dernière du cid) -> lifecycle purgé (zéro résurrection)
            removed_2, rem_2 = apply_remove(
                "RUN-ALPHA-PHASE-2", store_dir=store, history_path=hist, purge_reports=True,
            )
            self.assertEqual(rem_2, 1)
            self.assertEqual(
                removed_2["cleaned_sources"],
                {"history": True, "reports": False, "active_captures": True, "ignored": True, "session_meta": True},
            )
            self.assertNotIn(cid, load_session_meta(store))
            self.assertNotIn(cid, load_active_captures(store))
            self.assertNotIn(cid, load_ignored_sessions(store))


if __name__ == "__main__":
    unittest.main()
