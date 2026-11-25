#!/usr/bin/env python3
"""Tests des signaux techniques, de la résolution workspace/worktrees et du CLI --json complet."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PLUGIN_ROOT = REPO_ROOT / "plugin"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    classify_workspace_kind,
    collect_run_metrics,
    extract_technical_signals,
    load_history,
    normalize_worktree_path,
    record_session_metrics,
    resolve_agent_workspace,
)
from metrics.lifecycle import record_session_meta  # noqa: E402

CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"
CLI_SCRIPT = PLUGIN_ROOT / "scripts" / "cli.py"


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    raw = "\n".join(json.dumps(item) for item in lines) + "\n"
    tpath.write_text(raw, encoding="utf-8")
    return tpath


def _write_subagent_meta(
    parent_dir: Path,
    child_id: str,
    type_name: str,
    role: str,
    spawn_step: int = 2,
    state: str = "SUBAGENT_STATE_DONE",
    workspace_uris: list = None,
) -> None:
    sub_dir = parent_dir / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversationId": child_id,
        "subagentDescriptor": {"typeName": type_name, "role": role},
        "state": state,
        "spawnStepIndex": spawn_step,
        "workspaceUris": workspace_uris or [],
    }
    (sub_dir / f"{child_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _run_cli(args: list, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )


class TestTechnicalAndWorkspace(unittest.TestCase):
    def test_worktree_normalization_and_workspace_kind(self) -> None:
        self.assertEqual(
            normalize_worktree_path("~/gravity-worktrees/gravity-workflow-w1/plugin/foo.py"),
            "gravity-workflow",
        )
        self.assertEqual(
            normalize_worktree_path("/tmp/ws/gravity-worktrees/my-custom-app-w12/src/a.py"),
            "my-custom-app",
        )
        self.assertEqual(
            normalize_worktree_path("/tmp/.gemini/antigravity/worktrees/gravity-metrics-abc123/plugin/x.py"),
            "gravity-metrics",
        )
        self.assertEqual(
            normalize_worktree_path("/tmp/.worktrees/gravity-architect-fix/README.md"),
            "gravity-architect",
        )
        self.assertEqual(
            normalize_worktree_path(
                "file:///tmp/gravity-worktrees/gravity-workflow-w3",
                is_workspace_uri=True,
            ),
            "gravity-workflow",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atelier_proj = root / "my-atelier-proj"
            atelier_proj.mkdir()
            (atelier_proj / "projects.json").write_text('{"projects":[]}\n', encoding="utf-8")

            atelier_specs = root / "my-atelier-specs"
            (atelier_specs / "specs").mkdir(parents=True)

            code_dir = root / "gravity-workflow"
            code_dir.mkdir()

            self.assertEqual(
                classify_workspace_kind("my-atelier-proj", root_workspace="my-atelier-proj", root_workspace_path=atelier_proj),
                "atelier",
            )
            self.assertEqual(
                classify_workspace_kind("my-atelier-specs", root_workspace="my-atelier-specs", root_workspace_path=atelier_specs),
                "atelier",
            )
            self.assertEqual(
                classify_workspace_kind("gravity-workflow", root_workspace="my-atelier-proj", root_workspace_path=atelier_proj),
                "code",
            )
            self.assertEqual(
                classify_workspace_kind("gravity-workflow", root_workspace="gravity-workflow", root_workspace_path=code_dir),
                "code",
            )

    def test_swarm_inherit_worker_and_ro_reviewer_workspace_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            atelier = root / "gravity-studio"
            (atelier / "specs").mkdir(parents=True)
            wf_repo = root / "gravity-workflow"
            wf_repo.mkdir()
            gm_repo = root / "gravity-metrics"
            gm_repo.mkdir()

            root_id = "root1111-1111-4111-8111-111111111111"
            worker_id = "work2222-2222-4222-8222-222222222222"
            reviewer_id = "revw3333-3333-4333-8333-333333333333"
            uri_child_id = "uric4444-4444-4444-8444-444444444444"

            record_session_meta(root_id, cwd=str(atelier), project="gravity-studio", store_dir=store)

            _write_transcript(
                brain / root_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "/swarm implémente"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "write_file", "args": {"Path": str(atelier / "specs" / "s1.md")}}, {"name": "swarm"}]},
                ],
            )
            _write_subagent_meta(
                brain / root_id,
                worker_id,
                "worker",
                "Swarm Worker (wf-builder)",
                spawn_step=1,
                workspace_uris=[atelier.resolve().as_uri()],
            )
            _write_transcript(
                brain / worker_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:10Z", "content": "Edit workflow"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:12Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "edit_file", "args": {"Path": "~/gravity-worktrees/gravity-workflow-w1/plugin/engine.py"}}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:15Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "post_message"}]},
                ],
            )
            _write_subagent_meta(
                brain / root_id,
                reviewer_id,
                "reviewer",
                "Swarm Worker (metrics-reviewer)",
                spawn_step=1,
                workspace_uris=[atelier.resolve().as_uri()],
            )
            _write_transcript(
                brain / reviewer_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:20Z", "content": "Review metrics"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:22Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [
                        {"name": "read_file", "args": {"Path": str(atelier / "specs" / "s1.md")}},
                        {"name": "read_file", "args": {"Path": str(atelier / "tasks" / "t1.md")}},
                        {"name": "read_file", "args": {"Path": str(gm_repo / "plugin" / "metrics" / "collector.py")}},
                        {"name": "read_file", "args": {"Path": str(gm_repo / "plugin" / "metrics" / "history.py")}},
                    ]},
                ],
            )
            _write_subagent_meta(
                brain / root_id,
                uri_child_id,
                "worker",
                "Swarm Worker (uri-only)",
                spawn_step=1,
                workspace_uris=["file:///tmp/gravity-worktrees/gravity-architect-w2"],
            )
            _write_transcript(
                brain / uri_child_id,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:30Z", "content": "URI worker"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:32Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": []},
                ],
            )

            m = collect_run_metrics(root_id, brain, CATALOG_PATH, store_dir=store)
            by_ws = m["by_workspace"]
            self.assertIn("gravity-studio", by_ws)
            self.assertIn("gravity-workflow", by_ws)
            self.assertIn("gravity-metrics", by_ws)
            self.assertIn("gravity-architect", by_ws)
            self.assertEqual(by_ws["gravity-studio"]["workspace_kind"], "atelier")
            self.assertEqual(by_ws["gravity-workflow"]["workspace_kind"], "code")
            self.assertEqual(by_ws["gravity-metrics"]["workspace_kind"], "code")
            self.assertEqual(by_ws["gravity-architect"]["workspace_kind"], "code")
            self.assertEqual(by_ws["gravity-studio"]["agents"], 1)
            self.assertEqual(by_ws["gravity-workflow"]["agents"], 1)

            # Priorité 1: bridge_workspace prime sur tout
            self.assertEqual(
                resolve_agent_workspace(
                    {"bridge_workspace": "gravity", "workspace_uris": []},
                    {"write_paths": ["~/gravity-worktrees/gravity-workflow-w1/a.py"]},
                    root_workspace="gravity-studio",
                ),
                "gravity",
            )
            # Priorité 2: git -C / cd dans run_command
            self.assertEqual(
                resolve_agent_workspace(
                    {"workspace_uris": []},
                    {"shell_dirs": ["../gravity-workflow"]},
                    root_workspace="gravity-studio",
                    root_workspace_path=atelier,
                ),
                "gravity-workflow",
            )

    def test_technical_signals_and_history_compatibility(self) -> None:
        steps = [
            {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "Go"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:01Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 50_000, "cache_read_tokens": 0, "output_tokens": 100, "thinking": "a" * 40},
            {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:03Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 20_000, "cache_read_tokens": 0, "output_tokens": 100, "thinking": "b" * 20},
            {"step_index": 3, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:15:00Z", "content": "/compact"},
            {"step_index": 4, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:15:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 50},
        ]
        sig = extract_technical_signals(steps)
        self.assertEqual(sig["thinking_calls"], 2)
        self.assertEqual(sig["thinking_chars"], 60)
        self.assertEqual(sig["thinking_est_tokens"], 15)
        self.assertEqual(sig["compaction_count"], 2)
        # Deltas bornés <= 600s : 1000ms, 2000ms, 10000ms (897s exclu)
        self.assertEqual(sig["latency_p50_ms"], 2000)
        self.assertEqual(sig["latency_p95_ms"], 10000)
        self.assertAlmostEqual(sig["latency_avg_ms"], 4333.3, places=1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            hist = store / "data" / "history.jsonl"
            cid = "tech5555-5555-4555-8555-555555555555"
            _write_transcript(brain / cid, steps)
            m = collect_run_metrics(cid, brain, CATALOG_PATH, project="gravity-metrics", store_dir=store)
            self.assertEqual(m["summary"]["thinking_est_tokens"], 15)
            self.assertEqual(m["summary"]["compaction_count"], 2)
            self.assertEqual(m["summary"]["latency_p95_ms"], 10000)
            self.assertEqual(m["technical_signals"]["thinking_calls"], 2)

            rec, _ = record_session_metrics(m, store_dir=store, history_path=hist, label="tech-run")
            report_json = json.loads((store / "reports" / f"{rec['detail_slug']}.json").read_text(encoding="utf-8"))
            self.assertEqual(report_json["summary"]["thinking_est_tokens"], 15)
            self.assertEqual(report_json["technical_signals"]["latency_p95_ms"], 10000)

            loaded = load_history(hist)[0]
            self.assertEqual(loaded["thinking_est_tokens"], 15)
            self.assertEqual(loaded["compaction_count"], 2)
            self.assertEqual(loaded["latency_p95_ms"], 10000)
            self.assertEqual(loaded["workspace_kinds"], {"gravity-metrics": "code"})
            for legacy_key in (
                "status",
                "project",
                "ended_at",
                "error_rate_pct",
                "cache_hit_rate_pct",
                "max_context_tokens",
                "duplicate_spawns",
                "rewind_calls",
                "interrupted_agents",
                "cost_usd",
                "cost_output_usd",
            ):
                self.assertIn(legacy_key, loaded)

    def test_cli_json_all_subcommands_and_metadata_ignore_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            store = root / "store"
            ws = root / "gravity-metrics"
            ws.mkdir()
            cid = "cli66666-6666-4666-8666-666666666666"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T11:00:00Z", "content": "/gravity fix cli"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T11:00:04Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5000, "cache_read_tokens": 0, "output_tokens": 120, "thinking": "plan" * 10, "tool_calls": [{"name": "edit_file", "args": {"Path": "plugin/scripts/cli.py"}}]},
                ],
            )
            env_extra = {"GRAVITY_METRICS_HOME": str(store), "ANTIGRAVITY_BRAIN_DIR": str(brain)}
            base_args = ["--store", str(store)]

            p_start = _run_cli(["start", cid[:8], "-l", "cap-1", "--step", "0", "--workspace", str(ws), "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(p_start.returncode, 0, p_start.stderr)
            d_start = json.loads(p_start.stdout)
            self.assertTrue(d_start["ok"])
            self.assertEqual(d_start["capture"]["label"], "cap-1")

            p_ign = _run_cli(["ignore", cid[:8], "-r", "pause", "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(p_ign.returncode, 0)
            self.assertTrue(json.loads(p_ign.stdout)["ok"])
            p_pend_hid = _run_cli(["pending", "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(json.loads(p_pend_hid.stdout), [])

            p_unign = _run_cli(["unignore", cid[:8], "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(p_unign.returncode, 0)
            self.assertTrue(json.loads(p_unign.stdout)["removed"])
            p_pend_vis = _run_cli(["pending", "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(len(json.loads(p_pend_vis.stdout)), 1)

            p_rec = _run_cli(["record", cid[:8], "--brain-dir", str(brain), "--workspace", str(ws), *base_args, "--json"], env_extra)
            self.assertEqual(p_rec.returncode, 0, p_rec.stderr)
            d_rec = json.loads(p_rec.stdout)
            self.assertTrue(d_rec["ok"])
            self.assertEqual(d_rec["record"]["label"], "cap-1")

            p_upd = _run_cli(["record", cid[:8], "--update-sealed", "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(p_upd.returncode, 0, p_upd.stderr)
            self.assertTrue(json.loads(p_upd.stdout)["ok"])

            p_meta = _run_cli(["update-meta", "cap-1", "--label", "cap-renamed", "--project", "gravity-metrics", "--note", "edited", *base_args, "--json"], env_extra)
            self.assertEqual(p_meta.returncode, 0, p_meta.stderr)
            d_meta = json.loads(p_meta.stdout)
            self.assertEqual(d_meta["record"]["label"], "cap-renamed")
            self.assertEqual(d_meta["record"]["note"], "edited")

            p_list = _run_cli(["list", *base_args, "--json"], env_extra)
            self.assertEqual(p_list.returncode, 0)
            self.assertEqual(json.loads(p_list.stdout)["count"], 1)

            p_insp = _run_cli(["inspect", "cap-renamed", "--brain-dir", str(brain), *base_args, "--json"], env_extra)
            self.assertEqual(p_insp.returncode, 0, p_insp.stderr)
            self.assertEqual(json.loads(p_insp.stdout)["metrics"]["summary"]["thinking_est_tokens"], 10)

            p_rm = _run_cli(["remove", "cap-renamed", "--purge-reports", *base_args, "--json"], env_extra)
            self.assertEqual(p_rm.returncode, 0)
            self.assertEqual(json.loads(p_rm.stdout)["remaining"], 0)

            p_err = _run_cli(["remove", "missing-id", *base_args, "--json"], env_extra)
            self.assertNotEqual(p_err.returncode, 0)
            d_err = json.loads(p_err.stdout)
            self.assertFalse(d_err["ok"])
            self.assertTrue(bool(d_err["error"]))

            p_add = _run_cli(["add", cid[:8], "-l", "cap-added", "--brain-dir", str(brain), "--workspace", str(ws), *base_args, "--json"], env_extra)
            self.assertEqual(p_add.returncode, 0, p_add.stderr)
            d_add = json.loads(p_add.stdout)
            self.assertTrue(d_add["ok"])
            self.assertEqual(d_add["record"]["label"], "cap-added")


if __name__ == "__main__":
    unittest.main()
