#!/usr/bin/env python3
"""Tests unitaires pour plugin/metrics/audits_reader.py (découverte, sécurité anti-traversal, 256 Ko, liens)."""

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    MAX_AUDIT_BYTES,
    build_audit_links_by_session,
    discover_audit_roots,
    list_audits,
    list_audits_bundle,
    read_audit,
)


def _create_workspaces_bridge_db(db_path: Path, workspaces: list) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE workspaces (slug TEXT PRIMARY KEY, canonical_path TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL)"
    )
    for slug, cpath, dname in workspaces:
        con.execute("INSERT INTO workspaces VALUES (?, ?, ?)", (slug, str(cpath), dname))
    con.commit()
    con.close()


class TestAuditsReader(unittest.TestCase):
    def test_discovery_precedence_and_bridge_ro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_audits = root / "env-audits"
            env_audits.mkdir()
            (env_audits / "README.md").write_text("# Index Env\n", encoding="utf-8")

            ws_local = root / "local-ws"
            (ws_local / "audits" / "agents").mkdir(parents=True)
            (ws_local / "audits" / "agents" / "local.md").write_text("# Audit Local\n", encoding="utf-8")
            (ws_local / "audits" / "code").mkdir(parents=True)
            (ws_local / "audits" / "code" / "ignored-local.md").write_text("# Audit Code Local\n", encoding="utf-8")

            ws_bridge = root / "bridge-ws"
            (ws_bridge / "audits" / "agents").mkdir(parents=True)
            (ws_bridge / "audits" / "agents" / "bridge.md").write_text("# Audit Bridge\n", encoding="utf-8")
            (ws_bridge / "audits" / "code").mkdir(parents=True)
            (ws_bridge / "audits" / "code" / "ignored-bridge.md").write_text("# Audit Code Bridge\n", encoding="utf-8")

            db_path = root / "bridge" / "bridge.db"
            _create_workspaces_bridge_db(
                db_path,
                [("bridge-ws", ws_bridge, "Bridge WS"), ("missing-ws", root / "missing", "Missing")],
            )

            # 1) $GRAVITY_AUDITS_DIR prioritaire et isolé
            with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": str(env_audits)}, clear=False):
                roots_env = discover_audit_roots(
                    workspace_dirs=[ws_local],
                    bridge_db=db_path,
                    include_cwd=False,
                )
                self.assertEqual(len(roots_env), 1)
                self.assertEqual(roots_env[0]["path"], env_audits.resolve())

            # 2) Sans $GRAVITY_AUDITS_DIR : uniquement <workspace>/audits/agents/ (audits/code/ ignoré, zéro lecture SQLite bridge.db)
            with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": ""}, clear=False):
                roots_ws = discover_audit_roots(
                    workspace_dirs=[ws_local],
                    bridge_db=db_path,
                    include_cwd=False,
                )
                self.assertEqual(
                    [r["path"] for r in roots_ws],
                    [
                        (ws_local / "audits" / "agents").resolve(),
                    ],
                )
                audits_ws, _ = list_audits(
                    workspace_dirs=[ws_local],
                    bridge_db=db_path,
                    include_cwd=False,
                )
                self.assertEqual({a["filename"] for a in audits_ws}, {"local.md"})
                self.assertEqual(
                    discover_audit_roots(workspace_dirs=[], bridge_db=db_path, include_cwd=False),
                    [],
                )

            # 3) Base bridge.db absente tolérée sans création
            missing_db = root / "absent" / "bridge.db"
            with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": ""}, clear=False):
                roots_no_db = discover_audit_roots(
                    workspace_dirs=[],
                    bridge_db=missing_db,
                    include_cwd=False,
                )
                self.assertEqual(roots_no_db, [])
                self.assertFalse(missing_db.exists())

    def test_security_anti_traversal_md_only_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audits_dir = root / "ws" / "audits"
            audits_dir.mkdir(parents=True)
            (audits_dir / "valid.md").write_text("# Audit Valide\n\nContenu OK.\n", encoding="utf-8")
            (audits_dir / "notes.txt").write_text("pas un markdown\n", encoding="utf-8")
            secret_md = root / "secret.md"
            secret_md.write_text("# Secret hors périmètre\n", encoding="utf-8")

            link_path = audits_dir / "escape.md"
            symlink_created = False
            try:
                link_path.symlink_to(secret_md)
                symlink_created = True
            except OSError:
                symlink_created = False

            audits, _ = list_audits(audits_dir=audits_dir, include_cwd=False)
            # notes.txt et symlink escape.md doivent être exclus
            self.assertEqual([a["filename"] for a in audits], ["valid.md"])

            valid_id = audits[0]["audit_id"]
            loaded = read_audit(valid_id, audits_dir=audits_dir, include_cwd=False)
            self.assertEqual(loaded["title"], "Audit Valide")

            root_id = valid_id.split(":", 1)[0]
            bad_targets = [
                "",
                "../secret.md",
                f"{root_id}:../secret.md",
                f"{root_id}:sub/../../secret.md",
                f"{root_id}:notes.txt",
                "notes.txt",
                "/etc/passwd",
                "C:\\Windows\\win.ini",
                f"{root_id}:valid.md\x00.txt",
            ]
            if symlink_created:
                bad_targets.append(f"{root_id}:escape.md")

            for bad in bad_targets:
                with self.assertRaises((ValueError, FileNotFoundError), msg=f"Rejet attendu pour {bad!r}"):
                    read_audit(bad, audits_dir=audits_dir, include_cwd=False)

    def test_truncation_256kb_metadata_and_session_cross_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audits_dir = root / "gravity-studio" / "audits"
            audits_dir.mkdir(parents=True)

            cid_full = "aaaaaaaa-1111-2222-3333-444455556666"
            cid_pref = "bbbbbbbb-9999-8888-7777-666655554444"
            records = [
                {
                    "conversation_id": cid_full,
                    "label": "run-full-uuid",
                    "project": "gravity-metrics",
                    "cost_usd": 0.42,
                    "cost_2027_usd": 0.31,
                    "cache_hit_rate_pct": 85.0,
                    "started_at": "2026-11-22T10:00:00Z",
                },
                {
                    "conversation_id": cid_pref,
                    "label": "run-prefix-uuid",
                    "project": "gravity-studio",
                    "cost_usd": 0.15,
                    "cost_2027_usd": 0.12,
                    "cache_hit_rate_pct": 90.0,
                    "started_at": "2026-11-22T11:00:00Z",
                },
            ]

            (audits_dir / "README.md").write_text(
                "# Matrice des Audits\n\nStatut : PASS (non bloquant)\n\nIndex principal.\n",
                encoding="utf-8",
            )
            (audits_dir / "2026-11-22-revue.md").write_text(
                f"# Revue Architecture\n\n**Verdict :** BLOQUANT\n\n"
                f"Session complète `{cid_full}` et préfixe `{cid_pref[:8]}` analysés.\n",
                encoding="utf-8",
            )
            big_text = "# Gros Audit\n\nVerdict : WARN\n\n" + ("A" * (MAX_AUDIT_BYTES + 2048))
            (audits_dir / "2026-11-21-huge.md").write_text(big_text, encoding="utf-8")

            audits_bundle = list_audits_bundle(records=records, audits_dir=audits_dir, include_cwd=False)
            audits, roots = list_audits(records=records, audits_dir=audits_dir, include_cwd=False)
            self.assertEqual(len(roots), 1)
            # README.md exclu de audits / compteur / audit_links_by_session, exposé dans index_audit
            self.assertEqual(len(audits), 2)
            self.assertIsNotNone(audits_bundle.get("index_audit"))
            self.assertEqual(audits_bundle["index_audit"]["filename"], "README.md")
            self.assertEqual(audits_bundle["index_audit"]["verdict_tone"], "ok")
            self.assertEqual(audits[0]["filename"], "2026-11-22-revue.md")
            self.assertEqual(audits[0]["verdict"], "BLOQUANT")
            self.assertEqual(audits[0]["verdict_tone"], "danger")
            self.assertEqual(
                {s["label"] for s in audits[0]["linked_sessions"]},
                {"run-full-uuid", "run-prefix-uuid"},
            )

            links_by_sess = build_audit_links_by_session(audits)
            self.assertIn(cid_full, links_by_sess)
            self.assertIn(cid_pref, links_by_sess)
            self.assertEqual(
                links_by_sess[cid_full][0]["filename"] if "filename" in links_by_sess[cid_full][0] else links_by_sess[cid_full][0]["title"],
                "Revue Architecture",
            )

            huge_loaded = read_audit(audits[1]["audit_id"], records=records, audits_dir=audits_dir, include_cwd=False)
            self.assertTrue(huge_loaded["truncated"])
            self.assertGreater(huge_loaded["size_bytes"], MAX_AUDIT_BYTES)
            self.assertEqual(len(huge_loaded["content"].encode("utf-8")), MAX_AUDIT_BYTES)
            self.assertEqual(huge_loaded["verdict_tone"], "warn")

    def test_agents_scoping_relative_paths_and_code_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "gravity-studio"
            agents_dir = ws / "audits" / "agents"
            code_dir = ws / "audits" / "code"
            agents_dir.mkdir(parents=True)
            code_dir.mkdir(parents=True)

            (agents_dir / "2026-11-22-audit-01.md").write_text(
                "# Audit Agent 01\n\nVerdict : PASS\n",
                encoding="utf-8",
            )
            # Un fichier nommé code.md directement dans agents/ reste légitime
            (agents_dir / "code.md").write_text(
                "# Audit Code Name\n\nVerdict : PASS\n",
                encoding="utf-8",
            )
            (code_dir / "2026-11-22-audit-code-01.md").write_text(
                "# Audit Code Interdit\n\nVerdict : BLOQUANT\n",
                encoding="utf-8",
            )

            db_path = root / "bridge" / "bridge.db"
            missing_db = root / "absent" / "bridge.db"
            _create_workspaces_bridge_db(db_path, [("gravity-studio", ws, "Gravity Studio")])

            # 1) workspace_dirs, include_cwd=True et bridge_db ciblent uniquement audits/agents/
            old_cwd = Path.cwd()
            try:
                os.chdir(ws)
                with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": ""}, clear=False):
                    roots_cwd = discover_audit_roots(bridge_db=missing_db, include_cwd=True)
                    self.assertEqual(len(roots_cwd), 1)
                    self.assertEqual(roots_cwd[0]["path"], agents_dir.resolve())
                    self.assertEqual(roots_cwd[0]["workspace"], "gravity-studio")

                    audits_ws, _ = list_audits(workspace_dirs=[ws], bridge_db=missing_db, include_cwd=False)
                    self.assertEqual(
                        {a["filename"] for a in audits_ws},
                        {"2026-11-22-audit-01.md", "code.md"},
                    )

                    audits_bridge, _ = list_audits(bridge_db=db_path, include_cwd=False)
                    self.assertEqual(audits_bridge, [])

                    # Chemins relatifs (".", "audits", "audits/agents", workspace_dirs=["."])
                    roots_rel_ws = discover_audit_roots(workspace_dirs=["."], bridge_db=missing_db, include_cwd=False)
                    self.assertEqual(roots_rel_ws[0]["workspace"], "gravity-studio")
                    self.assertEqual(roots_rel_ws[0]["path"], agents_dir.resolve())

                    roots_rel_audits = discover_audit_roots(audits_dir="audits", include_cwd=False)
                    self.assertEqual(roots_rel_audits[0]["workspace"], "gravity-studio")
                    self.assertEqual(roots_rel_audits[0]["path"], agents_dir.resolve())

                    roots_rel_agents = discover_audit_roots(audits_dir="audits/agents", include_cwd=False)
                    self.assertEqual(roots_rel_agents[0]["workspace"], "gravity-studio")
                    self.assertEqual(roots_rel_agents[0]["path"], agents_dir.resolve())

                os.chdir(agents_dir)
                roots_dot_agents = discover_audit_roots(audits_dir=".", include_cwd=False)
                self.assertEqual(roots_dot_agents[0]["workspace"], "gravity-studio")
                self.assertEqual(roots_dot_agents[0]["path"], agents_dir.resolve())

                os.chdir(code_dir)
                self.assertEqual(discover_audit_roots(audits_dir=".", include_cwd=False), [])
            finally:
                os.chdir(old_cwd)

            # 2) $GRAVITY_AUDITS_DIR=<ws>/audits bascule vers <ws>/audits/agents sans code/
            with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": str(ws / "audits")}, clear=False):
                audits_env, roots_env = list_audits(include_cwd=False)
                self.assertEqual(len(roots_env), 1)
                self.assertEqual(roots_env[0]["workspace"], "gravity-studio")
                self.assertEqual(
                    {a["filename"] for a in audits_env},
                    {"2026-11-22-audit-01.md", "code.md"},
                )

            # 3) Rejet explicite de audits_dir=<ws>/audits/code
            self.assertEqual(discover_audit_roots(audits_dir=code_dir, include_cwd=False), [])
            audits_code_only, roots_code_only = list_audits(audits_dir=code_dir, include_cwd=False)
            self.assertEqual(audits_code_only, [])
            self.assertEqual(roots_code_only, [])
            with self.assertRaises(FileNotFoundError) as ctx:
                read_audit("2026-11-22-audit-code-01.md", audits_dir=code_dir, include_cwd=False)
            self.assertIn("audits/agents/", str(ctx.exception))

            with self.assertRaises((ValueError, FileNotFoundError)):
                read_audit(
                    "gravity-studio:../code/2026-11-22-audit-code-01.md",
                    workspace_dirs=[ws],
                    include_cwd=False,
                )

            # 4) Exclusion défensive d'un sous-dossier code/ sous audits_dir explicite sans agents/
            flat_env = root / "env-audits-flat"
            (flat_env / "code").mkdir(parents=True)
            (flat_env / "sub" / "code").mkdir(parents=True)
            (flat_env / "README.md").write_text("# Index Plat\n", encoding="utf-8")
            (flat_env / "code.md").write_text("# Fichier code.md autorisé\n", encoding="utf-8")
            (flat_env / "code" / "2026-11-22-audit-code-01.md").write_text("# Code 1\n", encoding="utf-8")
            (flat_env / "sub" / "code" / "nested.md").write_text("# Code 2\n", encoding="utf-8")

            flat_audits, flat_roots = list_audits(audits_dir=flat_env, include_cwd=False)
            flat_bundle = list_audits_bundle(audits_dir=flat_env, include_cwd=False)
            self.assertEqual({a["filename"] for a in flat_audits}, {"code.md"})
            self.assertIsNotNone(flat_bundle.get("index_audit"))
            self.assertEqual(flat_bundle["index_audit"]["filename"], "README.md")
            flat_root_id = flat_roots[0]["root_id"]
            with self.assertRaises(ValueError):
                read_audit(f"{flat_root_id}:code/2026-11-22-audit-code-01.md", audits_dir=flat_env, include_cwd=False)
            with self.assertRaises(ValueError):
                read_audit(f"{flat_root_id}:sub/code/nested.md", audits_dir=flat_env, include_cwd=False)
            ok_code_file = read_audit(f"{flat_root_id}:code.md", audits_dir=flat_env, include_cwd=False)
            self.assertEqual(ok_code_file["title"], "Fichier code.md autorisé")

    def test_v050_exclusions_and_verdicts_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audits_dir = root / "audits" / "agents"
            audits_dir.mkdir(parents=True)
            (audits_dir / "_TEMPLATE.md").write_text("# Audit #XX\nVerdict : PASS\n", encoding="utf-8")
            (audits_dir / "_brouillon.md").write_text("# Brouillon\nVerdict : FAIL\n", encoding="utf-8")
            (audits_dir / "README.md").write_text("# Index Audits\nVerdict : OK\n", encoding="utf-8")
            samples = [
                ("2026-11-23-a.md", "# A\n**Verdict global :** PARTIAL — 2 réserves mineures\n", "PARTIAL", "warn"),
                ("2026-11-23-b.md", "# B\n**Verdict:** WARN - attention requise\n", "WARN", "warn"),
                ("2026-11-23-c.md", "# C\nVerdict : UNTESTED | non exécuté\n", "UNTESTED", "neutral"),
                ("2026-11-23-d.md", "# D\nVerdict global : **OK** — conforme\n", "OK", "ok"),
                ("2026-11-23-e.md", "# E\n**Verdict :** KO — échec critique\n", "KO", "danger"),
                ("2026-11-23-f.md", "# F\n## Verdict global\n**PASS**\n", "PASS", "ok"),
            ]
            for fname, body, _, _ in samples:
                (audits_dir / fname).write_text(body, encoding="utf-8")

            bundle = list_audits_bundle(audits_dir=audits_dir, include_cwd=False)
            audits = bundle["audits"]
            self.assertEqual(len(audits), len(samples))
            self.assertIsNotNone(bundle["index_audit"])
            self.assertEqual(bundle["index_audit"]["filename"], "README.md")
            by_name = {a["filename"]: a for a in audits}
            self.assertNotIn("_TEMPLATE.md", by_name)
            self.assertNotIn("_brouillon.md", by_name)
            self.assertNotIn("README.md", by_name)
            for fname, _, exp_verdict, exp_tone in samples:
                self.assertEqual(by_name[fname]["verdict"], exp_verdict)
                self.assertEqual(by_name[fname]["verdict_tone"], exp_tone)
            # _TEMPLATE.md / _* bloqués également sur read_audit direct
            root_id = bundle["roots"][0]["root_id"]
            with self.assertRaises((ValueError, FileNotFoundError)):
                read_audit(f"{root_id}:_TEMPLATE.md", audits_dir=audits_dir, include_cwd=False)


if __name__ == "__main__":
    unittest.main()
