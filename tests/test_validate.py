#!/usr/bin/env python3
"""Tests unitaires isolés des contrôles D4 dans plugin/scripts/validate.py (100 % stdlib)."""

import contextlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate  # noqa: E402


def _pad_markdown(prefix: str, target_bytes: int) -> bytes:
    """Construit un contenu Markdown LF UTF-8 sans BOM exactement de taille target_bytes."""
    raw = prefix.encode("utf-8")
    if len(raw) > target_bytes:
        raise ValueError(f"Préfixe ({len(raw)} o) > cible ({target_bytes} o)")
    return raw + (b"a" * (target_bytes - len(raw)))


class TestValidateD4(unittest.TestCase):
    def _make_plugin_copy(self, tmp: str, *, dir_name: str = "plugin") -> Path:
        dst = Path(tmp) / dir_name
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

    def _run_main_captured(
        self, *argv: str, mock_dry_runs: bool = True
    ) -> tuple[int, str]:
        buf = io.StringIO()
        if mock_dry_runs:
            with (
                mock.patch.object(validate, "_run_hook_dry_runs"),
                mock.patch.object(validate, "_run_mcp_dry_run"),
                contextlib.redirect_stdout(buf),
            ):
                rc = validate.main(list(argv))
        else:
            with contextlib.redirect_stdout(buf):
                rc = validate.main(list(argv))
        return rc, buf.getvalue()

    def test_rules_agents_up_to_600_bytes_passes_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            rules = plugin_dir / "rules" / "AGENTS.md"
            rules.write_bytes(_pad_markdown("# Gravity Metrics\n\n- ok ", 600))

            rc, out = self._run_main_captured(
                "--plugin-root", str(plugin_dir), "--strict", mock_dry_runs=False
            )
            self.assertEqual(rc, 0, out)
            self.assertIn("[PASS]", out)

    def test_rules_agents_601_to_1000_warns_and_fails_strict(self) -> None:
        for size in (601, 1000):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as tmp:
                plugin_dir = self._make_plugin_copy(tmp)
                rules = plugin_dir / "rules" / "AGENTS.md"
                rules.write_bytes(_pad_markdown("# Gravity Metrics\n\n- ", size))

                rc_normal, out_normal = self._run_main_captured("--plugin-root", str(plugin_dir))
                self.assertEqual(rc_normal, 0, out_normal)
                self.assertIn("[WARN]", out_normal)
                self.assertIn("600 octets", out_normal)

                rc_strict, out_strict = self._run_main_captured(
                    "--plugin-root", str(plugin_dir), "--strict"
                )
                self.assertEqual(rc_strict, 1, out_strict)
                self.assertIn("[FAIL]", out_strict)

    def test_rules_agents_over_1000_bytes_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            rules = plugin_dir / "rules" / "AGENTS.md"
            rules.write_bytes(_pad_markdown("# Gravity Metrics\n\n- ", 1001))

            rc, out = self._run_main_captured("--plugin-root", str(plugin_dir))
            self.assertEqual(rc, 1, out)
            self.assertIn("[ERROR]", out)
            self.assertIn("1000 octets", out)

    def test_rules_agents_rejects_bom_crlf_and_frontmatter(self) -> None:
        cases = [
            ("bom", validate.UTF8_BOM + b"# Gravity Metrics\n\n- ok\n", "BOM UTF-8"),
            ("crlf", b"# Gravity Metrics\r\n\r\n- ok\r\n", "CRLF"),
            ("frontmatter", b"---\nname: bad\n---\n# Gravity Metrics\n", "frontmatter YAML"),
        ]
        for label, payload, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                plugin_dir = self._make_plugin_copy(tmp)
                (plugin_dir / "rules" / "AGENTS.md").write_bytes(payload)

                report = self._run_validate_fast(plugin_dir)
                self.assertFalse(report.ok(strict=False))
                self.assertTrue(
                    any(expected in err for err in report.errors),
                    f"Erreur attendue {expected!r} absente : {report.errors}",
                )

    def test_skill_md_limits_bom_and_crlf(self) -> None:
        header = "---\nname: gravity-metrics\ndescription: Test skill D4.\n---\n\n# Skill\n\n"
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            skill_md = plugin_dir / "skills" / "gravity-metrics" / "SKILL.md"

            skill_md.write_bytes(_pad_markdown(header, 6000))
            report_ok = self._run_validate_fast(plugin_dir)
            self.assertTrue(report_ok.ok(strict=True), report_ok.errors + report_ok.warnings)

            skill_md.write_bytes(_pad_markdown(header, 6001))
            report_big = self._run_validate_fast(plugin_dir)
            self.assertFalse(report_big.ok(strict=False))
            self.assertTrue(any("6000 octets" in e for e in report_big.errors), report_big.errors)

            skill_md.write_bytes(validate.UTF8_BOM + header.encode("utf-8"))
            report_bom = self._run_validate_fast(plugin_dir)
            self.assertTrue(any("BOM UTF-8" in e for e in report_bom.errors), report_bom.errors)

            skill_md.write_bytes(header.replace("\n", "\r\n").encode("utf-8"))
            report_crlf = self._run_validate_fast(plugin_dir)
            self.assertTrue(any("CRLF" in e for e in report_crlf.errors), report_crlf.errors)

    def test_root_agents_md_limits_bom_crlf_and_name_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp, dir_name="plugin")
            root_agents = Path(tmp) / "AGENTS.md"

            root_agents.write_bytes(_pad_markdown("# Root\n\n", 2500))
            report_ok = self._run_validate_fast(plugin_dir)
            self.assertTrue(report_ok.ok(strict=True), report_ok.errors + report_ok.warnings)

            root_agents.write_bytes(_pad_markdown("# Root\n\n", 2501))
            report_big = self._run_validate_fast(plugin_dir)
            self.assertTrue(any("2500 octets" in e for e in report_big.errors), report_big.errors)

            root_agents.write_bytes(validate.UTF8_BOM + b"# Root\n")
            report_bom = self._run_validate_fast(plugin_dir)
            self.assertTrue(any("BOM UTF-8" in e for e in report_bom.errors), report_bom.errors)

            root_agents.write_bytes(b"# Root\r\n")
            report_crlf = self._run_validate_fast(plugin_dir)
            self.assertTrue(any("CRLF" in e for e in report_crlf.errors), report_crlf.errors)

        with tempfile.TemporaryDirectory() as tmp:
            installed_dir = self._make_plugin_copy(tmp, dir_name="gravity-metrics")
            (Path(tmp) / "AGENTS.md").write_bytes(_pad_markdown("# Unrelated\n", 3000))
            report_installed = self._run_validate_fast(installed_dir)
            self.assertTrue(
                report_installed.ok(strict=True),
                report_installed.errors + report_installed.warnings,
            )

    def test_dry_runs_isolate_antigravity_brain_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = self._make_plugin_copy(tmp)
            captured_envs: list[dict[str, str]] = []
            real_run = validate.subprocess.run

            def _spy_run(*args, **kwargs):
                env = kwargs.get("env") or {}
                captured_envs.append(dict(env))
                bdir = Path(env["ANTIGRAVITY_BRAIN_DIR"])
                self.assertTrue(bdir.is_dir())
                return real_run(*args, **kwargs)

            with (
                mock.patch.dict(os.environ, {"ANTIGRAVITY_BRAIN_DIR": str(Path(tmp) / "outer_brain")}, clear=False),
                mock.patch.object(validate.subprocess, "run", side_effect=_spy_run),
            ):
                rc, out = self._run_main_captured(
                    "--plugin-root", str(plugin_dir), "--strict", mock_dry_runs=False
                )

            self.assertEqual(rc, 0, out)
            self.assertGreaterEqual(len(captured_envs), 2)
            for env in captured_envs:
                self.assertIn("ANTIGRAVITY_BRAIN_DIR", env)
                self.assertNotEqual(
                    Path(env["ANTIGRAVITY_BRAIN_DIR"]).resolve(),
                    (Path(tmp) / "outer_brain").resolve(),
                )
                self.assertEqual(
                    Path(env["ANTIGRAVITY_BRAIN_DIR"]).parent.resolve(),
                    Path(env["GRAVITY_METRICS_HOME"]).parent.resolve(),
                )


if __name__ == "__main__":
    unittest.main()
