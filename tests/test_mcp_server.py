#!/usr/bin/env python3
"""Tests bout-en-bout stdio JSON-RPC 2.0 du serveur MCP App Gravity Metrics (100 % stdlib)."""

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
SERVER_SCRIPT = PLUGIN_ROOT / "mcp" / "server.py"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import collect_run_metrics, record_session_metrics  # noqa: E402


def _write_transcript(conv_dir: Path, lines: list) -> None:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def _call_mcp_stdio(
    messages: list, store_dir: Optional[Path], brain_dir: Path, extra_env: dict = None
) -> list:
    env = dict(os.environ)
    if store_dir is not None:
        env["GRAVITY_METRICS_HOME"] = str(store_dir)
    else:
        env.pop("GRAVITY_METRICS_HOME", None)
    env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, "-B", str(SERVER_SCRIPT)],
        input=payload,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(PLUGIN_ROOT),
        env=env,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"MCP server failed (rc={proc.returncode}): {proc.stderr}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


class TestMcpServer(unittest.TestCase):
    def _seed_store(self, store: Path, brain: Path) -> None:
        c1 = "11111111-aaaa-bbbb-cccc-000000000001"
        c2 = "22222222-aaaa-bbbb-cccc-000000000002"
        _write_transcript(
            brain / c1,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T10:00:00Z", "content": "/gravity alpha"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 20_000, "output_tokens": 500, "tool_calls": []},
            ],
        )
        _write_transcript(
            brain / c2,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-21T11:00:00Z", "content": "/gravity beta résumé"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-21T11:00:12Z", "model": "gemini-4-neon", "input_tokens": 15_000, "cache_read_tokens": 30_000, "output_tokens": 800, "tool_calls": []},
            ],
        )
        history_path = store / "data" / "history.jsonl"
        m1 = collect_run_metrics(c1, brain, CATALOG_PATH, label="alpha-run", project="ProjA")
        record_session_metrics(m1, store_dir=store, history_path=history_path, label="alpha-run", note="note alpha")
        m2 = collect_run_metrics(c2, brain, CATALOG_PATH, label="beta-run", project="ProjB")
        record_session_metrics(m2, store_dir=store, history_path=history_path, label="beta-run", note="accent éàü")

    def test_protocol_handshake_resources_and_tools_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"

            responses = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                    {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}},
                    {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "ui://gravity-metrics/dashboard.html"}},
                ],
                store,
                brain,
            )
            # notifications/initialized ne produit aucune réponse
            self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4, 5])
            by_id = {r["id"]: r["result"] for r in responses}

            expected_version = json.loads(
                (PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8")
            )["version"]
            self.assertEqual(expected_version, "0.5.1")
            self.assertEqual(by_id[1]["serverInfo"]["name"], "gravity-metrics")
            self.assertEqual(by_id[1]["serverInfo"]["version"], expected_version)
            self.assertEqual(by_id[2], {})

            tools = {t["name"]: t for t in by_id[3]["tools"]}
            self.assertIn("open_metrics_dashboard", tools)
            open_meta = tools["open_metrics_dashboard"]["_meta"]
            self.assertEqual(open_meta["ui"]["resourceUri"], "ui://gravity-metrics/dashboard.html")
            self.assertEqual(open_meta["ui"]["visibility"], ["model", "app"])
            eps = open_meta["com.google.antigravity/ui"]["entrypoints"]
            self.assertEqual({e["type"] for e in eps}, {"aux_pane", "global"})
            view_enum = tools["open_metrics_dashboard"]["inputSchema"]["properties"]["view"]["enum"]
            self.assertEqual(view_enum, ["overview", "sessions", "reports", "pending", "compare", "audits", "history", "detail"])
            for helper_name in (
                "metrics_get_state",
                "metrics_get_session",
                "metrics_archive_session",
                "metrics_record_session",
                "metrics_update_session_meta",
                "metrics_ignore_session",
                "metrics_unignore_session",
                "metrics_remove_session",
                "metrics_list_audits",
                "metrics_read_audit",
            ):
                self.assertEqual(tools[helper_name]["_meta"]["ui"]["visibility"], ["app"])
            arch_props = tools["metrics_archive_session"]["inputSchema"]["properties"]
            self.assertEqual(arch_props["update_sealed"]["type"], "boolean")
            self.assertEqual(arch_props["update"]["type"], "boolean")
            self.assertIn("Enregistrer", tools["metrics_archive_session"]["title"])
            self.assertIn("Enregistrer", tools["metrics_record_session"]["title"])
            self.assertIn("Audits Agents", tools["metrics_list_audits"]["title"])
            self.assertIn("audits/agents/", tools["metrics_list_audits"]["description"])
            self.assertIn("Audit Agent", tools["metrics_read_audit"]["title"])
            self.assertIn("audits/agents/", tools["metrics_read_audit"]["description"])

            res_list = by_id[4]["resources"]
            self.assertEqual(len(res_list), 1)
            self.assertEqual(res_list[0]["uri"], "ui://gravity-metrics/dashboard.html")
            self.assertEqual(res_list[0]["mimeType"], "text/html;profile=mcp-app")

            content0 = by_id[5]["contents"][0]
            self.assertEqual(content0["mimeType"], "text/html;profile=mcp-app")
            html_txt = content0["text"]
            for required_token in (
                "ui/initialize",
                "ui/notifications/initialized",
                "ui/notifications/tool-result",
                "ui/notifications/host-context-changed",
                "com.google.antigravity/toggle-aux-pane",
                "ui/update-model-context",
                "ui/message",
                "Synthèse",
                "Sessions",
                "Enregistrées",
                "À enregistrer",
                "Enregistrer…",
                "Mettre à jour",
                "update-recorded-session",
                "updateRecordedSession",
                "Aucun projet enregistré.",
                "enregistrement 2.0.0 — repli projet",
                "Audits Agents",
                "audits/agents/",
                "Comparer",
                "Index &amp; Matrice",
                "renderSafeMarkdown",
                "renderProjectShareBars",
                "renderWorkspaceSharePanel",
            ):
                self.assertIn(required_token, html_txt)
            self.assertIn(f'version: "{expected_version}"', html_txt)
            self.assertIn("rec.bridge_children_count", html_txt)
            self.assertIn("pill bridge", html_txt)
            self.assertNotIn("confirmForceArchiveCid", html_txt)
            self.assertNotIn("has_pending_bridge_jobs", html_txt)
            self.assertNotIn("window.confirm", html_txt)
            self.assertNotIn("https://", html_txt)
            self.assertNotIn("http://", html_txt)

    def test_tools_call_lifecycle_and_cp1252_resilience(self) -> None:
        cp1252_env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            self._seed_store(store, brain)

            # 1) Lancement manuel {}, lancement projet, lancement détail ciblé, et get_state
            r1 = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {"project": "ProjA"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {"project": "ProjB", "target": "beta-run", "view": "detail"}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                ],
                store,
                brain,
                extra_env=cp1252_env,
            )
            by_id = {r["id"]: r["result"] for r in r1}

            sc_global = by_id[1]["structuredContent"]
            self.assertEqual(sc_global["initial_scope"], "global")
            self.assertEqual(sc_global["initial_project"], "")
            self.assertEqual(sc_global["initial_view"], "overview")
            self.assertEqual(len(sc_global["records"]), 2)
            self.assertEqual(sc_global["projects"], ["ProjA", "ProjB"])

            sc_proj = by_id[2]["structuredContent"]
            self.assertEqual(sc_proj["initial_scope"], "project")
            self.assertEqual(sc_proj["initial_project"], "ProjA")
            self.assertEqual(sc_proj["initial_view"], "overview")

            sc_det = by_id[3]["structuredContent"]
            self.assertEqual(sc_det["initial_scope"], "project")
            self.assertEqual(sc_det["initial_project"], "ProjB")
            self.assertEqual(sc_det["initial_view"], "detail")
            self.assertEqual(sc_det["initial_target"], "beta-run")
            self.assertEqual(sc_det["initial_detail"]["label"], "beta-run")
            self.assertTrue(sc_det["initial_detail_md_uri"].startswith("file:///"))

            sig = by_id[4]["structuredContent"]["signature"]
            self.assertTrue(by_id[4]["structuredContent"]["changed"])

            # 2) Court-circuit signature inchangée, get_session valide/invalide, et remove avec purge
            r2 = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {"since_signature": sig}}},
                    {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "metrics_get_session", "arguments": {"target": "alpha-run"}}},
                    {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "metrics_get_session", "arguments": {"target": "inexistante"}}},
                    {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "metrics_remove_session", "arguments": {"target": "alpha-run", "purge_reports": True}}},
                ],
                store,
                brain,
                extra_env=cp1252_env,
            )
            by_id2 = {r["id"]: r["result"] for r in r2}

            self.assertFalse(by_id2[10]["structuredContent"]["changed"])
            self.assertEqual(by_id2[11]["structuredContent"]["detail"]["label"], "alpha-run")
            self.assertTrue(by_id2[11]["structuredContent"]["report_md_uri"].endswith("alpha-run.md"))
            self.assertTrue(by_id2[12]["isError"])

            rm_sc = by_id2[13]["structuredContent"]
            self.assertEqual(rm_sc["remaining"], 1)
            self.assertEqual(rm_sc["removed"]["label"], "alpha-run")
            self.assertFalse((store / "reports" / "alpha-run.json").exists())
            self.assertFalse((store / "reports" / "alpha-run.md").exists())
            self.assertTrue((store / "reports" / "beta-run.json").is_file())

    def test_case_insensitive_project_deduplication_and_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            history_path = store / "data" / "history.jsonl"

            c1 = "33333333-aaaa-bbbb-cccc-000000000001"
            c2 = "44444444-aaaa-bbbb-cccc-000000000002"
            for cid, ts, lbl in ((c1, "2026-11-21T10:00:00Z", "g-low"), (c2, "2026-11-21T11:00:00Z", "g-cap")):
                _write_transcript(
                    brain / cid,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": ts, "content": f"/gravity {lbl}"},
                        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": ts, "model": "gemini-4-neon", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    ],
                )
            m1 = collect_run_metrics(c1, brain, CATALOG_PATH, label="g-low", project="gravity")
            record_session_metrics(m1, store_dir=store, history_path=history_path, label="g-low")
            # Simule une ancienne entrée mixte déjà présente dans history.jsonl
            m2 = collect_run_metrics(c2, brain, CATALOG_PATH, label="g-cap", project="Gravity")
            from metrics.history import load_history, save_history, to_session_record, upsert_session

            recs = upsert_session(load_history(history_path), to_session_record(m2, label="g-cap"))
            save_history(history_path, recs)

            resps = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {"project": "Gravity"}}},
                ],
                store,
                brain,
            )
            by_id = {r["id"]: r["result"] for r in resps}
            sc_all = by_id[1]["structuredContent"]
            self.assertEqual(sc_all["projects"], ["gravity"])

            sc_proj = by_id[2]["structuredContent"]
            self.assertEqual(sc_proj["projects"], ["gravity"])
            self.assertEqual(sc_proj["initial_project"], "gravity")
            self.assertEqual(sc_proj["initial_scope"], "project")
            self.assertIn("projet 'gravity' : 2 session(s) / 2 au total", by_id[2]["content"][0]["text"])

    def test_mcp_server_uses_gravity_home_metrics_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grav_home = root / "grav-home"
            store = grav_home / "metrics"
            brain = root / "brain"
            self._seed_store(store, brain)

            resps = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                ],
                None,
                brain,
                extra_env={"GRAVITY_HOME": str(grav_home)},
            )
            sc = resps[0]["result"]["structuredContent"]
            self.assertEqual(len(sc["records"]), 2)
            self.assertEqual(sc["projects"], ["ProjA", "ProjB"])

    def test_pending_sessions_and_archive_tool_and_historical_bridge_ro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            history_path = store / "data" / "history.jsonl"

            cid_root = "55555555-aaaa-bbbb-cccc-000000000005"
            cid_legacy_child = "66666666-aaaa-bbbb-cccc-000000000006"
            cid_hist = "77777777-aaaa-bbbb-cccc-000000000007"
            _write_transcript(
                brain / cid_root,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T14:00:00Z", "content": "/gravity-workflow run"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-22T14:00:10Z",
                        "model": "gemini-3.8-flash-tiered",
                        "input_tokens": 10_000,
                        "cache_read_tokens": 20_000,
                        "output_tokens": 400,
                        "tool_calls": [{"name": "write_file"}],
                    },
                ],
            )
            _write_transcript(
                brain / cid_legacy_child,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-22T14:00:20Z", "content": "[GRAVITY-BRIDGE | thread=thr-20261122-mcp1]"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-22T14:00:30Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )

            # Historique RO pré-existant (#01-#04) conservant bridge_children_count et bridge_workspaces
            history_path.parent.mkdir(parents=True, exist_ok=True)
            hist_rec = {
                "schema_version": "2.1.0",
                "status": "archived",
                "label": "legacy-audit-04",
                "detail_slug": "legacy-audit-04",
                "conversation_id": cid_hist,
                "project": "ProjHist",
                "cost_usd": 1.25,
                "cost_2027_usd": 1.25,
                "cache_hit_rate_pct": 80.0,
                "llm_calls": 10,
                "agents": 3,
                "wall_clock_s": 120.0,
                "bridge_children_count": 2,
                "bridge_workspaces": ["gravity-architect", "gravity-workflow"],
            }
            history_path.write_text(json.dumps(hist_rec) + "\n", encoding="utf-8")

            resps = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_archive_session", "arguments": {"conversation_id": cid_root[:8], "label": "mcp-run"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "metrics_archive_session", "arguments": {"conversation_id": cid_root[:8], "label": "mcp-run", "force": True}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "metrics_archive_session", "arguments": {"conversation_id": cid_root[:8], "label": "mcp-other", "force": False}}},
                ],
                store,
                brain,
            )
            by_id = {r["id"]: r["result"] for r in resps}

            state_sc = by_id[1]["structuredContent"]
            pend = state_sc["pending_sessions"]
            self.assertEqual([p["conversation_id"] for p in pend], [cid_root])
            hist_by_proj = {p["project"]: p for p in state_sc["by_project"]}
            self.assertEqual(hist_by_proj["ProjHist"]["bridge_sessions"], 1)
            self.assertEqual(state_sc["records"][0]["bridge_children_count"], 2)

            # Archivage direct sans garde-fou Bridge
            self.assertFalse(by_id[2]["isError"])
            arch_sc = by_id[2]["structuredContent"]
            self.assertEqual(arch_sc["archived"]["label"], "mcp-run")
            self.assertEqual(arch_sc["archived"]["agents"], 1)
            self.assertEqual(arch_sc["pending_sessions"], [])

            self.assertFalse(by_id[3]["isError"])
            self.assertEqual(by_id[3]["structuredContent"]["archived"]["label"], "mcp-run")

            self.assertFalse(by_id[4]["isError"])
            self.assertTrue(by_id[4]["structuredContent"]["already_archived"])
            self.assertEqual(by_id[4]["structuredContent"]["archived"]["label"], "mcp-run")

    def test_by_project_and_audits_mcp_tools_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            audits_base = root / "studio" / "audits"
            agents_dir = audits_base / "agents"
            code_dir = audits_base / "code"
            agents_dir.mkdir(parents=True)
            code_dir.mkdir(parents=True)
            self._seed_store(store, brain)

            (agents_dir / "README.md").write_text(
                "# Matrice Audits Agents\n\nVerdict : PASS\n\nIndex principal.\n",
                encoding="utf-8",
            )
            (agents_dir / "2026-11-22-audit-alpha.md").write_text(
                "# Audit Alpha\n\nVerdict : NON BLOQUANT\n\nConcerne 11111111-aaaa-bbbb-cccc-000000000001.\n",
                encoding="utf-8",
            )
            (code_dir / "2026-11-22-audit-code-01.md").write_text(
                "# Audit Code Interdit\n\nVerdict : BLOQUANT\n\nConcerne 22222222-aaaa-bbbb-cccc-000000000002.\n",
                encoding="utf-8",
            )
            (agents_dir / "ignore.txt").write_text("texte brut", encoding="utf-8")
            (root / "secret.md").write_text("# Secret\n", encoding="utf-8")

            env_extra = {"GRAVITY_AUDITS_DIR": str(audits_base)}
            resps = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {"view": "audits"}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "metrics_list_audits", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "studio:2026-11-22-audit-alpha.md"}}},
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "studio:../secret.md"}}},
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "studio:ignore.txt"}}},
                    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "studio:../code/2026-11-22-audit-code-01.md"}}},
                    {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "studio:code/2026-11-22-audit-code-01.md"}}},
                ],
                store,
                brain,
                extra_env=env_extra,
            )
            by_id = {r["id"]: r["result"] for r in resps}

            sc_open = by_id[1]["structuredContent"]
            self.assertEqual(sc_open["initial_view"], "audits")
            self.assertEqual(sc_open["audits_count"], 1)
            self.assertEqual(sc_open["index_audit"]["filename"], "README.md")
            by_proj = sc_open["by_project"]
            self.assertEqual({p["project"] for p in by_proj}, {"ProjA", "ProjB"})
            self.assertAlmostEqual(sum(p["share_pct"] for p in by_proj), 100.0, delta=0.2)
            self.assertIn("11111111-aaaa-bbbb-cccc-000000000001", sc_open["audit_links_by_session"])
            self.assertNotIn("22222222-aaaa-bbbb-cccc-000000000002", sc_open["audit_links_by_session"])

            sc_state = by_id[2]["structuredContent"]
            self.assertEqual(sc_state["audits_count"], 1)
            self.assertEqual(len(sc_state["by_project"]), 2)

            sc_list = by_id[3]["structuredContent"]
            self.assertEqual(sc_list["count"], 1)
            self.assertEqual(sc_list["index_audit"]["filename"], "README.md")
            self.assertEqual(
                {a["filename"] for a in sc_list["audits"]},
                {"2026-11-22-audit-alpha.md"},
            )

            self.assertFalse(by_id[4]["isError"])
            audit_obj = by_id[4]["structuredContent"]["audit"]
            self.assertEqual(audit_obj["workspace"], "studio")
            self.assertEqual(audit_obj["title"], "Audit Alpha")
            self.assertEqual(audit_obj["verdict_tone"], "ok")
            self.assertEqual(audit_obj["linked_sessions"][0]["label"], "alpha-run")

            self.assertTrue(by_id[5]["isError"])
            self.assertTrue(by_id[6]["isError"])
            self.assertTrue(by_id[7]["isError"])
            self.assertTrue(by_id[8]["isError"])

            # GRAVITY_AUDITS_DIR pointant directement sur audits/code => count == 0
            resps_code = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {"view": "audits"}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_list_audits", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "metrics_read_audit", "arguments": {"audit_id": "2026-11-22-audit-code-01.md"}}},
                ],
                store,
                brain,
                extra_env={"GRAVITY_AUDITS_DIR": str(code_dir)},
            )
            by_id_code = {r["id"]: r["result"] for r in resps_code}
            self.assertEqual(by_id_code[1]["structuredContent"]["audits_count"], 0)
            self.assertEqual(by_id_code[2]["structuredContent"]["count"], 0)
            self.assertEqual(by_id_code[2]["structuredContent"]["audits"], [])
            self.assertTrue(by_id_code[3]["isError"])

    def test_initialize_ui_capability_warning_matrix(self) -> None:
        from mcp.server import (  # noqa: E402
            _inspect_client_ui_support,
            _reset_host_client_state,
            handle_jsonrpc_message,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            self._seed_store(store, brain)

            # 1) Client avec capability MCP Apps déclarée -> zéro [WARN UI]
            res_ui = _call_mcp_stdio(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {
                                "extensions": {
                                    "io.modelcontextprotocol/ui": {
                                        "mimeTypes": ["text/html;profile=mcp-app"]
                                    }
                                }
                            },
                            "clientInfo": {"name": "Antigravity", "version": "2.34.0"},
                        },
                    },
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                ],
                store,
                brain,
            )
            open_ui = {r["id"]: r["result"] for r in res_ui}[2]
            self.assertFalse(open_ui["isError"])
            self.assertNotIn("[WARN UI]", open_ui["content"][0]["text"])

            # 2) Clients identifiés sans support MCP Apps (agy CLI & Antigravity < 2.34.0) -> [WARN UI] + isError=False
            for client_info, caps, expected_desc in (
                ({"name": "agy", "version": "1.2.0"}, {}, "agy 1.2.0"),
                ({"name": "Antigravity", "version": "2.33.0"}, {"roots": {}}, "Antigravity 2.33.0"),
            ):
                res_no_ui = _call_mcp_stdio(
                    [
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": caps,
                                "clientInfo": client_info,
                            },
                        },
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                    ],
                    store,
                    brain,
                )
                open_no_ui = {r["id"]: r["result"] for r in res_no_ui}[2]
                self.assertFalse(open_no_ui["isError"])
                txt = open_no_ui["content"][0]["text"]
                self.assertIn("[WARN UI]", txt)
                self.assertIn(expected_desc, txt)
                self.assertIn("v2.34.0+", txt)
                self.assertEqual(len(open_no_ui["structuredContent"]["records"]), 2)

            # 3) Handshake minimal sans capabilities, capabilities {} sans clientInfo, ou appel direct sans initialize -> zéro [WARN UI]
            for init_params in (
                {"protocolVersion": "2025-06-18"},
                {"protocolVersion": "2025-06-18", "capabilities": {}},
            ):
                res_min = _call_mcp_stdio(
                    [
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init_params},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                    ],
                    store,
                    brain,
                )
                open_min = {r["id"]: r["result"] for r in res_min}[2]
                self.assertFalse(open_min["isError"])
                self.assertNotIn("[WARN UI]", open_min["content"][0]["text"])

            res_direct = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
                ],
                store,
                brain,
            )
            self.assertFalse(res_direct[0]["result"]["isError"])
            self.assertNotIn("[WARN UI]", res_direct[0]["result"]["content"][0]["text"])

            # 4) Couverture directe des variantes capabilities & isolation _reset_host_client_state
            self.assertTrue(_inspect_client_ui_support({"capabilities": {"experimental": {"com.google.antigravity/ui": True}}})["ui_supported"])
            self.assertTrue(_inspect_client_ui_support({"capabilities": {"ui": True}})["ui_supported"])
            self.assertIs(_inspect_client_ui_support({"capabilities": {}, "clientInfo": {"name": "Antigravity", "version": "2.33.9"}})["ui_supported"], False)
            self.assertIsNone(_inspect_client_ui_support({"capabilities": {}})["ui_supported"])

            old_env = os.environ.get("GRAVITY_METRICS_HOME")
            old_brain = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
            try:
                os.environ["GRAVITY_METRICS_HOME"] = str(store)
                os.environ["ANTIGRAVITY_BRAIN_DIR"] = str(brain)
                _reset_host_client_state()
                r_no_init = handle_jsonrpc_message(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}}
                )
                self.assertNotIn("[WARN UI]", r_no_init["result"]["content"][0]["text"])

                handle_jsonrpc_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "initialize",
                        "params": {"capabilities": {}, "clientInfo": {"name": "agy", "version": "0.9.0"}},
                    }
                )
                r_warn = handle_jsonrpc_message(
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}}
                )
                self.assertIn("[WARN UI]", r_warn["result"]["content"][0]["text"])

                _reset_host_client_state()
                r_after_reset = handle_jsonrpc_message(
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}}
                )
                self.assertNotIn("[WARN UI]", r_after_reset["result"]["content"][0]["text"])
            finally:
                _reset_host_client_state()
                if old_env is None:
                    os.environ.pop("GRAVITY_METRICS_HOME", None)
                else:
                    os.environ["GRAVITY_METRICS_HOME"] = old_env
                if old_brain is None:
                    os.environ.pop("ANTIGRAVITY_BRAIN_DIR", None)
                else:
                    os.environ["ANTIGRAVITY_BRAIN_DIR"] = old_brain

    def test_r5_freshness_cache_invalidation_and_update_sealed_mcp(self) -> None:
        from unittest import mock  # noqa: E402
        from mcp import server as mcp_server  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "store"
            brain = root / "brain"
            history_path = store / "data" / "history.jsonl"

            cid_rec = "88888888-aaaa-bbbb-cccc-000000000008"
            cid_pend = "99999999-aaaa-bbbb-cccc-000000000009"

            rec_entries = [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:00:00Z", "content": "/gravity-workflow build feature"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 10_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "write_file"}]},
            ]
            _write_transcript(brain / cid_rec, rec_entries)
            _write_transcript(
                brain / cid_pend,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:05:00Z", "content": "/gravity-workflow pend"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "write_file"}]},
                ],
            )

            # 1) Cache polling invalidation when subagents/*.json and tasks/task-*.log evolve
            sig_before = mcp_server._compute_pending_source_sig(brain, store, history_path)
            sub_dir = brain / cid_pend / ".system_generated" / "subagents"
            sub_dir.mkdir(parents=True, exist_ok=True)
            (sub_dir / "sub-1.json").write_text(json.dumps({"conversationId": "sub-1"}), encoding="utf-8")
            sig_after_sub = mcp_server._compute_pending_source_sig(brain, store, history_path)
            self.assertNotEqual(sig_before, sig_after_sub)

            tasks_dir = brain / cid_pend / ".system_generated" / "tasks"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            (tasks_dir / "task-1.log").write_text("- [agent:builder] DONE\n", encoding="utf-8")
            sig_after_task = mcp_server._compute_pending_source_sig(brain, store, history_path)
            self.assertNotEqual(sig_after_sub, sig_after_task)

            # 2) Enregistrement initial via metrics_record_session
            res_init = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_record_session", "arguments": {"conversation_id": cid_rec[:8], "label": "fresh-run", "project": "ProjFresh"}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_get_session", "arguments": {"target": "fresh-run"}}},
                ],
                store,
                brain,
            )
            by_id = {r["id"]: r["result"] for r in res_init}
            self.assertFalse(by_id[1]["isError"])
            self.assertEqual(by_id[1]["structuredContent"]["recorded"]["label"], "fresh-run")
            self.assertFalse(by_id[2]["structuredContent"]["freshness"]["has_new_activity"])
            self.assertEqual(by_id[2]["structuredContent"]["freshness"]["new_llm_calls_delta"], 0)
            raw_hist_before = history_path.read_text(encoding="utf-8")

            # 3) Fast-path stat() skips _get_transcript_index when transcript mtime < archived_at
            tpath_rec = brain / cid_rec / ".system_generated" / "logs" / "transcript.jsonl"
            old_stat_rec = {
                "conversation_id": cid_rec,
                "ended_at": "2026-11-25T10:00:10Z",
                "archived_at": "2099-01-01T00:00:00Z",
                "root_last_step_index": 1,
            }
            with mock.patch.object(mcp_server, "_get_transcript_index", side_effect=AssertionError("fast-path should skip")) as _:
                fast_fresh = mcp_server._compute_recorded_freshness(old_stat_rec, brain)
                self.assertFalse(fast_fresh["has_new_activity"])
                self.assertEqual(fast_fresh["new_llm_calls_delta"], 0)

            # 4) Ajout d'un tour /gravity-metrics (ignoré) + 2 nouveaux PLANNER_RESPONSE hors /gravity-metrics
            _write_transcript(
                brain / cid_rec,
                rec_entries
                + [
                    {"step_index": 2, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:01:00Z", "content": "/gravity-metrics"},
                    {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:01:05Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 50, "tool_calls": [{"name": "open_metrics_dashboard"}]},
                    {"step_index": 4, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-25T10:02:00Z", "content": "Ajoute les tests"},
                    {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "write_file"}]},
                    {"step_index": 6, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-25T10:02:20Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 150, "tool_calls": []},
                ],
            )
            self.assertTrue(tpath_rec.is_file())

            res_poll = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_get_session", "arguments": {"target": "fresh-run"}}},
                ],
                store,
                brain,
            )
            by_poll = {r["id"]: r["result"] for r in res_poll}
            rec_state = by_poll[1]["structuredContent"]["records"][0]
            self.assertTrue(rec_state["has_new_activity"])
            self.assertEqual(rec_state["new_llm_calls_delta"], 2)
            self.assertEqual(by_poll[2]["structuredContent"]["freshness"], {"has_new_activity": True, "new_llm_calls_delta": 2})
            # history.jsonl strictement non muté lors des lectures
            self.assertEqual(history_path.read_text(encoding="utf-8"), raw_hist_before)

            # 5) Mise à jour via metrics_archive_session(update_sealed=True) sans doublon
            res_upd = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_archive_session", "arguments": {"conversation_id": cid_rec[:8], "label": "fresh-run", "update_sealed": True}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "metrics_get_session", "arguments": {"target": "fresh-run"}}},
                ],
                store,
                brain,
            )
            by_upd = {r["id"]: r["result"] for r in res_upd}
            self.assertFalse(by_upd[1]["isError"])
            self.assertTrue(by_upd[1]["structuredContent"]["updated_sealed"])
            self.assertEqual(len(by_upd[1]["structuredContent"]["records"]), 1)
            self.assertFalse(by_upd[1]["structuredContent"]["records"][0]["has_new_activity"])
            self.assertEqual(by_upd[1]["structuredContent"]["records"][0]["new_llm_calls_delta"], 0)
            self.assertGreater(by_upd[1]["structuredContent"]["records"][0]["llm_calls"], 1)
            self.assertFalse(by_upd[2]["structuredContent"]["freshness"]["has_new_activity"])

            # 6) Détection nouveau subagents/*.json post-archived_at même sans nouveau PLANNER_RESPONSE
            rec_sub_dir = brain / cid_rec / ".system_generated" / "subagents"
            rec_sub_dir.mkdir(parents=True, exist_ok=True)
            (rec_sub_dir / "child-late.json").write_text(
                json.dumps({"conversationId": "child-late", "spawnStepIndex": 99}),
                encoding="utf-8",
            )
            res_sub = _call_mcp_stdio(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "metrics_get_state", "arguments": {}}},
                ],
                store,
                brain,
            )
            self.assertTrue(res_sub[0]["result"]["structuredContent"]["records"][0]["has_new_activity"])

            # 7) Vérification _fast_unchanged_signature invalidation directe en mémoire
            old_env = os.environ.get("GRAVITY_METRICS_HOME")
            old_brain = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
            try:
                os.environ["GRAVITY_METRICS_HOME"] = str(store)
                os.environ["ANTIGRAVITY_BRAIN_DIR"] = str(brain)
                mcp_server._STATE_CACHE.clear()
                snap1 = mcp_server._load_state_snapshot(store, history_path)
                self.assertEqual(mcp_server._fast_unchanged_signature(store, history_path, snap1["signature"]), snap1["signature"])
                (tasks_dir / "task-2.log").write_text("- [agent:reviewer] PASS\n", encoding="utf-8")
                self.assertIsNone(mcp_server._fast_unchanged_signature(store, history_path, snap1["signature"]))
            finally:
                mcp_server._STATE_CACHE.clear()
                if old_env is None:
                    os.environ.pop("GRAVITY_METRICS_HOME", None)
                else:
                    os.environ["GRAVITY_METRICS_HOME"] = old_env
                if old_brain is None:
                    os.environ.pop("ANTIGRAVITY_BRAIN_DIR", None)
                else:
                    os.environ["ANTIGRAVITY_BRAIN_DIR"] = old_brain


if __name__ == "__main__":
    unittest.main()
