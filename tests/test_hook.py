#!/usr/bin/env python3
"""Tests unitaires du hook PreInvocation /gravity-metrics (100 % stdlib)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Optional
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "pre_invocation_metrics.py"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import METRICS_MARKER, load_active_captures, load_history  # noqa: E402


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return tpath


def _write_subagent(parent_dir: Path, child_id: str, type_name: str, role: str, spawn_step: int) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"{child_id}.json").write_text(
        json.dumps(
            {
                "conversationId": child_id,
                "subagentDescriptor": {"typeName": type_name, "role": role},
                "state": "SUBAGENT_STATE_DONE",
                "spawnStepIndex": spawn_step,
            }
        ),
        encoding="utf-8",
    )


def _run_hook(
    payload_raw: str, store_dir: Optional[Path], brain_dir: Path, extra_env: dict = None
) -> dict:
    env = dict(os.environ)
    if store_dir is not None:
        env["GRAVITY_METRICS_HOME"] = str(store_dir)
    else:
        env.pop("GRAVITY_METRICS_HOME", None)
    env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(HOOK_SCRIPT)],
        input=payload_raw,
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=str(PLUGIN_ROOT),
        env=env,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"Hook failed (rc={proc.returncode}): {proc.stderr}")
    if not proc.stdout.isascii():
        raise AssertionError(f"Hook stdout must be 7-bit ASCII-safe, got: {proc.stdout!r}")
    return json.loads(proc.stdout.strip())


class TestPreInvocationHook(unittest.TestCase):
    def test_fast_path_passthrough_and_legacy_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"

            for raw in (
                "",
                "{invalid_json",
                json.dumps({"lastUserInput": "Explique le code"}),
                json.dumps({"lastUserInput": "/gravity fast corrige le bug"}),
                json.dumps({"lastUserInput": "/gravity metrics"}),
                json.dumps({"lastUserInput": "/gravity metrics list"}),
                json.dumps({"lastUserInput": "/gravity-metrics-service"}),
            ):
                out = _run_hook(raw, store, brain)
                self.assertEqual(out, {})

            self.assertFalse(store.exists())

    def test_capture_flushed_and_unflushed_handoff_zero_workspace_footprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetAlpha"
            (ws / ".agents" / "gravity").mkdir(parents=True)
            (ws / ".agents" / "gravity" / "profile.json").write_text(
                json.dumps({"mode": "evo", "current_level": "medium", "pace": "slow", "verification": "safe"}),
                encoding="utf-8",
            )
            before_ws_files = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*"))

            cid = "1111aaaa-2222-3333-4444-555566667777"
            base_lines = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T15:00:00Z", "content": "Chat avant gravity"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T15:00:05Z", "model": "gemini-4-neon", "input_tokens": 500_000, "cache_read_tokens": 0, "output_tokens": 5_000, "tool_calls": []},
                {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T15:01:00Z", "content": "<USER_REQUEST>\n/gravity ajoute feature paiement\n</USER_REQUEST>"},
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T15:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 100_000, "cache_read_tokens": 400_000, "output_tokens": 10_000, "tool_calls": [{"name": "invoke_subagent"}]},
            ]
            _write_subagent(brain / cid, "sub-pre", "research", "Pre", spawn_step=1)
            _write_transcript(
                brain / "sub-pre",
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-4-neon", "input_tokens": 200_000, "cache_read_tokens": 0, "output_tokens": 1_000, "tool_calls": []}],
            )
            _write_subagent(brain / cid, "sub-ok", "teamwork_worker", "Builder", spawn_step=3)
            _write_transcript(
                brain / "sub-ok",
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T15:01:12Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 50_000, "cache_read_tokens": 50_000, "output_tokens": 2_000, "tool_calls": []}],
            )

            # Cas (a0) : /gravity-metrics nu reste en lecture seule (aucune écriture)
            tpath = _write_transcript(brain / cid, base_lines)
            payload_ro = {
                "conversationId": cid,
                "transcriptPath": str(tpath),
                "cwd": str(ws),
                "lastUserInput": "/gravity-metrics",
            }
            out_ro = _run_hook(json.dumps(payload_ro), store, brain)
            self.assertNotIn("terminationBehavior", out_ro)
            sys_ro = out_ro["injectSteps"][0]["systemMessage"]
            self.assertIn("lecture seule", sys_ro["metadata"]["user_markdown"])
            self.assertIn("open_metrics_dashboard", sys_ro["systemMessage"])
            self.assertFalse((store / "data" / "history.jsonl").exists())

            # Cas (a) : /gravity-metrics stop capture même sans flush disque du prompt courant
            payload_a = {
                "conversationId": cid,
                "transcriptPath": str(tpath),
                "cwd": str(ws),
                "lastUserInput": "/gravity-metrics stop",
            }
            out_a = _run_hook(json.dumps(payload_a), store, brain)
            self.assertNotIn("terminationBehavior", out_a)
            sys_a = out_a["injectSteps"][0]["systemMessage"]
            md_a = sys_a["metadata"]["user_markdown"]
            smsg_a = sys_a["systemMessage"]
            self.assertIn(METRICS_MARKER, md_a)
            self.assertIn(METRICS_MARKER, smsg_a)
            self.assertIn("open_metrics_dashboard", smsg_a)
            self.assertIn("ProjetAlpha", smsg_a)
            self.assertIn("METRICS.md", md_a)
            self.assertNotIn(".html", md_a)
            self.assertIn("evo→medium · slow · safe", md_a)

            self.assertFalse((store / "dashboard.html").exists())
            records = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["input_tokens"], 150_000)
            self.assertEqual(records[0]["agents"], 2)
            self.assertEqual(records[0]["project"], "ProjetAlpha")
            self.assertEqual(records[0]["gravity_profile"], "evo→medium · slow · safe")
            slug_a = records[0]["detail_slug"]
            self.assertTrue((store / "reports" / f"{slug_a}.json").is_file())
            self.assertTrue((store / "reports" / f"{slug_a}.md").is_file())
            self.assertFalse((store / "reports" / f"{slug_a}.html").exists())

            after_ws_files = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*"))
            self.assertEqual(before_ws_files, after_ws_files)

            # Cas (b1) : /gravity-metrics déjà flushé sur disque sans --force -> immuabilité (pas d'écrasement)
            tpath_b = _write_transcript(
                brain / cid,
                base_lines
                + [
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T15:02:00Z", "content": "/gravity-metrics mon-run -n \"test note\""},
                ],
            )
            payload_b_immut = {
                "conversationId": cid,
                "transcriptPath": str(tpath_b),
                "cwd": str(ws),
                "lastUserInput": '/gravity-metrics mon-run -n "test note"',
            }
            out_immut = _run_hook(json.dumps(payload_b_immut), store, brain)
            self.assertNotIn("terminationBehavior", out_immut)
            md_immut = out_immut["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("déjà archivée", md_immut)
            records_immut = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records_immut), 1)
            self.assertEqual(records_immut[0]["label"], records[0]["label"])

            # Cas (b2) : avec --force sur la même fenêtre -> mise à jour explicite autorisée
            payload_b = {
                "conversationId": cid,
                "transcriptPath": str(tpath_b),
                "cwd": str(ws),
                "lastUserInput": '/gravity-metrics mon-run -n "test note" --force',
            }
            out_b = _run_hook(json.dumps(payload_b), store, brain)
            self.assertNotIn("terminationBehavior", out_b)
            md_b = out_b["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("mon-run", md_b)
            self.assertIn("mon-run", out_b["injectSteps"][0]["systemMessage"]["systemMessage"])
            records_b = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records_b), 1)
            self.assertEqual(records_b[0]["label"], "mon-run")
            self.assertEqual(records_b[0]["note"], "test note")
            self.assertEqual(records_b[0]["input_tokens"], 150_000)
            self.assertEqual(records_b[0]["status"], "archived")

    def test_handoff_loop_guard_double_metrics_and_invalid_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetBeta"
            ws.mkdir()
            cid1 = "2222bbbb-0000-0000-0000-000000000001"

            # 1) Invocation 2+ d'un tour en cours -> passthrough {}
            t_in_progress = _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:00:00Z", "content": "/gravity run 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T16:01:01Z", "content": METRICS_MARKER},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:01:02Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 500, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "open_metrics_dashboard"}]},
                ],
            )
            out_mid = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid1,
                        "transcriptPath": str(t_in_progress),
                        "cwd": str(ws),
                        "invocationNum": 1,
                        "lastUserInput": "/gravity-metrics",
                    }
                ),
                store,
                brain,
            )
            self.assertEqual(out_mid, {})
            self.assertFalse((store / "data" / "history.jsonl").exists())

            # 1b) Invocation 2+ d'un tour /gravity-metrics sans METRICS_MARKER mais avec tool_calls -> passthrough {}
            t_mid_no_marker = _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:00:00Z", "content": "/gravity run 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:01:02Z", "model": "gemini-4-neon-xhigh", "input_tokens": 500, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "read_file"}]},
                ],
            )
            out_mid_no_marker = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid1,
                        "transcriptPath": str(t_mid_no_marker),
                        "cwd": str(ws),
                        "invocationNum": 1,
                        "lastUserInput": "/gravity-metrics",
                    }
                ),
                store,
                brain,
            )
            self.assertEqual(out_mid_no_marker, {})
            self.assertFalse((store / "data" / "history.jsonl").exists())

            # 2) Double /gravity-metrics stop après fin du tour précédent -> aucune entrée enregistrée, mais ouvre le widget
            t_finished = _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:00:00Z", "content": "/gravity run 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:01:00Z", "content": "/gravity-metrics stop"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T16:01:01Z", "content": METRICS_MARKER},
                    {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:01:02Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 500, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "open_metrics_dashboard"}]},
                    {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:01:03Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 400, "cache_read_tokens": 0, "output_tokens": 20, "tool_calls": []},
                ],
            )
            out_dup = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid1,
                        "transcriptPath": str(t_finished),
                        "cwd": str(ws),
                        "lastUserInput": "/gravity-metrics stop",
                    }
                ),
                store,
                brain,
            )
            self.assertNotIn("terminationBehavior", out_dup)
            sys_dup = out_dup["injectSteps"][0]["systemMessage"]
            self.assertIn("Aucune nouvelle activité", sys_dup["metadata"]["user_markdown"])
            self.assertIn("open_metrics_dashboard", sys_dup["systemMessage"])
            self.assertIn("ProjetBeta", sys_dup["systemMessage"])
            self.assertEqual(load_history(store / "data" / "history.jsonl"), [])

            # 3) Option invalide -> termine avec message d'erreur
            out_bad = _run_hook(json.dumps({"lastUserInput": "/gravity-metrics --invalide"}), store, brain)
            self.assertEqual(out_bad["terminationBehavior"], "terminate")
            self.assertIn("Erreur", out_bad["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"])

    def test_hook_windows_cp1252_encoding_resilience(self) -> None:
        cp1252_env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetÉté"
            (ws / ".agents" / "gravity").mkdir(parents=True)
            (ws / ".agents" / "gravity" / "profile.json").write_text(
                json.dumps({"mode": "evo", "current_level": "medium", "pace": "slow", "verification": "safe"}),
                encoding="utf-8",
            )

            cid = "4444dddd-0000-0000-0000-000000000004"
            tpath = _write_transcript(
                brain / cid,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-21T18:00:00Z",
                        "content": "/gravity implémente résumé Unicode — café",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-21T18:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 12_000,
                        "cache_read_tokens": 8_000,
                        "output_tokens": 600,
                        "tool_calls": [],
                    },
                ],
            )
            out_cap = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "lastUserInput": '/gravity-metrics résumé-cp1252 -n "Accent éàü — test"',
                    }
                ),
                store,
                brain,
                extra_env=cp1252_env,
            )
            self.assertNotIn("terminationBehavior", out_cap)
            sys_msg_obj = out_cap["injectSteps"][0]["systemMessage"]
            self.assertIn(METRICS_MARKER, sys_msg_obj["systemMessage"])
            self.assertIn("open_metrics_dashboard", sys_msg_obj["systemMessage"])
            md_cap = sys_msg_obj["metadata"]["user_markdown"]
            self.assertIn("evo→medium · slow · safe", md_cap)
            self.assertIn("résumé-cp1252", md_cap)

    def test_gravity_home_fallback_and_gravity_workflow_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grav_home = root / "gravity-home"
            brain = root / "brain"
            ws = root / "ProjetWorkflow"
            ws.mkdir()
            cid = "5555eeee-0000-0000-0000-000000000005"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T10:00:00Z", "content": "Chat libre"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T10:00:05Z", "model": "gemini-4-neon", "input_tokens": 500_000, "cache_read_tokens": 0, "output_tokens": 2_000, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T10:01:00Z", "content": "/gravity-workflow auto optimise tests"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T10:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 40_000, "cache_read_tokens": 60_000, "output_tokens": 1_000, "tool_calls": []},
                ],
            )
            out = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "lastUserInput": "/gravity-metrics stop",
                    }
                ),
                None,
                brain,
                extra_env={"GRAVITY_HOME": str(grav_home)},
            )
            self.assertNotIn("terminationBehavior", out)
            expected_history = grav_home / "metrics" / "data" / "history.jsonl"
            self.assertTrue(expected_history.is_file())
            records = load_history(expected_history)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["input_tokens"], 40_000)
            self.assertEqual(records[0]["window_mode"], "gravity_session")
            self.assertIn("gravity", records[0]["features_used"])

    def test_strict_passthrough_long_prompt_and_slug_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetPass"
            ws.mkdir()
            cid = "6666ffff-0000-0000-0000-000000000006"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T12:00:00Z", "content": "/gravity test"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T12:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )

            long_grill = "/gravity-metrics /grill-me " + ("a " * 170)
            long_prose = "/gravity-metrics Explique moi pourquoi cette session coute si cher par rapport aux autres"
            seven_words = "/gravity-metrics un deux trois quatre cinq six sept"
            for prompt in (long_grill, long_prose, seven_words):
                out = _run_hook(
                    json.dumps(
                        {
                            "conversationId": cid,
                            "transcriptPath": str(tpath),
                            "cwd": str(ws),
                            "lastUserInput": prompt,
                        }
                    ),
                    store,
                    brain,
                )
                self.assertEqual(out, {}, f"Passthrough attendu pour {prompt!r}")
            self.assertFalse((store / "data" / "history.jsonl").exists())

            long_explicit_lbl = "super-long-label-" + ("x" * 100)
            out_lbl = _run_hook(
                json.dumps(
                    {
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "lastUserInput": f'/gravity-metrics -l "{long_explicit_lbl}"',
                    }
                ),
                store,
                brain,
            )
            self.assertNotIn("terminationBehavior", out_lbl)
            recs = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertLessEqual(len(recs[0]["detail_slug"]), 64)

    def test_pending_archive_and_bridge_job_block(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            bridge_home = root / "bridge"
            bridge_home.mkdir()
            db_path = bridge_home / "bridge.db"
            ws = root / "GravityStudio"
            ws.mkdir()

            root_cid = "7777aaaa-1111-2222-3333-444455556666"
            child_cid = "8888bbbb-1111-2222-3333-444455556666"
            _write_transcript(
                brain / root_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T13:00:00Z", "content": "/gravity-architect sync"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T13:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 10_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 200,
                        "tool_calls": [
                            {
                                "name": "call_mcp_tool",
                                "args": {
                                    "ServerName": '"gravity-bridge_bridge"',
                                    "ToolName": '"send_message"',
                                    "Arguments": json.dumps(
                                        {
                                            "from_workspace": "gravity-studio",
                                            "to_workspace": "gravity-metrics",
                                            "msg_type": "handoff",
                                            "dispatch": True,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": json.dumps({"thread_id": "thr-20261122-abc123", "to_workspace": "gravity-metrics", "msg_type": "handoff"}),
                    },
                ],
            )
            _write_transcript(
                brain / child_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T13:00:20Z", "content": "[GRAVITY-BRIDGE | thread=thr-20261122-abc123]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T13:00:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )

            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
            )
            con.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, action TEXT, target_conversation_id TEXT, status TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-20261122-abc123", "gravity-studio", "gravity-metrics", "handoff", "open", child_cid, root_cid, "2026-11-22T13:00:15Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("job-1", "thr-20261122-abc123", "gravity-metrics", "new_conversation", child_cid, "dispatched", "2026-11-22T13:00:15Z"),
            )
            con.commit()
            con.close()

            extra_env = {"GRAVITY_BRIDGE_HOME": str(bridge_home)}

            # 1) /gravity-metrics pending -> liste uniquement la racine (exclut child_cid) sans écrire
            out_pend = _run_hook(
                json.dumps({"cwd": str(ws), "lastUserInput": "/gravity-metrics pending"}),
                store,
                brain,
                extra_env=extra_env,
            )
            self.assertNotIn("terminationBehavior", out_pend)
            md_pend = out_pend["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("7777aaaa", md_pend)
            self.assertNotIn("8888bbbb", md_pend)
            self.assertIn("job actif", md_pend)
            self.assertFalse((store / "data" / "history.jsonl").exists())

            # 2) Archivage bloqué tant que le job Bridge est 'dispatched' (zéro écriture)
            out_blk = _run_hook(
                json.dumps({"cwd": str(ws), "lastUserInput": "/gravity-metrics archive 7777aaaa"}),
                store,
                brain,
                extra_env=extra_env,
            )
            self.assertEqual(out_blk.get("terminationBehavior"), "terminate")
            md_blk = out_blk["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("--force", md_blk)
            self.assertFalse((store / "data" / "history.jsonl").exists())

            # 3) Clôture du job Bridge -> /gravity-metrics archive <prefix> réussit et unifie les tokens
            con = sqlite3.connect(str(db_path))
            con.execute("UPDATE jobs SET status = 'completed' WHERE id = 'job-1'")
            con.commit()
            con.close()

            out_arch = _run_hook(
                json.dumps({"cwd": str(ws), "lastUserInput": "/gravity-metrics archive 7777aaaa e2e-arch"}),
                store,
                brain,
                extra_env=extra_env,
            )
            self.assertNotIn("terminationBehavior", out_arch)
            recs = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["label"], "e2e-arch")
            self.assertEqual(recs[0]["agents"], 2)
            self.assertEqual(recs[0]["input_tokens"], 15_000)
            self.assertEqual(recs[0]["bridge_children_count"], 1)
            self.assertEqual(recs[0]["bridge_workspaces"], ["gravity-metrics"])

    def test_architect_auto_capture_idempotence_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            ws = root / "ProjetArch"
            ws.mkdir()

            cid = "9999aaaa-1111-2222-3333-444455556666"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "Intro"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )

            # 1) invocationNum = 1 / None / True + commandes maintenance/vide ignorés
            for bad_inv in (1, None, True):
                out = _run_hook(
                    json.dumps({
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "invocationNum": bad_inv,
                        "lastUserInput": "/gravity-architect spec nouveau module",
                    }),
                    store,
                    brain,
                )
                self.assertEqual(out, {})
            self.assertEqual(load_active_captures(store), {})

            for maint_cmd in (
                "/gravity-architect",
                "/gravity-architect   ",
                "/gravity-architect init",
                "/gravity-architect add",
                "/gravity-architect sync",
                "/gravity-architect status",
                "/gravity-architect archive mon-lot",
                "/gravity-architect audit security",
            ):
                out = _run_hook(
                    json.dumps({
                        "conversationId": cid,
                        "transcriptPath": str(tpath),
                        "cwd": str(ws),
                        "invocationNum": 0,
                        "lastUserInput": maint_cmd,
                    }),
                    store,
                    brain,
                )
                self.assertEqual(out, {})
            self.assertEqual(load_active_captures(store), {})

            # 2) invocationNum == 0 avec instruction -> passthrough {}, capture architect-<slug>, 0 fichier workspace
            out_ok = _run_hook(
                json.dumps({
                    "conversationId": cid,
                    "transcriptPath": str(tpath),
                    "cwd": str(ws),
                    "invocationNum": 0,
                    "lastUserInput": '/gravity-architect handoff /caveman ultra "Créer écosystème W1"',
                }),
                store,
                brain,
            )
            self.assertEqual(out_ok, {})
            self.assertEqual(list(ws.iterdir()), [])
            caps = load_active_captures(store)
            self.assertIn(cid, caps)
            self.assertEqual(caps[cid]["start_step"], 2)
            self.assertEqual(caps[cid]["label"], "architect-creer-ecosysteme-w1")
            self.assertEqual(caps[cid]["project"], "ProjetArch")

            # 3) Non-écrasement d'une capture ouverte (overwrite=False) sur second /gravity-architect invocationNum=0
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "content": "Intro"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "content": "/gravity-architect handoff"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 60, "tool_calls": []},
                ],
            )
            out_second = _run_hook(
                json.dumps({
                    "conversationId": cid,
                    "transcriptPath": str(tpath),
                    "cwd": str(ws),
                    "invocationNum": 0,
                    "lastUserInput": "/gravity-architect autre instruction",
                }),
                store,
                brain,
            )
            self.assertEqual(out_second, {})
            caps_after = load_active_captures(store)
            self.assertEqual(caps_after[cid]["start_step"], 2)
            self.assertEqual(caps_after[cid]["label"], "architect-creer-ecosysteme-w1")

            # 4) /gravity-metrics start explicite écrase (overwrite=True)
            out_explicit = _run_hook(
                json.dumps({
                    "conversationId": cid,
                    "transcriptPath": str(tpath),
                    "cwd": str(ws),
                    "lastUserInput": "/gravity-metrics start jalon-manuel",
                }),
                store,
                brain,
            )
            self.assertEqual(out_explicit.get("terminationBehavior"), "terminate")
            caps_explicit = load_active_captures(store)
            self.assertEqual(caps_explicit[cid]["start_step"], 4)
            self.assertEqual(caps_explicit[cid]["label"], "jalon-manuel")


if __name__ == "__main__":
    unittest.main()
