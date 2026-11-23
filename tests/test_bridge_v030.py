#!/usr/bin/env python3
"""Tests unitaires et E2E pour Gravity Metrics v0.3.0 (Bridge unifié, archivage immuable, pending)."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
CLI_SCRIPT = PLUGIN_ROOT / "scripts" / "cli.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    PendingBridgeJobsError,
    SCHEMA_VERSION,
    classify_agent,
    classify_metrics_prompt,
    collect_run_metrics,
    discover_agent_tree,
    discover_bridge_links,
    is_bridge_child_conversation,
    is_gravity_metrics_capture_prompt,
    is_gravity_session_prompt,
    list_gravity_conversations,
    list_pending_sessions,
    load_history,
    record_session_metrics,
    render_inspect_text,
    render_metrics_markdown,
    render_pending_table,
    render_session_detail_markdown,
    resolve_bridge_db_path,
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


def _create_bridge_db(db_path: Path, *, v2_schema: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    caller_col = ", caller_conversation_id TEXT" if v2_schema else ""
    con.execute(
        f"CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
        f"msg_type TEXT, status TEXT, target_conversation_id TEXT{caller_col}, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
        "action TEXT, target_conversation_id TEXT, status TEXT, created_at TEXT)"
    )
    return con


class TestBridgeTreeAndWorkspaces(unittest.TestCase):
    def test_bridge_db_resolution_precedence_and_missing_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            grav_home = root / "grav-home"
            bridge_home = root / "bridge-home"
            explicit = root / "custom-bridge.db"

            with mock.patch.object(Path, "home", return_value=fake_home):
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(
                        resolve_bridge_db_path(),
                        fake_home / ".gemini" / "gravity" / "bridge" / "bridge.db",
                    )
                with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=True):
                    self.assertEqual(
                        resolve_bridge_db_path(),
                        grav_home / "bridge" / "bridge.db",
                    )
                with mock.patch.dict(
                    os.environ,
                    {
                        "GRAVITY_HOME": str(grav_home),
                        "GRAVITY_BRIDGE_HOME": str(bridge_home),
                    },
                    clear=True,
                ):
                    self.assertEqual(resolve_bridge_db_path(), bridge_home / "bridge.db")
                    self.assertEqual(resolve_bridge_db_path(explicit), explicit)

            # Base absente -> tolérance stricte (liste vide)
            missing_tx = root / "missing" / "transcript.jsonl"
            self.assertEqual(
                discover_bridge_links("root-1", missing_tx, bridge_db=root / "absent.db"),
                [],
            )

    def test_dual_discovery_v010_and_v020_dedup_and_unified_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_path = root / "bridge.db"

            root_id = "f80dab92-0000-0000-0000-000000000001"
            sub_root = "sub-root-1"
            arch_id = "648e5b78-0000-0000-0000-000000000002"
            wf_id = "1ed41fce-0000-0000-0000-000000000003"
            wf_sub = "wf-sub-builder"
            br_id = "6210a81f-0000-0000-0000-000000000004"

            # Root: chat avant /gravity-architect (step 0..1), puis /gravity-architect (step 2..6)
            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:00:00Z", "content": "Chat prealable"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T18:00:10Z",
                        "model": "gemini-4-neon",
                        "input_tokens": 999_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 1_000,
                        "tool_calls": [
                            {
                                "name": "call_mcp_tool",
                                "args": {
                                    "ServerName": '"gravity-bridge_bridge"',
                                    "ToolName": '"dispatch_thread"',
                                    "Arguments": json.dumps({"thread_id": "thr-pre-ignored", "approved": True}),
                                },
                            }
                        ],
                    },
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:10:00Z", "content": "/gravity-architect spec lot"},
                    {
                        "step_index": 3,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T18:10:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 100_000,
                        "cache_read_tokens": 900_000,
                        "output_tokens": 5_000,
                        "tool_calls": [
                            {"name": "invoke_subagent"},
                            {
                                "name": "call_mcp_tool",
                                "args": {
                                    "ServerName": '"gravity-bridge_bridge"',
                                    "ToolName": '"send_message"',
                                    "Arguments": json.dumps(
                                        {
                                            "from_workspace": "gravity-studio",
                                            "to_workspace": "gravity-architect",
                                            "msg_type": "handoff",
                                            "dispatch": True,
                                        }
                                    ),
                                },
                            },
                        ],
                    },
                    {
                        "step_index": 4,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": 'Created At: 2026-11-22T18:10:12Z\n{\n  "thread_id": "thr-arch-1",\n  "to_workspace": "gravity-architect",\n  "msg_type": "handoff"\n}',
                    },
                    {
                        "step_index": 5,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T18:20:00Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 50_000,
                        "cache_read_tokens": 450_000,
                        "output_tokens": 2_000,
                        "tool_calls": [
                            {
                                "name": "call_mcp_tool",
                                "args": {
                                    "ServerName": '"gravity-bridge_bridge"',
                                    "ToolName": '"send_message"',
                                    "Arguments": json.dumps(
                                        {
                                            "from_workspace": "gravity-studio",
                                            "to_workspace": "gravity-workflow",
                                            "msg_type": "question",
                                            "dispatch": True,
                                        }
                                    ),
                                },
                            }
                        ],
                    },
                    {
                        "step_index": 6,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": json.dumps({"thread_id": "thr-wf-1", "to_workspace": "gravity-workflow", "msg_type": "question"}),
                    },
                ],
            )
            _write_subagent(brain / root_id, sub_root, "research", "Studio Explorer", spawn_step=3)
            _write_transcript(
                brain / sub_root,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:10:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 90_000, "output_tokens": 500, "tool_calls": []}],
            )

            # Pre-window bridge child (doit être exclu par gravity_window=True)
            _write_transcript(
                brain / "pre-ignored-conv",
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:00:15Z", "model": "gemini-4-neon", "input_tokens": 500_000, "cache_read_tokens": 0, "output_tokens": 1_000, "tool_calls": []}],
            )

            # 3 enfants Bridge (arch, wf -> wf_sub en depth 2, br découvert via caller_conversation_id v0.2.0+)
            _write_transcript(
                brain / arch_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:11:00Z", "content": "[GRAVITY-BRIDGE | thread=thr-arch-1]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:11:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 180_000, "output_tokens": 1_000, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / wf_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:21:00Z", "content": "[GRAVITY-BRIDGE | thread=thr-wf-1]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:21:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 30_000, "cache_read_tokens": 270_000, "output_tokens": 1_500, "tool_calls": [{"name": "invoke_subagent"}]},
                ],
            )
            _write_subagent(brain / wf_id, wf_sub, "builder", "Workflow Builder", spawn_step=1)
            _write_transcript(
                brain / wf_sub,
                [{"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:21:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 135_000, "output_tokens": 800, "tool_calls": []}],
            )
            _write_transcript(
                brain / br_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T18:30:00Z", "content": "[GRAVITY-BRIDGE | thread=thr-br-1]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T18:30:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 25_000, "cache_read_tokens": 225_000, "output_tokens": 1_200, "tool_calls": []},
                ],
            )

            # Cas 1 : bridge.db v0.2.0+ (avec caller_conversation_id -> thr-br-1 + dédup thr-arch-1)
            con = _create_bridge_db(db_path, v2_schema=True)
            rows_threads = [
                ("thr-pre-ignored", "gravity-studio", "gravity-architect", "handoff", "done", "pre-ignored-conv", root_id, "2026-11-22T18:00:12Z"),
                ("thr-arch-1", "gravity-studio", "gravity-architect", "handoff", "done", arch_id, root_id, "2026-11-22T18:10:12Z"),
                ("thr-wf-1", "gravity-studio", "gravity-workflow", "question", "done", wf_id, root_id, "2026-11-22T18:20:02Z"),
                ("thr-br-1", "gravity-studio", "gravity-bridge", "info", "done", br_id, root_id, "2026-11-22T18:30:00Z"),
            ]
            con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_threads)
            for jid, tid, ws_t, cid_t, st_ts in (
                ("j0", "thr-pre-ignored", "gravity-architect", "pre-ignored-conv", "2026-11-22T18:00:12Z"),
                ("j1", "thr-arch-1", "gravity-architect", arch_id, "2026-11-22T18:10:12Z"),
                ("j2", "thr-wf-1", "gravity-workflow", wf_id, "2026-11-22T18:20:02Z"),
                ("j3", "thr-br-1", "gravity-bridge", br_id, "2026-11-22T18:30:00Z"),
            ):
                con.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (jid, tid, ws_t, "new_conversation", cid_t, "completed", st_ts),
                )
            con.commit()
            con.close()

            # Vérifier la classification TYPE_TO_CATEGORY Bridge
            self.assertEqual(classify_agent("bridge_handoff", "bridge:gravity-architect"), "build")
            self.assertEqual(classify_agent("bridge_question", "bridge:gravity-workflow"), "discovery")
            self.assertEqual(classify_agent("bridge_info", "bridge:gravity-bridge"), "orchestration")
            self.assertEqual(classify_agent("bridge", "bridge:x"), "orchestration")

            m = collect_run_metrics(
                root_id,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                project="gravity-studio",
                bridge_db_path=db_path,
            )
            s = m["summary"]
            # root + sub_root + arch_id + wf_id + wf_sub + br_id = 6 agents, depth 2
            self.assertEqual(s["total_agents"], 6)
            self.assertEqual(s["max_tree_depth"], 2)
            self.assertEqual(s["execution_mode"], "bridge")
            self.assertIn("bridge", s["features_used"])
            self.assertIn("gravity", s["features_used"])
            self.assertNotIn("gemini-4-neon", m["by_model"])

            # by_workspace : gravity-studio (root+sub_root=2), gravity-workflow (wf_id+wf_sub=2), gravity-architect (1), gravity-bridge (1)
            by_ws = m["by_workspace"]
            self.assertEqual(
                set(by_ws.keys()),
                {"gravity-studio", "gravity-architect", "gravity-workflow", "gravity-bridge"},
            )
            self.assertEqual(by_ws["gravity-studio"]["agents"], 2)
            self.assertEqual(by_ws["gravity-workflow"]["agents"], 2)
            self.assertAlmostEqual(sum(v["cost_share_pct"] for v in by_ws.values()), 100.0, delta=0.1)

            # by_phase : chaque bridge:<workspace> agrège son sous-arbre
            self.assertIn("bridge:gravity-workflow", m["by_phase"])
            self.assertEqual(m["by_phase"]["bridge:gravity-workflow"]["agents"], 2)
            self.assertIn("bridge:gravity-architect", m["by_phase"])
            self.assertIn("bridge:gravity-bridge", m["by_phase"])

            # Rapports & inspect incluent workspace & arbre Bridge
            det_md = render_session_detail_markdown(m)
            self.assertIn("Répartition par Workspace", det_md)
            self.assertIn("Arbre Bridge Inter-Workspaces", det_md)
            self.assertIn("thr-arch-1", det_md)
            insp = render_inspect_text(m)
            self.assertIn("--- Par workspace ---", insp)
            self.assertIn("--- Arbre Bridge ---", insp)

            # Cas 2 : bridge.db v0.1.0 sans caller_conversation_id -> fallback transcript parent fonctionne
            db_v1 = root / "bridge-v1.db"
            con1 = _create_bridge_db(db_v1, v2_schema=False)
            con1.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("thr-arch-1", "gravity-studio", "gravity-architect", "handoff", "done", arch_id, "2026-11-22T18:10:12Z"),
            )
            con1.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("thr-wf-1", "gravity-studio", "gravity-workflow", "question", "done", wf_id, "2026-11-22T18:20:02Z"),
            )
            con1.commit()
            con1.close()

            tree_v1 = discover_agent_tree(root_id, brain, bridge_db_path=db_v1)
            tree_ids_v1 = {n["conversation_id"] for n in tree_v1}
            self.assertEqual(tree_ids_v1, {root_id, sub_root, arch_id, wf_id, wf_sub})


class TestEntrypointsExclusionSlugAndLifecycle(unittest.TestCase):
    def test_four_gravity_entrypoints_and_slug_bounds(self) -> None:
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

        # Slug borné <= 64 avec suffixe déterministe
        long_slug = slugify_label("prefix-" + ("word-" * 30))
        self.assertLessEqual(len(long_slug), 64)
        self.assertRegex(long_slug, r"-[0-9a-f]{8}$")
        self.assertEqual(long_slug, slugify_label("prefix-" + ("word-" * 30)))

        # Classification prompt /gravity-metrics
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics /grill-me " + "x" * 200))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics pending"))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics archive f80dab92"))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics " + "x" * 65))
        self.assertFalse(is_gravity_metrics_capture_prompt("/gravity-metrics a b c d e f g"))
        self.assertTrue(is_gravity_metrics_capture_prompt("/gravity-metrics my-run"))
        self.assertTrue(is_gravity_metrics_capture_prompt('/gravity-metrics -l "mon label tres explicite"'))
        self.assertEqual(classify_metrics_prompt("/gravity-metrics archive f80dab92 mon-lbl --force")["action"], "archive")

    def test_bridge_child_exclusion_pending_and_schema_210_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist_path = store / "data" / "history.jsonl"
            db_path = root / "bridge.db"

            cid_root = "aaaa0000-1111-2222-3333-444455556666"
            cid_br_prompt = "bbbb0000-1111-2222-3333-444455556666"
            cid_br_db = "cccc0000-1111-2222-3333-444455556666"
            cid_legacy = "dddd0000-1111-2222-3333-444455556666"

            _write_transcript(
                brain / cid_root,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T15:00:00Z", "content": "/gravity-bridge send"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T15:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 2000, "output_tokens": 100, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / cid_br_prompt,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T15:01:00Z", "content": "<USER_REQUEST>\n[GRAVITY-BRIDGE | thread=thr-1]\n</USER_REQUEST>"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T15:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / cid_br_db,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T15:02:00Z", "content": "/gravity tache cible"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T15:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )

            con = _create_bridge_db(db_path, v2_schema=True)
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-db-only", "gravity-studio", "gravity-metrics", "handoff", "open", cid_br_db, cid_root, "2026-11-22T15:02:00Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("job-active", "thr-db-only", "gravity-metrics", "new_conversation", cid_br_db, "claimed", "2026-11-22T15:02:00Z"),
            )
            con.commit()
            con.close()

            self.assertTrue(is_bridge_child_conversation(cid_br_prompt, brain_dir=brain, bridge_db_path=db_path))
            self.assertTrue(is_bridge_child_conversation(cid_br_db, brain_dir=brain, bridge_db_path=db_path))
            self.assertFalse(is_bridge_child_conversation(cid_root, brain_dir=brain, bridge_db_path=db_path))

            # Par défaut, enfants Bridge exclus de list_gravity_conversations et list_pending_sessions
            convs_default = {c["conversation_id"] for c in list_gravity_conversations(brain, include_all=False, bridge_db_path=db_path)}
            self.assertEqual(convs_default, {cid_root})
            convs_all = {c["conversation_id"] for c in list_gravity_conversations(brain, include_all=True, bridge_db_path=db_path)}
            self.assertEqual(convs_all, {cid_root, cid_br_prompt, cid_br_db})

            pending = list_pending_sessions(brain, hist_path, bridge_db_path=db_path)
            self.assertEqual([p["conversation_id"] for p in pending], [cid_root])
            self.assertTrue(pending[0]["has_pending_bridge_jobs"])
            self.assertIn("aaaa0000", render_pending_table(pending))

            # Rétrocompatibilité schema 2.0.0 sans status -> lu avec status='archived'
            legacy_rec = {
                "schema_version": "2.0.0",
                "label": "legacy-v200",
                "conversation_id": cid_legacy,
                "cost_usd": 1.0,
            }
            save_history(hist_path, [legacy_rec])
            loaded_legacy = load_history(hist_path)
            self.assertEqual(loaded_legacy[0]["status"], "archived")

            # Blocage job Bridge actif si force=False
            m = collect_run_metrics(cid_root, brain, CATALOG_PATH, gravity_window=True, bridge_db_path=db_path)
            with self.assertRaises(PendingBridgeJobsError) as ctx:
                record_session_metrics(m, store_dir=store, history_path=hist_path, label="arch-1", force=False)
            self.assertIn("--force", str(ctx.exception))

            # --force autorise l'archivage
            saved, _ = record_session_metrics(m, store_dir=store, history_path=hist_path, label="arch-1", force=True)
            self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
            self.assertEqual(saved["status"], "archived")
            self.assertTrue(saved["archived_at"])
            self.assertEqual(saved["bridge_children_count"], 1)
            self.assertEqual(saved["bridge_workspaces"], ["gravity-metrics"])
            self.assertEqual(saved["project"], "gravity-studio")
            self.assertIn("gravity-studio", saved["workspace_share_pct"])
            self.assertIn("gravity-metrics", saved["workspace_share_pct"])

            # Immuabilité : ré-archivage sans --force retourne already_archived=True sans écraser
            saved_again, _ = record_session_metrics(m, store_dir=store, history_path=hist_path, label="autre-label", force=False)
            self.assertTrue(saved_again.get("already_archived"))
            self.assertEqual(saved_again["label"], "arch-1")
            md_global = render_metrics_markdown(load_history(hist_path))
            self.assertIn("archivé", md_global)
            self.assertIn("Ventilation par Workspace", md_global)

            # CLI pending & archive sur une nouvelle session
            cid_cli = "eeee0000-1111-2222-3333-444455556666"
            _write_transcript(
                brain / cid_cli,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T16:00:00Z", "content": "/gravity-architect init"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T16:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            p_pend = subprocess.run(
                [sys.executable, "-B", str(CLI_SCRIPT), "pending", "--brain-dir", str(brain), "--history", str(hist_path), "--bridge-db", str(db_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(p_pend.returncode, 0, p_pend.stderr)
            self.assertIn("eeee0000", p_pend.stdout)

            p_arch = subprocess.run(
                [sys.executable, "-B", str(CLI_SCRIPT), "archive", "eeee0000", "-l", "cli-arch", "--brain-dir", str(brain), "--history", str(hist_path), "--bridge-db", str(db_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(p_arch.returncode, 0, p_arch.stderr)
            self.assertIn("cli-arch", p_arch.stdout)


if __name__ == "__main__":
    unittest.main()
