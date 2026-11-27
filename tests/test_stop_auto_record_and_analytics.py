#!/usr/bin/env python3
"""Tests unitaires et d'intégration TASK-UX-04 : filet Stop, classify_agent, linked_metrics et analytics 4 axes."""

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
STOP_HOOK = PLUGIN_ROOT / "hooks" / "stop_auto_record.py"
PROVIDER_SCRIPT = PLUGIN_ROOT / "scripts" / "dashboard_provider.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    archive_conversation,
    classify_agent,
    list_audits,
    load_active_captures,
    load_history,
    read_audit,
    start_active_capture,
)


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


def _run_stop_hook(
    raw_input: str,
    store: Path,
    brain: Path,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GRAVITY_METRICS_HOME": str(store),
        "ANTIGRAVITY_BRAIN_DIR": str(brain),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, "-B", str(STOP_HOOK)],
        input=raw_input,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )


def _invoke_provider(args: list, *, stdin_obj: dict) -> dict:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-B", str(PROVIDER_SCRIPT), *args],
        input=json.dumps(stdin_obj),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"dashboard_provider failed rc={proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)


class TestStopAutoRecordAndAnalytics(unittest.TestCase):
    def test_hooks_json_declares_stop_handler_and_fail_safe_guards(self) -> None:
        hooks_cfg = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("gravity-metrics-stop", hooks_cfg)
        stop_grp = hooks_cfg["gravity-metrics-stop"]
        self.assertIs(stop_grp.get("enabled"), True)
        self.assertEqual(
            stop_grp.get("Stop"),
            [{"type": "command", "command": "python3 hooks/stop_auto_record.py", "timeout": 10}],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            hist_path = store / "data" / "history.jsonl"

            # Malformed stdin / empty / fullyIdle=false -> exit 0, stdout {}, zero writes
            for raw in ("", "{broken-json", json.dumps({"fullyIdle": False, "conversationId": "c1"})):
                proc = _run_stop_hook(raw, store, brain)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout), {})
            self.assertFalse(hist_path.exists())

            cid = "st040001-1111-4111-8111-111111111111"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T11:00:00Z", "content": "/gravity-architect spec ux"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:00:10Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/ui.py"}}]},
                ],
            )
            # Without active capture -> does nothing even if fullyIdle=True
            proc_nocap = _run_stop_hook(
                json.dumps({"fullyIdle": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(proc_nocap.returncode, 0)
            self.assertEqual(json.loads(proc_nocap.stdout), {})
            self.assertEqual(load_history(hist_path), [])

            # Active capture without Validation marker (still in Production) -> does nothing
            start_active_capture(cid, start_step=0, label="stop-test-run", project="gravity-metrics", store_dir=store)
            proc_prod = _run_stop_hook(
                json.dumps({"fullyIdle": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(proc_prod.returncode, 0)
            self.assertEqual(json.loads(proc_prod.stdout), {})
            self.assertEqual(load_history(hist_path), [])
            self.assertIn(cid, load_active_captures(store))

            # Add Validation step WITH error -> must NOT auto-record
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T11:00:00Z", "content": "/gravity-architect spec ux"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:00:10Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/ui.py"}}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "ERROR", "created_at": "2026-11-27T11:01:00Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python3 plugin/scripts/validate.py --strict"}}]},
                ],
            )
            proc_err = _run_stop_hook(
                json.dumps({"fullyIdle": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(proc_err.returncode, 0)
            self.assertEqual(json.loads(proc_err.stdout), {})
            self.assertEqual(load_history(hist_path), [])

            # Clean Validation + payload error flag -> still blocked
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T11:00:00Z", "content": "/gravity-architect spec ux"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:00:10Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/ui.py"}}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:01:00Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python3 plugin/scripts/validate.py --strict && python3 -m unittest discover -s tests -v"}}]},
                    {"step_index": 3, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-27T11:01:05Z", "content": "Exit code 0"},
                ],
            )
            proc_pay_err = _run_stop_hook(
                json.dumps({"fullyIdle": True, "hasError": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(json.loads(proc_pay_err.stdout), {})
            self.assertEqual(load_history(hist_path), [])

            # Clean Validation + fullyIdle=True + 0 error -> silently archives and pops active_captures
            proc_ok = _run_stop_hook(
                json.dumps({"fullyIdle": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(proc_ok.returncode, 0, proc_ok.stderr)
            self.assertEqual(proc_ok.stdout.strip(), "{}")
            recs = load_history(hist_path)
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0]["label"], "stop-test-run")
            self.assertEqual(recs[0]["total_tokens"], 3_650)
            self.assertNotIn(cid, load_active_captures(store))

            # Subsequent update_sealed when active capture re-opened on same window updates without duplicate
            start_active_capture(cid, start_step=0, label="stop-test-run", project="gravity-metrics", store_dir=store)
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T11:00:00Z", "content": "/gravity-architect spec ux"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:00:10Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/ui.py"}}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:01:00Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python3 plugin/scripts/validate.py --strict"}}]},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:02:00Z", "model": "gemini-4-neon", "input_tokens": 400, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )
            proc_upd = _run_stop_hook(
                json.dumps({"fullyIdle": True, "conversationId": cid, "transcriptPath": str(tpath)}),
                store,
                brain,
            )
            self.assertEqual(proc_upd.returncode, 0)
            self.assertEqual(proc_upd.stdout.strip(), "{}")
            recs_after = load_history(hist_path)
            self.assertEqual(len(recs_after), 1)
            self.assertEqual(recs_after[0]["total_tokens"], 4_100)

    def test_classify_agent_native_antigravity_subagents(self) -> None:
        self.assertEqual(classify_agent("general", "Implémenter le module"), "build")
        self.assertEqual(classify_agent("general", "Security Reviewer"), "verification")
        self.assertEqual(classify_agent("general", "Codebase Explorer"), "discovery")
        self.assertEqual(classify_agent("code_review", "Audit diff"), "verification")
        self.assertEqual(classify_agent("code-review", ""), "verification")
        self.assertEqual(classify_agent("SUBAGENT_TYPE_CODE_REVIEW", "Check"), "verification")
        self.assertEqual(classify_agent("research", "Inspect"), "discovery")
        self.assertEqual(classify_agent("planner", "Plan waves"), "orchestration")
        self.assertEqual(classify_agent("builder", "Build UI"), "build")
        self.assertEqual(classify_agent("reviewer", "Cold review"), "verification")

    def test_audits_linked_metrics_and_dashboard_provider_analytics_4_axes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g_home = root / "gravity_home"
            m_home = g_home / "metrics"
            brain = root / "brain"
            ws = root / "AtelierUX"
            (ws / "audits" / "agents").mkdir(parents=True)
            (ws / "audits" / "code").mkdir(parents=True)

            # Session 1 (4 phases, swarm/workflow + subagents, 2 models)
            cid1 = "aa040001-1111-4111-8111-111111111111"
            sub_res = "sub-r001-1111-4111-8111-111111111111"
            sub_rev = "sub-v001-1111-4111-8111-111111111111"
            _write_transcript(
                brain / cid1,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity-architect spec dashboard"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:10Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 1_000, "output_tokens": 200, "tool_calls": [{"name": "invoke_subagent"}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:01:00Z", "model": "gemini-4-neon", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/core.py"}}]},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:02:00Z", "model": "gemini-4-neon", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python3 -m unittest discover -s tests -v"}}]},
                ],
            )
            _write_subagent(brain / cid1, sub_res, "research", "Explorer", spawn_step=1)
            _write_transcript(
                brain / sub_res,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 500, "output_tokens": 100, "tool_calls": [{"name": "finish"}]}],
            )
            _write_subagent(brain / cid1, sub_rev, "code_review", "Code Reviewer", spawn_step=3)
            _write_transcript(
                brain / sub_rev,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 1_500, "output_tokens": 200, "tool_calls": [{"name": "finish"}]}],
            )
            archive_conversation(
                cid1, store_dir=m_home, history_path=m_home / "data" / "history.jsonl",
                brain_dir=brain, catalog_path=CATALOG_PATH, label="session-a1", project="AtelierUX",
            )

            # Session 2 (single mode, gemini-3.8-flash-tiered)
            cid2 = "bb040002-2222-4222-8222-222222222222"
            _write_transcript(
                brain / cid2,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T10:00:00Z", "content": "/gravity run quick fix"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 2_000, "output_tokens": 500, "tool_calls": []},
                ],
            )
            archive_conversation(
                cid2, store_dir=m_home, history_path=m_home / "data" / "history.jsonl",
                brain_dir=brain, catalog_path=CATALOG_PATH, label="session-b1", project="AtelierUX",
            )

            # Pending session (4 phases, active capture) to check milestones & window_mode preservation
            cid_pend = "cc040003-3333-4333-8333-333333333333"
            _write_transcript(
                brain / cid_pend,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T11:00:00Z", "content": "/gravity-architect spec pending"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T11:00:10Z", "model": "gemini-4-neon", "input_tokens": 1_500, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file", "arguments": {"Path": "plugin/x.py"}}]},
                ],
            )
            start_active_capture(cid_pend, start_step=0, label="pending-ux", project="AtelierUX", store_dir=m_home)

            # Audits A & B + forbidden audits/code/
            (ws / "audits" / "agents" / "2026-11-27-audit-a.md").write_text(
                f"# Audit A\n\nVerdict : PASS\n\nSession liée `{cid1}`.\n",
                encoding="utf-8",
            )
            (ws / "audits" / "agents" / "2026-11-26-audit-b.md").write_text(
                f"# Audit B\n\nVerdict : WARN\n\nSession liée `{cid2[:8]}`.\n",
                encoding="utf-8",
            )
            (ws / "audits" / "code" / "forbidden.md").write_text("# Code Audit\n\nVerdict : BLOQUANT\n", encoding="utf-8")

            records = load_history(m_home / "data" / "history.jsonl")
            audits, _ = list_audits(records=records, workspace_dirs=[ws], include_cwd=False)
            self.assertEqual([a["filename"] for a in audits], ["2026-11-27-audit-a.md", "2026-11-26-audit-b.md"])
            aud_a, aud_b = audits[0], audits[1]

            lm_a = aud_a["linked_metrics"]
            self.assertEqual(lm_a["sessions_count"], 1)
            self.assertEqual(lm_a["total_tokens"], 10_800)
            self.assertEqual(lm_a["useful_tokens"], 7_800)
            self.assertEqual(lm_a["avg_tokens_per_session"], 10_800.0)
            self.assertEqual(lm_a["avg_useful_tokens_per_session"], 7_800.0)
            self.assertIn("verification", lm_a["by_category"])
            self.assertEqual(lm_a["by_category"]["verification"]["total_tokens"], 3_700)
            self.assertEqual({m["model"] for m in lm_a["primary_models"]}, {"gemini-3.8-flash-tiered", "gemini-4-neon"})

            lm_b = aud_b["linked_metrics"]
            self.assertEqual(lm_b["sessions_count"], 1)
            self.assertEqual(lm_b["total_tokens"], 6_500)
            self.assertEqual(lm_b["useful_tokens"], 4_500)
            self.assertEqual([m["model"] for m in lm_b["primary_models"]], ["gemini-3.8-flash-tiered"])

            with self.assertRaises((ValueError, FileNotFoundError)):
                read_audit("AtelierUX:../code/forbidden.md", records=records, workspace_dirs=[ws], include_cwd=False)

            view = _invoke_provider(
                ["--view", "metrics", "--json"],
                stdin_obj={
                    "params": {"project": "AtelierUX", "limit": 20},
                    "context": {"metrics_home": str(m_home), "brain_dir": str(brain), "audits_dirs": [str(ws / "audits")]},
                },
            )
            self.assertLess(len(json.dumps(view, ensure_ascii=False).encode("utf-8")), 450_000)

            kpis = view["kpis"]
            # Total tokens: 10800 + 6500 = 17300 ; Useful: 7800 + 4500 = 12300
            self.assertEqual(kpis["total_tokens"], 17_300)
            self.assertEqual(kpis["useful_tokens"], 12_300)
            self.assertEqual(kpis["avg_tokens_per_session"], 8_650.0)
            self.assertEqual(kpis["avg_useful_tokens_per_session"], 6_150.0)

            pend_item = view["pending_sessions"][0]
            self.assertEqual(pend_item["conversation_id"], cid_pend)
            self.assertEqual(pend_item["window_mode"], "custom")
            self.assertIsInstance(pend_item["milestones"], dict)
            self.assertEqual(
                [p["id"] for p in pend_item["milestones"]["phases"]],
                ["conception", "planification", "production", "validation"],
            )

            analytics = view["analytics"]
            self.assertEqual(
                set(analytics.keys()),
                {"by_agent_type", "by_phase", "by_session_mode", "by_model"},
            )
            required_axis_fields = {
                "count", "total_tokens", "useful_tokens",
                "avg_tokens", "avg_useful_tokens", "cache_hit_rate_pct", "share_pct",
            }
            for axis_name, axis_map in analytics.items():
                self.assertTrue(axis_map, f"Axe vide : {axis_name}")
                for bucket in axis_map.values():
                    self.assertTrue(required_axis_fields.issubset(bucket.keys()), (axis_name, bucket))
                    if bucket["count"] > 0:
                        self.assertEqual(bucket["avg_tokens"], round(bucket["total_tokens"] / bucket["count"], 1))
                        self.assertEqual(bucket["avg_useful_tokens"], round(bucket["useful_tokens"] / bucket["count"], 1))

            self.assertIn("verification", analytics["by_agent_type"])
            self.assertEqual(analytics["by_agent_type"]["verification"]["total_tokens"], 3_700)
            self.assertIn("production", analytics["by_phase"])
            self.assertIn("subagents", analytics["by_session_mode"])
            self.assertIn("single", analytics["by_session_mode"])
            self.assertEqual(analytics["by_model"]["gemini-3.8-flash-tiered"]["count"], 2)
            self.assertEqual(analytics["by_model"]["gemini-3.8-flash-tiered"]["total_tokens"], 11_800)
            self.assertEqual(analytics["by_model"]["gemini-3.8-flash-tiered"]["avg_tokens"], 5_900.0)

            links_map = view["audit_links_by_session"]
            self.assertIn(cid1, links_map)
            self.assertIn(cid2, links_map)
            self.assertEqual(links_map[cid1][0]["title"], "Audit A")


if __name__ == "__main__":
    unittest.main()
