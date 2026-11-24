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
REQUIRED_PLUGIN_FIELDS = OFFICIAL_PLUGIN_FIELDS - {"disabled"}
REQUIRED_MODULE_FIELDS = {
    "schema_version",
    "id",
    "plugin_name",
    "role",
    "storage",
    "commands",
    "capabilities",
    "ui",
    "signals",
}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
UTF8_BOM = b"\xef\xbb\xbf"
RULES_WARN_BYTES = 600
RULES_MAX_BYTES = 1000
SKILL_MAX_BYTES = 6000
ROOT_AGENTS_MAX_BYTES = 2500
ALLOWED_LOCAL_MODULES = {"metrics", "bridge_links", "hook_runtime", "cli", "validate", "mcp", "widget_html"}
ALLOWED_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp", "mcp-app"})
SUGGESTED_PROMPT_MAX_LEN = 100
SUGGESTED_PROMPT_RE = re.compile(r"^/[a-z0-9:_-]+(?:\s+[^—\r\n]+?)?\s+—\s+\S")
FLAT_HOOK_EVENTS = ("SessionStart", "PreInvocation", "PostInvocation", "Stop")
GROUPED_HOOK_EVENTS = ("PreToolUse", "PostToolUse")

FORBIDDEN_MOD_RE = re.compile(r"\bMod" + r"ule\s+0?\d\b", re.IGNORECASE)
FORBIDDEN_TERM_RE = re.compile(r"\bbench" + r"marks?\b", re.IGNORECASE)
PERSONAL_PATH_RE = re.compile(
    r"(?:"
    + r"[A-Za-z]:[/\\]Use"
    + r"rs[/\\][^\s\"'`<>]+"
    + r"|"
    + r"(?<![A-Za-z0-9_.:/~-])"
    + r"/Use"
    + r"rs/[^\s\"'`<>]+"
    + r"|"
    + r"(?<![A-Za-z0-9_.:/~-])"
    + r"/ho"
    + r"me/[^\s\"'`<>]+"
    + r")"
)
FILE_URI_RE = re.compile(r"fi" + r"le://", re.IGNORECASE)


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
    """Adapte 'python3' vers 'python' ou 'py -3' dans hooks.json et mcp_config.json sur une copie Windows."""
    if has_real_python3():
        print("[INFO]  Exécutable 'python3' réel détecté dans le PATH : aucune adaptation nécessaire.")
        return 0

    py_cmd = _detect_portable_python_cmd()
    any_changed = False

    hooks_json = plugin_root / "hooks.json"
    if hooks_json.is_file():
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
            hooks_json.write_bytes(
                (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            )
            print(f"[FIXED] {hooks_json} -> '{py_cmd}'")
            any_changed = True

    mcp_json = plugin_root / "mcp_config.json"
    if mcp_json.is_file():
        mdata = json.loads(mcp_json.read_text(encoding="utf-8"))
        mchanged = False
        servers = mdata.get("mcpServers") if isinstance(mdata, dict) else None
        if isinstance(servers, dict):
            for scfg in servers.values():
                if not isinstance(scfg, dict):
                    continue
                if scfg.get("command") == "python3":
                    args = list(scfg.get("args") or [])
                    if py_cmd == "py -3":
                        scfg["command"] = "py"
                        if not args or args[0] != "-3":
                            scfg["args"] = ["-3", *args]
                    else:
                        scfg["command"] = py_cmd
                    mchanged = True
        if mchanged:
            mcp_json.write_bytes(
                (json.dumps(mdata, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            )
            print(f"[FIXED] {mcp_json} -> '{py_cmd}'")
            any_changed = True

    if not any_changed:
        print(f"[INFO]  Aucun changement requis dans {plugin_root}.")
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


def _check_utf8_no_bom_lf(
    path: Path,
    label: str,
    report: Report,
    checked_paths: Optional[Set[Path]] = None,
) -> Optional[str]:
    """Lit un fichier texte en vérifiant UTF-8 sans BOM et fins de ligne LF."""
    if checked_paths is not None:
        checked_paths.add(path.resolve())
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        report.error(f"{label} illisible : {exc}")
        return None
    if raw_bytes.startswith(UTF8_BOM):
        report.error(f"{label} ne doit pas contenir de BOM UTF-8.")
    if b"\r\n" in raw_bytes or b"\r" in raw_bytes:
        report.error(f"{label} doit utiliser des fins de ligne LF (CRLF détecté).")
    body = raw_bytes[len(UTF8_BOM) :] if raw_bytes.startswith(UTF8_BOM) else raw_bytes
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.error(f"{label} UTF-8 invalide : {exc}")
        return None


def _read_markdown_d4(
    path: Path,
    label: str,
    report: Report,
    *,
    max_bytes: int,
    warn_bytes: Optional[int] = None,
    checked_paths: Optional[Set[Path]] = None,
) -> Optional[str]:
    """Lit un fichier Markdown en vérifiant UTF-8 sans BOM, LF et seuils D4 en octets."""
    txt = _check_utf8_no_bom_lf(path, label, report, checked_paths=checked_paths)
    if txt is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        size = len(txt.encode("utf-8"))
    if size > max_bytes:
        report.error(f"{label} dépasse {max_bytes} octets ({size}).")
    elif warn_bytes is not None and size > warn_bytes:
        report.warn(f"{label} dépasse le seuil recommandé de {warn_bytes} octets ({size}).")
    return txt


def _validate_skills(
    root: Path,
    report: Report,
    *,
    checked_paths: Optional[Set[Path]] = None,
) -> None:
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        report.error("Dossier requis manquant : skills/gravity-metrics/")
        return

    entries = [p for p in sorted(skills_dir.iterdir()) if p.is_dir()]
    names = [p.name for p in entries]
    if names != ["gravity-metrics"]:
        report.error(
            f"skills/ doit contenir uniquement 'gravity-metrics/' (trouvé : {names})."
        )
        return

    skill_md = skills_dir / "gravity-metrics" / "SKILL.md"
    if not skill_md.is_file():
        report.error("skills/gravity-metrics/SKILL.md introuvable.")
        return

    txt = _read_markdown_d4(
        skill_md,
        "skills/gravity-metrics/SKILL.md",
        report,
        max_bytes=SKILL_MAX_BYTES,
        checked_paths=checked_paths,
    )
    if txt is None:
        return
    fm_match = re.match(r"^\s*---\r?\n(.*?)\r?\n---\r?\n", txt, re.DOTALL)
    if not fm_match:
        report.error("skills/gravity-metrics/SKILL.md : frontmatter YAML manquant.")
        return
    fm = fm_match.group(1)
    if not re.search(r"^name:\s*gravity-metrics\s*$", fm, re.MULTILINE):
        report.error("skills/gravity-metrics/SKILL.md : 'name: gravity-metrics' manquant.")
    desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
    if not desc_m or not desc_m.group(1).strip():
        report.error("skills/gravity-metrics/SKILL.md : 'description' manquante ou vide.")


def _validate_mcp_config(root: Path, report: Report) -> None:
    mcp_json = root / "mcp_config.json"
    if not mcp_json.is_file():
        report.error("mcp_config.json introuvable à la racine du plugin.")
        return

    try:
        mcp_raw = mcp_json.read_text(encoding="utf-8")
        mcp_data = json.loads(mcp_raw)
    except Exception as exc:
        report.error(f"mcp_config.json invalide : {exc}")
        return

    if not isinstance(mcp_data, dict) or not isinstance(mcp_data.get("mcpServers"), dict):
        report.error("mcp_config.json doit contenir un objet 'mcpServers'.")
        return

    servers = mcp_data["mcpServers"]
    if "dashboard" not in servers or not isinstance(servers["dashboard"], dict):
        report.error("mcp_config.json : serveur 'dashboard' manquant.")
        return

    for sname, scfg in servers.items():
        if not isinstance(scfg, dict):
            report.error(f"mcp_config.json ({sname}) doit être un objet.")
            continue
        cmd = scfg.get("command")
        args = scfg.get("args")
        if not isinstance(cmd, str) or not cmd.strip():
            report.error(f"mcp_config.json ({sname}) : 'command' manquant.")
        if not isinstance(args, list):
            report.error(f"mcp_config.json ({sname}) : 'args' doit être une liste.")
            continue
        for arg in args:
            if isinstance(arg, str) and arg.endswith(".py"):
                rel = arg.replace("${PLUGIN_ROOT}/", "").replace("${PLUGIN_ROOT}\\", "").lstrip("/\\")
                if not (root / rel).is_file():
                    report.error(f"mcp_config.json ({sname}) : script introuvable {arg!r}")

    if os.name == "nt" and '"command": "python3"' in mcp_raw and not has_real_python3():
        report.info(
            "mcp_config.json portable ('python3') détecté sous Windows : adapté automatiquement lors de install.py."
        )


def _run_hook_dry_runs(plugin_root: Path, report: Report) -> None:
    hook_script = plugin_root / "hooks" / "pre_invocation_metrics.py"
    if not hook_script.is_file():
        report.error(f"Script hook introuvable : {hook_script}")
        return
    start_hook = plugin_root / "hooks" / "session_start_metrics.py"
    if not start_hook.is_file():
        report.error(f"Script hook SessionStart introuvable : {start_hook}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        brain_dir = Path(tmp) / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["GRAVITY_METRICS_HOME"] = str(Path(tmp) / "store")
        env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        s_proc = subprocess.run(
            [sys.executable, "-B", str(start_hook)],
            input=json.dumps({"conversationId": "val-sess-1", "cwd": str(plugin_root)}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(plugin_root),
            env=env,
            timeout=5,
            check=False,
        )
        if s_proc.returncode != 0 or (s_proc.stdout.strip() != "{}"):
            report.error(
                f"Dry-run SessionStart invalide (rc={s_proc.returncode}, out={s_proc.stdout!r}, err={s_proc.stderr!r})"
            )

        passthrough_inputs = [
            {"lastUserInput": "bonjour"},
            {"lastUserInput": "/gravity fast corrige le bug"},
            {"lastUserInput": "/gravity metrics"},
            {"lastUserInput": "/gravity-metrics-service"},
            {"lastUserInput": "/gravity-metrics /grill-me analyse tout ce depot"},
            {"lastUserInput": "/gravity-metrics voici une longue question libre de plus de soixante caracteres sans label explicite"},
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

        open_proc = subprocess.run(
            [sys.executable, "-B", str(hook_script)],
            input=json.dumps({"lastUserInput": "/gravity-metrics", "cwd": str(plugin_root)}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(plugin_root),
            env=env,
            timeout=5,
            check=False,
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
                if (
                    "terminationBehavior" in out
                    or not open_proc.stdout.isascii()
                    or not umd.strip()
                    or "open_metrics_dashboard" not in smsg
                ):
                    report.error(f"Réponse '/gravity-metrics' non conforme : {out!r}")
            except Exception as exc:
                report.error(f"Sortie JSON invalide pour '/gravity-metrics' : {exc}")

        bad_proc = subprocess.run(
            [sys.executable, "-B", str(hook_script)],
            input=json.dumps({"lastUserInput": "/gravity-metrics --invalide"}),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(plugin_root),
            env=env,
            timeout=5,
            check=False,
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


def _run_mcp_dry_run(plugin_root: Path, report: Report) -> None:
    server_script = plugin_root / "mcp" / "server.py"
    if not server_script.is_file():
        report.error(f"Serveur MCP introuvable : {server_script}")
        return

    with tempfile.TemporaryDirectory() as tmp:
        brain_dir = Path(tmp) / "brain"
        brain_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["GRAVITY_METRICS_HOME"] = str(Path(tmp) / "store")
        env["ANTIGRAVITY_BRAIN_DIR"] = str(brain_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "val", "version": "1.0"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "ui://gravity-metrics/dashboard.html"}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "open_metrics_dashboard", "arguments": {}}},
        ]
        stdin_payload = "\n".join(json.dumps(r) for r in requests) + "\n"
        proc = subprocess.run(
            [sys.executable, "-B", str(server_script)],
            input=stdin_payload,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(plugin_root),
            env=env,
            timeout=8,
            check=False,
        )
        if proc.returncode != 0:
            report.error(f"Dry-run MCP échoué (rc={proc.returncode}): {proc.stderr}")
            return

        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if len(lines) != 5:
            report.error(f"Dry-run MCP : 5 réponses attendues, obtenu {len(lines)} ({proc.stderr})")
            return

        try:
            resps = {obj["id"]: obj["result"] for obj in (json.loads(ln) for ln in lines)}
        except Exception as exc:
            report.error(f"Dry-run MCP : JSON-RPC invalide ({exc})")
            return

        tools = (resps.get(2) or {}).get("tools") or []
        open_tool = next((t for t in tools if t.get("name") == "open_metrics_dashboard"), None)
        if not open_tool:
            report.error("MCP tools/list : 'open_metrics_dashboard' manquant.")
        else:
            meta = open_tool.get("_meta") or {}
            ui_uri = (meta.get("ui") or {}).get("resourceUri")
            eps = (meta.get("com.google.antigravity/ui") or {}).get("entrypoints") or []
            ep_types = {e.get("type") for e in eps if isinstance(e, dict)}
            if ui_uri != "ui://gravity-metrics/dashboard.html":
                report.error(f"MCP resourceUri invalide : {ui_uri!r}")
            if not {"aux_pane", "global"}.issubset(ep_types):
                report.error(f"MCP entrypoints incomplets : {ep_types!r}")

        contents = (resps.get(4) or {}).get("contents") or []
        if not contents:
            report.error("MCP resources/read : contenu vide.")
        else:
            c0 = contents[0]
            html_txt = str(c0.get("text") or "")
            if c0.get("mimeType") != "text/html;profile=mcp-app":
                report.error(f"MCP mimeType invalide : {c0.get('mimeType')!r}")
            if not html_txt or len(html_txt.encode("utf-8")) >= 10 * 1024 * 1024:
                report.error("MCP HTML vide ou >= 10 MiB.")
            if "window.confirm" in html_txt or "https://" in html_txt or "http://" in html_txt:
                report.error("MCP HTML : dialogue natif ou URL externe détecté (CSP).")

        call_res = resps.get(5) or {}
        if call_res.get("isError") or not isinstance((call_res.get("structuredContent") or {}).get("records"), list):
            report.error(f"MCP open_metrics_dashboard résultat invalide : {call_res!r}")


def _read_json_no_bom(
    path: Path,
    label: str,
    report: Report,
    *,
    checked_paths: Optional[Set[Path]] = None,
) -> Optional[Dict[str, Any]]:
    """Lit un fichier JSON objet en vérifiant UTF-8 sans BOM et fins de ligne LF."""
    text = _check_utf8_no_bom_lf(path, label, report, checked_paths=checked_paths)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except Exception as exc:
        report.error(f"{label} invalide : {exc}")
        return None
    if not isinstance(data, dict):
        report.error(f"{label} doit contenir un objet JSON.")
        return None
    return data


def _is_str_list(val: Any, *, allow_empty: bool = False) -> bool:
    if not isinstance(val, list):
        return False
    if not allow_empty and not val:
        return False
    return all(isinstance(item, str) and bool(item.strip()) for item in val)


def _validate_module_contract(
    root: Path,
    manifest: Dict[str, Any],
    report: Report,
    *,
    checked_paths: Optional[Set[Path]] = None,
) -> None:
    """Valide le contrat modulaire v1 dans config/module.json."""
    module_path = root / "config" / "module.json"
    if not module_path.is_file():
        report.error("config/module.json introuvable.")
        return

    data = _read_json_no_bom(
        module_path, "config/module.json", report, checked_paths=checked_paths
    )
    if data is None:
        return

    missing = sorted(REQUIRED_MODULE_FIELDS - set(data.keys()))
    if missing:
        report.error(f"config/module.json : champs requis manquants : {missing}")
    unknown = sorted(set(data.keys()) - REQUIRED_MODULE_FIELDS)
    if unknown:
        report.error(f"config/module.json : champs inconnus détectés : {unknown}")

    sv = data.get("schema_version")
    if isinstance(sv, bool) or sv != 1:
        report.error(f"config/module.json : 'schema_version' doit être 1, obtenu {sv!r}.")

    if data.get("id") != "metrics":
        report.error(f"config/module.json : 'id' doit être 'metrics', obtenu {data.get('id')!r}.")

    expected_pname = manifest.get("name") or "gravity-metrics"
    if data.get("plugin_name") != expected_pname:
        report.error(
            f"config/module.json : 'plugin_name' doit être {expected_pname!r}, obtenu {data.get('plugin_name')!r}."
        )

    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        report.error("config/module.json : 'role' doit être une chaîne non vide.")

    storage = data.get("storage")
    if not isinstance(storage, dict):
        report.error("config/module.json : 'storage' doit être un objet.")
    else:
        if storage.get("subdir") != "metrics":
            report.error(
                f"config/module.json : 'storage.subdir' doit être 'metrics', obtenu {storage.get('subdir')!r}."
            )
        if storage.get("env_override") != "GRAVITY_METRICS_HOME":
            report.error(
                "config/module.json : 'storage.env_override' doit être 'GRAVITY_METRICS_HOME', "
                f"obtenu {storage.get('env_override')!r}."
            )

    commands = data.get("commands")
    if not _is_str_list(commands) or "/gravity-metrics" not in commands or not all(
        c.startswith("/") for c in commands
    ):
        report.error("config/module.json : 'commands' doit lister des commandes '/' incluant '/gravity-metrics'.")

    if not _is_str_list(data.get("capabilities")):
        report.error("config/module.json : 'capabilities' doit être une liste non vide de chaînes.")

    ui = data.get("ui")
    if not isinstance(ui, dict):
        report.error("config/module.json : 'ui' doit être un objet.")
    else:
        ui_mode = ui.get("mode")
        if not isinstance(ui_mode, str) or not ui_mode.strip():
            report.error("config/module.json : 'ui.mode' doit être une chaîne non vide.")
        elif ui_mode not in ALLOWED_UI_MODES:
            report.error(
                f"config/module.json : 'ui.mode' invalide ({ui_mode!r}), attendu dans {sorted(ALLOWED_UI_MODES)}."
            )
        if not isinstance(ui.get("target"), str) or not ui["target"].strip():
            report.error("config/module.json : 'ui.target' doit être une chaîne non vide.")

    signals = data.get("signals")
    if not isinstance(signals, dict):
        report.error("config/module.json : 'signals' doit être un objet.")
    else:
        if not _is_str_list(signals.get("produces"), allow_empty=True):
            report.error("config/module.json : 'signals.produces' doit être une liste de chaînes.")
        if not _is_str_list(signals.get("consumes"), allow_empty=True):
            report.error("config/module.json : 'signals.consumes' doit être une liste de chaînes.")


def validate_plugin(root: Path, report: Report) -> None:
    """Audite la structure, le manifeste, le contrat module, les règles, les hooks, le serveur MCP App et la conformité stdlib."""
    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return

    checked_utf8: Set[Path] = set()
    manifest = _read_json_no_bom(
        manifest_path, "plugin.json", report, checked_paths=checked_utf8
    )
    if manifest is None:
        return

    missing_keys = sorted(REQUIRED_PLUGIN_FIELDS - set(manifest.keys()))
    if missing_keys:
        report.error(f"plugin.json : champs officiels requis manquants : {missing_keys}")

    unknown_keys = sorted(set(manifest.keys()) - OFFICIAL_PLUGIN_FIELDS)
    if unknown_keys:
        report.error(f"plugin.json : champs non officiels détectés : {unknown_keys}")

    if manifest.get("name") != "gravity-metrics":
        report.error(f"plugin.json : 'name' doit être 'gravity-metrics', obtenu {manifest.get('name')!r}.")

    display_name = manifest.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        report.error("plugin.json : 'displayName' doit être une chaîne non vide.")

    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version.strip()):
        report.error(f"plugin.json : 'version' SemVer invalide : {version!r}.")

    desc = str(manifest.get("description") or "")
    if not desc or len(desc) > 160:
        report.error(f"plugin.json : 'description' vide ou > 160 caractères ({len(desc)}).")

    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip():
        report.error("plugin.json : 'author.name' doit être une chaîne non vide.")

    for url_key in ("homepage", "repository"):
        val = manifest.get(url_key)
        if not isinstance(val, str) or not val.strip():
            report.error(f"plugin.json : '{url_key}' doit être une chaîne non vide.")

    prompts = manifest.get("suggestedPrompts")
    if not isinstance(prompts, list) or len(prompts) != 3 or not all(isinstance(p, str) and p.strip() for p in prompts):
        report.error("plugin.json : exactement 3 'suggestedPrompts' non vides sont requis.")
    else:
        for idx, prompt in enumerate(prompts):
            if len(prompt) > SUGGESTED_PROMPT_MAX_LEN:
                report.error(
                    f"plugin.json : suggestedPrompts[{idx}] dépasse {SUGGESTED_PROMPT_MAX_LEN} caractères ({len(prompt)})."
                )
            if not SUGGESTED_PROMPT_RE.match(prompt):
                report.error(
                    f"plugin.json : suggestedPrompts[{idx}] doit respecter le format '/cmd — résumé' ({prompt!r})."
                )

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

    _validate_module_contract(root, manifest, report, checked_paths=checked_utf8)
    _validate_skills(root, report, checked_paths=checked_utf8)
    _validate_mcp_config(root, report)

    rules_file = root / "rules" / "AGENTS.md"
    if not rules_file.is_file():
        report.error("rules/AGENTS.md introuvable.")
    else:
        rules_txt = _read_markdown_d4(
            rules_file,
            "rules/AGENTS.md",
            report,
            max_bytes=RULES_MAX_BYTES,
            warn_bytes=RULES_WARN_BYTES,
            checked_paths=checked_utf8,
        )
        if rules_txt is not None and rules_txt.lstrip().startswith("---"):
            report.error("rules/AGENTS.md ne doit pas contenir de frontmatter YAML.")

    root_agents = root.parent / "AGENTS.md"
    if root.name == "plugin" and root_agents.is_file():
        _read_markdown_d4(
            root_agents,
            "AGENTS.md racine",
            report,
            max_bytes=ROOT_AGENTS_MAX_BYTES,
            checked_paths=checked_utf8,
        )

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

    for sub in ("metrics", "hooks", "mcp", "scripts", "skills"):
        if not (root / sub).is_dir():
            report.error(f"Dossier requis manquant : {sub}/")

    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        _check_stdlib_only(py_file, report)

    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file():
            continue
        rel = fpath.relative_to(root)
        if "__pycache__" in rel.parts or ".git" in rel.parts:
            continue
        suffix = fpath.suffix.lower()
        if suffix in (".py", ".md", ".json") and fpath.resolve() not in checked_utf8:
            _check_utf8_no_bom_lf(fpath, rel.as_posix(), report, checked_paths=checked_utf8)
        if suffix not in (".py", ".md", ".json", ".html", ".svg"):
            continue
        content = fpath.read_text(encoding="utf-8", errors="replace")
        if FORBIDDEN_MOD_RE.search(content):
            report.error(f"Jargon interdit détecté dans {fpath}")
        if FORBIDDEN_TERM_RE.search(content):
            report.error(f"Terme hors périmètre détecté dans {fpath}")
        if "tests" not in rel.parts:
            if PERSONAL_PATH_RE.search(content):
                report.error(f"Fuite de chemin personnel OS détectée dans {rel.as_posix()}")
            if FILE_URI_RE.search(content):
                report.error(f"URI locale interdite détectée dans {rel.as_posix()}")

    _run_hook_dry_runs(root, report)
    _run_mcp_dry_run(root, report)


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
        help="Adapte 'python3' vers 'python' ou 'py -3' dans hooks.json et mcp_config.json sous Windows.",
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
