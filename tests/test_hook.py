#!/usr/bin/env python3
"""Tests unitaires du hook PreInvocation /gravity metrics (100 % stdlib)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "gravity-metrics-plugin"
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
    payload_raw: str, store_dir: Path, brain_dir: Path, extra_env: dict = None
) -> dict:
    env = dict(os.environ)
    env["GRAVITY_METRICS_HOME"] = str(store_dir)
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
    return json.loads(proc.stdout.strip())


class TestPreInvocationHook(unittest.TestCase):
    def test_fast_path_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"

            for raw in (
                "",
                "{invalid_json",
                json.dumps({"lastUserInput": "Explique le code"}),
                json.dumps({"lastUserInput": "/gravity fast corrige le bug"}),
                json.dumps({"lastUserInput": "/gravity metrics-service"}),
            ):
                out = _run_hook(raw, store, brain)
                self.assertEqual(out, {})

            self.assertFalse(store.exists())

    def test_capture_flushed_and_unflushed_zero_workspace_footprint(self) -> None:
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

            # Cas (a) : /gravity metrics pas encore flushé sur disque
            tpath = _write_transcript(brain / cid, base_lines)
            payload_a = {
                "conversationId": cid,
                "transcriptPath": str(tpath),
                "cwd": str(ws),
                "lastUserInput": "/gravity metrics",
            }
            out_a = _run_hook(json.dumps(payload_a), store, brain)
            self.assertEqual(out_a["terminationBehavior"], "terminate")
            md_a = out_a["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn(METRICS_MARKER, md_a)
            self.assertIn("METRICS.md", md_a)
            self.assertIn("file:///", md_a)
            self.assertIn("ProjetAlpha", md_a)
            self.assertIn("evo→medium · slow · safe", md_a)

            records = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["input_tokens"], 150_000)
            self.assertEqual(records[0]["agents"], 2)
            self.assertEqual(records[0]["project"], "ProjetAlpha")
            self.assertEqual(records[0]["gravity_profile"], "evo→medium · slow · safe")

            # Zéro fichier créé dans le workspace cible !
            after_ws_files = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*"))
            self.assertEqual(before_ws_files, after_ws_files)

            # Cas (b) : /gravity metrics déjà flushé sur disque (même fenêtre mise à jour avec label explicite)
            tpath_b = _write_transcript(
                brain / cid,
                base_lines
                + [
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T15:02:00Z", "content": "/gravity metrics mon-run -n \"test note\""},
                ],
            )
            payload_b = {
                "conversationId": cid,
                "transcriptPath": str(tpath_b),
                "cwd": str(ws),
                "lastUserInput": '/gravity metrics mon-run -n "test note"',
            }
            out_b = _run_hook(json.dumps(payload_b), store, brain)
            md_b = out_b["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("mon-run", md_b)
            records_b = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records_b), 1)
            self.assertEqual(records_b[0]["label"], "mon-run")
            self.assertEqual(records_b[0]["note"], "test note")
            self.assertEqual(records_b[0]["input_tokens"], 150_000)

    def test_double_metrics_guard_and_subcommands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            cid1 = "2222bbbb-0000-0000-0000-000000000001"
            cid2 = "3333cccc-0000-0000-0000-000000000002"

            t1 = _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:00:00Z", "content": "/gravity run 1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T16:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T16:01:00Z", "content": "/gravity metrics"},
                    {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T16:01:01Z", "content": METRICS_MARKER},
                ],
            )
            _write_transcript(
                brain / cid2,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T17:00:00Z", "content": "/gravity run 2"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T17:00:05Z", "model": "gemini-4-neon", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                ],
            )

            # Double /gravity metrics sans nouvelle activité -> aucune entrée ajoutée
            out_dup = _run_hook(
                json.dumps({"conversationId": cid1, "transcriptPath": str(t1), "lastUserInput": "/gravity metrics"}),
                store,
                brain,
            )
            self.assertEqual(out_dup["terminationBehavior"], "terminate")
            md_dup = out_dup["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("Aucune nouvelle activité", md_dup)
            self.assertEqual(load_history(store / "data" / "history.jsonl"), [])

            # add <UUID>
            out_add1 = _run_hook(
                json.dumps({"lastUserInput": f'/gravity metrics add {cid1} sess-1 -n "premier"'}),
                store,
                brain,
            )
            self.assertIn("sess-1", out_add1["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"])
            out_add2 = _run_hook(
                json.dumps({"lastUserInput": f"/gravity metrics add {cid2} sess-2"}),
                store,
                brain,
            )
            self.assertIn("sess-2", out_add2["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"])

            # list --regen
            out_list = _run_hook(json.dumps({"lastUserInput": "/gravity metrics list --regen"}), store, brain)
            md_list = out_list["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("sess-1", md_list)
            self.assertIn("sess-2", md_list)
            self.assertNotIn("Δ vs Ref", md_list)
            self.assertNotIn("*(REF)*", md_list)

            # inspect
            out_insp = _run_hook(json.dumps({"lastUserInput": "/gravity metrics inspect sess-2"}), store, brain)
            md_insp = out_insp["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("Inspection — `sess-2`", md_insp)
            self.assertIn("gemini-4-neon", md_insp)

            # remove --purge-reports
            out_rm = _run_hook(json.dumps({"lastUserInput": "/gravity metrics remove sess-1 --purge-reports"}), store, brain)
            md_rm = out_rm["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("sess-1", md_rm)
            self.assertFalse((store / "reports" / "sess-1.json").exists())
            self.assertEqual(len(load_history(store / "data" / "history.jsonl")), 1)

            # help & option invalide
            out_help = _run_hook(json.dumps({"lastUserInput": "/gravity metrics help"}), store, brain)
            self.assertIn("/gravity metrics list", out_help["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"])
            out_bad = _run_hook(json.dumps({"lastUserInput": "/gravity metrics --invalide"}), store, brain)
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

            out_help = _run_hook(
                json.dumps({"lastUserInput": "/gravity metrics help"}),
                store,
                brain,
                extra_env=cp1252_env,
            )
            self.assertEqual(out_help["terminationBehavior"], "terminate")
            self.assertIn("→", out_help["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"])

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
                        "lastUserInput": '/gravity metrics résumé-cp1252 -n "Accent éàü — test"',
                    }
                ),
                store,
                brain,
                extra_env=cp1252_env,
            )
            self.assertEqual(out_cap["terminationBehavior"], "terminate")
            sys_msg_obj = out_cap["injectSteps"][0]["systemMessage"]
            self.assertEqual(sys_msg_obj["systemMessage"], METRICS_MARKER)
            md_cap = sys_msg_obj["metadata"]["user_markdown"]
            self.assertIn("evo→medium · slow · safe", md_cap)
            self.assertIn("résumé-cp1252", md_cap)

    def test_subcommands_and_error_do_not_poison_capture_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            cid = "5555eeee-0000-0000-0000-000000000005"
            other_cid = "6666ffff-0000-0000-0000-000000000006"

            _write_transcript(
                brain / other_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T18:30:00Z", "content": "/gravity autre"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T18:30:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )

            base_lines = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:00:00Z", "content": "/gravity implémente module billing"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T19:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 0, "output_tokens": 1_000, "tool_calls": []},
                {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:01:00Z", "content": "/gravity metrics list"},
                {"step_index": 3, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T19:01:01Z", "content": "### Gravity Metrics — Historique vide"},
                {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:01:10Z", "content": "/gravity metrics help"},
                {"step_index": 5, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T19:01:11Z", "content": "### Gravity Metrics — Commandes"},
                {"step_index": 6, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:01:20Z", "content": "/gravity metrics --invalide"},
                {"step_index": 7, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T19:01:21Z", "content": "### Gravity Metrics — Erreur"},
                {"step_index": 8, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:01:30Z", "content": f"/gravity metrics add {other_cid}"},
                {"step_index": 9, "type": "SYSTEM_MESSAGE", "status": "DONE", "created_at": "2026-11-21T19:01:31Z", "content": "### Gravity Metrics — enregistrée"},
            ]
            tpath = _write_transcript(brain / cid, base_lines)

            # Capture finale non flushée : ne doit pas être vidée par list/help/--invalide/add
            out_unflushed = _run_hook(
                json.dumps({"conversationId": cid, "transcriptPath": str(tpath), "lastUserInput": "/gravity metrics"}),
                store,
                brain,
            )
            self.assertEqual(out_unflushed["terminationBehavior"], "terminate")
            md_unflushed = out_unflushed["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertNotIn("Aucune nouvelle activité", md_unflushed)
            self.assertEqual(out_unflushed["injectSteps"][0]["systemMessage"]["systemMessage"], METRICS_MARKER)

            records = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["input_tokens"], 15_000)
            self.assertEqual(records[0]["window_mode"], "gravity_session")

            # Même capture déjà flushée sur disque
            tpath_flushed = _write_transcript(
                brain / cid,
                base_lines
                + [
                    {"step_index": 10, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T19:02:00Z", "content": "/gravity metrics billing-run"},
                ],
            )
            out_flushed = _run_hook(
                json.dumps({"conversationId": cid, "transcriptPath": str(tpath_flushed), "lastUserInput": "/gravity metrics billing-run"}),
                store,
                brain,
            )
            md_flushed = out_flushed["injectSteps"][0]["systemMessage"]["metadata"]["user_markdown"]
            self.assertIn("billing-run", md_flushed)
            records_flushed = load_history(store / "data" / "history.jsonl")
            self.assertEqual(len(records_flushed), 1)
            self.assertEqual(records_flushed[0]["label"], "billing-run")
            self.assertEqual(records_flushed[0]["input_tokens"], 15_000)


if __name__ == "__main__":
    unittest.main()
