#!/usr/bin/env python3
"""Tests unitaires et d'intégration TASK-UX-01 : continuité /gravity, 4 phases alignées et history.jsonl."""

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
PRE_HOOK = PLUGIN_ROOT / "hooks" / "pre_invocation_metrics.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    METRICS_MARKER,
    archive_conversation,
    collect_run_metrics,
    find_archived_session,
    is_gravity_session_prompt,
    list_pending_sessions,
    load_active_captures,
    load_history,
    resolve_gravity_window,
    to_session_record,
)
from metrics.milestone_analyzer import _is_window_archived, _load_archived_keys  # noqa: E402


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(item if isinstance(item, str) else json.dumps(item) for item in lines) + "\n"
    tpath.write_text(raw, encoding="utf-8")
    return tpath


def _write_subagent(
    parent_dir: Path,
    child_id: str,
    type_name: str,
    role: str,
    spawn_step: int,
) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"{child_id}.json").write_text(
        json.dumps(
            {
                "conversationId": child_id,
                "subagentDescriptor": {"typeName": type_name, "role": role},
                "state": "SUBAGENT_STATE_DONE",
                "spawnStepIndex": spawn_step,
                "workspaceUris": [],
            }
        ),
        encoding="utf-8",
    )


def _run_pre_hook(
    payload: dict,
    store: Path,
    brain: Path,
    *,
    extra_env: dict | None = None,
) -> dict:
    env = {
        **os.environ,
        "GRAVITY_METRICS_HOME": str(store),
        "ANTIGRAVITY_BRAIN_DIR": str(brain),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(PRE_HOOK)],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"Hook failed rc={proc.returncode} stderr={proc.stderr}")
    return json.loads(proc.stdout)


class TestSessionContinuityAndPhases(unittest.TestCase):
    def test_consultative_gravity_prompts_excluded_from_session_anchor(self) -> None:
        for cmd in (
            "/gravity",
            "/gravity   ",
            "/gravity:gravity",
            "/gravity status",
            "/gravity doctor",
            "/gravity dashboard",
            "/gravity modules",
            "/gravity help",
            "/gravity overview",
            "/gravity:gravity status --json",
        ):
            self.assertFalse(is_gravity_session_prompt(cmd), cmd)

        for cmd in (
            "/gravity init",
            "/gravity run fix",
            "/gravity-architect spec auth",
            "/gravity-workflow auto fix",
            "/gravity-bridge status",
        ):
            self.assertTrue(is_gravity_session_prompt(cmd), cmd)

    def test_gravity_then_architect_and_workflow_zero_duplicate_and_idempotent_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g_home = root / "gravity_home"
            store = root / "store"
            brain = root / "brain"
            ws = root / "Atelier"
            ws.mkdir()
            (g_home / "core").mkdir(parents=True)
            (g_home / "core" / "modules.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "modules": [
                            {
                                "id": "gravity-architect",
                                "commands": ["/gravity-architect"],
                                "capture_policy": {
                                    "commands": ["/gravity-architect"],
                                    "exclude_subcommands": ["init", "sync", "status", "audit", "archive"],
                                    "consultative_bypass": True,
                                    "label_prefix": "architect",
                                },
                            },
                            {
                                "id": "gravity-workflow",
                                "commands": ["/gravity-workflow"],
                                "capture_policy": {
                                    "commands": ["/gravity-workflow"],
                                    "exclude_subcommands": ["status", "evals", "init", "help"],
                                    "consultative_bypass": True,
                                    "label_prefix": "workflow",
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            cid = "ux010001-1111-4111-8111-111111111111"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:01:00Z", "content": "/gravity status"},
                    {"step_index": 3, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:02:00Z", "content": "/gravity-architect spec dashboard ux"},
                ],
            )
            env_g = {"GRAVITY_HOME": str(g_home)}

            # /gravity and /gravity status do not open any capture
            self.assertEqual(
                _run_pre_hook(
                    {"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "invocationNum": 0, "lastUserInput": "/gravity"},
                    store, brain, extra_env=env_g,
                ),
                {},
            )
            self.assertEqual(
                _run_pre_hook(
                    {"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "invocationNum": 0, "lastUserInput": "/gravity status"},
                    store, brain, extra_env=env_g,
                ),
                {},
            )
            self.assertEqual(load_active_captures(store), {})

            # /gravity-architect starts capture at step 3
            _run_pre_hook(
                {"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "invocationNum": 0, "lastUserInput": "/gravity-architect spec dashboard ux"},
                store, brain, extra_env=env_g,
            )
            caps_1 = load_active_captures(store)
            self.assertIn(cid, caps_1)
            self.assertEqual(caps_1[cid]["start_step"], 3)
            orig_label = caps_1[cid]["label"]
            self.assertTrue(orig_label.startswith("architect-"))

            # Subsequent /gravity-workflow reuses active capture idempotently without resetting start_step or label
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:01:00Z", "content": "/gravity status"},
                    {"step_index": 3, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:02:00Z", "content": "/gravity-architect spec dashboard ux"},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                    {"step_index": 5, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:03:00Z", "content": "/gravity-workflow build dashboard ux"},
                    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:03:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/ui.py"}}]},
                ],
            )
            _run_pre_hook(
                {"conversationId": cid, "transcriptPath": str(tpath), "cwd": str(ws), "invocationNum": 0, "lastUserInput": "/gravity-workflow build dashboard ux"},
                store, brain, extra_env=env_g,
            )
            caps_2 = load_active_captures(store)
            self.assertEqual(caps_2[cid]["start_step"], 3)
            self.assertEqual(caps_2[cid]["label"], orig_label)

            # Archive pops active_captures; pending_sessions and history.jsonl must stay free of duplicates
            hist_path = store / "data" / "history.jsonl"
            saved_rec, recs, _ = archive_conversation(
                cid, store_dir=store, history_path=hist_path, brain_dir=brain, catalog_path=CATALOG_PATH,
            )
            self.assertEqual(len(recs), 1)
            self.assertEqual(saved_rec["window_start_step"], 3)
            self.assertEqual(saved_rec["root_last_step_index"], 6)
            self.assertEqual(saved_rec["input_tokens"], 30_000)
            self.assertEqual(load_active_captures(store), {})

            win_after = resolve_gravity_window(tpath, current_prompt_is_metrics=False)
            self.assertEqual(win_after["start_step_index"], 3)
            self.assertEqual(list_pending_sessions(brain, hist_path, store_dir=store), [])

            # Re-archiving without --force returns already_archived without creating a duplicate
            dup_rec, recs2, _ = archive_conversation(
                cid, store_dir=store, history_path=hist_path, brain_dir=brain, catalog_path=CATALOG_PATH,
            )
            self.assertTrue(dup_rec.get("already_archived"))
            self.assertEqual(len(recs2), 1)

    def test_sealed_interval_overlap_in_is_window_archived_and_find_archived_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history.jsonl"
            cid = "ov010001-1111-4111-8111-111111111111"
            records = [
                {
                    "conversation_id": cid,
                    "status": "archived",
                    "label": "sealed-3-10",
                    "window_start_step": 3,
                    "root_last_step_index": 10,
                }
            ]
            hist.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
            keys = _load_archived_keys(hist)

            # Overlapping windows ([0..10], [5..12], [7..None]) must match
            self.assertTrue(_is_window_archived(cid, 0, "gravity_session", keys, win_end_step=10))
            self.assertTrue(_is_window_archived(cid, 5, "gravity_session", keys, win_end_step=12))
            self.assertTrue(_is_window_archived(cid, 7, "gravity_session", keys))
            self.assertIsNotNone(find_archived_session(records, cid, 0, window_end_step=10))
            self.assertIsNotNone(find_archived_session(records, cid, 7))

            # Disjoint later window ([11..15]) must not match
            self.assertFalse(_is_window_archived(cid, 11, "gravity_session", keys, win_end_step=15))
            self.assertIsNone(find_archived_session(records, cid, 11, window_end_step=15))

    def test_4_phases_alignment_tokens_category_models_and_verif_step_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"
            cid = "ph010001-1111-4111-8111-111111111111"
            sub_res = "sub-res-0001-4111-8111-111111111111"
            wf_plan = "bbbb0001-1111-4111-8111-111111111111"
            wf_build = "bbbb0002-1111-4111-8111-111111111111"
            wf_rev = "bbbb0003-1111-4111-8111-111111111111"

            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T10:00:00Z", "content": "/gravity-architect spec ui"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:00:10Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "invoke_subagent"}]},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T10:01:00Z", "content": "/gravity-architect handoff"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:01:10Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "update_steps"}]},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:02:00Z", "model": "gemini-4-neon", "input_tokens": 3_000, "cache_read_tokens": 1_000, "output_tokens": 300, "tool_calls": [{"name": "run_workflow"}, {"name": "edit_file", "arguments": {"Path": "plugin/core.py"}}]},
                    {
                        "step_index": 5,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-27T10:03:00Z",
                        "content": (
                            f"- [agent:planner] Planner Main: DONE ({wf_plan})\n"
                            f"- [agent:builder] Builder Core: DONE ({wf_build})\n"
                            f"- [agent:reviewer] Reviewer Final: DONE ({wf_rev})\n"
                        ),
                    },
                    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:04:00Z", "model": "gemini-4-neon", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python3 -m unittest discover -s tests -v"}}]},
                    # Root ecosystem docs edited in Phase 4 must NOT overwrite verif_step
                    {"step_index": 7, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:05:00Z", "model": "gemini-4-neon", "input_tokens": 500, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "ECOSYSTEM.md"}}, {"name": "write_file", "arguments": {"Path": "docs/INDEX.md"}}, {"name": "write_file", "arguments": {"Path": "audits/agents/2026-11-27.md"}}, {"name": "edit_file", "arguments": {"Path": "CHANGELOG.md"}}, {"name": "edit_file", "arguments": {"Path": "ROADMAP.md"}}]},
                ],
            )
            _write_subagent(brain / cid, sub_res, "research", "Explorer", spawn_step=1)
            _write_transcript(
                brain / sub_res,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:00:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_200, "cache_read_tokens": 300, "output_tokens": 100, "tool_calls": [{"name": "finish"}]}],
            )
            _write_transcript(
                brain / wf_plan,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:02:10Z", "model": "gemini-4-neon", "input_tokens": 2_500, "cache_read_tokens": 500, "output_tokens": 200, "tool_calls": [{"name": "finish"}]}],
            )
            _write_transcript(
                brain / wf_build,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:02:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 2_000, "output_tokens": 500, "tool_calls": [{"name": "finish"}]}],
            )
            _write_transcript(
                brain / wf_rev,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:03:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_500, "cache_read_tokens": 500, "output_tokens": 250, "tool_calls": [{"name": "finish"}]}],
            )

            # Before archiving: pending_sessions also exposes aligned 4 phases in Validation
            pending = list_pending_sessions(brain, hist_path, store_dir=store)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["current_phase"], "Validation")
            self.assertEqual(
                [p["id"] for p in pending[0]["milestones"]["phases"]],
                ["conception", "planification", "production", "validation"],
            )

            metrics = collect_run_metrics(cid, brain, CATALOG_PATH, gravity_window=True)
            ms = metrics["milestones"]
            self.assertEqual(ms["mode"], "4_phases")
            self.assertEqual(ms["current_phase"], "Validation")
            self.assertEqual(
                [p["id"] for p in ms["phases"]],
                ["conception", "planification", "production", "validation"],
            )
            by_pid = {p["id"]: p for p in ms["phases"]}
            # Conception: root step 1 (1600) + sub_res (1600) = 3200
            self.assertEqual(by_pid["conception"]["tokens"], 3_200)
            # Planification: root step 3 (2200) + wf_plan (3200) = 5400
            self.assertEqual(by_pid["planification"]["tokens"], 5_400)
            # Production: root step 4 (4300) + wf_build (8500) = 12800
            self.assertEqual(by_pid["production"]["tokens"], 12_800)
            # Validation: root step 6 (4400) + step 7 (550) + wf_rev (4250) = 9200
            self.assertEqual(by_pid["validation"]["tokens"], 9_200)
            self.assertEqual(
                sum(p["tokens"] for p in ms["phases"]),
                metrics["summary"]["total_tokens"],
            )

            rec = to_session_record(metrics, label="ux01-phases")
            self.assertEqual(
                [p["id"] for p in rec["milestones"]["phases"]],
                ["conception", "planification", "production", "validation"],
            )
            self.assertEqual([p["tokens"] for p in rec["milestones"]["phases"]], [3_200, 5_400, 12_800, 9_200])
            self.assertIn("category_tokens", rec)
            self.assertEqual(rec["category_tokens"]["discovery"], {"agents": 1, "tokens": 1_600, "useful_tokens": 1_300})
            self.assertEqual(rec["category_tokens"]["build"], {"agents": 1, "tokens": 8_500, "useful_tokens": 6_500})
            self.assertEqual(rec["category_tokens"]["verification"], {"agents": 1, "tokens": 4_250, "useful_tokens": 3_750})
            self.assertEqual(rec["models"]["gemini-4-neon"]["agents"], 2)
            self.assertEqual(rec["models"]["gemini-4-neon"]["tokens"], 16_250)
            self.assertEqual(rec["models"]["gemini-4-neon"]["useful_tokens"], 14_250)
            self.assertEqual(rec["models"]["gemini-3.8-flash-tiered"]["agents"], 3)
            self.assertEqual(rec["models"]["gemini-3.8-flash-tiered"]["tokens"], 14_350)
            self.assertEqual(rec["models"]["gemini-3.8-flash-tiered"]["useful_tokens"], 11_550)

            # Backward compatibility with legacy history.jsonl without category_tokens/tokens
            legacy_hist = root / "legacy_history.jsonl"
            legacy_hist.write_text(
                json.dumps({"conversation_id": "leg-1", "label": "legacy", "models": {"gemini-4-neon": {"calls": 1, "cost_usd": 0.1}}}) + "\n",
                encoding="utf-8",
            )
            loaded_legacy = load_history(legacy_hist)
            self.assertEqual(len(loaded_legacy), 1)
            self.assertIsNone(loaded_legacy[0].get("category_tokens"))


if __name__ == "__main__":
    unittest.main()
