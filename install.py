#!/usr/bin/env python3
"""Cross-platform installer and uninstaller for Gravity Metrics (100% Python 3.10+ stdlib)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_SOURCE = REPO_ROOT / "plugin"
PLUGIN_NAME = "gravity-metrics"

if str(PLUGIN_SOURCE) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SOURCE))

from _shared import installer_core  # noqa: E402

EXEC_SCRIPTS: tuple[str, ...] = (
    "hooks/pre_invocation_metrics.py",
    "hooks/session_start_metrics.py",
    "scripts/cli.py",
    "scripts/dashboard_provider.py",
    "scripts/validate.py",
)

has_real_python3 = installer_core.has_real_python3
read_plugin_manifest = installer_core.read_plugin_manifest
is_unsafe_target = installer_core.is_unsafe_target


def is_gravity_metrics_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only when plugin_dir contains a Gravity Metrics plugin manifest."""
    manifest = read_plugin_manifest(plugin_dir)
    return bool(manifest is not None and manifest.get("name") == PLUGIN_NAME)


def resolve_target_dir(scope: str, workspace: Optional[Path], target: Optional[Path]) -> Path:
    """Compute the installation directory for copy-based installs."""
    return installer_core.resolve_target_dir(scope, workspace, target, plugin_name=PLUGIN_NAME)


def resolve_store_dir() -> Path:
    """Resolve centralized store ($GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics)."""
    for env_key, suffix in (("GRAVITY_METRICS_HOME", None), ("GRAVITY_HOME", "metrics")):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            base = Path(raw).expanduser()
            return base / suffix if suffix else base
    return Path.home() / ".gemini" / "gravity" / "metrics"


def _next_steps(target_dir: Optional[Path], _args: object) -> None:
    print("\nNext steps:")
    if target_dir is not None:
        print(f"  - Installed plugin location : {target_dir}")
    print(f"  - Centralized metrics store : {resolve_store_dir()}")
    print("  - Run /gravity-metrics to open the unified Gravity dashboard (metrics view).")
    print("  - Or use 'python plugin/scripts/cli.py list' / 'pending' from the CLI.")


SPEC = installer_core.InstallSpec(
    plugin_name=PLUGIN_NAME,
    display_name="Gravity Metrics",
    plugin_source=PLUGIN_SOURCE,
    repo_root=REPO_ROOT,
    exec_scripts=EXEC_SCRIPTS,
    has_executable_manifest=True,
    epilog="Examples:\n  python install.py --check\n  python install.py\n  python install.py --force\n  python install.py --scope workspace --workspace /path/to/project\n  python install.py --uninstall",
    installed_check_fn=is_gravity_metrics_plugin_dir,
    next_steps_fn=_next_steps,
    record_sources=True,
    agy_strict_default=True,
)


def run_plugin_validation(plugin_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run strict validator against a staged or installed plugin directory."""
    return installer_core.run_plugin_validation(plugin_dir, SPEC)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return installer_core.run_installer(SPEC, argv)


if __name__ == "__main__":
    raise SystemExit(main())
