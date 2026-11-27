#!/usr/bin/env python3
"""Tests unitaires SPI-1 pour plugin/scripts/dashboard_provider.py (100 % stdlib)."""

import hashlib
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

    def test_health_view_ro_states_and_symlink_safety(self) -> None:
        mod_cfg = json.loads((PLUGIN_ROOT / "config" / "module.json").read_text(encoding="utf-8"))
        self.assertIn("health", mod_cfg["dashboard"]["views"])
        self.assertIn("live-meta", mod_cfg["dashboard"]["views"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m_home = root / "metrics_uninit"

            # 1) Uninitialized metrics_home -> WARN, strictly RO (never creates m_home)
            h_missing = _invoke(["--view", "health", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(h_missing["ok"])
            self.assertEqual(h_missing["status"], "WARN")
            self.assertEqual(h_missing["summary"], "history.jsonl/METRICS.md absent(s)")
            self.assertTrue(any("history.jsonl" in w and "/gravity-metrics" in w for w in h_missing["warnings"]))
            self.assertFalse(m_home.exists())

            # 2) Valid METRICS.md + history.jsonl -> OK with exact session count
            hist_path = m_home / "data" / "history.jsonl"
            md_path = m_home / "METRICS.md"
            hist_path.parent.mkdir(parents=True)
            md_path.write_text("# Metrics\n", encoding="utf-8")
            hist_path.write_text(
                '\ufeff{"conversation_id":"c1"}\n\n{"id":"c2"}\n',
                encoding="utf-8",
            )
            h_ok = _invoke(["--view", "health", "--metrics-home", str(m_home), "--json"])
            self.assertEqual(h_ok, {"ok": True, "status": "OK", "summary": "2 session(s)", "warnings": []})

            # 3) Corrupt JSONL / non-dict JSON -> WARN "history.jsonl invalide"
            hist_path.write_text('{"id":"c1"}\n[1, 2, 3]\n', encoding="utf-8")
            h_nondict = _invoke(["--view", "health", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(h_nondict["ok"])
            self.assertEqual(h_nondict["status"], "WARN")
            self.assertEqual(h_nondict["summary"], "history.jsonl invalide")
            self.assertTrue(h_nondict["warnings"])

            hist_path.write_text("{corrupt\n", encoding="utf-8")
            h_bad = _invoke(["--view", "health", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(h_bad["ok"])
            self.assertEqual(h_bad["status"], "WARN")
            self.assertEqual(h_bad["summary"], "history.jsonl invalide")
            self.assertTrue(h_bad["warnings"])

            # 4) Symlink refusal on history.jsonl and m_home
            hist_path.unlink()
            real_target = root / "real_history.jsonl"
            real_target.write_text('{"id":"ok"}\n', encoding="utf-8")
            try:
                hist_path.symlink_to(real_target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported in this environment")
            h_sym = _invoke(["--view", "health", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(h_sym["ok"])
            self.assertEqual(h_sym["status"], "WARN")
            self.assertTrue(any("symlink" in w.lower() for w in h_sym["warnings"]))

            hist_path.unlink()
            hist_path.write_text('{"id":"ok"}\n', encoding="utf-8")
            sym_home = root / "sym_metrics"
            sym_home.symlink_to(m_home, target_is_directory=True)
            h_sym_home = _invoke(["--view", "health", "--metrics-home", str(sym_home), "--json"])
            self.assertTrue(h_sym_home["ok"])
            self.assertEqual(h_sym_home["status"], "WARN")
            self.assertTrue(any("symlink" in w.lower() for w in h_sym_home["warnings"]))

    def test_live_meta_view_filters_worktrees_signature_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m_home = root / "metrics_home"

            def _expected_sig(home: Path) -> str:
                def _tok(p: Path) -> str:
                    try:
                        st = p.stat()
                        return f"{st.st_mtime_ns}:{st.st_size}"
                    except FileNotFoundError:
                        return "missing"

                parts = [
                    str(home.resolve()),
                    _tok(home / "active_captures.json"),
                    _tok(home / "session_meta.json"),
                    _tok(home / "data" / "history.jsonl"),
                ]
                return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]

            # 1) Uninitialized m_home -> empty maps, compatible signature, zero writes
            empty_res = _invoke(["--view", "live-meta", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(empty_res["ok"])
            self.assertEqual(empty_res["active_captures"], {})
            self.assertEqual(empty_res["session_meta"], {})
            self.assertEqual(empty_res["recorded_by_cid"], {})
            self.assertEqual(empty_res["recent_archived"], [])
            self.assertEqual(empty_res["signature"], _expected_sig(m_home))
            self.assertFalse(m_home.exists())

            # 2) active_captures + session_meta with worktree filtering
            m_home.mkdir(parents=True)
            cap_path = m_home / "active_captures.json"
            meta_path = m_home / "session_meta.json"
            cap_path.write_text(
                json.dumps({"schema_version": 1, "captures": {"cid-1": {"start_step": 2, "label": "cap-1"}}}),
                encoding="utf-8",
            )
            meta_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sessions": {
                            "cid-1": {"cwd": str(root / "gravity-metrics"), "project": "gravity-metrics"},
                            "cid-wt1": {"cwd": "/home/user/gravity-worktrees/gravity-metrics-w1", "project": "wt1"},
                            "cid-wt2": {"cwd": "/tmp/.worktrees/gravity-workflow-fix", "project": "wt2"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            res = _invoke(["--view", "live-meta", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(res["ok"])
            self.assertEqual(res["active_captures"], {"cid-1": {"start_step": 2, "label": "cap-1"}})
            self.assertEqual(list(res["session_meta"].keys()), ["cid-1"])
            self.assertEqual(res["signature"], _expected_sig(m_home))

            # 3) --since-signature fast-path
            unch = _invoke(
                ["--view", "live-meta", "--metrics-home", str(m_home), "--since-signature", res["signature"], "--json"]
            )
            self.assertEqual(unch, {"ok": True, "changed": False, "unchanged": True, "signature": res["signature"]})

            # 4) Symlink safety on session_meta.json and m_home
            meta_path.unlink()
            real_meta = root / "real_meta.json"
            real_meta.write_text(json.dumps({"schema_version": 1, "sessions": {"cid-s": {"cwd": "/tmp/ws"}}}), encoding="utf-8")
            try:
                meta_path.symlink_to(real_meta)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks not supported in this environment")
            res_sym = _invoke(["--view", "live-meta", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(res_sym["ok"])
            self.assertEqual(res_sym["active_captures"], {"cid-1": {"start_step": 2, "label": "cap-1"}})
            self.assertEqual(res_sym["session_meta"], {})
            self.assertTrue(any("symlink" in w.lower() for w in res_sym["warnings"]))

            sym_home = root / "sym_live_home"
            sym_home.symlink_to(m_home, target_is_directory=True)
            res_sym_home = _invoke(["--view", "live-meta", "--metrics-home", str(sym_home), "--json"])
            self.assertTrue(res_sym_home["ok"])
            self.assertEqual(res_sym_home["active_captures"], {})
            self.assertEqual(res_sym_home["session_meta"], {})
            self.assertEqual(res_sym_home["recorded_by_cid"], {})
            self.assertEqual(res_sym_home["recent_archived"], [])
            self.assertTrue(any("symlink" in w.lower() for w in res_sym_home["warnings"]))

    def test_live_meta_recorded_index_multi_window_and_history_invalidation(self) -> None:
        from metrics import save_history

        with tempfile.TemporaryDirectory() as tmp:
            m_home = Path(tmp) / "metrics_home"
            hist_path = m_home / "data" / "history.jsonl"

            records = [
                {
                    "conversation_id": "cid-multi-1",
                    "status": "archived",
                    "label": "multi-w1-updated",
                    "detail_slug": "multi-w1-updated",
                    "project": "ProjM",
                    "ended_at": "2026-11-27T08:00:00Z",
                    "archived_at": "2026-11-27T14:00:00Z",
                    "input_tokens": 900,
                    "cache_read_tokens": 100,
                    "output_tokens": 200,
                    "agents": 2,
                    "primary_model": "gemini-3.8-flash-tiered",
                    "current_phase": "Clôture / Enregistrée",
                },
                {
                    "conversation_id": "cid-multi-1",
                    "status": "archived",
                    "label": "multi-w2-earlier-archive",
                    "detail_slug": "multi-w2-earlier-archive",
                    "project": "ProjM",
                    "ended_at": "2026-11-27T11:00:00Z",
                    "archived_at": "2026-11-27T11:05:00Z",
                    "total_tokens": 5000,
                    "useful_tokens": 2500,
                    "agents": 3,
                    "primary_model": "gemini-3.8-flash-tiered",
                    "current_phase": "Clôture / Enregistrée",
                },
                {
                    "conversation_id": "cid-active-only",
                    "status": "active",
                    "label": "active-rec",
                    "project": "ProjM",
                    "ended_at": "2026-11-27T16:00:00Z",
                    "total_tokens": 300,
                },
            ]
            for i in range(34):
                records.append({
                    "conversation_id": f"cid-arch-{i:02d}",
                    "status": "archived",
                    "label": f"arch-{i:02d}",
                    "detail_slug": f"arch-{i:02d}",
                    "project": "ProjM",
                    "ended_at": f"2026-11-26T{i % 24:02d}:{i:02d}:00Z",
                    "archived_at": f"2026-11-26T{i % 24:02d}:{i:02d}:30Z",
                    "total_tokens": 100 + i,
                    "useful_tokens": 50 + i,
                    "agents": 1,
                    "primary_model": "gemini-3.8-flash-tiered",
                    "current_phase": "validation",
                })
            save_history(hist_path, records)

            res = _invoke(["--view", "live-meta", "--metrics-home", str(m_home), "--json"])
            self.assertTrue(res["ok"])
            rec_by_cid = res["recorded_by_cid"]
            self.assertIn("cid-multi-1", rec_by_cid)
            self.assertEqual(rec_by_cid["cid-multi-1"]["label"], "multi-w1-updated")
            self.assertEqual(rec_by_cid["cid-multi-1"]["archived_at"], "2026-11-27T14:00:00Z")
            self.assertEqual(rec_by_cid["cid-multi-1"]["total_tokens"], 1200)
            self.assertEqual(rec_by_cid["cid-multi-1"]["useful_tokens"], 1100)
            for k in (
                "label", "project", "status", "detail_slug", "ended_at", "archived_at",
                "total_tokens", "useful_tokens", "agents", "primary_model", "current_phase",
            ):
                self.assertIn(k, rec_by_cid["cid-multi-1"])

            recent = res["recent_archived"]
            self.assertEqual(len(recent), 30)
            self.assertEqual(recent[0]["conversation_id"], "cid-multi-1")
            self.assertTrue(all(item["conversation_id"] != "cid-active-only" for item in recent))

            # Modifying history.jsonl invalidates live-meta signature
            sig_before = res["signature"]
            records.append({
                "conversation_id": "cid-newest",
                "status": "archived",
                "label": "newest-run",
                "ended_at": "2026-11-27T17:00:00Z",
                "archived_at": "2026-11-27T17:01:00Z",
                "total_tokens": 42,
            })
            save_history(hist_path, records)
            res_after = _invoke(
                ["--view", "live-meta", "--metrics-home", str(m_home), "--since-signature", sig_before, "--json"]
            )
            self.assertTrue(res_after["changed"])
            self.assertNotEqual(res_after["signature"], sig_before)
            self.assertEqual(res_after["recent_archived"][0]["conversation_id"], "cid-newest")

    def test_recent_first_resolution_update_meta_slug_and_sealed_update(self) -> None:
        from metrics import resolve_session_index, save_history

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m_home = root / "metrics"
            brain = root / "brain"
            hist_path = m_home / "data" / "history.jsonl"

            # Collision test: older vs newer record with identical label/slug
            save_history(hist_path, [
                {"conversation_id": "aaaa0000-1111-2222-3333-000000000001", "label": "dup-run", "detail_slug": "dup-run", "cost_usd": 1.0, "status": "archived"},
                {"conversation_id": "aaaa0000-1111-2222-3333-000000000002", "label": "dup-run", "detail_slug": "dup-run", "cost_usd": 5.0, "status": "archived"},
            ])
            self.assertEqual(resolve_session_index(load_history(hist_path), "dup-run"), 1)
            det_dup = _invoke(["--view", "session-detail", "--metrics-home", str(m_home), "--target", "dup-run", "--json"])
            self.assertTrue(det_dup["found"])
            self.assertEqual(det_dup["record"]["cost_usd"], 5.0)
            self.assertEqual(det_dup["source"], "history_fallback")
            self.assertTrue(det_dup["summary_only"])

            # Multi-window update-meta slug & run_action("update") targeting specific window
            m_home2 = root / "metrics2"
            hist2 = m_home2 / "data" / "history.jsonl"
            cid = "bbbb1111-2222-3333-4444-000000000010"
            tpath = _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity win1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                ],
            )
            archive_conversation(
                cid, store_dir=m_home2, history_path=hist2, brain_dir=brain, catalog_path=CATALOG_PATH,
                label="Win Alpha 1", project="ProjW", from_step=0, gravity_window=True,
            )
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity win1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 10, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T10:00:00Z", "content": "/gravity win2"},
                    {"step_index": 11, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": []},
                ],
            )
            archive_conversation(
                cid, store_dir=m_home2, history_path=hist2, brain_dir=brain, catalog_path=CATALOG_PATH,
                label="Win Alpha 2", project="ProjW", from_step=10, gravity_window=True,
            )

            # session-detail by cid returns latest window (Win Alpha 2)
            det_cid = _invoke(["--view", "session-detail", "--metrics-home", str(m_home2), "--target", cid, "--json"])
            self.assertEqual(det_cid["label"], "Win Alpha 2")
            self.assertEqual(det_cid["source"], "report_json")
            self.assertFalse(det_cid["summary_only"])

            # update-meta renames Win Alpha 1 and returns new detail_slug
            upd_meta = _invoke(
                ["--action", "update-meta", "--json"],
                stdin_obj={"params": {"target": "win-alpha-1", "label": "Win Alpha One"}, "context": {"metrics_home": str(m_home2)}},
            )
            self.assertTrue(upd_meta["ok"])
            self.assertEqual(upd_meta["detail_slug"], "win-alpha-one")
            self.assertEqual(upd_meta["record"]["detail_slug"], "win-alpha-one")
            det_new = _invoke(["--view", "session-detail", "--metrics-home", str(m_home2), "--target", "win-alpha-one", "--json"])
            self.assertTrue(det_new["found"])
            self.assertEqual(det_new["label"], "Win Alpha One")
            self.assertEqual(det_new["source"], "report_json")

            # run_action("update") targeting window 1 via slug updates window 1 without clobbering window 2
            upd_w1 = _invoke(
                ["--action", "update", "--json"],
                stdin_obj={
                    "params": {"target": "win-alpha-one", "conversation_id": cid, "note": "re-sealed w1"},
                    "context": {"metrics_home": str(m_home2), "brain_dir": str(brain)},
                },
            )
            self.assertTrue(upd_w1["ok"])
            self.assertEqual(upd_w1["detail_slug"], "win-alpha-one")
            self.assertEqual(upd_w1["record"]["window_start_step"], 0)
            self.assertEqual(upd_w1["record"]["label"], "Win Alpha One")
            self.assertEqual(upd_w1["record"]["note"], "re-sealed w1")

            # Mutate window 2 and run_action("update") targeting window 2 via label
            _write_transcript(
                brain / cid,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T09:00:00Z", "content": "/gravity win1"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T09:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": []},
                    {"step_index": 10, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T10:00:00Z", "content": "/gravity win2"},
                    {"step_index": 11, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T10:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 4_000, "cache_read_tokens": 0, "output_tokens": 400, "tool_calls": []},
                ],
            )
            upd_w2 = _invoke(
                ["--action", "update", "--json"],
                stdin_obj={
                    "params": {"target": cid, "label": "Win Alpha 2"},
                    "context": {"metrics_home": str(m_home2), "brain_dir": str(brain)},
                },
            )
            self.assertTrue(upd_w2["ok"])
            self.assertEqual(upd_w2["record"]["window_start_step"], 10)
            self.assertEqual(upd_w2["record"]["input_tokens"], 4_000)
            self.assertEqual(len(load_history(hist2)), 2)

            # Missing report JSON falls back cleanly with summary_only=True
            (m_home2 / "reports" / "win-alpha-one.json").unlink()
            det_fb = _invoke(["--view", "session-detail", "--metrics-home", str(m_home2), "--target", "win-alpha-one", "--json"])
            self.assertTrue(det_fb["found"])
            self.assertEqual(det_fb["source"], "history_fallback")
            self.assertTrue(det_fb["summary_only"])
            self.assertTrue(tpath.is_file())

    def test_metrics_filters_pending_ignored_and_transcript_signature_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m_home = root / "metrics"
            brain = root / "brain"

            cid_pa = "cccc1111-2222-3333-4444-000000000001"
            cid_pb = "cccc2222-2222-3333-4444-000000000002"
            cid_ia = "cccc3333-2222-3333-4444-000000000003"
            cid_ib = "cccc4444-2222-3333-4444-000000000004"
            for cid, cmd in (
                (cid_pa, "/gravity alpha-pend"),
                (cid_pb, "/gravity beta-pend"),
                (cid_ia, "/gravity alpha-ign"),
                (cid_ib, "/gravity beta-ign"),
            ):
                _write_transcript(
                    brain / cid,
                    [
                        {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T12:00:00Z", "content": cmd},
                        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T12:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file"}]},
                    ],
                )
            record_session_meta(cid_pa, cwd=str(root / "ProjA"), project="ProjA", store_dir=m_home)
            record_session_meta(cid_ia, cwd=str(root / "ProjA"), project="ProjA", store_dir=m_home)
            record_session_meta(cid_pb, cwd=str(root / "ProjB"), project="ProjB", store_dir=m_home)
            record_session_meta(cid_ib, cwd=str(root / "ProjB"), project="ProjB", store_dir=m_home)
            ignore_session(cid_ia, reason="noise-alpha", store_dir=m_home)
            ignore_session(cid_ib, reason="noise-beta", store_dir=m_home)

            ctx = {"metrics_home": str(m_home), "brain_dir": str(brain)}
            view_a = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {"project": "ProjA"}, "context": ctx})
            self.assertEqual([p["conversation_id"] for p in view_a["pending_sessions"]], [cid_pa])
            self.assertEqual([i["conversation_id"] for i in view_a["ignored_sessions"]], [cid_ia])

            view_q = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {"q": "noise-beta"}, "context": ctx})
            self.assertEqual(view_q["pending_sessions"], [])
            self.assertEqual([i["conversation_id"] for i in view_q["ignored_sessions"]], [cid_ib])

            # Mutating transcript.jsonl in existing brain/<cid> invalidates metrics signature
            view_all = _invoke(["--view", "metrics", "--json"], stdin_obj={"params": {}, "context": ctx})
            sig_before = view_all["signature"]
            _write_transcript(
                brain / cid_pa,
                [
                    {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": "2026-11-27T12:00:00Z", "content": "/gravity alpha-pend"},
                    {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T12:00:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 1_000, "cache_read_tokens": 0, "output_tokens": 100, "tool_calls": [{"name": "edit_file"}]},
                    {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": "2026-11-27T12:01:10Z", "model": "gemini-3.8-flash-tiered", "input_tokens": 2_000, "cache_read_tokens": 0, "output_tokens": 200, "tool_calls": [{"name": "edit_file"}]},
                ],
            )
            view_mut = _invoke(
                ["--view", "metrics", "--since-signature", sig_before, "--json"],
                stdin_obj={"params": {}, "context": ctx},
            )
            self.assertTrue(view_mut["changed"])
            self.assertNotEqual(view_mut["signature"], sig_before)
            pa_item = next(p for p in view_mut["pending_sessions"] if p["conversation_id"] == cid_pa)
            self.assertEqual(pa_item["llm_calls"], 2)


if __name__ == "__main__":
    unittest.main()
