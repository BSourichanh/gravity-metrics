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

from metrics import METRICS_MARKER, load_history  # noqa: E402


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

            # Cas (a) : /gravity-metrics pas encore flushé sur disque
            tpath = _write_transcript(brain / cid, base_lines)
            payload_a = {
                "conversationId": cid,
                "transcriptPath": str(tpath),
                "cwd": str(ws),
                "lastUserInput": "/gravity-metrics",
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

            # Cas (b) : /gravity-metrics déjà flushé sur disque (même fenêtre mise à jour avec label explicite)
            tpath_b = _write_transcript(
                brain / cid,
                base_lines
                + [
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T15:02:00Z", "content": "/gravity-metrics mon-run -n \"test note\""},
                ],
            )
            payload_b = {
                "conversationId": cid,
                "transcriptPath": str(tpath_b),
                "cwd": str(ws),
                "lastUserInput": '/gravity-metrics mon-run -n "test note"',
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

            # 2) Double /gravity-metrics après fin du tour précédent -> aucune entrée enregistrée, mais ouvre le widget
            t_finished = _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:00:00Z", "content": "/gravity run 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:01:00Z", "content": "/gravity-metrics"},
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
                        "lastUserInput": "/gravity-metrics",
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
                        "lastUserInput": "/gravity-metrics",
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


if __name__ == "__main__":
    unittest.main()
