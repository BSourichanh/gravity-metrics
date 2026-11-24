#!/usr/bin/env python3
"""Tests de rétrocompatibilité lecture seule des anciens rapports archivés (#01-#04) et invariants v0.3.0+."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
CLI_SCRIPT = PLUGIN_ROOT / "scripts" / "cli.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from mcp.widget_html import render_mcp_widget_html  # noqa: E402
from metrics import (  # noqa: E402
    SCHEMA_VERSION,
    classify_agent,
    classify_metrics_prompt,
    collect_run_metrics,
    discover_agent_tree,
    is_bridge_child_conversation,
    is_gravity_metrics_capture_prompt,
    is_gravity_session_prompt,
    list_gravity_conversations,
    list_pending_sessions,
    load_history,
    load_session_for_inspect,
    record_session_metrics,
    render_chat_summary_markdown,
    render_inspect_text,
    render_metrics_markdown,
    render_pending_table,
    render_session_detail_markdown,
    save_history,
    slugify_label,
    suggest_auto_label,
    to_session_record,
)


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return tpath


def _write_subagent(
    parent_dir: Path, child_id: str, type_name: str, role: str, spawn_step: int = 1
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
            }
        ),
        encoding="utf-8",
    )


class TestHistoricalBridgeReportsROCompat(unittest.TestCase):
    def test_historical_reports_01_04_ro_rendering_and_badge_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"

            root_id = "f80dab92-0000-0000-0000-000000000001"
            sub_root = "sub-root-1"
            arch_id = "648e5b78-0000-0000-0000-000000000002"
            wf_id = "1ed41fce-0000-0000-0000-000000000003"

            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:10:00Z", "content": "/gravity-architect spec lot"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T18:10:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 100_000,
                        "cache_read_tokens": 900_000,
                        "output_tokens": 5_000,
                        "tool_calls": [{"name": "invoke_subagent"}],
                    },
                ],
            )
            _write_subagent(brain / root_id, sub_root, "research", "Studio Explorer", spawn_step=1)
            _write_transcript(
                brain / sub_root,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:10:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 90_000, "output_tokens": 500, "tool_calls": []}],
            )

            # Classification historique Bridge préservée
            self.assertEqual(classify_agent("bridge_handoff", "bridge:gravity-architect"), "build")
            self.assertEqual(classify_agent("bridge_question", "bridge:gravity-workflow"), "discovery")
            self.assertEqual(classify_agent("bridge_info", "bridge:gravity-bridge"), "orchestration")
            self.assertEqual(classify_agent("bridge", "bridge:x"), "orchestration")

            # Collecte native (zéro lecture bridge.db)
            m = collect_run_metrics(root_id, brain, CATALOG_PATH, gravity_window=True, project="gravity-studio")
            self.assertEqual(m["summary"]["total_agents"], 2)
            self.assertEqual(m["bridge_pending_jobs"], [])
            tree = discover_agent_tree(root_id, brain)
            self.assertEqual({n["conversation_id"] for n in tree}, {root_id, sub_root})

            # Rapport archivé historique (#01-#04) avec bridge metadata, workspace shares & agents Bridge
            archived_compat = dict(m)
            archived_compat["label"] = "audit-04-compat"
            archived_compat["bridge"] = {
                "children_count": 2,
                "workspaces": ["gravity-architect", "gravity-workflow"],
                "pending_jobs": [],
            }
            ws_base = dict(m["by_workspace"]["gravity-studio"])
            archived_compat["by_workspace"] = {
                "gravity-studio": {**ws_base, "cost_share_pct": 40.0},
                "gravity-architect": {**ws_base, "cost_share_pct": 35.0},
                "gravity-workflow": {**ws_base, "cost_share_pct": 25.0},
            }
            archived_compat["agents"] = list(m["agents"]) + [
                {
                    **m["agents"][0],
                    "conversation_id": arch_id,
                    "parent_id": root_id,
                    "depth": 1,
                    "type_name": "bridge_handoff",
                    "role": "bridge:gravity-architect",
                    "category": "build",
                    "source": "bridge",
                    "bridge_thread_id": "thr-arch-1",
                    "bridge_workspace": "gravity-architect",
                    "workspace": "gravity-architect",
                },
                {
                    **m["agents"][0],
                    "conversation_id": wf_id,
                    "parent_id": root_id,
                    "depth": 1,
                    "type_name": "bridge_question",
                    "role": "bridge:gravity-workflow",
                    "category": "discovery",
                    "source": "bridge",
                    "bridge_thread_id": "thr-wf-1",
                    "bridge_workspace": "gravity-workflow",
                    "workspace": "gravity-workflow",
                },
            ]

            rec = to_session_record(archived_compat, label="audit-04-compat")
            self.assertEqual(rec["bridge_children_count"], 2)
            self.assertEqual(rec["bridge_workspaces"], ["gravity-architect", "gravity-workflow"])

            # Persistance RO dans history.jsonl + reports/<slug>.json et rechargement hors-ligne sans brain
            reports_dir = store / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / f"{rec['detail_slug']}.json").write_text(
                json.dumps(archived_compat, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            save_history(hist_path, [rec])

            loaded_det, slug = load_session_for_inspect(
                "audit-04-compat",
                store_dir=store,
                history_path=hist_path,
                brain_dir=root / "empty-brain",
                catalog_path=CATALOG_PATH,
            )
            self.assertEqual(slug, rec["detail_slug"])
            self.assertEqual(loaded_det["bridge"]["children_count"], 2)

            md_global = render_metrics_markdown(load_history(hist_path))
            self.assertIn("bridge:2", md_global)
            self.assertIn("Ventilation par Workspace", md_global)

            det_md = render_session_detail_markdown(loaded_det)
            self.assertIn("Répartition par Workspace", det_md)
            self.assertIn("Arbre Bridge Inter-Workspaces", det_md)
            self.assertIn("thr-arch-1", det_md)

            insp = render_inspect_text(loaded_det)
            self.assertIn("--- Par workspace ---", insp)
            self.assertIn("--- Arbre Bridge ---", insp)

            chat_md = render_chat_summary_markdown(loaded_det, rec, store)
            self.assertIn("audit-04-compat", chat_md)
            self.assertIn("Workspaces (Bridge)", chat_md)

            # UI MCP App : badge RO historique bridge:N et Arbre Bridge conservés, modale/alerte live retirées
            html_ui = render_mcp_widget_html()
            self.assertIn("rec.bridge_children_count", html_ui)
            self.assertIn("bridge:", html_ui)
            self.assertIn("Arbre Bridge Inter-Workspaces", html_ui)
            self.assertNotIn("confirmForceArchiveCid", html_ui)
            self.assertNotIn("has_pending_bridge_jobs", html_ui)


class TestEntrypointsExclusionSlugAndLegacySchema(unittest.TestCase):
    def test_gravity_entrypoints_and_slug_bounds(self) -> None:
        for cmd in (
            "/gravity init",
            "/gravity-workflow auto fix",
            "/gravity-architect spec auth",
            "/gravity-bridge status",
            "/gravity-architect:gravity-architect handoff",
        ):
            self.assertTrue(is_gravity_session_prompt(cmd), cmd)
        self.assertFalse(is_gravity_session_prompt("/gravity-metrics"))

        self.assertEqual(
            suggest_auto_label("MyApp", "/gravity-architect spec nouveau module", "2026-11-22T10:00:00Z", []),
            "myapp-nouveau-module",
        )
        self.assertEqual(
            suggest_auto_label("MyApp", "/gravity-bridge send audit bridge", "2026-11-22T10:00:00Z", []),
            "myapp-audit-bridge",
        )

        long_slug = slugify_label("prefix-" + ("word-" * 30))
        self.assertLessEqual(len(long_slug), 64)
        self.assertRegex(long_slug, r"-[0-9a-f]{8}$")
        self.assertEqual(long_slug, slugify_label("prefix-" + ("word-" * 30)))

        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics /grill-me " + "x" * 200))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics pending"))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics archive f80dab92"))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics " + "x" * 65))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics a b c d e f g"))
        self.assertTrue(is_gravity_metrics_capture_prompt("/gravity-metrics my-run"))
        self.assertTrue(is_gravity_metrics_capture_prompt('/gravity-metrics -l "mon label tres explicite"'))
        self.assertEqual(classify_metrics_prompt("/gravity-metrics archive f80dab92 mon-lbl --force")["action"], "archive")

    def test_legacy_transcript_exclusion_schema_200_compat_and_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"

            cid_root = "aaaa0000-1111-2222-3333-444455556666"
            cid_br_prompt = "bbbb0000-1111-2222-3333-444455556666"
            cid_legacy = "dddd0000-1111-2222-3333-444455556666"

            _write_transcript(
                brain / cid_root,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T15:00:00Z", "content": "/gravity-workflow fix"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T15:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 2000, "output_tokens": 100, "tool_calls": [{"name": "write_file"}]},
                ],
            )
            _write_transcript(
                brain / cid_br_prompt,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T15:01:00Z", "content": "<USER_REQUEST>\n[GRAVITY-BRIDGE | thread=thr-1]\n</USER_REQUEST>"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T15:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )

            self.assertTrue(is_bridge_child_conversation(cid_br_prompt, brain_dir=brain))
            self.assertFalse(is_bridge_child_conversation(cid_root, brain_dir=brain))

            convs_default = {c["conversation_id"] for c in list_gravity_conversations(brain, include_all=False)}
            self.assertEqual(convs_default, {cid_root})
            convs_all = {c["conversation_id"] for c in list_gravity_conversations(brain, include_all=True)}
            self.assertEqual(convs_all, {cid_root, cid_br_prompt})

            pending = list_pending_sessions(brain, hist_path)
            self.assertEqual([p["conversation_id"] for p in pending], [cid_root])
            self.assertIn("aaaa0000", render_pending_table(pending))

            # Rétrocompatibilité schema 2.0.0 sans status -> lu avec status='archived' et badge bridge:1
            legacy_rec = {
                "schema_version": "2.0.0",
                "label": "legacy-v200",
                "conversation_id": cid_legacy,
                "cost_usd": 1.0,
                "bridge_children_count": 1,
                "bridge_workspaces": ["gravity-metrics"],
                "workspace_share_pct": {"gravity-studio": 60.0, "gravity-metrics": 40.0},
            }
            save_history(hist_path, [legacy_rec])
            loaded_legacy = load_history(hist_path)
            self.assertEqual(loaded_legacy[0]["status"], "archived")
            self.assertEqual(loaded_legacy[0]["bridge_children_count"], 1)
            self.assertIn("bridge:1", render_metrics_markdown(loaded_legacy))

            m = collect_run_metrics(cid_root, brain, CATALOG_PATH, gravity_window=True, project="gravity-studio")
            saved, _ = record_session_metrics(m, store_dir=store, history_path=hist_path, label="arch-1", force=False)
            self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
            self.assertEqual(saved["status"], "archived")
            self.assertTrue(saved["archived_at"])
            self.assertEqual(saved["project"], "gravity-studio")

            saved_again, _ = record_session_metrics(m, store_dir=store, history_path=hist_path, label="autre-label", force=False)
            self.assertTrue(saved_again.get("already_archived"))
            self.assertEqual(saved_again["label"], "arch-1")

            # CLI pending & archive sans bridge.db
            cid_cli = "eeee0000-1111-2222-3333-444455556666"
            _write_transcript(
                brain / cid_cli,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T16:00:00Z", "content": "/gravity-architect init"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T16:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "write_file"}]},
                ],
            )
            p_pend = subprocess.run(
                [sys.executable, "-B", str(CLI_SCRIPT), "pending", "--brain-dir", str(brain), "--history", str(hist_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(p_pend.returncode, 0, p_pend.stderr)
            self.assertIn("eeee0000", p_pend.stdout)

            p_arch = subprocess.run(
                [sys.executable, "-B", str(CLI_SCRIPT), "archive", "eeee0000", "-l", "cli-arch", "--brain-dir", str(brain), "--history", str(hist_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(p_arch.returncode, 0, p_arch.stderr)
            self.assertIn("cli-arch", p_arch.stdout)


if __name__ == "__main__":
    unittest.main()
