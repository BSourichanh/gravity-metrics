#!/usr/bin/env python3
"""Tests unitaires de l'installeur multi-OS install.py (100 % stdlib)."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = PROJECT_ROOT / "install.py"
SOURCE_HOOKS = PROJECT_ROOT / "plugin" / "hooks.json"
SOURCE_MCP = PROJECT_ROOT / "plugin" / "mcp_config.json"
VALIDATE_SCRIPT = PROJECT_ROOT / "plugin" / "scripts" / "validate.py"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import install as installer  # noqa: E402


def _run_install(*args: str, extra_env: dict = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-B", str(INSTALL_SCRIPT), *args],
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


class TestInstaller(unittest.TestCase):
    def test_check_and_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "installed-plugin"
            r_chk = _run_install("--check", "--target", str(target))
            self.assertEqual(r_chk.returncode, 0, r_chk.stderr)
            self.assertIn("Gravity Metrics Environment Check", r_chk.stdout)

            r_dry = _run_install("--dry-run", "--target", str(target))
            self.assertEqual(r_dry.returncode, 0, r_dry.stderr)
            self.assertFalse(target.exists())

    def test_copy_install_upgrade_workspace_and_uninstall(self) -> None:
        source_hooks_before = SOURCE_HOOKS.read_text(encoding="utf-8")
        source_mcp_before = SOURCE_MCP.read_text(encoding="utf-8")
        self.assertIn("python3 hooks/pre_invocation_metrics.py", source_hooks_before)
        self.assertIn('"command": "python3"', source_mcp_before)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "plugins" / "gravity-metrics"

            r_inst = _run_install("--target", str(target))
            self.assertEqual(r_inst.returncode, 0, r_inst.stderr + r_inst.stdout)
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((target / "config" / "module.json").is_file())
            self.assertTrue((target / "hooks" / "pre_invocation_metrics.py").is_file())
            self.assertTrue((target / "mcp_config.json").is_file())
            self.assertTrue((target / "mcp" / "server.py").is_file())
            self.assertTrue((target / "skills" / "gravity-metrics" / "SKILL.md").is_file())

            # Le dépôt source garde toujours 'python3' portable
            self.assertEqual(SOURCE_HOOKS.read_text(encoding="utf-8"), source_hooks_before)
            self.assertEqual(SOURCE_MCP.read_text(encoding="utf-8"), source_mcp_before)
            if os.name == "nt" and not installer.has_real_python3():
                installed_hooks = (target / "hooks.json").read_text(encoding="utf-8")
                installed_mcp = (target / "mcp_config.json").read_text(encoding="utf-8")
                self.assertNotIn('"python3 ', installed_hooks)
                self.assertNotIn('"python3"', installed_mcp)

            # Refus sans --force
            r_dup = _run_install("--target", str(target))
            self.assertNotEqual(r_dup.returncode, 0)

            # Réinstallation avec --force
            r_force = _run_install("--target", str(target), "--force")
            self.assertEqual(r_force.returncode, 0, r_force.stderr + r_force.stdout)

            # Désinstallation
            r_uninst = _run_install("--uninstall", "--target", str(target))
            self.assertEqual(r_uninst.returncode, 0, r_uninst.stderr + r_uninst.stdout)
            self.assertFalse(target.exists())

            # Scope workspace
            ws = root / "my-project"
            ws.mkdir()
            r_ws = _run_install("--scope", "workspace", "--workspace", str(ws))
            self.assertEqual(r_ws.returncode, 0, r_ws.stderr + r_ws.stdout)
            self.assertTrue((ws / ".agents" / "plugins" / "gravity-metrics" / "plugin.json").is_file())

    def test_safety_guards_reject_unrelated_and_unsafe_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unrelated = Path(tmp) / "other-plugin"
            unrelated.mkdir()
            (unrelated / "plugin.json").write_text(json.dumps({"name": "other"}), encoding="utf-8")

            r_ow = _run_install("--target", str(unrelated), "--force")
            self.assertNotEqual(r_ow.returncode, 0)
            self.assertTrue((unrelated / "plugin.json").is_file())

            r_rm = _run_install("--uninstall", "--target", str(unrelated))
            self.assertNotEqual(r_rm.returncode, 0)
            self.assertTrue((unrelated / "plugin.json").is_file())

            self.assertIsNotNone(installer.is_unsafe_target(Path.home(), installer.PLUGIN_SOURCE))
            self.assertIsNotNone(
                installer.is_unsafe_target(installer.PLUGIN_SOURCE, installer.PLUGIN_SOURCE)
            )

    def test_cp1252_env_and_validate_expanduser(self) -> None:
        cp1252_env = {"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "installed"
            r_inst = _run_install("--target", str(target), extra_env=cp1252_env)
            self.assertEqual(r_inst.returncode, 0, r_inst.stderr + r_inst.stdout)

            fake_home = root / "home"
            tilde_plugin = fake_home / "plugin-copy"
            shutil.copytree(installer.PLUGIN_SOURCE, tilde_plugin)
            val_env = dict(os.environ)
            val_env.update(cp1252_env)
            val_env["HOME"] = str(fake_home)
            val_env["USERPROFILE"] = str(fake_home)
            r_val = subprocess.run(
                [sys.executable, "-B", str(VALIDATE_SCRIPT), "--plugin-root", "~/plugin-copy", "--strict"],
                cwd=str(PROJECT_ROOT),
                env=val_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertEqual(r_val.returncode, 0, r_val.stderr + r_val.stdout)
            self.assertIn("[PASS]", r_val.stdout)

    def test_empty_dir_recovery_and_locked_dir_inplace_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty_target = root / "empty-target"
            empty_target.mkdir()
            rc_empty = installer.install_copy(installer.PLUGIN_SOURCE, empty_target)
            self.assertEqual(rc_empty, 0)
            self.assertTrue((empty_target / "plugin.json").is_file())

            locked_target = root / "locked-target"
            self.assertEqual(installer.install_copy(installer.PLUGIN_SOURCE, locked_target), 0)
            stale_top = locked_target / "stale.txt"
            stale_nested = locked_target / "metrics" / "stale_old.py"
            stale_top.write_text("old", encoding="utf-8")
            stale_nested.write_text("# old", encoding="utf-8")

            with mock.patch.object(installer.os, "rename", side_effect=PermissionError("WinError 32 locked")):
                rc_inplace = installer.install_copy(
                    installer.PLUGIN_SOURCE, locked_target, force=True
                )
            self.assertEqual(rc_inplace, 0)
            self.assertFalse(stale_top.exists())
            self.assertFalse(stale_nested.exists())
            self.assertTrue((locked_target / "plugin.json").is_file())

            r_val = subprocess.run(
                [sys.executable, "-B", str(VALIDATE_SCRIPT), "--plugin-root", str(locked_target), "--strict"],
                cwd=str(PROJECT_ROOT),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            self.assertEqual(r_val.returncode, 0, r_val.stderr + r_val.stdout)
            self.assertIn("[PASS]", r_val.stdout)

    def test_resolve_store_dir_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            grav_home = root / "grav-home"
            explicit = root / "explicit-metrics"

            with mock.patch.object(installer.Path, "home", return_value=fake_home):
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(
                        installer.resolve_store_dir(),
                        fake_home / ".gemini" / "gravity" / "metrics",
                    )
                with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=True):
                    self.assertEqual(installer.resolve_store_dir(), grav_home / "metrics")
                with mock.patch.dict(
                    os.environ,
                    {
                        "GRAVITY_HOME": str(grav_home),
                        "GRAVITY_METRICS_HOME": str(explicit),
                    },
                    clear=True,
                ):
                    self.assertEqual(installer.resolve_store_dir(), explicit)


if __name__ == "__main__":
    unittest.main()
