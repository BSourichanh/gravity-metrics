#!/usr/bin/env python3
"""Cross-platform installer and uninstaller for the Gravity Metrics Antigravity plugin (100% Python 3 stdlib)."""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_SOURCE = REPO_ROOT / "gravity-metrics-plugin"
PLUGIN_NAME = "gravity-metrics"

EXEC_SCRIPTS: Tuple[str, ...] = (
    "hooks/pre_invocation_metrics.py",
    "scripts/cli.py",
    "scripts/validate.py",
)

COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


def has_real_python3() -> bool:
    """Return True if a real 'python3' binary is on PATH (ignoring WindowsApps alias)."""
    found = shutil.which("python3")
    if not found:
        return False
    if os.name == "nt" and "windowsapps" in found.lower():
        return False
    return True


def read_plugin_manifest(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    """Safely read and parse plugin.json from a plugin directory."""
    if not plugin_dir.is_dir():
        return None
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_gravity_metrics_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only when plugin_dir contains a Gravity Metrics plugin manifest."""
    manifest = read_plugin_manifest(plugin_dir)
    return bool(manifest is not None and manifest.get("name") == PLUGIN_NAME)


def is_unsafe_target(path: Path, plugin_src: Path) -> Optional[str]:
    """Return an error reason if target path is unsafe to overwrite or remove."""
    resolved = path.resolve()
    src_resolved = plugin_src.resolve()
    home_resolved = Path.home().resolve()

    if resolved == resolved.parent:
        return f"Refusing unsafe filesystem root target: {resolved}"
    if resolved == home_resolved:
        return f"Refusing unsafe home directory target: {resolved}"
    if resolved == src_resolved or src_resolved in resolved.parents or resolved in src_resolved.parents:
        return (
            f"Refusing target inside or enclosing source plugin directory "
            f"({resolved} vs {src_resolved})"
        )
    return None


def ensure_posix_exec_bits(plugin_dir: Path) -> None:
    """Ensure executable permissions (0755-equivalent) on POSIX platforms."""
    if os.name == "nt":
        return
    exec_mask = (
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    for rel_path in EXEC_SCRIPTS:
        script = plugin_dir / rel_path
        if script.is_file():
            current_mode = script.stat().st_mode
            script.chmod(current_mode | exec_mask)


def resolve_target_dir(
    scope: str, workspace: Optional[Path], target: Optional[Path]
) -> Path:
    """Compute the installation directory for copy-based installs."""
    if target is not None:
        return target.expanduser().resolve()
    if scope == "global":
        return (Path.home() / ".gemini" / "config" / "plugins" / PLUGIN_NAME).resolve()
    if workspace is None:
        raise ValueError("--workspace is required when --scope is 'workspace'")
    return (workspace.expanduser().resolve() / ".agents" / "plugins" / PLUGIN_NAME).resolve()


def _run_subprocess(
    cmd: List[str],
    cwd: Optional[Path] = None,
    *,
    dont_write_bytecode: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command capturing UTF-8 text output without raising on non-zero exit."""
    env = None
    if dont_write_bytecode:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _print_process_output(proc: subprocess.CompletedProcess) -> None:
    """Print captured stdout/stderr streams if non-empty."""
    if proc.stdout and proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)


def run_plugin_validation(plugin_dir: Path) -> subprocess.CompletedProcess:
    """Run strict validator against a staged or installed plugin directory."""
    validator = plugin_dir / "scripts" / "validate.py"
    return _run_subprocess(
        [sys.executable, "-B", str(validator), "--plugin-root", str(plugin_dir), "--strict"],
        dont_write_bytecode=True,
    )


def run_fix_python_cmd(plugin_dir: Path) -> subprocess.CompletedProcess:
    """Adapt 'python3' references to 'python' or 'py -3' on Windows copies."""
    validator = plugin_dir / "scripts" / "validate.py"
    return _run_subprocess(
        [sys.executable, "-B", str(validator), "--plugin-root", str(plugin_dir), "--fix-python-cmd"],
        dont_write_bytecode=True,
    )


def print_next_steps(target_dir: Optional[Path]) -> None:
    """Print concise post-install instructions without touching config.json."""
    store_dir = Path.home() / ".gemini" / "gravity-metrics"
    print("\nNext steps:")
    if target_dir is not None:
        print(f"  - Installed plugin location : {target_dir}")
    print(f"  - Centralized metrics store : {store_dir}")
    print("  - Manage plugin in Antigravity : [Gravity Metrics](customization://gravity-metrics)")
    print("  - End any /gravity session with /gravity metrics (zero tokens) to record it.")


def check_environment(
    plugin_src: Path,
    target_dir: Optional[Path],
    scope: str,
    method: str,
) -> int:
    """Inspect prerequisites and current install status without modifying anything."""
    src_manifest = read_plugin_manifest(plugin_src) or {}
    src_version = src_manifest.get("version", "unknown")
    agy_bin = shutil.which("agy")
    real_py3 = has_real_python3()

    print("Gravity Metrics Environment Check")
    print(f"  Platform         : {platform.system()} {platform.release()} ({os.name})")
    print(f"  Python           : {sys.version.split()[0]} ({sys.executable})")
    print(f"  Real 'python3'   : {'yes' if real_py3 else 'no (Windows copy install will auto-adapt)'}")
    print(f"  Antigravity CLI  : {agy_bin if agy_bin else 'not found on PATH'}")
    print(f"  Source plugin    : {plugin_src} (v{src_version})")
    print(f"  Selected method  : {method} (scope={scope})")

    if target_dir is not None:
        unsafe_reason = is_unsafe_target(target_dir, plugin_src)
        if unsafe_reason:
            print(f"  Target status    : UNSAFE ({unsafe_reason})")
        elif target_dir.exists():
            if is_gravity_metrics_plugin_dir(target_dir):
                inst_manifest = read_plugin_manifest(target_dir) or {}
                inst_version = inst_manifest.get("version", "unknown")
                print(f"  Target status    : installed at {target_dir} (v{inst_version})")
            else:
                print(f"  Target status    : occupied by non-gravity-metrics directory ({target_dir})")
        else:
            print(f"  Target status    : not installed ({target_dir})")
    else:
        print("  Target status    : managed by 'agy plugin install'")
    return 0


def install_copy(
    plugin_src: Path,
    target_dir: Path,
    *,
    force: bool = False,
    no_fix_python: bool = False,
    skip_validate: bool = False,
    dry_run: bool = False,
) -> int:
    """Install or upgrade the Gravity Metrics plugin using a staged copy and rollback."""
    unsafe_reason = is_unsafe_target(target_dir, plugin_src)
    if unsafe_reason:
        print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
        return 1

    src_manifest = read_plugin_manifest(plugin_src) or {}
    src_version = str(src_manifest.get("version", "unknown"))

    replacing = False
    if target_dir.exists() or target_dir.is_symlink():
        if not is_gravity_metrics_plugin_dir(target_dir):
            print(
                f"[ERROR] Target path exists and is not a Gravity Metrics plugin: {target_dir}\n"
                "Refusing to overwrite unrelated files.",
                file=sys.stderr,
            )
            return 1
        inst_manifest = read_plugin_manifest(target_dir) or {}
        inst_version = str(inst_manifest.get("version", "unknown"))
        if not force:
            print(
                f"[ERROR] Gravity Metrics v{inst_version} is already installed at {target_dir} "
                f"(source is v{src_version}).\n"
                "Re-run with --force to upgrade or reinstall.",
                file=sys.stderr,
            )
            return 1
        replacing = True

    will_fix_py = os.name == "nt" and not no_fix_python and not has_real_python3()

    if dry_run:
        action = "Upgrade/reinstall" if replacing else "Install"
        print(f"[DRY-RUN] {action} plugin from {plugin_src} -> {target_dir}")
        print("[DRY-RUN] Set POSIX executable bits (0755) on scripts when on Linux/macOS")
        if will_fix_py:
            print("[DRY-RUN] Run validate.py --fix-python-cmd on staged copy (Windows)")
        if not skip_validate:
            print("[DRY-RUN] Run validate.py --strict on staged copy before committing")
        return 0

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=".gmetrics-stage-", dir=str(target_dir.parent)))
    staged_dir = stage_parent / PLUGIN_NAME
    backup_parent: Optional[Path] = None

    try:
        shutil.copytree(plugin_src, staged_dir, ignore=COPY_IGNORE)
        ensure_posix_exec_bits(staged_dir)

        if will_fix_py:
            fix_proc = run_fix_python_cmd(staged_dir)
            if fix_proc.returncode != 0:
                print("[ERROR] Failed to adapt python3 commands on staged copy.", file=sys.stderr)
                _print_process_output(fix_proc)
                return 1
            print("[OK]    Adapted 'python3' references on installed copy for Windows.")

        if not skip_validate:
            val_proc = run_plugin_validation(staged_dir)
            if val_proc.returncode != 0:
                print(
                    "[ERROR] Strict validation failed on staged plugin; aborting without modifying target.",
                    file=sys.stderr,
                )
                _print_process_output(val_proc)
                return 1
            print("[OK]    Strict plugin validation passed.")

        if replacing:
            backup_parent = Path(
                tempfile.mkdtemp(prefix=".gmetrics-backup-", dir=str(target_dir.parent))
            )
            backup_dir = backup_parent / PLUGIN_NAME
            shutil.move(str(target_dir), str(backup_dir))
            try:
                shutil.move(str(staged_dir), str(target_dir))
            except Exception as exc:
                print(
                    f"[ERROR] Failed to move staged plugin into {target_dir}: {exc}",
                    file=sys.stderr,
                )
                if not target_dir.exists() and backup_dir.exists():
                    shutil.move(str(backup_dir), str(target_dir))
                return 1
            shutil.rmtree(backup_parent, ignore_errors=True)
            backup_parent = None
            print(f"[OK]    Upgraded Gravity Metrics v{src_version} at {target_dir}")
        else:
            shutil.move(str(staged_dir), str(target_dir))
            print(f"[OK]    Installed Gravity Metrics v{src_version} at {target_dir}")
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)
        if backup_parent is not None and target_dir.exists():
            shutil.rmtree(backup_parent, ignore_errors=True)

    return 0


def install_agy(plugin_src: Path, *, dry_run: bool = False) -> int:
    """Validate and install Gravity Metrics using the Antigravity CLI ('agy')."""
    agy_bin = shutil.which("agy")
    if not agy_bin:
        print(
            "[ERROR] 'agy' CLI was not found on PATH.\n"
            "Use the default copy installer instead: python install.py",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"[DRY-RUN] {agy_bin} plugin validate {plugin_src}")
        print(f"[DRY-RUN] {agy_bin} plugin install {plugin_src}")
        return 0

    val_proc = _run_subprocess([agy_bin, "plugin", "validate", str(plugin_src)])
    _print_process_output(val_proc)
    if val_proc.returncode != 0:
        print("[ERROR] 'agy plugin validate' failed.", file=sys.stderr)
        return 1

    inst_proc = _run_subprocess([agy_bin, "plugin", "install", str(plugin_src)])
    _print_process_output(inst_proc)
    if inst_proc.returncode != 0:
        print("[ERROR] 'agy plugin install' failed.", file=sys.stderr)
        return 1

    print("[OK]    Installed Gravity Metrics via 'agy plugin install'.")
    if os.name == "nt" and not has_real_python3():
        print(
            "[NOTE]  Windows without real 'python3' detected: if hooks fail, run\n"
            "        'python gravity-metrics-plugin/scripts/validate.py --plugin-root <installed-dir> --fix-python-cmd'\n"
            "        against your installed copy."
        )
    return 0


def uninstall_plugin(
    plugin_src: Path,
    target_dir: Optional[Path],
    *,
    method: str = "copy",
    dry_run: bool = False,
) -> int:
    """Safely uninstall Gravity Metrics from the target location or via 'agy'."""
    if method == "agy":
        agy_bin = shutil.which("agy")
        if not agy_bin:
            print("[ERROR] 'agy' CLI was not found on PATH.", file=sys.stderr)
            return 1
        if dry_run:
            print(f"[DRY-RUN] {agy_bin} plugin uninstall {PLUGIN_NAME}")
            return 0
        proc = _run_subprocess([agy_bin, "plugin", "uninstall", PLUGIN_NAME])
        _print_process_output(proc)
        if proc.returncode != 0:
            print("[ERROR] 'agy plugin uninstall' failed.", file=sys.stderr)
            return 1
        print("[OK]    Uninstalled Gravity Metrics via 'agy plugin uninstall'.")
        return 0

    assert target_dir is not None
    unsafe_reason = is_unsafe_target(target_dir, plugin_src)
    if unsafe_reason:
        print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
        return 1

    if not target_dir.exists() and not target_dir.is_symlink():
        print(f"[ERROR] Gravity Metrics is not installed at {target_dir}.", file=sys.stderr)
        return 1

    if not is_gravity_metrics_plugin_dir(target_dir):
        print(
            f"[ERROR] Refusing to remove non-gravity-metrics directory: {target_dir}",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"[DRY-RUN] Remove Gravity Metrics plugin directory: {target_dir}")
        return 0

    shutil.rmtree(target_dir)
    print(f"[OK]    Removed Gravity Metrics plugin from {target_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for the Gravity Metrics installer."""
    parser = argparse.ArgumentParser(
        description="Install, upgrade, validate, or uninstall the Gravity Metrics Antigravity plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python install.py --check\n"
            "  python install.py\n"
            "  python install.py --force\n"
            "  python install.py --scope workspace --workspace /path/to/project\n"
            "  python install.py --uninstall"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("global", "workspace"),
        default="global",
        help="Install globally (~/.gemini/config/plugins/gravity-metrics) or inside a project (.agents/plugins/gravity-metrics).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Project workspace directory (required when --scope workspace).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Override exact plugin destination directory (incompatible with --method agy).",
    )
    parser.add_argument(
        "--method",
        choices=("copy", "agy"),
        default="copy",
        help="Installation backend: 'copy' (default, App/IDE/CLI) or 'agy' (delegates to agy CLI).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upgrade or reinstall when Gravity Metrics already exists at the target plugin directory.",
    )
    parser.add_argument(
        "--no-fix-python",
        action="store_true",
        help="Skip automatic Windows 'python3' -> 'python'/'py -3' adaptation on the installed copy.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip strict validation during install.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned operations without modifying files or running mutating commands.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Inspect environment prerequisites and target status without modifying anything.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Safely remove an installed Gravity Metrics plugin.",
    )
    return parser


def _configure_stdio() -> None:
    """Force stdout/stderr to UTF-8 so Windows cp1252 consoles never crash."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the Gravity Metrics cross-platform installer CLI."""
    _configure_stdio()
    if sys.version_info < (3, 10):
        print("[ERROR] Python 3.10+ is required to run install.py.", file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    if not PLUGIN_SOURCE.is_dir() or not is_gravity_metrics_plugin_dir(PLUGIN_SOURCE):
        print(
            f"[ERROR] Source plugin directory is missing or invalid: {PLUGIN_SOURCE}",
            file=sys.stderr,
        )
        return 1

    if args.scope == "workspace" and args.workspace is None:
        print("[ERROR] --workspace PATH is required when --scope is 'workspace'.", file=sys.stderr)
        return 2

    if args.method == "agy" and (args.scope == "workspace" or args.target is not None):
        print(
            "[ERROR] --method agy only supports --scope global without --target.",
            file=sys.stderr,
        )
        return 2

    target_dir: Optional[Path] = None
    if args.method == "copy":
        target_dir = resolve_target_dir(args.scope, args.workspace, args.target)

    if args.check:
        return check_environment(PLUGIN_SOURCE, target_dir, args.scope, args.method)

    if args.uninstall:
        return uninstall_plugin(
            PLUGIN_SOURCE,
            target_dir,
            method=args.method,
            dry_run=args.dry_run,
        )

    if args.method == "agy":
        rc = install_agy(PLUGIN_SOURCE, dry_run=args.dry_run)
    else:
        assert target_dir is not None
        rc = install_copy(
            PLUGIN_SOURCE,
            target_dir,
            force=args.force,
            no_fix_python=args.no_fix_python,
            skip_validate=args.skip_validate,
            dry_run=args.dry_run,
        )

    if rc != 0:
        return rc

    if not args.dry_run:
        print_next_steps(target_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
