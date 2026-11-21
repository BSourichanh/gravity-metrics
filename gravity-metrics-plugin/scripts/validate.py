#!/usr/bin/env python3
"""Validateur strict du plugin autonome Gravity Metrics (100 % stdlib Python 3)."""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT_DEFAULT = SCRIPT_DIR.parent

OFFICIAL_PLUGIN_FIELDS = {
    "name",
    "displayName",
    "version",
    "description",
    "logo",
    "suggestedPrompts",
    "author",
    "homepage",
    "repository",
    "disabled",
}
ALLOWED_LOCAL_MODULES = {"metrics", "hook_runtime", "cli", "validate"}
FLAT_HOOK_EVENTS = ("SessionStart", "PreInvocation", "PostInvocation", "Stop")
GROUPED_HOOK_EVENTS = ("PreToolUse", "PostToolUse")

FORBIDDEN_MOD_RE = re.compile(r"\bMod" + r"ule\s+0?\d\b", re.IGNORECASE)
FORBIDDEN_TERM_RE = re.compile(r"\bbench" + r"marks?\b", re.IGNORECASE)


class Report:
    """Collecteur d'erreurs, d'avertissements et d'informations de validation."""

    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.infos: List[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def ok(self, strict: bool) -> bool:
        return len(self.errors) == 0 and (not strict or len(self.warnings) == 0)


def has_real_python3() -> bool:
    """Vérifie la présence d'un vrai exécutable python3 (en ignorant le stub WindowsApps)."""
    found = shutil.which("python3")
    if not found:
        return False
    if os.name == "nt" and "windowsapps" in found.lower():
        return False
    return True


def _detect_portable_python_cmd() -> str:
    py_bin = shutil.which("python")
    if py_bin and not (os.name == "nt" and "windowsapps" in py_bin.lower()):
        return "python"
    if shutil.which("py"):
        return "py -3"
    return "python"


def _rewrite_python3_cmd(cmd: str, replacement: str) -> str:
    if cmd == "python3":
        return replacement
    if cmd.startswith("python3 "):
        return f"{replacement} {cmd[len('python3 '):]}"
    return cmd


def _extract_py_paths_from_cmd(cmd: str) -> List[str]:
    return re.findall(r"[^\s\"']+\.py\b", cmd or "")


def fix_python_cmd(plugin_root: Path) -> int:
    """Adapte 'python3' vers 'python' ou 'py -3' dans hooks.json sur une copie Windows."""
    if has_real_python3():
        print("[INFO]  Exécutable 'python3' réel détecté dans le PATH : aucune adaptation nécessaire.")
        return 0

    py_cmd = _detect_portable_python_cmd()
    hooks_json = plugin_root / "hooks.json"
    if not hooks_json.is_file():
        print(f"[INFO]  Aucun hooks.json dans {plugin_root}.")
        return 0

    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    changed = False
    if isinstance(data, dict):
        for group_cfg in data.values():
            if not isinstance(group_cfg, dict):
                continue
            for ev in GROUPED_HOOK_EVENTS:
                for entry in group_cfg.get(ev, []) or []:
                    if isinstance(entry, dict):
                        for h in entry.get("hooks", []) or []:
                            if isinstance(h, dict) and isinstance(h.get("command"), str):
                                new_cmd = _rewrite_python3_cmd(h["command"], py_cmd)
                                if new_cmd != h["command"]:
                                    h["command"] = new_cmd
                                    changed = True
            for ev in FLAT_HOOK_EVENTS:
                for h in group_cfg.get(ev, []) or []:
                    if isinstance(h, dict) and isinstance(h.get("command"), str):
                        new_cmd = _rewrite_python3_cmd(h["command"], py_cmd)
                        if new_cmd != h["command"]:
                            h["command"] = new_cmd
                            changed = True
    if changed:
        hooks_json.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"[FIXED] {hooks_json} -> '{py_cmd}'")
    else:
        print(f"[INFO]  Aucun changement requis dans {hooks_json}.")
    return 0


def _configure_stdio() -> None:
    """Force stdout/stderr en UTF-8 pour éviter les erreurs cp1252 sous Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _check_stdlib_only(py_file: Path, report: Report) -> None:
    stdlib_names_raw = getattr(sys, "stdlib_module_names", None)
    if not stdlib_names_raw:
        report.error("Python 3.10+ requis pour la validation stdlib.")
        return
    stdlib_names: Set[str] = set(stdlib_names_raw)
    allowed = stdlib_names | ALLOWED_LOCAL_MODULES
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except SyntaxError as exc:
        report.error(f"Syntaxe Python invalide dans {py_file}: {exc}")
        return
    for node in ast.walk(tree):
        mods: List[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [node.module.split(".")[0]]
        for mod in mods:
            if mod not in allowed:
                report.error(f"Import non stdlib {mod!r} détecté dans {py_file}")


def _run_hook_dry_runs(plugin_root: Path, report: Report) -> None:
    hook_script = plugin_root / "hooks" / "pre_invocation_metrics.py"
    if not hook_script.is_file():
        report.error(f"Script hook introuvable : {hook_script}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["GRAVITY_METRICS_HOME"] = str(Path(tmp) / "store")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        passthrough_inputs = [
            {"lastUserInput": "bonjour"},
            {"lastUserInput": "/gravity fast corrige le bug"},
            {"lastUserInput": "/gravity metrics-service"},
        ]
        for payload in passthrough_inputs:
            proc = subprocess.run(
                [sys.executable, "-B", str(hook_script)],
                input=json.dumps(payload),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                cwd=str(plugin_root),
                env=env,
                timeout=5,
                check=False,
            )
            if proc.returncode != 0:
                report.error(f"Dry-run hook échoué (rc={proc.returncode}) pour {payload!r}: {proc.stderr}")
                continue
            try:
                out = json.loads(proc.stdout.strip() or "null")
            except Exception as exc:
                report.error(f"Sortie JSON invalide du hook pour {payload!r}: {exc}")
                continue
            if out != {}:
                report.error(f"Fast-path attendu {{}} pour {payload!r}, obtenu {out!r}")

        help_proc = subprocess.run(
            [sys.executable, "-B", str(hook_script)],
            input=json.dumps({"lastUserInput": "/gravity metrics help"}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(plugin_root),
            env=env,
            timeout=5,
            check=False,
        )
        if help_proc.returncode != 0:
            report.error(f"Dry-run '/gravity metrics help' échoué (rc={help_proc.returncode}): {help_proc.stderr}")
        else:
            try:
                out = json.loads(help_proc.stdout.strip())
                steps = out.get("injectSteps") or []
                umd = steps[0]["systemMessage"]["metadata"]["user_markdown"] if steps else ""
                if out.get("terminationBehavior") != "terminate" or not umd.strip():
                    report.error(f"Réponse '/gravity metrics help' non conforme : {out!r}")
            except Exception as exc:
                report.error(f"Sortie JSON invalide pour '/gravity metrics help' : {exc}")


def validate_plugin(root: Path, report: Report) -> None:
    """Audite la structure, le manifeste, les règles, les hooks et la conformité stdlib."""
    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.error(f"plugin.json invalide : {exc}")
        return

    if not isinstance(manifest, dict):
        report.error("plugin.json doit contenir un objet JSON.")
        return

    unknown_keys = sorted(set(manifest.keys()) - OFFICIAL_PLUGIN_FIELDS)
    if unknown_keys:
        report.error(f"plugin.json : champs non officiels détectés : {unknown_keys}")

    if manifest.get("name") != "gravity-metrics":
        report.error(f"plugin.json : 'name' doit être 'gravity-metrics', obtenu {manifest.get('name')!r}.")

    desc = str(manifest.get("description") or "")
    if not desc or len(desc) > 160:
        report.error(f"plugin.json : 'description' vide ou > 160 caractères ({len(desc)}).")

    prompts = manifest.get("suggestedPrompts")
    if not isinstance(prompts, list) or len(prompts) != 3 or not all(isinstance(p, str) and p.strip() for p in prompts):
        report.warn("plugin.json : exactement 3 'suggestedPrompts' non vides sont recommandés.")

    logo_rel = str(manifest.get("logo") or "")
    logo_path = root / logo_rel
    if not logo_rel or not logo_path.is_file():
        report.error(f"Logo introuvable : {logo_rel!r}")
    elif logo_path.suffix.lower() == ".svg":
        svg = logo_path.read_text(encoding="utf-8", errors="replace")
        vb = re.search(
            r"""viewBox\s*=\s*["']0\s+0\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)["']""",
            svg,
        )
        if not vb or float(vb.group(1)) <= 0 or float(vb.group(1)) != float(vb.group(2)):
            report.error(f"Logo {logo_rel} : viewBox carré 1:1 non détecté.")

    if (root / "skills").exists():
        report.error("Le dossier skills/ ne doit pas exister (aucune commande /metrics séparée).")

    rules_file = root / "rules" / "AGENTS.md"
    if not rules_file.is_file():
        report.error("rules/AGENTS.md introuvable.")
    else:
        raw_bytes = rules_file.read_bytes()
        if len(raw_bytes) >= 2048:
            report.error(f"rules/AGENTS.md dépasse 2048 octets ({len(raw_bytes)}).")
        rules_txt = raw_bytes.decode("utf-8", errors="replace")
        if rules_txt.lstrip().startswith("---"):
            report.error("rules/AGENTS.md ne doit pas contenir de frontmatter YAML.")

    hooks_json = root / "hooks.json"
    if not hooks_json.is_file():
        report.error("hooks.json introuvable.")
    else:
        try:
            hooks_raw = hooks_json.read_text(encoding="utf-8")
            hooks_data = json.loads(hooks_raw)
            if not isinstance(hooks_data, dict) or not hooks_data:
                report.error("hooks.json doit être un objet JSON non vide.")
            else:
                pre_inv_found = False
                for gname, gcfg in hooks_data.items():
                    if not isinstance(gcfg, dict):
                        report.error(f"hooks.json ({gname}) doit être un objet.")
                        continue
                    for ev in FLAT_HOOK_EVENTS:
                        if ev not in gcfg:
                            continue
                        entries = gcfg[ev]
                        if not isinstance(entries, list):
                            report.error(f"hooks.json ({gname}.{ev}) doit être un tableau plat.")
                            continue
                        for h in entries:
                            if not isinstance(h, dict) or "hooks" in h or "command" not in h:
                                report.error(
                                    f"hooks.json ({gname}.{ev}) doit contenir des handlers plats sans wrapper 'hooks'."
                                )
                                continue
                            if ev == "PreInvocation":
                                pre_inv_found = True
                            for py_rel in _extract_py_paths_from_cmd(str(h.get("command", ""))):
                                target_py = (root / py_rel).resolve()
                                if not target_py.is_file():
                                    report.error(f"hooks.json ({gname}.{ev}) : script introuvable {py_rel!r}")
                if not pre_inv_found:
                    report.error("hooks.json doit déclarer au moins un handler PreInvocation.")
                if os.name == "nt" and ('"python3 ' in hooks_raw or '"python3"' in hooks_raw) and not has_real_python3():
                    report.info(
                        "Configuration portable ('python3') détectée sous Windows : adaptée automatiquement lors de install.py."
                    )
        except Exception as exc:
            report.error(f"hooks.json invalide : {exc}")

    catalog_path = root / "config" / "pricing_catalog.json"
    if not catalog_path.is_file():
        report.error("config/pricing_catalog.json introuvable.")
    else:
        try:
            cat = json.loads(catalog_path.read_text(encoding="utf-8"))
            if not isinstance(cat, dict) or "2026_nov_standard" not in (cat.get("profiles") or {}):
                report.error("config/pricing_catalog.json : profil '2026_nov_standard' manquant.")
        except Exception as exc:
            report.error(f"config/pricing_catalog.json invalide : {exc}")

    for sub in ("metrics", "hooks", "scripts"):
        if not (root / sub).is_dir():
            report.error(f"Dossier requis manquant : {sub}/")

    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        _check_stdlib_only(py_file, report)

    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file() or "__pycache__" in fpath.parts:
            continue
        if fpath.suffix.lower() not in (".py", ".md", ".json", ".html", ".svg"):
            continue
        content = fpath.read_text(encoding="utf-8", errors="replace")
        if FORBIDDEN_MOD_RE.search(content):
            report.error(f"Jargon interdit détecté dans {fpath}")
        if FORBIDDEN_TERM_RE.search(content):
            report.error(f"Terme hors périmètre détecté dans {fpath}")

    _run_hook_dry_runs(root, report)


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Validateur Gravity Metrics")
    parser.add_argument("--strict", action="store_true", help="Échoue également sur les avertissements.")
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=PLUGIN_ROOT_DEFAULT,
        help="Racine du plugin gravity-metrics à auditer.",
    )
    parser.add_argument(
        "--fix-python-cmd",
        action="store_true",
        help="Adapte 'python3' vers 'python' ou 'py -3' dans hooks.json sous Windows.",
    )
    args = parser.parse_args(argv)
    plugin_root = args.plugin_root.expanduser().resolve()

    if args.fix_python_cmd:
        return fix_python_cmd(plugin_root)

    report = Report()
    validate_plugin(plugin_root, report)

    for info in report.infos:
        print(f"[INFO]  {info}")
    for err in report.errors:
        print(f"[ERROR] {err}")
    for warn in report.warnings:
        print(f"[WARN]  {warn}")

    if report.ok(args.strict):
        print(f"[PASS] Validation Plugin réussie (0 erreur, {len(report.warnings)} avertissement).")
        return 0
    print(
        f"[FAIL] Validation Plugin échouée ({len(report.errors)} erreur(s), {len(report.warnings)} avertissement(s))."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
