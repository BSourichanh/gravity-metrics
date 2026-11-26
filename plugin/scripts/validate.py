#!/usr/bin/env python3
"""Strict standalone validator for Gravity Metrics (delegates to _shared.validator_core)."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT_DEFAULT = SCRIPT_DIR.parent
if str(PLUGIN_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT_DEFAULT))

from _shared import validator_core  # noqa: E402
from _shared.validator_core import (  # noqa: E402
    Report,
    UTF8_BOM,
    ValidatorConfig,
    fix_python_cmd,
    has_real_python3,
    validate_plugin_tree,
)

FORBIDDEN_MOD_RE = validator_core.FORBIDDEN_MOD_RE
FORBIDDEN_TERM_RE = re.compile(r"\bbench" + r"marks?\b", re.IGNORECASE)
ALLOWED_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp", "mcp-app"})

ALLOWED_LOCAL_MODULES: frozenset[str] = (
    validator_core.ALLOWED_LOCAL_MODULES
    - frozenset({"mcp", "widget_html", "bridge_links", "hook_runtime"})
) | frozenset(
    {
        "hooks",
        "collector",
        "prompt_classifier",
        "transcript_index",
        "agent_tree",
        "milestone_analyzer",
        "technical_signals",
        "workspace_resolver",
        "history",
        "lifecycle",
        "store",
        "audits_reader",
        "markdown_report",
        "pricing",
        "formatters",
    }
)


def _check_metrics_contract_extra(root: Path, report: Report) -> None:
    mpath = root / "config" / "module.json"
    if mpath.is_file():
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            storage = data.get("storage") or {}
            if isinstance(storage, dict):
                if storage.get("subdir") != "metrics":
                    report.error(f"config/module.json : 'storage.subdir' doit être 'metrics', obtenu {storage.get('subdir')!r}.")
                if storage.get("env_override") != "GRAVITY_METRICS_HOME":
                    report.error(f"config/module.json : 'storage.env_override' doit être 'GRAVITY_METRICS_HOME', obtenu {storage.get('env_override')!r}.")
            cmds = data.get("commands")
            if not isinstance(cmds, list) or "/gravity-metrics" not in cmds:
                report.error("config/module.json : 'commands' doit inclure '/gravity-metrics'.")
            ui = data.get("ui")
            if isinstance(ui, dict) and ui.get("mode") not in ALLOWED_UI_MODES:
                report.error(f"config/module.json : 'ui.mode' invalide ({ui.get('mode')!r}).")
            dash = data.get("dashboard")
            if isinstance(dash, dict) and isinstance(dash.get("provider_script"), str):
                prov = (root / dash["provider_script"]).resolve()
                if not prov.is_file():
                    report.error(f"config/module.json : provider_script introuvable ({dash['provider_script']!r}).")

    cat_path = root / "config" / "pricing_catalog.json"
    if not cat_path.is_file():
        report.error("config/pricing_catalog.json introuvable.")
    else:
        try:
            cat = json.loads(cat_path.read_text(encoding="utf-8"))
            if not isinstance(cat, dict) or "2026_nov_standard" not in (cat.get("profiles") or {}):
                report.error("config/pricing_catalog.json : profil '2026_nov_standard' manquant.")
        except (OSError, ValueError) as exc:
            report.error(f"config/pricing_catalog.json invalide : {exc}")


def _check_hooks_extra(root: Path, report: Report) -> None:
    hooks_json = root / "hooks.json"
    if not hooks_json.is_file():
        return
    try:
        raw = hooks_json.read_bytes()
        data = json.loads((raw[len(UTF8_BOM) :] if raw.startswith(UTF8_BOM) else raw).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    pre_inv_found = False
    for gcfg in data.values():
        if isinstance(gcfg, dict) and isinstance(gcfg.get("PreInvocation"), list) and gcfg["PreInvocation"]:
            pre_inv_found = True
    if not pre_inv_found:
        report.error("hooks.json doit déclarer au moins un handler PreInvocation.")


def _run_hook_dry_runs(plugin_root: Path, report: Report) -> None:
    hook_script = plugin_root / "hooks" / "pre_invocation_metrics.py"
    start_hook = plugin_root / "hooks" / "session_start_metrics.py"
    if not hook_script.is_file():
        report.error(f"Script hook introuvable : {hook_script}")
        return
    if not start_hook.is_file():
        report.error(f"Script hook SessionStart introuvable : {start_hook}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        env = {
            **os.environ,
            "GRAVITY_HOME": str(tmp_path / "gravity-home"),
            "GRAVITY_METRICS_HOME": str(tmp_path / "store"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        s_proc = subprocess.run(
            [sys.executable, "-B", str(start_hook)],
            input=json.dumps({"conversationId": "val-sess-1", "cwd": str(plugin_root)}),
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            cwd=str(plugin_root), env=env, timeout=5, check=False,
        )
        if s_proc.returncode != 0 or s_proc.stdout.strip() != "{}":
            report.error(f"Dry-run SessionStart invalide (rc={s_proc.returncode}, out={s_proc.stdout!r}, err={s_proc.stderr!r})")

        for payload in (
            {"lastUserInput": "bonjour"},
            {"lastUserInput": "/gravity fast corrige le bug"},
            {"lastUserInput": "/gravity metrics"},
            {"lastUserInput": "/gravity-metrics-service"},
            {"lastUserInput": "/gravity-metrics /grill-me analyse tout ce depot"},
            {"lastUserInput": "/gravity-metrics voici une longue question libre de plus de soixante caracteres sans label explicite"},
        ):
            proc = subprocess.run(
                [sys.executable, "-B", str(hook_script)],
                input=json.dumps(payload), text=True, encoding="utf-8", errors="replace",
                capture_output=True, cwd=str(plugin_root), env=env, timeout=5, check=False,
            )
            if proc.returncode != 0:
                report.error(f"Dry-run hook échoué (rc={proc.returncode}) pour {payload!r}: {proc.stderr}")
                continue
            try:
                out = json.loads(proc.stdout.strip() or "null")
            except ValueError as exc:
                report.error(f"Sortie JSON invalide du hook pour {payload!r}: {exc}")
                continue
            if out != {}:
                report.error(f"Fast-path attendu {{}} pour {payload!r}, obtenu {out!r}")

        open_proc = subprocess.run(
            [sys.executable, "-B", str(hook_script)],
            input=json.dumps({"lastUserInput": "/gravity-metrics", "cwd": str(plugin_root)}),
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            cwd=str(plugin_root), env=env, timeout=5, check=False,
        )
        if open_proc.returncode != 0:
            report.error(f"Dry-run '/gravity-metrics' échoué (rc={open_proc.returncode}): {open_proc.stderr}")
        else:
            try:
                out = json.loads(open_proc.stdout.strip())
                steps = out.get("injectSteps") or []
                sys_obj = steps[0]["systemMessage"] if steps else {}
                umd = (sys_obj.get("metadata") or {}).get("user_markdown", "")
                smsg = sys_obj.get("systemMessage", "")
                if "terminationBehavior" in out or not open_proc.stdout.isascii() or not umd.strip() or "open_gravity_dashboard" not in smsg:
                    report.error(f"Réponse '/gravity-metrics' non conforme : {out!r}")
            except Exception as exc:
                report.error(f"Sortie JSON invalide pour '/gravity-metrics' : {exc}")

        bad_proc = subprocess.run(
            [sys.executable, "-B", str(hook_script)],
            input=json.dumps({"lastUserInput": "/gravity-metrics --invalide"}),
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            cwd=str(plugin_root), env=env, timeout=5, check=False,
        )
        if bad_proc.returncode != 0:
            report.error(f"Dry-run flag invalide échoué (rc={bad_proc.returncode}): {bad_proc.stderr}")
        else:
            try:
                out = json.loads(bad_proc.stdout.strip())
                steps = out.get("injectSteps") or []
                umd = steps[0]["systemMessage"]["metadata"]["user_markdown"] if steps else ""
                if out.get("terminationBehavior") != "terminate" or not bad_proc.stdout.isascii() or "Erreur" not in umd:
                    report.error(f"Réponse flag invalide non conforme : {out!r}")
            except Exception as exc:
                report.error(f"Sortie JSON invalide pour flag invalide : {exc}")


def _extra_metrics_checks(root: Path, report: Report) -> None:
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        names = [p.name for p in sorted(skills_dir.iterdir()) if p.is_dir()]
        if names != ["gravity-metrics"]:
            report.error(f"skills/ doit contenir uniquement 'gravity-metrics/' (trouvé : {names}).")
    _check_metrics_contract_extra(root, report)
    _check_hooks_extra(root, report)
    _run_hook_dry_runs(root, report)


_BASE_CFG = ValidatorConfig(
    plugin_root=PLUGIN_ROOT_DEFAULT,
    expected_plugin_name="gravity-metrics",
    expected_module_id="metrics",
    required_dirs=("assets", "config", "hooks", "metrics", "rules", "scripts", "skills", "_shared"),
    allowed_local_modules=ALLOWED_LOCAL_MODULES,
    extra_forbidden_patterns=(("terme hors périmètre", FORBIDDEN_TERM_RE),),
    require_module_contract=True,
    require_hooks_config=True,
    require_mcp_config=False,
    require_rules_agents=True,
    rules_warn_bytes=600,
    rules_max_bytes=1000,
    skill_max_bytes=6000,
    repo_agents_max_bytes=2500,
    extra_check_fn=_extra_metrics_checks,
)


def validate_plugin(root: Path, report: Report) -> None:
    """Audit plugin structure, manifest, module contract, hooks, stdlib, and dry-runs."""
    validate_plugin_tree(replace(_BASE_CFG, plugin_root=root), report)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return validator_core.run_cli(_BASE_CFG, argv, description="Validateur strict Gravity Metrics")


if __name__ == "__main__":
    raise SystemExit(main())
