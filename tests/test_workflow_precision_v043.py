#!/usr/bin/env python3
"""Tests unitaires et d'intégration v0.4.3 : précision run_workflow et terminaison finish."""

import json
from pathlib import Path
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
    discover_workflow_children,
    load_pricing_catalog,
    parse_agent_transcript,
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
    state: str = "SUBAGENT_STATE_DONE",
) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"{child_id}.json").write_text(
        json.dumps(
            {
                "conversationId": child_id,
                "subagentDescriptor": {"typeName": type_name, "role": role},
                "state": state,
                "spawnStepIndex": spawn_step,
                "workspaceUris": [],
            }
        ),
        encoding="utf-8",
    )


def _planner_step(
    step_idx: int,
    ts: str,
    tool_calls: object = None,
    status: str = "DONE",
    in_tok: int = 10_000,
    out_tok: int = 400,
) -> dict:
    return {
        "step_index": step_idx,
        "type": "PLANNER_RESPONSE",
        "status": status,
        "created_at": ts,
        "model": "gemini-3.8-flash-tiered",
        "input_tokens": in_tok,
        "cache_read_tokens": 0,
        "output_tokens": out_tok,
        "tool_calls": [] if tool_calls is None else tool_calls,
    }


class TestWorkflowPrecisionV043(unittest.TestCase):
    def test_absence_of_run_workflow_rejects_generic_and_non_workflow_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_cid = "11111111-1111-4111-8111-111111111111"
            ext_uuid_generic = "aaaaaaaa-0001-4000-8000-000000000001"
            ext_uuid_cmd = "aaaaaaaa-0002-4000-8000-000000000002"
            ext_uuid_other = "aaaaaaaa-0003-4000-8000-000000000003"
            ext_uuid_no_tc = "aaaaaaaa-0004-4000-8000-000000000004"

            _write_transcript(
                brain / root_cid,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T14:00:00Z",
                        "content": "Inspecte les logs du workspace",
                    },
                    _planner_step(
                        1,
                        "2026-11-22T14:00:05Z",
                        tool_calls=[{"name": "read_file"}, {"name": "run_command"}],
                    ),
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f"Command output:\n- [agent:planner] Planner: done ({ext_uuid_generic})\n",
                    },
                    _planner_step(3, "2026-11-22T14:00:10Z", tool_calls=[]),
                ],
            )

            msgs_dir = brain / root_cid / ".system_generated" / "messages"
            msgs_dir.mkdir(parents=True, exist_ok=True)
            (msgs_dir / "msg-cmd.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "run_command"}, "stepIndex": 2}},
                        "content": f"- [agent:planner] Planner: done ({ext_uuid_cmd})\n",
                    }
                ),
                encoding="utf-8",
            )
            (msgs_dir / "msg-other.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"toolCall": {"name": "other_tool"}, "stepIndex": 2}},
                        "content": f"- [agent:planner] Planner: done ({ext_uuid_other})\n",
                    }
                ),
                encoding="utf-8",
            )
            (msgs_dir / "msg-no-tc.json").write_text(
                json.dumps(
                    {
                        "sourceMetadata": {"tool": {"stepIndex": 2}},
                        "content": f"- [agent:planner] Planner: done ({ext_uuid_no_tc})\n",
                    }
                ),
                encoding="utf-8",
            )

            # Le transcript externe existe sur disque mais ne doit pas être rattaché
            for ext_id in (ext_uuid_generic, ext_uuid_cmd, ext_uuid_other, ext_uuid_no_tc):
                _write_transcript(
                    brain / ext_id,
                    [_planner_step(0, "2026-11-22T13:55:00Z", tool_calls=[])],
                )

            self.assertEqual(discover_workflow_children(root_cid, brain), [])

            metrics = collect_run_metrics(root_cid, brain, CATALOG_PATH, gravity_window=False)
            self.assertEqual(metrics["summary"]["total_agents"], 1)
            self.assertEqual(metrics["summary"]["execution_mode"], "single")

    def test_parallel_tools_and_user_input_reset_in_transcript_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            parent_cid = "22222222-2222-4222-8222-222222222222"
            ignored_cmd = "bbbbbbbb-0001-4000-8000-000000000001"
            ignored_exit = "bbbbbbbb-0002-4000-8000-000000000002"
            ignored_lines = "bbbbbbbb-0003-4000-8000-000000000003"
            accepted_wf = "bbbbbbbb-0004-4000-8000-000000000004"
            ignored_after_reset = "bbbbbbbb-0005-4000-8000-000000000005"

            _write_transcript(
                brain / parent_cid,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T14:10:00Z",
                        "content": "/gravity-workflow parallel",
                    },
                    _planner_step(
                        1,
                        "2026-11-22T14:10:05Z",
                        tool_calls=[{"name": "run_workflow"}, {"name": "run_command"}],
                    ),
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f"Command output:\n- [agent:builder] Ignored Shell: DONE ({ignored_cmd})\n",
                    },
                    {
                        "step_index": 3,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f"Command exited with code 0.\n- [agent:builder] Ignored Exited: DONE ({ignored_exit})\n",
                    },
                    {
                        "step_index": 4,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f"Showing lines 1-10:\n- [agent:builder] Ignored Lines: DONE ({ignored_lines})\n",
                    },
                    {
                        "step_index": 5,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f'Workflow "X" completed.\nLogs:\n- [agent:builder] Accepted Workflow: DONE ({accepted_wf})\n',
                    },
                    {
                        "step_index": 6,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T14:11:00Z",
                        "content": "Tour suivant sans workflow",
                    },
                    _planner_step(
                        7,
                        "2026-11-22T14:11:05Z",
                        tool_calls=[{"name": "run_command"}],
                    ),
                    {
                        "step_index": 8,
                        "type": "GENERIC",
                        "status": "DONE",
                        "content": f'Workflow "Fake" completed.\nLogs:\n- [agent:builder] Ignored After Reset: DONE ({ignored_after_reset})\n',
                    },
                ],
            )

            children = discover_workflow_children(parent_cid, brain)
            self.assertEqual([c["conversation_id"] for c in children], [accepted_wf])
            self.assertEqual(children[0]["spawn_step_index"], 5)

    def test_async_system_message_filtering_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"

            # Cas A : aucun PLANNER_RESPONSE run_workflow ; seuls les marqueurs du parent matchent
            parent_no_wf = "33333333-3333-4333-8333-333333333333"
            other_parent = "44444444-4444-4444-8444-444444444444"
            cid_task_id = "cccccccc-0001-4000-8000-000000000001"
            cid_sender = "cccccccc-0002-4000-8000-000000000002"
            cid_unmarked = "cccccccc-0003-4000-8000-000000000003"
            cid_foreign = "cccccccc-0004-4000-8000-000000000004"

            _write_transcript(
                brain / parent_no_wf,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "Analyse",
                    },
                    _planner_step(1, "2026-11-22T14:20:00Z", tool_calls=[{"name": "read_file"}]),
                    {
                        "step_index": 2,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": (
                            f'Task id "{parent_no_wf.upper()}/task-9" finished:\n'
                            f"- [agent:planner] Planner Marker TaskId: DONE ({cid_task_id})\n"
                        ),
                    },
                    {
                        "step_index": 3,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": (
                            f"[Message] sender={parent_no_wf.upper()}/task-10\n"
                            f"- [agent:reviewer] Reviewer Marker Sender: DONE ({cid_sender})\n"
                        ),
                    },
                    {
                        "step_index": 4,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": f"- [agent:builder] Unmarked Noise: DONE ({cid_unmarked})\n",
                    },
                    {
                        "step_index": 5,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": (
                            f'Task id "{other_parent}/task-1" sender={other_parent}/task-1:\n'
                            f"- [agent:builder] Foreign Parent Marker: DONE ({cid_foreign})\n"
                        ),
                    },
                ],
            )
            found_a = discover_workflow_children(parent_no_wf, brain)
            self.assertEqual([c["conversation_id"] for c in found_a], [cid_task_id, cid_sender])

            # Cas B : has_any_wf_call reste actif après un USER_INPUT ultérieur
            parent_with_wf = "55555555-5555-4555-8555-555555555555"
            cid_async_later = "cccccccc-0005-4000-8000-000000000005"
            _write_transcript(
                brain / parent_with_wf,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "/gravity-workflow run",
                    },
                    _planner_step(1, "2026-11-22T14:21:00Z", tool_calls=[{"name": "run_workflow"}]),
                    {
                        "step_index": 2,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "Autre question entre temps",
                    },
                    {
                        "step_index": 3,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": f"- [agent:builder] Async After UserInput: DONE ({cid_async_later})\n",
                    },
                ],
            )
            found_b = discover_workflow_children(parent_with_wf, brain)
            self.assertEqual([c["conversation_id"] for c in found_b], [cid_async_later])
            self.assertEqual(found_b[0]["spawn_step_index"], 3)

    def test_clean_termination_with_finish_and_mixed_tree(self) -> None:
        catalog = load_pricing_catalog(CATALOG_PATH)
        profile = catalog["default_profile"]
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"

            # 1. PLANNER_RESPONSE(DONE, finish) -> SYSTEM_MESSAGE -> GENERIC(DONE) => True
            t_finish_generic = _write_transcript(
                brain / "case-finish-generic",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:00:00Z",
                        tool_calls=[{"name": "finish"}],
                        status="DONE",
                    ),
                    {
                        "step_index": 1,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:00:01Z",
                        "content": "Background notification",
                    },
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:00:02Z",
                        "content": "Finished.",
                    },
                ],
            )
            res_fg = parse_agent_transcript(t_finish_generic, catalog, profile)
            self.assertTrue(res_fg["clean_termination"])

            # 2. PLANNER_RESPONSE(DONE, finish) en dernier step non-système => True
            t_finish_planner = _write_transcript(
                brain / "case-finish-planner",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:01:00Z",
                        tool_calls=[{"name": "finish"}],
                        status="DONE",
                    ),
                    {
                        "step_index": 1,
                        "type": "SYSTEM_MESSAGE",
                        "status": "DONE",
                        "content": "Trailing system message",
                    },
                ],
            )
            res_fp = parse_agent_transcript(t_finish_planner, catalog, profile)
            self.assertTrue(res_fp["clean_termination"])

            # 3. GENERIC(RUNNING) après finish => False
            t_running = _write_transcript(
                brain / "case-running",
                [
                    _planner_step(0, "2026-11-22T15:02:00Z", tool_calls=[{"name": "finish"}], status="DONE"),
                    {"step_index": 1, "type": "GENERIC", "status": "RUNNING", "content": "Still running"},
                ],
            )
            self.assertFalse(
                parse_agent_transcript(t_running, catalog, profile)["clean_termination"]
            )

            # 4. GENERIC(ERROR) après finish => False
            t_error = _write_transcript(
                brain / "case-error",
                [
                    _planner_step(0, "2026-11-22T15:03:00Z", tool_calls=[{"name": "finish"}], status="DONE"),
                    {"step_index": 1, "type": "GENERIC", "status": "ERROR", "content": "Failed"},
                ],
            )
            self.assertFalse(
                parse_agent_transcript(t_error, catalog, profile)["clean_termination"]
            )

            # 5. GENERIC(DONE) après read_file / run_command => False
            for bad_tool in ("read_file", "run_command"):
                t_other = _write_transcript(
                    brain / f"case-{bad_tool}",
                    [
                        _planner_step(
                            0,
                            "2026-11-22T15:04:00Z",
                            tool_calls=[{"name": bad_tool}],
                            status="DONE",
                        ),
                        {"step_index": 1, "type": "GENERIC", "status": "DONE", "content": "Tool output"},
                    ],
                )
                self.assertFalse(
                    parse_agent_transcript(t_other, catalog, profile)["clean_termination"]
                )

            # 6. Transcript manquant => False
            res_missing = parse_agent_transcript(
                brain / "does-not-exist" / "transcript.jsonl",
                catalog,
                profile,
            )
            self.assertTrue(res_missing["transcript_missing"])
            self.assertFalse(res_missing["clean_termination"])

            # 7. Worker /swarm terminé par post_message + swarm_wait -> 2× GENERIC(DONE), SUBAGENT_STATE_KILLED => True
            t_swarm_done = _write_transcript(
                brain / "case-swarm-done",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:05:00Z",
                        tool_calls=[{"name": "post_message"}, {"name": "swarm_wait"}],
                        status="DONE",
                    ),
                    {"step_index": 1, "type": "GENERIC", "status": "DONE", "content": "Message #2 posted."},
                    {"step_index": 2, "type": "SYSTEM_MESSAGE", "status": "DONE", "content": "Board idle"},
                    {"step_index": 3, "type": "GENERIC", "status": "DONE", "content": "Swarm wait parked."},
                ],
            )
            self.assertTrue(
                parse_agent_transcript(
                    t_swarm_done,
                    catalog,
                    profile,
                    subagent_state="SUBAGENT_STATE_KILLED",
                )["clean_termination"]
            )

            # 8. Cas réels interrompus / erreur avec SUBAGENT_STATE_KILLED ou ERROR => False
            t_swarm_running = _write_transcript(
                brain / "case-swarm-running",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:06:00Z",
                        tool_calls=[{"name": "post_message"}, {"name": "swarm_wait"}],
                        status="DONE",
                    ),
                    {"step_index": 1, "type": "GENERIC", "status": "DONE", "content": "Message #2 posted."},
                    {"step_index": 2, "type": "GENERIC", "status": "RUNNING", "content": "Waiting..."},
                ],
            )
            self.assertFalse(
                parse_agent_transcript(
                    t_swarm_running,
                    catalog,
                    profile,
                    subagent_state="SUBAGENT_STATE_KILLED",
                )["clean_termination"]
            )
            t_swarm_shell_err = _write_transcript(
                brain / "case-swarm-shell-err",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:06:30Z",
                        tool_calls=[{"name": "post_message"}, {"name": "swarm_wait"}],
                        status="DONE",
                    ),
                    {"step_index": 1, "type": "GENERIC", "status": "DONE", "content": "Command exited with code 2\nfail"},
                    {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "Swarm wait parked."},
                ],
            )
            self.assertFalse(
                parse_agent_transcript(
                    t_swarm_shell_err,
                    catalog,
                    profile,
                    subagent_state="SUBAGENT_STATE_KILLED",
                )["clean_termination"]
            )
            t_swarm_mid_edit = _write_transcript(
                brain / "case-swarm-mid-edit",
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:07:00Z",
                        tool_calls=[{"name": "edit_file"}],
                        status="DONE",
                    ),
                    {"step_index": 1, "type": "GENERIC", "status": "DONE", "content": "Edited"},
                ],
            )
            self.assertFalse(
                parse_agent_transcript(
                    t_swarm_mid_edit,
                    catalog,
                    profile,
                    subagent_state="SUBAGENT_STATE_KILLED",
                )["clean_termination"]
            )
            self.assertFalse(
                parse_agent_transcript(
                    t_finish_generic,
                    catalog,
                    profile,
                    subagent_state="SUBAGENT_STATE_ERROR",
                )["clean_termination"]
            )

            # 9. Intégration collect_run_metrics sur arbre mixte (racine + natif + workflow + worker /swarm KILLED)
            root_id = "66666666-6666-4666-8666-666666666666"
            sub_id = "dddddddd-0001-4000-8000-000000000001"
            wf_id = "eeeeeeee-0001-4000-8000-000000000001"
            swarm_rev_id = "ffffffff-0001-4000-8000-000000000001"

            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:10:00Z",
                        "content": "/gravity-workflow build",
                    },
                    _planner_step(
                        1,
                        "2026-11-22T15:10:05Z",
                        tool_calls=[{"name": "run_workflow"}],
                        status="DONE",
                    ),
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:10:20Z",
                        "content": f"- [agent:builder] Builder Worker: DONE ({wf_id})\n",
                    },
                    _planner_step(3, "2026-11-22T15:10:30Z", tool_calls=[], status="DONE"),
                ],
            )
            _write_subagent(brain / root_id, sub_id, "research", "Explorer", spawn_step=1)
            _write_subagent(
                brain / root_id,
                swarm_rev_id,
                "worker",
                "Swarm Worker (dip-reviewer)",
                spawn_step=1,
                state="SUBAGENT_STATE_KILLED",
            )
            for cid, ts_base in ((sub_id, "2026-11-22T15:10:1"), (wf_id, "2026-11-22T15:10:2")):
                _write_transcript(
                    brain / cid,
                    [
                        _planner_step(
                            0,
                            f"{ts_base}0Z",
                            tool_calls=[{"name": "finish"}],
                            status="DONE",
                        ),
                        {
                            "step_index": 1,
                            "type": "GENERIC",
                            "status": "DONE",
                            "created_at": f"{ts_base}5Z",
                            "content": "Completed via finish.",
                        },
                    ],
                )
            _write_transcript(
                brain / swarm_rev_id,
                [
                    _planner_step(
                        0,
                        "2026-11-22T15:10:22Z",
                        tool_calls=[{"name": "post_message"}, {"name": "swarm_wait"}],
                        status="DONE",
                    ),
                    {
                        "step_index": 1,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:10:23Z",
                        "content": "Posted [done] PASS.",
                    },
                    {
                        "step_index": 2,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-22T15:10:24Z",
                        "content": "Waiting on swarm.",
                    },
                ],
            )

            metrics = collect_run_metrics(root_id, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(metrics["summary"]["total_agents"], 4)
            self.assertEqual(metrics["efficiency_and_waste"]["interrupted_agents"]["count"], 0)
            self.assertTrue(all(ag["clean_termination"] for ag in metrics["agents"]))
            rev_ag = {ag["conversation_id"]: ag for ag in metrics["agents"]}[swarm_rev_id]
            self.assertEqual(rev_ag["category"], "verification")

    def test_r3_native_swarm_subagents_and_direct_edits_phases(self) -> None:
        from metrics import list_pending_sessions

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"

            # 1) Conception seule : research + édition docs atelier (`specs/`, `tasks/`, `tracking/`) -> reste Conception
            cid_conc = "r3000001-1111-4111-8111-111111111111"
            sub_res = "r3sub001-1111-4111-8111-111111111111"
            _write_transcript(
                brain / cid_conc,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T11:00:00Z", "content": "/gravity-architect spec r3"},
                    _planner_step(1, "2026-11-25T11:00:05Z", tool_calls=[{"name": "invoke_subagent"}]),
                    _planner_step(
                        2,
                        "2026-11-25T11:00:10Z",
                        tool_calls=[
                            {"name": "write_file", "args": {"Path": "/repo/gravity-studio/specs/2026-11-25-r3.md"}},
                            {"name": "edit_file", "args": {"Path": "tasks/2026-11-25-w1.md"}},
                            {"name": "replace_file_content", "args": {"TargetFile": "tracking/IMPACT.md"}},
                        ],
                    ),
                ],
            )
            _write_subagent(brain / cid_conc, sub_res, "research", "Researcher", spawn_step=1)
            _write_transcript(brain / sub_res, [_planner_step(0, "2026-11-25T11:00:06Z", tool_calls=[])])

            m_conc = collect_run_metrics(cid_conc, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(m_conc["milestones"]["mode"], "4_phases")
            self.assertEqual(m_conc["milestones"]["current_phase"], "Conception")
            pend_map = {p["conversation_id"]: p for p in list_pending_sessions(brain)}
            self.assertEqual(pend_map[cid_conc]["milestones"]["current_phase"], "Conception")

            # 2) /swarm + subagent builder -> Production -> Vérification & Push -> Clôture / Enregistrée
            cid_swarm = "r3000002-1111-4111-8111-111111111111"
            sub_bld = "r3sub002-1111-4111-8111-111111111111"
            steps_swarm = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T11:10:00Z", "content": "/gravity-architect handoff r3"},
                _planner_step(1, "2026-11-25T11:10:05Z", tool_calls=[{"name": "write_file", "args": {"Path": "specs/r3.md"}}], in_tok=2_000, out_tok=100),
                {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T11:11:00Z", "content": "/swarm lancer build"},
                _planner_step(3, "2026-11-25T11:11:05Z", tool_calls=[{"name": "swarm"}], in_tok=4_000, out_tok=200),
            ]
            _write_transcript(brain / cid_swarm, steps_swarm)
            _write_subagent(brain / cid_swarm, sub_bld, "teamwork_worker", "Builder", spawn_step=3)
            _write_transcript(brain / sub_bld, [_planner_step(0, "2026-11-25T11:11:08Z", tool_calls=[{"name": "finish"}], in_tok=6_000, out_tok=300)])

            m_swarm_prod = collect_run_metrics(cid_swarm, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(m_swarm_prod["milestones"]["current_phase"], "Production")
            pend_swarm = {p["conversation_id"]: p for p in list_pending_sessions(brain)}
            self.assertEqual(pend_swarm[cid_swarm]["milestones"]["current_phase"], "Production")

            steps_verif = steps_swarm + [
                _planner_step(4, "2026-11-25T11:12:00Z", tool_calls=[{"name": "run_command", "args": {"CommandLine": '"python3 scripts/audit_ecosystem.py --strict"'}}], in_tok=1_500, out_tok=50),
            ]
            _write_transcript(brain / cid_swarm, steps_verif)
            m_swarm_verif = collect_run_metrics(cid_swarm, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(m_swarm_verif["milestones"]["current_phase"], "Vérification & Push")
            pend_verif = {p["conversation_id"]: p for p in list_pending_sessions(brain)}
            self.assertEqual(pend_verif[cid_swarm]["milestones"]["current_phase"], "Vérification & Push")

            steps_close = steps_verif + [
                _planner_step(5, "2026-11-25T11:13:00Z", tool_calls=[{"name": "run_command", "args": {"CommandLine": f'"python3 plugin/scripts/cli.py record {cid_swarm[:8]} --update"'}}], in_tok=500, out_tok=20),
            ]
            _write_transcript(brain / cid_swarm, steps_close)
            m_swarm_close = collect_run_metrics(cid_swarm, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(m_swarm_close["milestones"]["current_phase"], "Clôture / Enregistrée")
            by_id = {ph["id"]: ph for ph in m_swarm_close["milestones"]["phases"]}
            self.assertEqual(by_id["conception"]["tokens"], 2100)
            self.assertEqual(by_id["production"]["tokens"], 10500)
            self.assertEqual(by_id["verification"]["tokens"], 1550)
            self.assertEqual(by_id["cloture"]["tokens"], 520)

            # 3) Édition directe hors docs atelier -> déclenche Production
            cid_edit = "r3000003-1111-4111-8111-111111111111"
            _write_transcript(
                brain / cid_edit,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T11:20:00Z", "content": "/gravity-architect fix"},
                    _planner_step(1, "2026-11-25T11:20:05Z", tool_calls=[{"name": "edit_file", "args": {"Path": "plugin/metrics/collector.py"}}]),
                ],
            )
            m_edit = collect_run_metrics(cid_edit, brain, CATALOG_PATH, gravity_window=True)
            self.assertEqual(m_edit["milestones"]["current_phase"], "Production")


if __name__ == "__main__":
    unittest.main()
