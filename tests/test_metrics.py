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
    archive_conversation,
    classify_agent,
    collect_run_metrics,
    compute_call_cost,
    default_store_dir,
    discover_agent_tree,
    list_gravity_conversations,
    load_active_captures,
    load_history,
    load_pricing_catalog,
    next_step_index_in_transcript,
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

            lbl_chained = suggest_auto_label(
                "MyApp",
                "/gravity-workflow standard /caveman ultra /grill-me corrige bug auth cache pipeline",
                "2026-11-22T09:06:00Z",
                [],
            )
            self.assertEqual(lbl_chained, "myapp-corrige-bug-auth-cache-pipeline")
            self.assertLessEqual(len(lbl_chained), 64)

            lbl_boost_plan = suggest_auto_label(
                "MyApp",
                "/boost /plan ultra refactorer moteur collecte metriques complet",
                "2026-11-22T09:07:00Z",
                [],
            )
            self.assertEqual(lbl_boost_plan, "myapp-refactorer-moteur-collecte-metriques-complet")
            self.assertLessEqual(len(lbl_boost_plan), 64)

            # Ne doit pas mordre /plan-b ni avaler audit après send
            lbl_plan_b = suggest_auto_label("MyApp", "/plan-b secours", "2026-11-22T09:08:00Z", [])
            self.assertEqual(lbl_plan_b, "myapp-plan-secours")
            lbl_bridge = suggest_auto_label("MyApp", "/gravity-bridge send audit bridge", "2026-11-22T09:09:00Z", [])
            self.assertEqual(lbl_bridge, "myapp-audit-bridge")

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
        self.assertEqual(slugify_label("créer écosystème"), "creer-ecosysteme")
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

            # Sous-commande `start` (calcul transcript + --step explicite + prefixe ambigu)
            store_start = root / "start_store"
            ws_start = root / "ProjetStart"
            ws_start.mkdir()
            self.assertEqual(
                next_step_index_in_transcript(brain / cid1 / ".system_generated" / "logs" / "transcript.jsonl"),
                2,
            )
            self.assertEqual(next_step_index_in_transcript(root / "absent.jsonl"), 0)

            r_start_tx = run_cli(
                "start",
                "aaaa1111",
                "-l",
                "depuis-tx",
                "--workspace",
                str(ws_start),
                "--brain-dir",
                str(brain),
                "--store",
                str(store_start),
            )
            self.assertEqual(r_start_tx.returncode, 0, r_start_tx.stderr)
            caps_tx = load_active_captures(store_start)
            self.assertEqual(caps_tx[cid1]["start_step"], 2)
            self.assertEqual(caps_tx[cid1]["label"], "depuis-tx")
            self.assertEqual(caps_tx[cid1]["project"], "ProjetStart")

            r_start_step = run_cli(
                "start",
                "cccc3333-0000-0000-0000-000000000003",
                "-l",
                "step-manuel",
                "--step",
                "7",
                "--brain-dir",
                str(brain),
                "--store",
                str(store_start),
            )
            self.assertEqual(r_start_step.returncode, 0, r_start_step.stderr)
            caps_step = load_active_captures(store_start)
            self.assertEqual(caps_step["cccc3333-0000-0000-0000-000000000003"]["start_step"], 7)

            # Préfixe ambigu rejeté même avec --step
            _write_transcript(brain / "aaaa9999-0000-0000-0000-000000000009", [])
            r_start_amb = run_cli(
                "start",
                "aaaa",
                "--step",
                "0",
                "--brain-dir",
                str(brain),
                "--store",
                str(store_start),
            )
            self.assertEqual(r_start_amb.returncode, 1)

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
        allowed = stdlib_names | {"metrics", "hook_runtime", "cli", "validate", "install", "mcp", "widget_html"}
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


class TestAudit0409Fixes(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_pricing_catalog(CATALOG_PATH)

    def test_aud_04_09a_self_archiving_root_clean_termination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            cid_plan = "a409a001-1111-2222-3333-444455556666"
            cid_gen = "a409a002-1111-2222-3333-444455556666"
            cid_crashed = "a409a003-1111-2222-3333-444455556666"
            cid_sub_crash = "a409a004-1111-2222-3333-444455556666"

            # 1) Racine avec dernier appel actif `cli.py archive` -> clean_termination=True
            t_plan = _write_transcript(
                brain / cid_plan,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:00:00Z", "content": "/gravity-architect lot"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 5_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 200,
                        "tool_calls": [
                            {"name": "run_command", "args": {"CommandLine": f"python plugin/scripts/cli.py archive {cid_plan} --store {store}"}}
                        ],
                    },
                ],
            )
            parsed_plan = parse_agent_transcript(t_plan, self.catalog, "2026_nov_standard")
            self.assertTrue(parsed_plan["clean_termination"])

            # 2) Racine avec GENERIC RUNNING suivant `cli.py add` -> clean_termination=True
            t_gen = _write_transcript(
                brain / cid_gen,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:05:00Z", "content": "/gravity run"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:05:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 4_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 150,
                        "tool_calls": [
                            {"name": "run_command", "args": {"CommandLine": f"python3 scripts/cli.py add {cid_gen} --gravity-window"}}
                        ],
                    },
                    {"step_index": 2, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-24T18:05:12Z", "content": "Running..."},
                ],
            )
            parsed_gen = parse_agent_transcript(t_gen, self.catalog, "2026_nov_standard")
            self.assertTrue(parsed_gen["clean_termination"])

            # Sous-agent réellement interrompu reste False même avec self_archiving=True sur la racine
            _write_subagent_meta(brain / cid_gen, cid_sub_crash, "teamwork_worker", "Builder", spawn_step=1)
            _write_transcript(
                brain / cid_sub_crash,
                [
                    {
                        "step_index": 0,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:05:11Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 1_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 50,
                        "tool_calls": [{"name": "edit_file"}],
                    },
                    {"step_index": 1, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-24T18:05:12Z", "content": "Interrupted"},
                ],
            )
            m_gen = collect_run_metrics(cid_gen, brain, CATALOG_PATH, gravity_window=True, self_archiving=True)
            ag_by_id = {a["conversation_id"]: a for a in m_gen["agents"]}
            self.assertTrue(ag_by_id[cid_gen]["clean_termination"])
            self.assertFalse(ag_by_id[cid_sub_crash]["clean_termination"])
            self.assertEqual(m_gen["efficiency_and_waste"]["interrupted_agents"]["count"], 1)

            # 3) E2E cli.py archive sur session racine active sans flag -> 0 faux positif Interrompu
            proc_arch = subprocess.run(
                [sys.executable, "-B", str(CLI_SCRIPT), "archive", cid_plan, "-l", "self-arch-ok", "--brain-dir", str(brain), "--store", str(store)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc_arch.returncode, 0, proc_arch.stderr)
            recs = load_history(store / "data" / "history.jsonl")
            self.assertEqual(recs[0]["interrupted_agents"], 0)
            det = json.loads((store / "reports" / f"{recs[0]['detail_slug']}.json").read_text(encoding="utf-8"))
            self.assertTrue(det["agents"][0]["clean_termination"])

            # 4) Session réellement crashée (dernier outil != cli.py) archivée a posteriori reste Interrompu=False,
            #    tandis que self_archiving=True explicite sur session active non-ERROR passe True.
            t_crash = _write_transcript(
                brain / cid_crashed,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:10:00Z", "content": "/gravity fix"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:10:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 2_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                        "tool_calls": [{"name": "read_file"}],
                    },
                    {"step_index": 2, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-24T18:10:11Z", "content": "Waiting"},
                ],
            )
            self.assertFalse(parse_agent_transcript(t_crash, self.catalog, "2026_nov_standard")["clean_termination"])
            saved_crashed, _, _ = archive_conversation(
                cid_crashed,
                store_dir=store,
                history_path=store / "data" / "history.jsonl",
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                label="real-crash",
            )
            self.assertEqual(saved_crashed["interrupted_agents"], 1)

            saved_explicit, _, _ = archive_conversation(
                cid_crashed,
                store_dir=store,
                history_path=store / "data" / "history.jsonl",
                brain_dir=brain,
                catalog_path=CATALOG_PATH,
                label="explicit-self",
                force=True,
                self_archiving=True,
            )
            self.assertEqual(saved_explicit["interrupted_agents"], 0)

            # 5) Garde-fou ERROR / INVALID / exit != 0 même avec self_archiving=True (zéro écrasement store.py)
            for idx, (tag, tail_step) in enumerate(
                [
                    (
                        "err-planner",
                        {
                            "step_index": 1,
                            "type": "PLANNER_RESPONSE",
                            "status": "ERROR",
                            "created_at": "2026-11-24T18:15:10Z",
                            "model": "gemini-3.8-flash-tiered",
                            "input_tokens": 1_000,
                            "cache_read_tokens": 0,
                            "output_tokens": 0,
                            "tool_calls": [],
                        },
                    ),
                    (
                        "inv-planner",
                        {
                            "step_index": 1,
                            "type": "PLANNER_RESPONSE",
                            "status": "INVALID",
                            "created_at": "2026-11-24T18:16:10Z",
                            "model": "gemini-3.8-flash-tiered",
                            "input_tokens": 1_000,
                            "cache_read_tokens": 0,
                            "output_tokens": 0,
                            "tool_calls": [],
                        },
                    ),
                    (
                        "shell-nz",
                        {
                            "step_index": 1,
                            "type": "GENERIC",
                            "status": "DONE",
                            "created_at": "2026-11-24T18:17:10Z",
                            "content": "Command exited with code 1\n\nCommand output:\nfail",
                        },
                    ),
                ],
                start=1,
            ):
                cid_err = f"a409a10{idx}-1111-2222-3333-444455556666"
                t_err = _write_transcript(
                    brain / cid_err,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:15:00Z", "content": "/gravity fix"},
                        tail_step,
                    ],
                )
                self.assertFalse(
                    parse_agent_transcript(t_err, self.catalog, "2026_nov_standard", self_archiving=True)["clean_termination"],
                    tag,
                )
                rec_err, _, det_err = archive_conversation(
                    cid_err,
                    store_dir=store,
                    history_path=store / "data" / "history.jsonl",
                    brain_dir=brain,
                    catalog_path=CATALOG_PATH,
                    label=f"err-{tag}",
                    self_archiving=True,
                )
                self.assertFalse(det_err["agents"][0]["clean_termination"], tag)
                self.assertEqual(rec_err["interrupted_agents"], 1, tag)

            # 6) Sous-agent crashé lançant cli.py archive <root> + racine crashée ciblant autre cid + faux positifs shell
            cid_sub_cli = "a409a201-1111-2222-3333-444455556666"
            cid_root_cli = "a409a202-1111-2222-3333-444455556666"
            _write_transcript(
                brain / cid_root_cli,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:20:00Z", "content": "/gravity run"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T18:20:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )
            _write_subagent_meta(brain / cid_root_cli, cid_sub_cli, "teamwork_worker", "Builder", spawn_step=1)
            _write_transcript(
                brain / cid_sub_cli,
                [
                    {
                        "step_index": 0,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:20:06Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 1_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 50,
                        "tool_calls": [
                            {"name": "run_command", "args": {"CommandLine": f"python plugin/scripts/cli.py archive {cid_root_cli}"}}
                        ],
                    },
                    {"step_index": 1, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-24T18:20:07Z", "content": "Hung"},
                ],
            )
            m_sub_cli = collect_run_metrics(cid_root_cli, brain, CATALOG_PATH, gravity_window=True)
            ag_sub = {a["conversation_id"]: a for a in m_sub_cli["agents"]}[cid_sub_cli]
            self.assertFalse(ag_sub["clean_termination"])

            for fp_idx, bad_cmd in enumerate(
                [
                    f"python plugin/scripts/cli.py archive {cid_crashed}",
                    "python plugin/scripts/cli.py pending; git add foo",
                    "python plugin/scripts/cli.py pending && git add foo",
                    "python plugin/scripts/cli.py list --store /tmp/archive",
                ],
                start=1,
            ):
                cid_fp = f"a409a30{fp_idx}-1111-2222-3333-444455556666"
                t_fp = _write_transcript(
                    brain / cid_fp,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:25:00Z", "content": "/gravity test"},
                        {
                            "step_index": 1,
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "created_at": "2026-11-24T18:25:05Z",
                            "model": "gemini-3.8-flash-tiered",
                            "input_tokens": 1_000,
                            "cache_read_tokens": 0,
                            "output_tokens": 50,
                            "tool_calls": [{"name": "run_command", "args": {"CommandLine": bad_cmd}}],
                        },
                        {"step_index": 2, "type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-24T18:25:06Z", "content": "Waiting"},
                    ],
                )
                self.assertFalse(
                    parse_agent_transcript(t_fp, self.catalog, "2026_nov_standard")["clean_termination"],
                    bad_cmd,
                )

            # 7) PreInvocation `/gravity-metrics stop` propage self_archiving=True à collect_run_metrics
            cid_hook_stop = "a409a401-1111-2222-3333-444455556666"
            ws_hook = root / "WsHook"
            ws_hook.mkdir()
            t_hook = _write_transcript(
                brain / cid_hook_stop,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:30:00Z", "content": "/gravity fix"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T18:30:05Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 3_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                        "tool_calls": [{"name": "edit_file"}],
                    },
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T18:30:10Z", "content": "/gravity-metrics stop hook-self"},
                ],
            )
            env_h = os.environ.copy()
            env_h["GRAVITY_METRICS_HOME"] = str(store)
            env_h["ANTIGRAVITY_BRAIN_DIR"] = str(brain)
            env_h["PYTHONUTF8"] = "1"
            p_hook = subprocess.run(
                [sys.executable, "-B", str(PLUGIN_ROOT / "hooks" / "pre_invocation_metrics.py")],
                input=json.dumps({"conversationId": cid_hook_stop, "transcriptPath": str(t_hook), "cwd": str(ws_hook), "lastUserInput": "/gravity-metrics stop hook-self"}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env_h,
                check=False,
            )
            self.assertEqual(p_hook.returncode, 0, p_hook.stderr)
            rec_hook = next(r for r in load_history(store / "data" / "history.jsonl") if r["label"] == "hook-self")
            self.assertEqual(rec_hook["interrupted_agents"], 0)

    def test_aud_04_09b_reviewer_post_fail_excluded_from_duplicates(self) -> None:
        def _setup_wf_tree(
            brain: Path,
            root_id: str,
            reviewer_outputs: list,
            *,
            extra_builders: int = 1,
        ) -> None:
            tasks_dir = brain / root_id / ".system_generated" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T19:00:00Z", "content": "/gravity-workflow build"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T19:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "run_workflow"}]},
                ],
            )
            log_lines = []
            for b_idx in range(extra_builders):
                bcid = f"{root_id[:8]}-0000-4000-8000-000000000b0{b_idx + 1}"
                log_lines.append(f"- [agent:builder] Builder: DONE ({bcid})")
                _write_transcript(
                    brain / bcid,
                    [
                        {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": f"2026-11-24T19:0{b_idx + 1}:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    ],
                )
            for r_idx, rev_payload in enumerate(reviewer_outputs):
                rcid = f"{root_id[:8]}-0000-4000-8000-000000000f0{r_idx + 1}"
                log_lines.append(f"- [agent:reviewer] Reviewer: DONE ({rcid})")
                _write_transcript(
                    brain / rcid,
                    [
                        {
                            "step_index": 0,
                            "type": "PLANNER_RESPONSE",
                            "status": "DONE",
                            "created_at": f"2026-11-24T19:1{r_idx}:00Z",
                            "model": "gemini-4-hydrogen-tiered-xhigh",
                            "input_tokens": 5_000,
                            "cache_read_tokens": 0,
                            "output_tokens": 200,
                            "content": rev_payload if isinstance(rev_payload, str) else json.dumps(rev_payload),
                            "tool_calls": [],
                        }
                    ],
                )
            (tasks_dir / "task-1.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"

            # 1) FAIL JSON -> PASS JSON => 0 doublon Reviewer
            r_fail = "b409b001-1111-4111-8111-111111111111"
            _setup_wf_tree(
                brain,
                r_fail,
                [
                    {"verdict": "FAIL", "blockers": ["SQLite leak"], "majors": [], "summary": "Blocked"},
                    {"verdict": "PASS", "blockers": [], "majors": [], "summary": "Fixed"},
                ],
            )
            m_fail = collect_run_metrics(r_fail, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(len([a for a in m_fail["agents"] if a["type_name"] == "reviewer"]), 2)
            dups_fail = m_fail["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_fail["duplicate_agent_count"], 0)
            self.assertEqual(dups_fail["groups"], [])

            # 2) PASS avec blockers/majors non vides -> 2e passe exclue (0 doublon)
            r_maj = "b409b002-1111-4111-8111-111111111111"
            _setup_wf_tree(
                brain,
                r_maj,
                [
                    {"verdict": "PASS", "blockers": [], "majors": ["Missing test"], "summary": "Needs fix"},
                    {"verdict": "PASS", "blockers": [], "majors": [], "summary": "OK"},
                ],
            )
            m_maj = collect_run_metrics(r_maj, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(len([a for a in m_maj["agents"] if a["type_name"] == "reviewer"]), 2)
            self.assertEqual(m_maj["efficiency_and_waste"]["duplicate_role_spawns"]["duplicate_agent_count"], 0)

            # 3) PASS sans findings -> 2e passe Reviewer inutile EST comptée en doublon
            r_pass2 = "b409b003-1111-4111-8111-111111111111"
            _setup_wf_tree(
                brain,
                r_pass2,
                [
                    {"verdict": "PASS", "blockers": [], "majors": [], "summary": "Clean"},
                    {"verdict": "PASS", "blockers": [], "majors": [], "summary": "Redundant"},
                ],
            )
            m_pass2 = collect_run_metrics(r_pass2, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(len([a for a in m_pass2["agents"] if a["type_name"] == "reviewer"]), 2)
            dups_pass2 = m_pass2["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_pass2["duplicate_agent_count"], 1)
            self.assertEqual(dups_pass2["groups"][0]["type_name"], "reviewer")

            # 4) FAIL -> PASS -> PASS + Builder doublon : 2e Reviewer exclu, 3e Reviewer + 2e Builder comptés
            r_mixed = "b409b004-1111-4111-8111-111111111111"
            _setup_wf_tree(
                brain,
                r_mixed,
                [
                    "**Verdict :** FAIL\n- [BLOCKER] Guard missing",
                    {"verdict": "PASS", "blockers": [], "majors": []},
                    {"verdict": "PASS", "blockers": [], "majors": []},
                ],
                extra_builders=2,
            )
            m_mixed = collect_run_metrics(r_mixed, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(len([a for a in m_mixed["agents"] if a["type_name"] == "reviewer"]), 3)
            dups_mixed = m_mixed["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_mixed["duplicate_agent_count"], 2)
            by_type = {g["type_name"]: g for g in dups_mixed["groups"]}
            self.assertEqual(by_type["reviewer"]["duplicate_count"], 1)
            self.assertEqual(by_type["reviewer"]["spawn_count"], 2)
            self.assertEqual(by_type["builder"]["duplicate_count"], 1)

            # 5) FAIL -> FAIL -> PASS : seule la 2e passe (idx==1) est exclue ; la 3e passe (idx==2) EST comptée
            r_fail2 = "b409b005-1111-4111-8111-111111111111"
            _setup_wf_tree(
                brain,
                r_fail2,
                [
                    {"verdict": "FAIL", "blockers": ["B1"], "majors": [], "summary": "Fail 1"},
                    {"verdict": "FAIL", "blockers": [], "majors": ["M2"], "summary": "Fail 2"},
                    {"verdict": "PASS", "blockers": [], "majors": [], "summary": "Pass 3"},
                ],
            )
            m_fail2 = collect_run_metrics(r_fail2, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(len([a for a in m_fail2["agents"] if a["type_name"] == "reviewer"]), 3)
            dups_fail2 = m_fail2["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_fail2["duplicate_agent_count"], 1)
            self.assertEqual(len(dups_fail2["groups"]), 1)
            self.assertEqual(dups_fail2["groups"][0]["type_name"], "reviewer")
            self.assertEqual(dups_fail2["groups"][0]["duplicate_count"], 1)
            self.assertEqual(dups_fail2["groups"][0]["spawn_count"], 2)

    def test_r4_shell_unwrapping_transcript_index_and_record_flags(self) -> None:
        from metrics.collector import (
            _extract_shell_command_str,
            _get_transcript_index,
            _segment_is_cli_self_archive,
            classify_metrics_prompt,
            load_pricing_catalog,
            parse_agent_transcript,
        )

        # 1) Désencapsulation CommandLine JSON doublement quotée
        tc_quoted = {
            "name": "run_command",
            "args": {"CommandLine": '"git -C ../gravity-metrics push origin dev"'},
        }
        self.assertEqual(
            _extract_shell_command_str(tc_quoted),
            "git -C ../gravity-metrics push origin dev",
        )
        tc_quoted_cli = {
            "name": "run_command",
            "args": {"CommandLine": '"python3 plugin/scripts/cli.py record abc123 --update"'},
        }
        self.assertEqual(
            _extract_shell_command_str(tc_quoted_cli),
            "python3 plugin/scripts/cli.py record abc123 --update",
        )

        # 2) classify_metrics_prompt : record/archive & --update/--update-sealed
        p_rec = classify_metrics_prompt("/gravity-metrics record f80dab92 my-lbl --update")
        self.assertEqual(p_rec["action"], "record")
        self.assertEqual(p_rec["target"], "f80dab92")
        self.assertEqual(p_rec["label"], "my-lbl")
        self.assertTrue(p_rec["update"])

        p_arch = classify_metrics_prompt("/gravity-metrics archive f80dab92 --update-sealed")
        self.assertEqual(p_arch["action"], "archive")
        self.assertTrue(p_arch["update"])

        p_stop = classify_metrics_prompt("/gravity-metrics stop --update")
        self.assertEqual(p_stop["action"], "stop")
        self.assertTrue(p_stop["update"])

        p_cap = classify_metrics_prompt("/gravity-metrics --update")
        self.assertEqual(p_cap["action"], "capture")
        self.assertTrue(p_cap["update"])

        # 3) _segment_is_cli_self_archive & parse_agent_transcript acceptent `cli.py record`
        cid = "d4000001-1111-4111-8111-111111111111"
        self.assertTrue(_segment_is_cli_self_archive(f"python3 cli.py record {cid[:8]} --update", cid))
        self.assertFalse(_segment_is_cli_self_archive("echo cli.py record d4000001", cid))

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T12:00:00Z", "content": "/gravity-architect sync"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T12:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "write_file", "args": {"Path": "specs/s.md"}}, {"name": "swarm"}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T12:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "edit_file", "args": {"Path": "plugin/metrics/collector.py"}}]},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T12:00:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "run_command", "args": {"CommandLine": '"python3 ../gravity/scripts/ecosystem_install.py --update"'}}]},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T12:00:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [tc_quoted]},
                    {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T12:00:25Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "run_command", "args": {"CommandLine": f'"python3 plugin/scripts/cli.py record {cid[:8]}"'}}]},
                ],
            )
            tx = _get_transcript_index(tpath)
            self.assertEqual(tx["max_step_index"], 5)
            entries = tx["entries"]
            self.assertTrue(entries[1]["has_swarm_or_subagent_tool"])
            self.assertFalse(entries[1]["has_direct_edit_tool"])
            self.assertTrue(entries[2]["has_direct_edit_tool"])
            self.assertTrue(entries[3]["has_push_or_verif"])
            self.assertTrue(entries[4]["has_push_or_verif"])
            self.assertTrue(entries[5]["has_cli_record_or_archive"])

            catalog = load_pricing_catalog(CATALOG_PATH)
            parsed = parse_agent_transcript(
                tpath,
                catalog=catalog,
                profile=catalog["default_profile"],
                allow_cli_self_archive=True,
                conversation_id=cid,
            )
            self.assertTrue(parsed["clean_termination"])
            self.assertEqual(parsed["last_step_index"], 5)


if __name__ == "__main__":
    unittest.main()
