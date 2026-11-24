#!/usr/bin/env python3
"""Tests unitaires isolés pour AUD-03-01, AUD-03-05 et INT-01/03/04/06 (100 % stdlib)."""

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"

for p in (str(PLUGIN_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from metrics.collector import collect_run_metrics, list_pending_sessions  # noqa: E402
import validate  # noqa: E402


def _write_transcript(conv_dir: Path, entries: list[dict]) -> Path:
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    tpath = logs_dir / "transcript.jsonl"
    raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    tpath.write_bytes(raw.encode("utf-8"))
    return tpath


def _write_subagent_meta(
    parent_dir: Path,
    child_id: str,
    type_name: str,
    role: str,
    *,
    spawn_step: int,
) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversationId": child_id,
        "subagentDescriptor": {"typeName": type_name, "role": role},
        "state": "SUBAGENT_STATE_DONE",
        "spawnStepIndex": spawn_step,
        "workspaceUris": [],
    }
    (sub_dir / f"{child_id}.json").write_bytes(
        (json.dumps(payload) + "\n").encode("utf-8")
    )


def _create_bridge_db(
    db_path: Path,
    threads: list[tuple[str, str, str, str, str, str, str, str]],
    jobs: list[tuple[str, str, str, str, str, str, str]],
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, from_workspace TEXT, to_workspace TEXT, "
        "msg_type TEXT, status TEXT, target_conversation_id TEXT, caller_conversation_id TEXT, created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, thread_id TEXT, target_workspace TEXT, "
        "action TEXT, target_conversation_id TEXT, status TEXT, created_at TEXT)"
    )
    con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", threads)
    con.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)", jobs)
    con.commit()
    con.close()


class TestBridgeDuplicatesAndMultiWaveMilestones(unittest.TestCase):
    def test_bridge_children_excluded_from_duplicate_role_spawns_native_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_path = root / "bridge" / "bridge.db"

            root_id = "dup00001-1111-2222-3333-444455556666"
            b1 = "dup00002-1111-2222-3333-444455556666"
            b2 = "dup00003-1111-2222-3333-444455556666"
            b3 = "dup00004-1111-2222-3333-444455556666"
            n1 = "dup00005-1111-2222-3333-444455556666"
            n2 = "dup00006-1111-2222-3333-444455556666"

            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T10:00:00Z", "content": "/gravity-architect orchestrate multi"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "mcp_gravity-bridge_bridge_send_message"}]},
                    {"step_index": 2, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:11Z", "content": json.dumps({"thread_id": "thr-1", "to_workspace": "repo-a"})},
                    {"step_index": 3, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:12Z", "content": json.dumps({"thread_id": "thr-2", "to_workspace": "repo-b"})},
                    {"step_index": 4, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T10:00:13Z", "content": json.dumps({"thread_id": "thr-3", "to_workspace": "repo-a"})},
                ],
            )
            for idx, b_id in enumerate((b1, b2, b3), start=1):
                _write_transcript(
                    brain / b_id,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": f"2026-11-24T10:0{idx}:00Z", "content": f"[GRAVITY-BRIDGE | thread=thr-{idx}]"},
                        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": f"2026-11-24T10:0{idx}:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 15_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": []},
                    ],
                )

            _create_bridge_db(
                db_path,
                threads=[
                    ("thr-1", "studio", "repo-a", "handoff", "closed", b1, root_id, "2026-11-24T10:00:11Z"),
                    ("thr-2", "studio", "repo-b", "handoff", "closed", b2, root_id, "2026-11-24T10:00:12Z"),
                    ("thr-3", "studio", "repo-a", "handoff", "closed", b3, root_id, "2026-11-24T10:00:13Z"),
                ],
                jobs=[
                    ("job-1", "thr-1", "repo-a", "new_conversation", b1, "completed", "2026-11-24T10:00:11Z"),
                    ("job-2", "thr-2", "repo-b", "new_conversation", b2, "completed", "2026-11-24T10:00:12Z"),
                    ("job-3", "thr-3", "repo-a", "new_conversation", b3, "completed", "2026-11-24T10:00:13Z"),
                ],
            )

            data_no_native = collect_run_metrics(
                root_id, brain, CATALOG_PATH, label="bridge-only", bridge_db_path=db_path
            )
            dups_only = data_no_native["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_only["duplicate_agent_count"], 0)
            self.assertEqual(dups_only["groups"], [])

            # Ajouter 2 sous-agents natifs de même rôle sous la racine -> toujours détectés
            _write_subagent_meta(brain / root_id, n1, "teamwork_worker", "Builder", spawn_step=1)
            _write_subagent_meta(brain / root_id, n2, "teamwork_worker", "Builder", spawn_step=1)
            for idx, n_id in enumerate((n1, n2), start=4):
                _write_transcript(
                    brain / n_id,
                    [
                        {"step_index": 0, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": f"2026-11-24T10:0{idx}:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    ],
                )

            data_mixed = collect_run_metrics(
                root_id, brain, CATALOG_PATH, label="bridge-plus-native", bridge_db_path=db_path
            )
            dups_mixed = data_mixed["efficiency_and_waste"]["duplicate_role_spawns"]
            self.assertEqual(dups_mixed["duplicate_agent_count"], 1)
            self.assertEqual(len(dups_mixed["groups"]), 1)
            self.assertEqual(dups_mixed["groups"][0]["role"], "Builder")

    def test_multi_wave_bridge_milestones_and_zero_wave_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            db_path = root / "bridge" / "bridge.db"

            root_id = "wave0001-1111-2222-3333-444455556666"
            w1_cid = "wave0002-1111-2222-3333-444455556666"
            w2_cid = "wave0003-1111-2222-3333-444455556666"
            solo_id = "solo0001-1111-2222-3333-444455556666"

            # Wave 1 (step 2) -> callback/verif intermédiaire (steps 4, 5) -> Wave 2 (step 6)
            def _bridge_call(to_ws: str) -> dict:
                return {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": '"gravity-bridge_bridge"',
                        "ToolName": '"send_message"',
                        "Arguments": json.dumps(
                            {
                                "from_workspace": "studio",
                                "to_workspace": to_ws,
                                "msg_type": "handoff",
                                "dispatch": True,
                            }
                        ),
                    },
                }

            root_entries_active = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:00:00Z", "content": "/gravity-architect spec multi-wave"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [_bridge_call("repo-w1")]},
                {"step_index": 3, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T11:00:11Z", "content": json.dumps({"thread_id": "thr-w1", "to_workspace": "repo-w1"})},
                {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:01:00Z", "content": "[GRAVITY-BRIDGE CALLBACK] wave 1 done"},
                {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "python -m unittest"}}]},
                {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:02:00Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [_bridge_call("repo-w2")]},
                {"step_index": 7, "type": "GENERIC", "status": "DONE", "created_at": "2026-11-24T11:02:01Z", "content": json.dumps({"thread_id": "thr-w2", "to_workspace": "repo-w2"})},
            ]
            _write_transcript(brain / root_id, root_entries_active)
            _write_transcript(
                brain / w1_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:00:20Z", "content": "[GRAVITY-BRIDGE | thread=thr-w1]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:00:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / w2_cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:02:10Z", "content": "[GRAVITY-BRIDGE | thread=thr-w2]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:02:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 500, "tool_calls": []},
                ],
            )

            # Session solo sans vague (compatibilité 2 phases)
            _write_transcript(
                brain / solo_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T09:00:00Z", "content": "/gravity-workflow Corrige le parser"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 8_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "write_file"}]},
                ],
            )

            _create_bridge_db(
                db_path,
                threads=[
                    ("thr-w1", "studio", "repo-w1", "handoff", "closed", w1_cid, root_id, "2026-11-24T11:00:11Z"),
                    ("thr-w2", "studio", "repo-w2", "handoff", "open", w2_cid, root_id, "2026-11-24T11:02:01Z"),
                ],
                jobs=[
                    ("job-w1", "thr-w1", "repo-w1", "new_conversation", w1_cid, "completed", "2026-11-24T11:00:11Z"),
                    ("job-w2", "thr-w2", "repo-w2", "new_conversation", w2_cid, "claimed", "2026-11-24T11:02:01Z"),
                ],
            )

            # Wave 2 active => current_phase == "Production" malgré callback/verif Wave 1
            active_data = collect_run_metrics(
                root_id, brain, CATALOG_PATH, label="wave2-active", bridge_db_path=db_path
            )
            self.assertEqual(active_data["milestones"]["mode"], "4_phases")
            self.assertEqual(active_data["milestones"]["current_phase"], "Production")
            pending_active = {
                p["conversation_id"]: p
                for p in list_pending_sessions(brain, bridge_db_path=db_path)
            }
            self.assertEqual(pending_active[root_id]["milestones"]["current_phase"], "Production")
            self.assertEqual(pending_active[solo_id]["milestones"]["mode"], "2_phases")
            self.assertEqual(pending_active[solo_id]["milestones"]["current_phase"], "Production")
            self.assertEqual([ph["id"] for ph in pending_active[solo_id]["milestones"]["phases"]], ["production", "cloture"])

            # Compatibilité 0-vague collect_run_metrics
            solo_data = collect_run_metrics(solo_id, brain, CATALOG_PATH, label="solo-run")
            self.assertEqual(solo_data["milestones"]["mode"], "2_phases")
            self.assertEqual(solo_data["milestones"]["current_phase"], "Production")
            self.assertEqual([ph["id"] for ph in solo_data["milestones"]["phases"]], ["production", "cloture"])

            # Clôture Wave 2 + callback/verif Wave 2 (steps 8, 9)
            con = sqlite3.connect(str(db_path))
            con.execute("UPDATE jobs SET status = 'completed' WHERE id = 'job-w2'")
            con.execute("UPDATE threads SET status = 'closed' WHERE id = 'thr-w2'")
            con.commit()
            con.close()
            _write_transcript(
                brain / root_id,
                root_entries_active
                + [
                    {"step_index": 8, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-24T11:03:00Z", "content": "[GRAVITY-BRIDGE CALLBACK] wave 2 done"},
                    {"step_index": 9, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-24T11:03:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 9_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "run_command", "arguments": {"CommandLine": "git push origin dev"}}]},
                ],
            )

            done_data = collect_run_metrics(
                root_id, brain, CATALOG_PATH, label="wave2-done", bridge_db_path=db_path
            )
            ms = done_data["milestones"]
            self.assertEqual(ms["current_phase"], "Vérification & Push")
            by_id = {ph["id"]: ph for ph in ms["phases"]}
            self.assertEqual(by_id["production"]["status"], "done")
            self.assertEqual(by_id["verification"]["status"], "active")
            # Wave 2 (step 5 root=6200 + w1=10300 + w2=20500) + step 2(3100) + step 4(4100) = 44200 en production; step 7(9300) en verification
            self.assertEqual(by_id["conception"]["tokens"], 2100)
            self.assertEqual(by_id["production"]["tokens"], 44200)
            self.assertEqual(by_id["verification"]["tokens"], 9300)

            pending_done = {
                p["conversation_id"]: p
                for p in list_pending_sessions(brain, bridge_db_path=db_path)
            }
            self.assertEqual(pending_done[root_id]["milestones"]["current_phase"], "Vérification & Push")


class TestValidateHardening(unittest.TestCase):
    def _make_plugin_copy(self, tmp: str) -> Path:
        dst = Path(tmp) / "plugin"
        shutil.copytree(
            PLUGIN_ROOT,
            dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        return dst

    def _run_validate_fast(self, plugin_dir: Path) -> validate.Report:
        report = validate.Report()
        with (
            mock.patch.object(validate, "_run_hook_dry_runs"),
            mock.patch.object(validate, "_run_mcp_dry_run"),
        ):
            validate.validate_plugin(plugin_dir, report)
        return report

    def test_int01_ui_mode_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            mod_path = plugin_dir / "config" / "module.json"
            data = json.loads(mod_path.read_text(encoding="utf-8"))
            data["ui"]["mode"] = "legacy-html"
            mod_path.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))

            report = self._run_validate_fast(plugin_dir)
            self.assertFalse(report.ok(strict=False))
            self.assertTrue(any("ui.mode" in err for err in report.errors), report.errors)

    def test_int03_suggested_prompts_errors(self) -> None:
        cases = [
            ("wrong_count", ["/gravity-metrics — Un", "/gravity-metrics pending — Deux"]),
            (
                "too_long",
                [
                    "/gravity-metrics — " + ("x" * 90),
                    "/gravity-metrics pending — Lister les sessions en attente",
                    "/gravity-metrics start — Démarrer la capture",
                ],
            ),
            (
                "bad_format",
                [
                    "Ouvrir le dashboard sans slash ni tiret",
                    "/gravity-metrics pending — Lister les sessions en attente",
                    "/gravity-metrics start — Démarrer la capture",
                ],
            ),
        ]
        for name, prompts in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                plugin_dir = self._make_plugin_copy(tmp)
                mpath = plugin_dir / "plugin.json"
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
                manifest["suggestedPrompts"] = prompts
                mpath.write_bytes((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

                report = self._run_validate_fast(plugin_dir)
                self.assertFalse(report.ok(strict=False), report.warnings)
                self.assertTrue(
                    any("suggestedPrompts" in err for err in report.errors),
                    f"Erreur suggestedPrompts attendue ({name}): {report.errors}",
                )

    def test_int04_bom_and_crlf_on_py_json_md(self) -> None:
        for kind, rel_path, body in [
            ("py", "metrics/extra.py", "def ok():\n    return 1\n"),
            ("json", "hooks.json", None),
            ("md", "README.md", None),
        ]:
            for defect, prefix, repl in [
                ("bom", validate.UTF8_BOM, False),
                ("crlf", b"", True),
            ]:
                with self.subTest(kind=kind, defect=defect), tempfile.TemporaryDirectory() as tmp:
                    plugin_dir = self._make_plugin_copy(tmp)
                    target = plugin_dir / rel_path
                    raw = body.encode("utf-8") if body is not None else target.read_bytes()
                    if repl:
                        raw = raw.replace(b"\n", b"\r\n")
                    target.write_bytes(prefix + raw)

                    report = self._run_validate_fast(plugin_dir)
                    self.assertFalse(report.ok(strict=False))
                    expected = "BOM UTF-8" if defect == "bom" else "CRLF"
                    self.assertTrue(
                        any(expected in err for err in report.errors),
                        f"{expected} manquant pour {rel_path}: {report.errors}",
                    )

    def test_int06_personal_path_and_file_uri_leaks_and_tests_exclusion(self) -> None:
        leak_samples = [
            ("win_bs", "C:\\" + "Users\\alice\\repo"),
            ("win_fs", "D:/" + "Users/bob/proj"),
            ("macos", "/Use" + "rs/alice/dev"),
            ("linux", "/ho" + "me/bob/work"),
            ("file_uri", "fi" + "le:///tmp/report.html"),
        ]
        for name, leak in leak_samples:
            with self.subTest(sample=name), tempfile.TemporaryDirectory() as tmp:
                plugin_dir = self._make_plugin_copy(tmp)
                (plugin_dir / "README.md").write_bytes(f"# Doc\n\nPath: {leak}\n".encode("utf-8"))

                report = self._run_validate_fast(plugin_dir)
                self.assertFalse(report.ok(strict=False), f"Fuite non détectée: {name}")

        # Chemin ~/home/... et répertoire tests/ sous plugin_dir ne doivent pas déclencher d'erreur
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            (plugin_dir / "README.md").write_bytes(("# Doc\n\nPath: ~/ho" + "me/ok\n").encode("utf-8"))
            tests_sub = plugin_dir / "tests"
            tests_sub.mkdir(exist_ok=True)
            (tests_sub / "test_fixture.py").write_bytes(
                ('SAMPLE = "' + 'C:/' + 'Users/dev/p"\nURI = "' + 'fi' + 'le:///x"\n').encode("utf-8"),
            )
            report_ok = self._run_validate_fast(plugin_dir)
            self.assertTrue(report_ok.ok(strict=True), report_ok.errors + report_ok.warnings)


if __name__ == "__main__":
    unittest.main()
