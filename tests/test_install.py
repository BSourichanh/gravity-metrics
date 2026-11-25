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
            root = Path(tmp)
            fake_home = root / "home"
            fake_home.mkdir()
            isolated_env = {
                "HOME": str(fake_home),
                "USERPROFILE": str(fake_home),
            }
            target = root / "installed-plugin"
            r_chk = _run_install("--check", "--target", str(target), extra_env=isolated_env)
            self.assertEqual(r_chk.returncode, 0, r_chk.stderr)
            self.assertIn("Gravity Metrics Environment Check", r_chk.stdout)
            self.assertNotIn("Strict validation:", r_chk.stdout)
            self.assertFalse(target.exists())

            r_chk_strict = _run_install(
                "--check", "--strict", "--target", str(target), extra_env=isolated_env
            )
            self.assertEqual(r_chk_strict.returncode, 0, r_chk_strict.stderr + r_chk_strict.stdout)
            self.assertIn("Strict validation: PASSED (0 error, 0 warning)", r_chk_strict.stdout)
            self.assertFalse(target.exists())

            r_dry = _run_install("--dry-run", "--target", str(target), extra_env=isolated_env)
            self.assertEqual(r_dry.returncode, 0, r_dry.stderr)
            self.assertIn("Run validate.py --strict", r_dry.stdout)
            self.assertFalse(target.exists())

            r_dry_skip = _run_install(
                "--dry-run", "--skip-validate", "--target", str(target), extra_env=isolated_env
            )
            self.assertEqual(r_dry_skip.returncode, 0, r_dry_skip.stderr)
            self.assertNotIn("Run validate.py --strict", r_dry_skip.stdout)
            self.assertFalse(target.exists())

            r_dry_override = _run_install(
                "--dry-run", "--strict", "--skip-validate", "--target", str(target), extra_env=isolated_env
            )
            self.assertEqual(r_dry_override.returncode, 0, r_dry_override.stderr)
            self.assertIn("Run validate.py --strict", r_dry_override.stdout)
            self.assertFalse(target.exists())

    def test_strict_check_failure_and_agy_strict_flag(self) -> None:
        failed_proc = subprocess.CompletedProcess(
            args=["validate.py", "--strict"],
            returncode=1,
            stdout="",
            stderr="[FAIL] synthetic error",
        )
        with mock.patch.object(installer, "run_plugin_validation", return_value=failed_proc):
            rc = installer.check_environment(
                installer.PLUGIN_SOURCE, None, "global", "copy", strict=True
            )
            self.assertEqual(rc, 1)

        with mock.patch.object(installer.shutil, "which", return_value="/usr/bin/agy"):
            ok_proc = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="", stderr="")
            with mock.patch.object(installer, "_run_subprocess", return_value=ok_proc) as run_mock:
                rc_agy = installer.install_agy(installer.PLUGIN_SOURCE, dry_run=False)
                self.assertEqual(rc_agy, 0)
                first_cmd = run_mock.call_args_list[0].args[0]
                self.assertEqual(
                    first_cmd,
                    ["/usr/bin/agy", "plugin", "validate", str(installer.PLUGIN_SOURCE), "--strict"],
                )

    def test_copy_install_upgrade_workspace_and_uninstall(self) -> None:
        source_hooks_before = SOURCE_HOOKS.read_text(encoding="utf-8")
        self.assertIn("python3 hooks/pre_invocation_metrics.py", source_hooks_before)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "plugins" / "gravity-metrics"

            r_inst = _run_install("--target", str(target))
            self.assertEqual(r_inst.returncode, 0, r_inst.stderr + r_inst.stdout)
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((target / "config" / "module.json").is_file())
            self.assertTrue((target / "hooks" / "pre_invocation_metrics.py").is_file())
            self.assertTrue((target / "hooks" / "session_start_metrics.py").is_file())
            self.assertFalse((target / "mcp_config.json").exists())
            self.assertFalse((target / "mcp").exists())
            self.assertTrue((target / "skills" / "gravity-metrics" / "SKILL.md").is_file())

            # Le dépôt source garde toujours 'python3' portable
            self.assertEqual(SOURCE_HOOKS.read_text(encoding="utf-8"), source_hooks_before)
            if os.name == "nt" and not installer.has_real_python3():
                installed_hooks = (target / "hooks.json").read_text(encoding="utf-8")
                self.assertNotIn('"python3 ', installed_hooks)

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

    def test_global_install_records_sources_json_atomically_and_preserves_other_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            grav_home = root / "grav-home"
            fake_home.mkdir()
            core_dir = grav_home / "core"
            core_dir.mkdir(parents=True)
            sources_file = core_dir / "sources.json"
            existing_gravity = {
                "plugin_name": "gravity",
                "repo_dir": "/tmp/gravity",
                "repository": "https://github.com/BSourichanh/gravity.git",
                "install_method": "copy",
                "target_dir": "/tmp/plugins/gravity",
                "git_branch": "dev",
                "git_sha": "abc1234",
                "installed_version": "0.3.0",
                "installed_at": "2026-11-25T12:00:00Z",
            }
            sources_file.write_text(
                json.dumps({"schema_version": 1, "sources": {"gravity": existing_gravity}}, indent=2)
                + "\n",
                encoding="utf-8",
            )

            isolated_env = {
                "HOME": str(fake_home),
                "USERPROFILE": str(fake_home),
                "GRAVITY_HOME": str(grav_home),
            }
            r_inst = _run_install("--scope", "global", extra_env=isolated_env)
            self.assertEqual(r_inst.returncode, 0, r_inst.stderr + r_inst.stdout)

            raw_bytes = sources_file.read_bytes()
            self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r\n", raw_bytes)
            self.assertEqual([p.name for p in core_dir.iterdir()], ["sources.json"])

            data = json.loads(raw_bytes.decode("utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertRegex(data["updated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertEqual(data["sources"]["gravity"], existing_gravity)

            entry = data["sources"]["gravity-metrics"]
            expected_target = (fake_home / ".gemini" / "config" / "plugins" / "gravity-metrics").resolve()
            self.assertEqual(entry["plugin_name"], "gravity-metrics")
            self.assertEqual(entry["repo_dir"], str(PROJECT_ROOT.resolve()))
            self.assertEqual(entry["repository"], "https://github.com/BSourichanh/gravity-metrics.git")
            self.assertEqual(entry["install_method"], "copy")
            self.assertEqual(entry["target_dir"], str(expected_target))
            self.assertEqual(entry["installed_version"], "0.5.1")
            self.assertRegex(entry["installed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertFalse((expected_target / ".gravity-source.json").exists())

            # Global --method agy also records provenance with target_dir=None
            with mock.patch.object(installer.Path, "home", return_value=fake_home), mock.patch.dict(
                os.environ, isolated_env, clear=False
            ), mock.patch.object(installer, "install_agy", return_value=0):
                rc_agy = installer.main(["--scope", "global", "--method", "agy"])
            self.assertEqual(rc_agy, 0)
            data_agy = json.loads(sources_file.read_text(encoding="utf-8"))
            self.assertEqual(data_agy["sources"]["gravity-metrics"]["install_method"], "agy")
            self.assertIsNone(data_agy["sources"]["gravity-metrics"]["target_dir"])
            self.assertEqual(data_agy["sources"]["gravity"], existing_gravity)

    def test_sources_json_skipped_on_dry_run_check_workspace_target_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            grav_home = root / "grav-home"
            ws = root / "workspace"
            custom_target = root / "custom-target"
            fake_home.mkdir()
            ws.mkdir()

            isolated_env = {
                "HOME": str(fake_home),
                "USERPROFILE": str(fake_home),
                "GRAVITY_HOME": str(grav_home),
            }
            sources_file = grav_home / "core" / "sources.json"

            self.assertEqual(_run_install("--check", "--scope", "global", extra_env=isolated_env).returncode, 0)
            self.assertFalse(sources_file.exists())

            self.assertEqual(_run_install("--dry-run", "--scope", "global", extra_env=isolated_env).returncode, 0)
            self.assertFalse(sources_file.exists())

            self.assertEqual(
                _run_install("--scope", "workspace", "--workspace", str(ws), extra_env=isolated_env).returncode,
                0,
            )
            self.assertFalse(sources_file.exists())

            self.assertEqual(
                _run_install("--scope", "global", "--target", str(custom_target), extra_env=isolated_env).returncode,
                0,
            )
            self.assertFalse(sources_file.exists())

            # Install globally, remove sources.json, then verify --uninstall does not recreate it
            self.assertEqual(_run_install("--scope", "global", extra_env=isolated_env).returncode, 0)
            self.assertTrue(sources_file.is_file())
            sources_file.unlink()
            self.assertEqual(_run_install("--uninstall", "--scope", "global", extra_env=isolated_env).returncode, 0)
            self.assertFalse(sources_file.exists())

    def test_record_install_source_tolerates_missing_git_and_dot_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            no_git_repo = root / "no-git-repo"
            no_git_repo.mkdir()
            grav_home = root / "grav-home"

            self.assertEqual(installer._probe_git_metadata(no_git_repo), (None, None))

            with mock.patch.object(
                installer.subprocess, "run", side_effect=FileNotFoundError("git not found")
            ):
                self.assertEqual(installer._probe_git_metadata(PROJECT_ROOT), (None, None))

            with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(grav_home)}, clear=False), mock.patch.object(
                installer.subprocess, "run", side_effect=FileNotFoundError("git not found")
            ):
                recorded = installer._record_install_source(
                    installer.PLUGIN_SOURCE,
                    PROJECT_ROOT,
                    method="copy",
                    target_dir=root / "target",
                )
            self.assertEqual(recorded, grav_home / "core" / "sources.json")
            data = json.loads((grav_home / "core" / "sources.json").read_text(encoding="utf-8"))
            entry = data["sources"]["gravity-metrics"]
            self.assertIsNone(entry["git_branch"])
            self.assertIsNone(entry["git_sha"])
            self.assertEqual(entry["installed_version"], "0.5.1")


if __name__ == "__main__":
    unittest.main()
