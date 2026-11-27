#!/usr/bin/env python3
"""Tests unitaires SPI-1 pour plugin/scripts/dashboard_provider.py (100 % stdlib)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
PROVIDER_SCRIPT = PLUGIN_ROOT / "scripts" / "dashboard_provider.py"
CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    archive_conversation,
    ignore_session,
    load_active_captures,
    load_history,
    load_ignored_sessions,
    load_session_meta,
    record_session_meta,
    start_active_capture,
)


def _write_transcript(conv_dir: Path, lines: list) -> Path:
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return tpath


def _invoke(args: list, *, stdin_obj: dict = None, env_extra: dict = None) -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-B", str(PROVIDER_SCRIPT), *args],
        input=json.dumps(stdin_obj) if stdin_obj is not None else "",
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"dashboard_provider failed (rc={proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout)


class TestDashboardProviderSpi1(unittest.TestCase):
    def test_metrics_view_session_detail_audit_detail_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g_home = root / "gravity_home"
            m_home = g_home / "metrics"
            brain = root / "brain"
            ws = root / "ProjetDash"
            (ws / "audits" / "agents").mkdir(parents=True)
            (ws / "audits" / "code").mkdir(parents=True)
            (ws / "audits" / "agents" / "README.md").write_text("# Index Audits\n\nVerdict : PASS\n", encoding="utf-8")
            (ws / "audits" / "agents" / "2026-11-26-revue.md").write_text("# Revue Agent\n\nVerdict : PASS\n", encoding="utf-8")
            (ws / "audits" / "code" / "secret-code.md").write_text("# Code Secret\n\nVerdict : FAIL\n", encoding="utf-8")

            cid_rec = "11111111-aaaa-bbbb-cccc-000000000001"
            cid_pend = "22222222-aaaa-bbbb-cccc-000000000002"
            _write_transcript(
                brain / cid_rec,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T10:00:00Z", "content": "/gravity build api"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 12_000, "cache_read_tokens": 8_000, "output_tokens": 500, "tool_calls": []},
                ],
            )
            _write_transcript(
                brain / cid_pend,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-26T11:00:00Z", "content": "/gravity test ui"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-26T11:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 6_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            record_session_meta(cid_pend, cwd=str(ws), project="ProjetDash", store_dir=m_home)
            start_active_capture(cid_pend, start_step=0, label="pend-cap", project="ProjetDash", store_dir=m_home)

            archive_conversation(
                cid_rec, store_dir=m_home, history_path=m_home / "data" / "history.jsonl",
                brain_dir=brain, catalog_path=CATALOG_PATH, label="dash-run", project="ProjetDash",
            )
            # Inject raw trajectory into report JSON to verify stripping
            rpt_path = m_home / "reports" / "dash-run.json"
            rpt_obj = json.loads(rpt_path.read_text(encoding="utf-8"))
            rpt_obj["raw_trajectory"] = [{"secret": True}]
            if rpt_obj.get("agents"):
                rpt_obj["agents"][0]["transcript"] = [{"huge": True}]
            rpt_path.write_text(json.dumps(rpt_obj), encoding="utf-8")

            env_extra = {"GRAVITY_HOME": str(g_home), "ANTIGRAVITY_BRAIN_DIR": str(brain)}
            env_extra.pop("GRAVITY_METRICS_HOME", None)

            # 1) --view metrics (all required keys + audits/code excluded)
            view_res = _invoke(
                ["--view", "metrics", "--json"],
                stdin_obj={"params": {"project": "ProjetDash", "limit": 10}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            required_keys = {
                "changed", "unchanged", "signature", "metrics_home", "metrics_md_uri",
                "filters", "total_records", "total_matching", "truncated", "kpis",
                "projects", "by_project", "records", "active_captures",
                "pending_sessions", "ignored_sessions", "audits", "index_audit",
                "index_audits", "warnings",
            }
            self.assertTrue(required_keys.issubset(view_res.keys()), required_keys - set(view_res))
            self.assertTrue(view_res["changed"])
            self.assertFalse(view_res["unchanged"])
            self.assertEqual(view_res["total_matching"], 1)
            self.assertEqual(len(view_res["pending_sessions"]), 1)
            self.assertEqual([a["filename"] for a in view_res["audits"]], ["2026-11-26-revue.md"])
            self.assertEqual(view_res["index_audit"]["filename"], "README.md")

            # 2) --since-signature unchanged fast-path
            unch = _invoke(
                ["--view", "metrics", "--since-signature", view_res["signature"], "--json"],
                stdin_obj={"params": {"project": "ProjetDash", "limit": 10}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            self.assertEqual(unch, {"changed": False, "unchanged": True, "signature": view_res["signature"]})

            # 3) --view session-detail (required keys + trajectory stripped)
            det = _invoke(
                ["--view", "session-detail", "--json"],
                stdin_obj={"params": {"target": "dash-run"}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            for k in ("found", "source", "target", "slug", "conversation_id", "label", "report_md_uri", "detail", "record", "warnings"):
                self.assertIn(k, det)
            self.assertTrue(det["found"])
            self.assertEqual(det["source"], "report_json")
            self.assertNotIn("raw_trajectory", det["detail"])
            if det["detail"].get("agents"):
                self.assertNotIn("transcript", det["detail"]["agents"][0])

            # 4) --view audit-detail (allows agents audit, blocks audits/code & traversal)
            aud_ok = _invoke(
                ["--view", "audit-detail", "--json"],
                stdin_obj={"params": {"path": "2026-11-26-revue.md"}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            self.assertTrue(aud_ok["found"])
            self.assertIn("Revue Agent", aud_ok["content"])
            for bad_aud in ("code/secret-code.md", "../code/secret-code.md"):
                aud_bad = _invoke(
                    ["--view", "audit-detail", "--json"],
                    stdin_obj={"params": {"path": bad_aud}, "context": {"metrics_home": str(m_home)}},
                    env_extra=env_extra,
                )
                self.assertFalse(aud_bad["found"])

            # 5) Actions: ignore -> unignore -> update-meta -> record -> remove
            ign = _invoke(
                ["--action", "ignore", "--json"],
                stdin_obj={"params": {"target": cid_pend[:8], "reason": "noise"}, "context": {"metrics_home": str(m_home), "brain_dir": str(brain)}},
                env_extra=env_extra,
            )
            self.assertTrue(ign["ok"])
            self.assertIn(cid_pend, load_ignored_sessions(m_home))

            unign = _invoke(
                ["--action", "unignore", "--json"],
                stdin_obj={"params": {"target": cid_pend[:8]}, "context": {"metrics_home": str(m_home), "brain_dir": str(brain)}},
                env_extra=env_extra,
            )
            self.assertTrue(unign["ok"])
            self.assertNotIn(cid_pend, load_ignored_sessions(m_home))

            upd_m = _invoke(
                ["--action", "update-meta", "--json"],
                stdin_obj={"params": {"target": "dash-run", "label": "dash-renamed", "note": "updated"}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            self.assertTrue(upd_m["ok"])
            self.assertEqual(upd_m["record"]["label"], "dash-renamed")

            rec_act = _invoke(
                ["--action", "record", "--json"],
                stdin_obj={"params": {"target": cid_pend, "label": "pend-recorded", "project": "ProjetDash"}, "context": {"metrics_home": str(m_home), "brain_dir": str(brain)}},
                env_extra=env_extra,
            )
            self.assertTrue(rec_act["ok"])
            self.assertEqual(len(load_history(m_home / "data" / "history.jsonl")), 2)

            rm_act = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": "pend-recorded", "purge_reports": True}, "context": {"metrics_home": str(m_home)}},
                env_extra=env_extra,
            )
            self.assertTrue(rm_act["ok"])
            self.assertFalse(rm_act["already_absent"])
            self.assertTrue(rm_act["cleaned_sources"]["history"])
            self.assertEqual(rm_act["remaining"], 1)

    def test_pending_excludes_session_meta_subagents_and_non_gravity_and_surfaces_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g_home = root / "gravity_home"
            m_home = g_home / "metrics"
            brain = root / "brain"

            cid_grav = "aaaa1111-aaaa-bbbb-cccc-000000000001"
            cid_sub = "bbbb2222-aaaa-bbbb-cccc-000000000002"
            cid_nongrav = "cccc3333-aaaa-bbbb-cccc-000000000003"
            cid_meta_only = "dddd4444-aaaa-bbbb-cccc-000000000004"
            cid_orphan_cap = "eeee5555-aaaa-bbbb-cccc-000000000005"
            cid_orphan_ign = "ffff6666-aaaa-bbbb-cccc-000000000006"

            _write_transcript(
                brain / cid_grav,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T08:00:00Z", "content": "/gravity build feature"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T08:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 5_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "edit_file"}]},
                ],
            )
            _write_transcript(
                brain / cid_sub,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T08:01:00Z", "content": "[GRAVITY-BRIDGE] child worker"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T08:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file"}]},
                ],
            )
            _write_transcript(
                brain / cid_nongrav,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T08:02:00Z", "content": "Simple question sans gravity"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T08:02:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 3_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file"}]},
                ],
            )

            for cid in (cid_grav, cid_sub, cid_nongrav, cid_meta_only):
                record_session_meta(cid, cwd=str(root / "ProjA"), project="ProjA", store_dir=m_home)
            start_active_capture(cid_orphan_cap, start_step=0, label="orphan-cap", project="ProjA", store_dir=m_home)
            ignore_session(cid_orphan_ign, reason="old-orphan", store_dir=m_home)

            env_extra = {"GRAVITY_HOME": str(g_home)}
            ctx = {"metrics_home": str(m_home), "brain_dir": str(brain)}
            view = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {}, "context": ctx}, env_extra=env_extra)

            pending_by_cid = {p["conversation_id"]: p for p in view["pending_sessions"]}
            ignored_by_cid = {i["conversation_id"]: i for i in view["ignored_sessions"]}

            self.assertEqual(set(pending_by_cid), {cid_grav, cid_orphan_cap})
            self.assertTrue(pending_by_cid[cid_grav]["brain_exists"])
            self.assertFalse(pending_by_cid[cid_grav]["orphan"])
            self.assertFalse(pending_by_cid[cid_orphan_cap]["brain_exists"])
            self.assertTrue(pending_by_cid[cid_orphan_cap]["orphan"])

            self.assertEqual(set(ignored_by_cid), {cid_orphan_ign})
            self.assertFalse(ignored_by_cid[cid_orphan_ign]["brain_exists"])
            self.assertTrue(ignored_by_cid[cid_orphan_ign]["orphan"])
            for excluded in (cid_sub, cid_nongrav, cid_meta_only):
                self.assertNotIn(excluded, pending_by_cid)
                self.assertNotIn(excluded, ignored_by_cid)

            # Removing brain_dir triggers safe fallback (still zero session_meta pollution)
            missing_brain = root / "missing_brain"
            fb_view = _invoke(
                ["--view", "metrics", "--json"],
                stdin_obj={"params": {}, "context": {"metrics_home": str(m_home), "brain_dir": str(missing_brain)}},
                env_extra=env_extra,
            )
            self.assertEqual({p["conversation_id"] for p in fb_view["pending_sessions"]}, {cid_orphan_cap})
            self.assertEqual({i["conversation_id"] for i in fb_view["ignored_sessions"]}, {cid_orphan_ign})
            self.assertFalse(fb_view["pending_sessions"][0]["brain_exists"])
            self.assertTrue(fb_view["pending_sessions"][0]["orphan"])

            # Removing orphan capture & orphan ignored cleans them completely
            rm_cap = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": cid_orphan_cap}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(rm_cap["ok"])
            self.assertFalse(rm_cap["already_absent"])
            self.assertTrue(rm_cap["cleaned_sources"]["active_captures"])

            rm_ign = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": cid_orphan_ign}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(rm_ign["ok"])
            self.assertFalse(rm_ign["already_absent"])
            self.assertTrue(rm_ign["cleaned_sources"]["ignored"])

            view_after = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {}, "context": ctx}, env_extra=env_extra)
            self.assertEqual([p["conversation_id"] for p in view_after["pending_sessions"]], [cid_grav])
            self.assertEqual(view_after["ignored_sessions"], [])
            self.assertNotIn(cid_orphan_cap, load_active_captures(m_home))
            self.assertNotIn(cid_orphan_ign, load_ignored_sessions(m_home))

            # Brain dir appearance/mutation invalidates signature via _stat_token(brain_dir)
            sig_before = view_after["signature"]
            cid_new = "99990000-aaaa-bbbb-cccc-000000000009"
            _write_transcript(
                brain / cid_new,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T08:05:00Z", "content": "/gravity new"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T08:05:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "write_file"}]},
                ],
            )
            os.utime(brain, None)
            view_new = _invoke(
                ["--view", "metrics", "--since-signature", sig_before, "--json"],
                stdin_obj={"params": {}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(view_new["changed"])
            self.assertNotEqual(view_new["signature"], sig_before)
            self.assertIn(cid_new, {p["conversation_id"] for p in view_new["pending_sessions"]})

    def test_remove_detail_slug_without_resurrection_and_idempotent_ghost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g_home = root / "gravity_home"
            m_home = g_home / "metrics"
            brain = root / "brain"

            cid = "77778888-aaaa-bbbb-cccc-000000000007"
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity mixed"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 9_000, "cache_read_tokens": 0, "output_tokens": 300, "tool_calls": [{"name": "edit_file"}]},
                ],
            )
            record_session_meta(cid, cwd=str(root / "ProjMix"), project="ProjMix", store_dir=m_home)
            saved_rec, _, _ = archive_conversation(
                cid, store_dir=m_home, history_path=m_home / "data" / "history.jsonl",
                brain_dir=brain, catalog_path=CATALOG_PATH, label="Dash Run Mixed", project="ProjMix",
            )
            slug = saved_rec["detail_slug"]
            self.assertNotEqual(slug, "Dash Run Mixed")
            # Simulate leftover ignored/meta entries + deleted brain folder
            ignore_session(cid, reason="stale-ignore", store_dir=m_home)
            for p in sorted((brain / cid).rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            (brain / cid).rmdir()

            env_extra = {"GRAVITY_HOME": str(g_home)}
            ctx = {"metrics_home": str(m_home), "brain_dir": str(brain)}

            rm_res = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": slug, "conversation_id": cid}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(rm_res["ok"])
            self.assertFalse(rm_res["already_absent"])
            self.assertEqual(rm_res["remaining"], 0)
            self.assertTrue(rm_res["cleaned_sources"]["history"])
            self.assertTrue(rm_res["cleaned_sources"]["reports"])
            self.assertTrue(rm_res["cleaned_sources"]["ignored"])
            self.assertTrue(rm_res["cleaned_sources"]["session_meta"])
            self.assertFalse((m_home / "reports" / f"{slug}.json").exists())
            self.assertFalse((m_home / "reports" / f"{slug}.md").exists())
            self.assertNotIn(cid, load_session_meta(m_home))
            self.assertNotIn(cid, load_ignored_sessions(m_home))

            # Zero resurrection in get_metrics_view
            view_clean = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {}, "context": ctx}, env_extra=env_extra)
            self.assertEqual(view_clean["total_records"], 0)
            self.assertEqual(view_clean["pending_sessions"], [])
            self.assertEqual(view_clean["ignored_sessions"], [])

            # Idempotent remove on already-removed slug & completely unknown ghost
            rm_again = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": slug, "conversation_id": cid}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(rm_again["ok"])
            self.assertTrue(rm_again["already_absent"])
            self.assertEqual(rm_again["remaining"], 0)
            self.assertFalse(any(rm_again["cleaned_sources"].values()))

            rm_ghost = _invoke(
                ["--action", "remove", "--json"],
                stdin_obj={"params": {"target": "ghost-session-absent"}, "context": ctx},
                env_extra=env_extra,
            )
            self.assertTrue(rm_ghost["ok"])
            self.assertTrue(rm_ghost["already_absent"])
            self.assertEqual(rm_ghost["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
