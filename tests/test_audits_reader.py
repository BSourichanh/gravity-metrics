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
            (ws_local / "audits").mkdir(parents=True)
            (ws_local / "audits" / "local.md").write_text("# Audit Local\n", encoding="utf-8")

            ws_bridge = root / "bridge-ws"
            (ws_bridge / "audits").mkdir(parents=True)
            (ws_bridge / "audits" / "bridge.md").write_text("# Audit Bridge\n", encoding="utf-8")

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

            # 2) Sans $GRAVITY_AUDITS_DIR : <workspace>/audits/ puis <canonical_path>/audits/ (dédupliqués)
            with mock.patch.dict(os.environ, {"GRAVITY_AUDITS_DIR": ""}, clear=False):
                roots_ws = discover_audit_roots(
                    workspace_dirs=[ws_local],
                    bridge_db=db_path,
                    include_cwd=False,
                )
                self.assertEqual(
                    [r["path"] for r in roots_ws],
                    [(ws_local / "audits").resolve(), (ws_bridge / "audits").resolve()],
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

            audits, roots = list_audits(records=records, audits_dir=audits_dir, include_cwd=False)
            self.assertEqual(len(roots), 1)
            self.assertEqual(len(audits), 3)
            # README.md épinglé en premier, puis date décroissante
            self.assertTrue(audits[0]["is_index"])
            self.assertEqual(audits[0]["verdict_tone"], "ok")
            self.assertEqual(audits[1]["filename"], "2026-11-22-revue.md")
            self.assertEqual(audits[1]["verdict"], "BLOQUANT")
            self.assertEqual(audits[1]["verdict_tone"], "danger")
            self.assertEqual(
                {s["label"] for s in audits[1]["linked_sessions"]},
                {"run-full-uuid", "run-prefix-uuid"},
            )

            links_by_sess = build_audit_links_by_session(audits)
            self.assertIn(cid_full, links_by_sess)
            self.assertIn(cid_pref, links_by_sess)
            self.assertEqual(links_by_sess[cid_full][0]["filename"] if "filename" in links_by_sess[cid_full][0] else links_by_sess[cid_full][0]["title"], "Revue Architecture")

            huge_loaded = read_audit(audits[2]["audit_id"], records=records, audits_dir=audits_dir, include_cwd=False)
            self.assertTrue(huge_loaded["truncated"])
            self.assertGreater(huge_loaded["size_bytes"], MAX_AUDIT_BYTES)
            self.assertEqual(len(huge_loaded["content"].encode("utf-8")), MAX_AUDIT_BYTES)
            self.assertEqual(huge_loaded["verdict_tone"], "warn")


if __name__ == "__main__":
    unittest.main()
