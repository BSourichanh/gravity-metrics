#!/usr/bin/env python3
"""Tests unitaires et d'intégration v0.4.2 : run_workflow, callback Bridge v0.2.0 et filtrage strict."""

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    collect_run_metrics,
    discover_agent_tree,
    discover_bridge_links,
    discover_workflow_children,
    list_gravity_conversations,
    list_pending_sessions,
    load_bridge_caller_conversation_ids,
    load_bridge_target_conversation_ids,
    load_known_child_conversation_ids,
    render_inspect_text,
    render_session_detail_markdown,
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
    spawn_step: int = 1,
    workspace_uris: list = None,
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
                "workspaceUris": workspace_uris or [],
            }
        ),
        encoding="utf-8",
    )


def _planner_step(
    step_idx: int,
    ts: str,
    in_tok: int = 10_000,
    out_tok: int = 500,
    tool_calls: list = None,
) -> dict:
    return {
        "step_index": step_idx,
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": ts,
        "model": "gemini-3.8-flash-tiered",
        "input_tokens": in_tok,
        "cache_read_tokens": 0,
        "output_tokens": out_tok,
        "tool_calls": list(tool_calls) if tool_calls is not None else [],
    }


class TestWorkflowAndPendingV042(unittest.TestCase):
    def test_mixed_tree_root_bridge_run_workflow_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_path = root / "bridge.db"

            root_id = "11111111-1111-4111-8111-111111111111"
            bridge_cid = "22222222-2222-4222-8222-222222222222"
            wf_p1 = "aaaaaaaa-0001-4000-8000-000000000001"
            wf_b1 = "aaaaaaaa-0002-4000-8000-000000000002"
            wf_r1 = "aaaaaaaa-0003-4000-8000-000000000003"
            wf_p2 = "aaaaaaaa-0004-4000-8000-000000000004"
            wf_b2 = "aaaaaaaa-0005-4000-8000-000000000005"
            wf_r2 = "aaaaaaaa-0006-4000-8000-000000000006"

            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T10:00:00Z",
                        "content": "/gravity-workflow evo fix lot",
                    },
                    _planner_step(1, "2026-11-22T10:00:10Z", 20_000, 800),
                ],
            )

            # Enfant Bridge -> 6 enfants run_workflow via tasks/, messages/, transcript.jsonl
            _write_transcript(
                brain / bridge_cid,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T10:01:00Z",
                        "content": "[GRAVITY-BRIDGE | thread=thr-wf-mix]",
                    },
                    _planner_step(1, "2026-11-22T10:01:10Z", 15_000, 600),
                    _planner_step(
                        51,
                        "2026-11-22T10:01:20Z",
                        tool_calls=[{"name": "run_workflow"}],
                    ),
                    {
                        "step_index": 52,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": (
                            f"- [agent:builder] Builder Duplicate Should Keep 49: DONE ({wf_b1})\n"
                            f"- [agent:reviewer] Reviewer Sync Transcript Long Role: DONE ({wf_r2})\n"
                        ),
                    },
                    {
                        "step_index": 53,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": f"- [agent:planner] Planner Sync System: DONE ({wf_p2})\n",
                    },
                ],
            )

            tasks_dir = brain / bridge_cid / ".system_generated" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            # Fichier non-numéroté avec step_index=None, puis task-49.log avec step_index=49
            (tasks_dir / "task-notes.log").write_text(
                f"- [agent:planner] Planner Main Phase One: DONE ({wf_p1})\n",
                encoding="utf-8",
            )
            (tasks_dir / "task-49.log").write_text(
                "\n".join(
                    [
                        f"- [agent:planner] Planner Main Phase One: DONE ({wf_p1})",
                        f"- [agent:builder] Builder Core Implementation: DONE ({wf_b1})",
                        # Référence cyclique vers parent (casse différente) -> ignorée par discover_workflow_children
                        f"- [agent:root] Parent Self Cycle: DONE ({bridge_cid.upper()})",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            msgs_dir = brain / bridge_cid / ".system_generated" / "messages"
            msgs_dir.mkdir(parents=True, exist_ok=True)
            # read.json doit être strictement ignoré
            (msgs_dir / "read.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "run_workflow"}, "stepIndex": 10}},
                        "content": "- [agent:builder] Ignored Read Json: DONE (bbbbbbbb-0000-4000-8000-000000000000)\n",
                    }
                ),
                encoding="utf-8",
            )
            (msgs_dir / "msg-50.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "run_workflow"}, "stepIndex": 50}},
                        "content": (
                            f"- [agent:reviewer] Reviewer Phase One Audit: DONE ({wf_r1})\n"
                            f"- [agent:planner] Planner Dup Step 50 Ignored: DONE ({wf_p1})\n"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            (msgs_dir / "msg-51.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "run_workflow"}, "stepIndex": 51}},
                        "content": f"- [agent:builder] Builder Phase Two Worker: DONE ({wf_b2})\n",
                    }
                ),
                encoding="utf-8",
            )
            ignored_cmd_cid = "cccccccc-0000-4000-8000-000000000000"
            (msgs_dir / "msg-ignored-cmd.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "run_command"}, "stepIndex": 51}},
                        "content": f"- [agent:builder] Ignored Command Output: DONE ({ignored_cmd_cid})\n",
                    }
                ),
                encoding="utf-8",
            )

            for idx, (wcid, in_tok) in enumerate(
                (
                    (wf_p1, 8_000),
                    (wf_b1, 30_000),
                    (wf_r1, 12_000),
                    (wf_p2, 7_000),
                    (wf_b2, 25_000),
                    (wf_r2, 11_000),
                ),
                start=1,
            ):
                _write_transcript(
                    brain / wcid,
                    [
                        _planner_step(0, f"2026-11-22T10:0{idx}:00Z", in_tok, 400),
                        # Référence cyclique inverse depuis un enfant workflow vers root & parent
                        {
                            "step_index": 1,
                            "type": "GENERIC",
                            "status": "DONE",
                            "content": (
                                f"- [agent:root] Back To Root: DONE ({root_id})\n"
                                f"- [agent:root] Back To Parent: DONE ({bridge_cid})\n"
                            ),
                        },
                    ],
                )

            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
                "msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
            )
            con.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
                "action TEXT, direction TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, status TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-wf-mix", "gravity-studio", "gravity-metrics", "handoff", "done", bridge_cid, root_id, "2026-11-22T10:00:30Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-out", "thr-wf-mix", "gravity-metrics", "new_conversation", "outbound", bridge_cid, root_id, "completed", "2026-11-22T10:00:30Z"),
            )
            con.commit()
            con.close()

            # Vérifier discover_workflow_children directement (déduplication & premier step valide)
            wf_children = discover_workflow_children(bridge_cid, brain)
            self.assertEqual(
                [w["conversation_id"] for w in wf_children],
                [wf_p1, wf_b1, wf_r1, wf_b2, wf_r2, wf_p2],
            )
            by_wcid = {w["conversation_id"]: w for w in wf_children}
            self.assertEqual(by_wcid[wf_p1]["spawn_step_index"], 49)
            self.assertEqual(by_wcid[wf_b1]["spawn_step_index"], 49)
            self.assertEqual(by_wcid[wf_r1]["spawn_step_index"], 50)
            self.assertEqual(by_wcid[wf_b2]["spawn_step_index"], 51)
            self.assertEqual(by_wcid[wf_r2]["spawn_step_index"], 52)
            self.assertEqual(by_wcid[wf_p2]["spawn_step_index"], 53)
            self.assertNotIn("bbbbbbbb-0000-4000-8000-000000000000", by_wcid)
            self.assertNotIn(ignored_cmd_cid, by_wcid)

            metrics = collect_run_metrics(
                root_id,
                brain,
                CATALOG_PATH,
                gravity_window=True,
                project="gravity-studio",
                bridge_db_path=db_path,
            )
            summary = metrics["summary"]
            self.assertEqual(summary["total_agents"], 8)
            self.assertEqual(summary["max_tree_depth"], 2)
            self.assertEqual(summary["execution_mode"], "workflow")
            self.assertIn("workflow", summary["features_used"])

            wf_agents = [a for a in metrics["agents"] if a["source"] == "workflow"]
            self.assertEqual(len(wf_agents), 6)
            for wag in wf_agents:
                self.assertEqual(wag["bridge_workspace"], "gravity-metrics")
                self.assertEqual(wag["depth"], 2)

            self.assertEqual(metrics["by_workspace"]["gravity-studio"]["agents"], 1)
            self.assertEqual(metrics["by_workspace"]["gravity-metrics"]["agents"], 7)

            self.assertEqual(metrics["by_category"]["orchestration"]["agents"], 3)
            self.assertEqual(metrics["by_category"]["build"]["agents"], 3)
            self.assertEqual(metrics["by_category"]["verification"]["agents"], 2)

            inspect_txt = render_inspect_text(metrics)
            self.assertIn("[workflow]", inspect_txt)

            detail_md = render_session_detail_markdown(metrics)
            self.assertIn("[workflow · d.2]", detail_md)

            known_children = load_known_child_conversation_ids(brain, bridge_db_path=db_path)
            self.assertNotIn(root_id, known_children)
            self.assertIn(bridge_cid, known_children)
            self.assertIn(wf_p1, known_children)

    def test_bridge_v020_callback_and_v010_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_v2 = root / "bridge-v2.db"
            db_v1 = root / "bridge-v1.db"

            root_id = "33333333-3333-4333-8333-333333333333"
            child_id = "44444444-4444-4444-8444-444444444444"
            root_no_out = "55555555-5555-4555-8555-555555555555"
            child_no_out = "66666666-6666-4666-8666-666666666666"

            # Root identifiée via caller_conversation_id (prompt sans commande /gravity)
            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T11:00:00Z",
                        "content": "Dispatch vers metrics",
                    },
                    _planner_step(1, "2026-11-22T11:00:10Z"),
                ],
            )
            _write_transcript(
                brain / child_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T11:01:00Z",
                        "content": "[GRAVITY-BRIDGE | thread=thr-cb-1]",
                    },
                    _planner_step(1, "2026-11-22T11:01:10Z"),
                ],
            )

            _write_transcript(
                brain / root_no_out,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T11:10:00Z",
                        "content": "/gravity-bridge status",
                    },
                    _planner_step(1, "2026-11-22T11:10:10Z"),
                ],
            )
            _write_transcript(
                brain / child_no_out,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T11:11:00Z",
                        "content": "[GRAVITY-BRIDGE | thread=thr-cb-2]",
                    },
                    _planner_step(1, "2026-11-22T11:11:10Z"),
                ],
            )

            con = sqlite3.connect(str(db_v2))
            con.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
                "msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
            )
            con.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
                "action TEXT, direction TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, status TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-cb-1", "gravity-studio", "gravity-metrics", "handoff", "open", child_id, root_id, "2026-11-22T11:00:20Z"),
            )
            # Outbound terminé + callback actif pointant vers root_id avec caller=child_id
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-out-1", "thr-cb-1", "gravity-metrics", "new_conversation", "outbound", child_id, root_id, "completed", "2026-11-22T11:00:20Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-cb-1", "thr-cb-1", "gravity-studio", "send_message", "callback", root_id, child_id, "dispatched", "2026-11-22T11:02:00Z"),
            )

            # Thread sans job outbound en base, mais avec callback actif uniquement
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-cb-2", "gravity-studio", "gravity-bridge", "question", "open", child_no_out, root_no_out, "2026-11-22T11:10:20Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-cb-2", "thr-cb-2", "gravity-studio", "send_message", "callback", root_no_out, child_no_out, "queued", "2026-11-22T11:12:00Z"),
            )
            con.commit()
            con.close()

            target_ids = load_bridge_target_conversation_ids(db_v2)
            self.assertEqual(target_ids, {child_id, child_no_out})
            self.assertNotIn(root_id, target_ids)
            self.assertNotIn(root_no_out, target_ids)

            caller_ids = load_bridge_caller_conversation_ids(db_v2)
            self.assertEqual(caller_ids, {root_id, root_no_out})

            convs = list_gravity_conversations(brain, include_all=False, bridge_db_path=db_v2)
            self.assertEqual({c["conversation_id"] for c in convs}, {root_id, root_no_out})

            pend = list_pending_sessions(brain, root / "history.jsonl", bridge_db_path=db_v2)
            self.assertEqual({p["conversation_id"] for p in pend}, {root_id, root_no_out})

            # discover_bridge_links sur root_id : garde child_id & gravity-metrics, expose callback actif
            links_root = discover_bridge_links(
                root_id,
                brain / root_id / ".system_generated" / "logs" / "transcript.jsonl",
                bridge_db=db_v2,
            )
            self.assertEqual(len(links_root), 1)
            self.assertEqual(links_root[0]["thread_id"], "thr-cb-1")
            self.assertEqual(links_root[0]["target_conversation_id"], child_id)
            self.assertEqual(links_root[0]["to_workspace"], "gravity-metrics")
            self.assertEqual(links_root[0]["created_at"], "2026-11-22T11:00:20Z")
            self.assertTrue(links_root[0]["has_active_job"])
            self.assertEqual(links_root[0]["job_id"], "job-cb-1")
            self.assertEqual(links_root[0]["job_status"], "dispatched")
            self.assertEqual(links_root[0]["job_action"], "send_message")

            # discover_bridge_links sur root_no_out : fallback callback actif quand outbound absent
            links_no_out = discover_bridge_links(
                root_no_out,
                brain / root_no_out / ".system_generated" / "logs" / "transcript.jsonl",
                bridge_db=db_v2,
            )
            self.assertEqual(len(links_no_out), 1)
            self.assertEqual(links_no_out[0]["target_conversation_id"], child_no_out)
            self.assertEqual(links_no_out[0]["to_workspace"], "gravity-bridge")
            self.assertTrue(links_no_out[0]["has_active_job"])
            self.assertEqual(links_no_out[0]["job_id"], "job-cb-2")
            self.assertEqual(links_no_out[0]["job_status"], "queued")
            self.assertEqual(links_no_out[0]["job_action"], "send_message")

            # discover_bridge_links sur child_id : le callback ne réinjecte pas thr-cb-1
            links_child = discover_bridge_links(
                child_id,
                brain / child_id / ".system_generated" / "logs" / "transcript.jsonl",
                bridge_db=db_v2,
            )
            self.assertEqual(links_child, [])

            # Tolérance schéma v0.1.0 sans colonne direction ni caller_conversation_id
            con1 = sqlite3.connect(str(db_v1))
            con1.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
                "msg_type TEXT, status TEXT, target_conversation_id TEXT, created_at TEXT)"
            )
            con1.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
                "action TEXT, target_conversation_id TEXT, status TEXT, created_at TEXT)"
            )
            con1.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("thr-v1", "gravity-studio", "gravity-metrics", "handoff", "open", child_id, "2026-11-22T11:00:20Z"),
            )
            con1.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("job-v1-out", "thr-v1", "gravity-metrics", "new_conversation", child_id, "completed", "2026-11-22T11:00:20Z"),
            )
            con1.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("job-v1-cb", "thr-v1", "gravity-studio", "send_message", root_id, "dispatched", "2026-11-22T11:02:00Z"),
            )
            con1.commit()
            con1.close()

            self.assertEqual(load_bridge_target_conversation_ids(db_v1), {child_id})
            self.assertEqual(load_bridge_caller_conversation_ids(db_v1), set())

    def test_strict_pending_filtering_and_root_cycle_non_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_path = root / "bridge.db"
            runs_file = root / "runs.jsonl"

            cid_slash = "grav-slash"
            cid_sub_cycle = "sub-1"
            cid_runs = "77777777-7777-4777-8777-777777777777"
            cid_caller = "88888888-8888-4888-8888-888888888888"
            cid_bridge_child = "99999999-9999-4999-8999-999999999999"
            cid_wf_child_user = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
            cid_noise_install = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            cid_noise_uri = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
            cid_noise_sub_uri = "ffffffff-ffff-4fff-8fff-ffffffffffff"

            # 1. Racine via /gravity* + cycle sub-1 -> grav-slash (typeName: "root")
            _write_transcript(
                brain / cid_slash,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:00:00Z",
                        "content": "<USER_REQUEST>\n/gravity status\n</USER_REQUEST>",
                    },
                    _planner_step(1, "2026-11-22T12:00:10Z"),
                ],
            )
            _write_subagent(brain / cid_slash, cid_sub_cycle, "teamwork_worker", "Builder")
            _write_subagent(brain / cid_sub_cycle, cid_slash, "root", "root")
            _write_transcript(
                brain / cid_sub_cycle,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:00:02Z",
                        "content": "/gravity-workflow internal",
                    },
                    _planner_step(1, "2026-11-22T12:00:05Z"),
                ],
            )

            # 2. Racine via runs.jsonl
            runs_file.write_text(json.dumps({"session_id": cid_runs}) + "\n", encoding="utf-8")
            _write_transcript(
                brain / cid_runs,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:01:00Z",
                        "content": "Implémenter feature run",
                    },
                    _planner_step(1, "2026-11-22T12:01:10Z", tool_calls=[{"name": "write_file"}]),
                ],
            )

            # 3. Racine via caller_conversation_id dans bridge.db
            _write_transcript(
                brain / cid_caller,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:02:00Z",
                        "content": "Session sans slash mais appelant Bridge",
                    },
                    _planner_step(1, "2026-11-22T12:02:10Z"),
                ],
            )
            _write_transcript(
                brain / cid_bridge_child,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:02:20Z",
                        "content": "[GRAVITY-BRIDGE | thread=thr-strict-1]",
                    },
                    _planner_step(1, "2026-11-22T12:02:30Z"),
                ],
            )

            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
                "msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
            )
            con.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
                "action TEXT, direction TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, status TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thr-strict-1", "studio", "gravity-metrics", "handoff", "done", cid_bridge_child, cid_caller, "2026-11-22T12:02:15Z"),
            )
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-strict-1", "thr-strict-1", "gravity-metrics", "new_conversation", "outbound", cid_bridge_child, cid_caller, "completed", "2026-11-22T12:02:15Z"),
            )
            con.commit()
            con.close()

            # 4. Enfant run_workflow ayant un USER_INPUT -> strictement exclu
            tasks_dir = brain / cid_slash / ".system_generated" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            (tasks_dir / "task-1.log").write_text(
                f"- [agent:builder] Workflow Child With Input: DONE ({cid_wf_child_user})\n",
                encoding="utf-8",
            )
            _write_transcript(
                brain / cid_wf_child_user,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:03:00Z",
                        "content": "/gravity-workflow nested prompt",
                    },
                    _planner_step(1, "2026-11-22T12:03:10Z"),
                ],
            )

            # 5. Faux positifs historiques à exclure : "install moi gravity-metrics" & workspaceUris gravity-*
            _write_transcript(
                brain / cid_noise_install,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:04:00Z",
                        "content": "install moi gravity-metrics sur mon poste",
                    },
                    _planner_step(1, "2026-11-22T12:04:10Z"),
                ],
            )
            _write_transcript(
                brain / cid_noise_uri,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:05:00Z",
                        "content": "Analyse un autre projet ordinaire",
                    },
                    _planner_step(1, "2026-11-22T12:05:10Z"),
                ],
            )
            _write_subagent(
                brain / cid_noise_uri,
                cid_noise_sub_uri,
                "research",
                "Explorer",
                workspace_uris=["file:///d:/Neko/Documents/GitHub/gravity-metrics"],
            )
            _write_transcript(
                brain / cid_noise_sub_uri,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T12:05:15Z",
                        "content": "Subagent explorer prompt",
                    },
                    _planner_step(1, "2026-11-22T12:05:20Z"),
                ],
            )

            known_children = load_known_child_conversation_ids(brain, bridge_db_path=db_path)
            self.assertNotIn(cid_slash, known_children)
            self.assertIn(cid_sub_cycle, known_children)
            self.assertIn(cid_wf_child_user, known_children)
            self.assertIn(cid_bridge_child, known_children)
            self.assertIn(cid_noise_sub_uri, known_children)

            # Anti-cycle discover_agent_tree sans boucle
            tree = discover_agent_tree(cid_slash, brain, bridge_db_path=db_path)
            self.assertEqual(
                {n["conversation_id"] for n in tree},
                {cid_slash, cid_sub_cycle, cid_wf_child_user},
            )

            expected_roots = {cid_slash, cid_runs, cid_caller}
            convs = list_gravity_conversations(
                brain,
                include_all=False,
                runs_jsonl_paths=[runs_file],
                bridge_db_path=db_path,
            )
            self.assertEqual({c["conversation_id"] for c in convs}, expected_roots)

            pend = list_pending_sessions(
                brain,
                root / "history.jsonl",
                bridge_db_path=db_path,
                runs_jsonl_paths=[runs_file],
            )
            self.assertEqual({p["conversation_id"] for p in pend}, expected_roots)


if __name__ == "__main__":
    unittest.main()
