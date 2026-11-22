#!/usr/bin/env python3
"""Serveur MCP App stdio JSON-RPC 2.0 pour Gravity Metrics (100 % stdlib Python 3)."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

MCP_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = MCP_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from metrics import (  # noqa: E402
    PendingBridgeJobsError,
    apply_remove,
    archive_conversation,
    default_brain_dir,
    list_pending_sessions,
    load_history,
    load_session_for_inspect,
    resolve_session_index,
    resolve_store_paths,
    slugify_label,
)
from widget_html import render_mcp_widget_html  # noqa: E402

SERVER_NAME = "gravity-metrics"
SERVER_VERSION = "0.3.0"
APP_URI = "ui://gravity-metrics/dashboard.html"
APP_MIME = "text/html;profile=mcp-app"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
MAX_TRAJECTORY_BYTES = 450_000


def _history_signature(history_path: Path) -> str:
    if not history_path.is_file():
        return "missing"
    try:
        st = history_path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "error"


def _canonical_projects_map(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """Associe chaque projet normalisé (casefold) à sa casse canonique la plus fréquente."""
    grouped: Dict[str, Counter] = defaultdict(Counter)
    for r in records:
        p = str(r.get("project") or "").strip()
        if p:
            grouped[p.casefold()][p] += 1
    return {
        cf: min(counts.items(), key=lambda kv: (-kv[1], kv[0] != kv[0].lower(), kv[0]))[0]
        for cf, counts in grouped.items()
    }


def _collect_projects(records: List[Dict[str, Any]]) -> List[str]:
    return sorted(_canonical_projects_map(records).values(), key=lambda s: (s.casefold(), s))


def _file_uri_if_exists(path: Path) -> str:
    try:
        if path.is_file():
            return path.resolve().as_uri()
    except OSError:
        pass
    return ""


def _resolve_slug_for_rec(rec: Optional[Dict[str, Any]], cid: str, label: str) -> str:
    if isinstance(rec, dict) and rec.get("detail_slug"):
        return str(rec["detail_slug"])
    return slugify_label(label, f"session-{cid[:8]}")


def _pending_signature(pending: List[Dict[str, Any]]) -> str:
    compact = [
        (
            p.get("conversation_id"),
            p.get("llm_calls"),
            p.get("bridge_children_count"),
            p.get("bridge_pending_jobs_count"),
            p.get("updated_at"),
        )
        for p in pending
    ]
    raw = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _load_state_snapshot(
    store_dir: Path, history_path: Path
) -> Tuple[str, List[Dict[str, Any]], List[str], str, List[Dict[str, Any]]]:
    hist_sig = _history_signature(history_path)
    records = load_history(history_path)
    projects = _collect_projects(records)
    metrics_md_uri = _file_uri_if_exists(store_dir / "METRICS.md")
    pending_sessions = list_pending_sessions(default_brain_dir(), history_path)
    sig = f"{hist_sig}|p:{_pending_signature(pending_sessions)}"
    return sig, records, projects, metrics_md_uri, pending_sessions


def _tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "open_metrics_dashboard",
            "title": "Gravity Metrics",
            "description": (
                "Ouvre le tableau de bord interactif Gravity Metrics dans le panneau latéral "
                "ou en pleine page. Tous les arguments sont optionnels."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Nom du projet actif à filtrer par défaut (optionnel).",
                    },
                    "target": {
                        "type": "string",
                        "description": "Label ou UUID d'une session à afficher directement en vue détail (optionnel).",
                    },
                    "view": {
                        "type": "string",
                        "enum": ["history", "detail"],
                        "description": "Vue initiale souhaitée : history ou detail (optionnel).",
                    },
                },
            },
            "_meta": {
                "ui": {
                    "resourceUri": APP_URI,
                    "visibility": ["model", "app"],
                },
                "com.google.antigravity/ui": {
                    "entrypoints": [
                        {"type": "aux_pane", "title": "Gravity Metrics"},
                        {"type": "global", "title": "Gravity Metrics"},
                    ]
                },
            },
        },
        {
            "name": "metrics_get_state",
            "title": "Lire l'état Gravity Metrics",
            "description": "Retourne l'historique consolidé et les sessions en attente pour le widget.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since_signature": {
                        "type": "string",
                        "description": "Signature précédente pour éviter de renvoyer des données inchangées.",
                    }
                },
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_get_session",
            "title": "Lire le détail d'une session",
            "description": "Charge le rapport JSON détaillé d'une session Gravity Metrics.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Label, slug ou UUID de la session.",
                    }
                },
                "required": ["target"],
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_archive_session",
            "title": "Archiver une session",
            "description": "Analyse et archive de façon immuable une session Gravity en attente.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "UUID ou préfixe unique de la conversation à archiver.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Label optionnel (auto-généré si omis).",
                    },
                    "note": {
                        "type": "string",
                        "description": "Note optionnelle.",
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Mesurer toute la conversation au lieu de la fenêtre Gravity.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Forcer l'archivage même si déjà archivée ou si un job Bridge est actif.",
                    },
                    "profile": {
                        "type": "string",
                        "description": "Profil tarifaire (défaut: 2026_nov_standard).",
                    },
                },
                "required": ["conversation_id"],
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_remove_session",
            "title": "Supprimer une session",
            "description": "Retire une session de l'historique centralisé et régénère METRICS.md.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Label ou UUID de la session à supprimer.",
                    },
                    "purge_reports": {
                        "type": "boolean",
                        "description": "Supprimer aussi les fichiers dans reports/.",
                    },
                },
                "required": ["target"],
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
    ]


def _tool_ok(text: str, structured: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def _tool_err(message: str) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _handle_open_metrics_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    store_dir, history_path = resolve_store_paths()
    brain_dir = default_brain_dir()
    catalog_path = PLUGIN_ROOT / "config" / "pricing_catalog.json"

    sig, records, projects, metrics_md_uri, pending_sessions = _load_state_snapshot(
        store_dir, history_path
    )
    proj_map = _canonical_projects_map(records)
    raw_project = str(args.get("project") or "").strip()
    initial_project = proj_map.get(raw_project.casefold(), raw_project) if raw_project else ""
    initial_scope = "project" if initial_project else "global"
    raw_target = str(args.get("target") or "").strip()
    req_view = str(args.get("view") or "").strip().lower()

    initial_target = ""
    initial_view = "history"
    initial_detail: Optional[Dict[str, Any]] = None
    initial_detail_slug = ""
    initial_detail_md_uri = ""

    if raw_target and records:
        try:
            idx = resolve_session_index(records, raw_target)
            rec = records[idx]
            initial_target = str(rec.get("label") or rec.get("conversation_id") or raw_target)
            if not initial_project and rec.get("project"):
                rec_proj = str(rec["project"]).strip()
                initial_project = proj_map.get(rec_proj.casefold(), rec_proj)
                initial_scope = "project"
            if req_view != "history":
                initial_view = "detail"
                metrics, slug = load_session_for_inspect(
                    initial_target,
                    store_dir=store_dir,
                    history_path=history_path,
                    brain_dir=brain_dir,
                    catalog_path=catalog_path,
                )
                initial_detail = metrics
                initial_detail_slug = slug or _resolve_slug_for_rec(
                    rec, metrics["root_conversation_id"], metrics["label"]
                )
                initial_detail_md_uri = _file_uri_if_exists(
                    store_dir / "reports" / f"{initial_detail_slug}.md"
                )
        except Exception:
            initial_target = ""
            initial_view = "history"

    structured: Dict[str, Any] = {
        "signature": sig,
        "records": records,
        "projects": projects,
        "pending_sessions": pending_sessions,
        "metrics_md_uri": metrics_md_uri,
        "initial_scope": initial_scope,
        "initial_project": initial_project,
        "initial_view": initial_view,
        "initial_target": initial_target,
    }

    if initial_detail is not None:
        candidate = dict(structured)
        candidate["initial_detail"] = initial_detail
        candidate["initial_detail_slug"] = initial_detail_slug
        candidate["initial_detail_md_uri"] = initial_detail_md_uri
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= MAX_TRAJECTORY_BYTES:
            structured = candidate

    if len(json.dumps(structured, ensure_ascii=False).encode("utf-8")) > MAX_TRAJECTORY_BYTES:
        structured["records"] = records[-50:]

    if initial_project:
        target_cf = initial_project.casefold()
        scoped = [r for r in records if str(r.get("project") or "").strip().casefold() == target_cf]
        scope_desc = f"projet '{initial_project}' : {len(scoped)} session(s) / {len(records)} au total"
    else:
        scoped = records
        scope_desc = f"{len(records)} session(s) enregistrée(s)"

    if initial_target:
        summary_txt = f"Gravity Metrics ouvert sur '{initial_target}' ({scope_desc})."
    elif scoped:
        last_rec = scoped[-1]
        summary_txt = (
            f"Gravity Metrics ouvert ({scope_desc}). Dernière session : "
            f"{last_rec.get('label')} (${float(last_rec.get('cost_usd') or 0.0):.4f}, "
            f"cache {float(last_rec.get('cache_hit_rate_pct') or 0.0):.1f}%)."
        )
    else:
        summary_txt = f"Gravity Metrics ouvert ({scope_desc})."

    return _tool_ok(summary_txt, structured)


def _handle_metrics_get_state(args: Dict[str, Any]) -> Dict[str, Any]:
    store_dir, history_path = resolve_store_paths()
    since_sig = str(args.get("since_signature") or "").strip()
    sig, records, projects, metrics_md_uri, pending_sessions = _load_state_snapshot(
        store_dir, history_path
    )
    if since_sig and since_sig == sig:
        return _tool_ok("Inchangé.", {"changed": False, "signature": sig})
    return _tool_ok(
        f"{len(records)} session(s) chargée(s), {len(pending_sessions)} en attente.",
        {
            "changed": True,
            "signature": sig,
            "records": records,
            "projects": projects,
            "pending_sessions": pending_sessions,
            "metrics_md_uri": metrics_md_uri,
        },
    )


def _handle_metrics_get_session(args: Dict[str, Any]) -> Dict[str, Any]:
    target = str(args.get("target") or "").strip()
    if not target:
        return _tool_err("Le paramètre 'target' est requis.")
    store_dir, history_path = resolve_store_paths()
    brain_dir = default_brain_dir()
    catalog_path = PLUGIN_ROOT / "config" / "pricing_catalog.json"
    metrics, slug = load_session_for_inspect(
        target,
        store_dir=store_dir,
        history_path=history_path,
        brain_dir=brain_dir,
        catalog_path=catalog_path,
    )
    resolved_slug = slug or slugify_label(
        str(metrics.get("label") or ""), f"session-{metrics['root_conversation_id'][:8]}"
    )
    report_md_uri = _file_uri_if_exists(store_dir / "reports" / f"{resolved_slug}.md")
    return _tool_ok(
        f"Détail session '{metrics['label']}' chargé.",
        {
            "detail": metrics,
            "slug": resolved_slug,
            "report_md_uri": report_md_uri,
        },
    )


def _handle_metrics_archive_session(args: Dict[str, Any]) -> Dict[str, Any]:
    cid_arg = str(args.get("conversation_id") or "").strip()
    if not cid_arg:
        return _tool_err("Le paramètre 'conversation_id' est requis.")
    store_dir, history_path = resolve_store_paths()
    brain_dir = default_brain_dir()
    catalog_path = PLUGIN_ROOT / "config" / "pricing_catalog.json"

    label = str(args.get("label") or "").strip()
    note = str(args.get("note") or "").strip()
    full = bool(args.get("full", False))
    force = bool(args.get("force", False))
    profile = str(args.get("profile") or "2026_nov_standard").strip() or "2026_nov_standard"

    try:
        saved_rec, _, _metrics = archive_conversation(
            cid_arg,
            store_dir=store_dir,
            history_path=history_path,
            brain_dir=brain_dir,
            catalog_path=catalog_path,
            label=label,
            note=note,
            pricing_profile=profile,
            gravity_window=not full,
            full_conversation=full,
            force=force,
        )
    except PendingBridgeJobsError as exc:
        return _tool_err(str(exc))

    sig, records, projects, metrics_md_uri, pending_sessions = _load_state_snapshot(
        store_dir, history_path
    )
    already = bool(saved_rec.get("already_archived"))
    msg = (
        f"Session '{saved_rec.get('label')}' déjà archivée (immuable)."
        if already
        else f"Session '{saved_rec.get('label')}' archivée."
    )
    return _tool_ok(
        msg,
        {
            "archived": saved_rec,
            "already_archived": already,
            "signature": sig,
            "records": records,
            "projects": projects,
            "pending_sessions": pending_sessions,
            "metrics_md_uri": metrics_md_uri,
        },
    )


def _handle_metrics_remove_session(args: Dict[str, Any]) -> Dict[str, Any]:
    target = str(args.get("target") or "").strip()
    if not target:
        return _tool_err("Le paramètre 'target' est requis.")
    purge_reports = bool(args.get("purge_reports", False))
    store_dir, history_path = resolve_store_paths()
    removed, remaining = apply_remove(
        target,
        store_dir=store_dir,
        history_path=history_path,
        purge_reports=purge_reports,
    )
    sig, records, projects, metrics_md_uri, pending_sessions = _load_state_snapshot(
        store_dir, history_path
    )
    return _tool_ok(
        f"Session '{removed.get('label')}' supprimée ({remaining} restante(s)).",
        {
            "removed": removed,
            "remaining": remaining,
            "signature": sig,
            "records": records,
            "projects": projects,
            "pending_sessions": pending_sessions,
            "metrics_md_uri": metrics_md_uri,
        },
    )


_TOOL_DISPATCH = {
    "open_metrics_dashboard": _handle_open_metrics_dashboard,
    "metrics_get_state": _handle_metrics_get_state,
    "metrics_get_session": _handle_metrics_get_session,
    "metrics_archive_session": _handle_metrics_archive_session,
    "metrics_remove_session": _handle_metrics_remove_session,
}


def handle_jsonrpc_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Traite un message JSON-RPC 2.0 MCP et retourne la réponse (None pour les notifications)."""
    if not isinstance(msg, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    req_id = msg.get("id")
    has_id = "id" in msg and req_id is not None
    method = msg.get("method")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if not has_id:
        return None

    if not isinstance(method, str):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

    try:
        if method == "initialize":
            client_proto = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
            result: Dict[str, Any] = {
                "protocolVersion": client_proto,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tool_definitions()}
        elif method == "tools/call":
            tool_name = str(params.get("name") or "")
            tool_args = params.get("arguments")
            if not isinstance(tool_args, dict):
                tool_args = {}
            handler = _TOOL_DISPATCH.get(tool_name)
            if handler is None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                }
            try:
                result = handler(tool_args)
            except Exception as exc:
                result = _tool_err(str(exc))
        elif method == "resources/list":
            result = {
                "resources": [
                    {
                        "uri": APP_URI,
                        "name": "gravity_metrics_dashboard",
                        "title": "Gravity Metrics Dashboard",
                        "description": "Tableau de bord interactif des coûts, tokens, cache et agents Gravity.",
                        "mimeType": APP_MIME,
                        "_meta": {"ui": {"prefersBorder": False}},
                    }
                ]
            }
        elif method == "resources/templates/list":
            result = {"resourceTemplates": []}
        elif method == "resources/read":
            uri = str(params.get("uri") or "")
            if uri != APP_URI:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32002, "message": f"Resource not found: {uri}"},
                }
            result = {
                "contents": [
                    {
                        "uri": APP_URI,
                        "mimeType": APP_MIME,
                        "text": render_mcp_widget_html(),
                        "_meta": {"ui": {"prefersBorder": False}},
                    }
                ]
            }
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": str(exc)},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _write_jsonrpc(payload: Dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(line.encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    while True:
        raw_line = sys.stdin.buffer.readline()
        if not raw_line:
            break
        text = raw_line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            _write_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            continue
        response = handle_jsonrpc_message(msg)
        if response is not None:
            _write_jsonrpc(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
