#!/usr/bin/env python3
"""Serveur MCP App stdio JSON-RPC 2.0 pour Gravity Metrics (100 % stdlib Python 3)."""

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

MCP_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = MCP_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from metrics import (  # noqa: E402
    apply_remove,
    archive_conversation,
    build_audit_links_by_session,
    default_brain_dir,
    discover_audit_roots,
    ignore_session,
    list_audits_bundle,
    list_pending_sessions,
    load_active_captures,
    load_history,
    load_session_for_inspect,
    read_audit,
    resolve_session_index,
    resolve_store_paths,
    slugify_label,
    unignore_session,
    update_session_metadata,
)
from mcp.widget_html import render_mcp_widget_html  # noqa: E402

SERVER_NAME = "gravity-metrics"
SERVER_VERSION = "0.5.1"
APP_URI = "ui://gravity-metrics/dashboard.html"
APP_MIME = "text/html;profile=mcp-app"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
MIN_ANTIGRAVITY_MCP_APP_VERSION = (2, 34, 0)
MAX_TRAJECTORY_BYTES = 450_000
VALID_DASHBOARD_VIEWS = {
    "overview",
    "sessions",
    "reports",
    "pending",
    "compare",
    "audits",
    "history",
    "detail",
}

_STATE_CACHE: Dict[Tuple[str, ...], Dict[str, Any]] = {}


def _default_host_client_state() -> Dict[str, Any]:
    return {
        "initialized": False,
        "ui_supported": None,
        "client_name": "",
        "client_version": "",
        "reason": "",
    }


_HOST_CLIENT_STATE: Dict[str, Any] = _default_host_client_state()


def _reset_host_client_state() -> None:
    """Réinitialise l'état de capacité UI de l'hôte MCP (isolation des tests)."""
    _HOST_CLIENT_STATE.clear()
    _HOST_CLIENT_STATE.update(_default_host_client_state())


def _parse_semver_tuple(raw: Any) -> Optional[Tuple[int, int, int]]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(raw or ""))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _is_truthy_ui_capability(value: Any) -> bool:
    return value is True or (isinstance(value, dict) and len(value) > 0)


def _inspect_client_ui_support(params: Any) -> Dict[str, Any]:
    """Classifie la capacité MCP Apps UI du client depuis `initialize.params`."""
    p = params if isinstance(params, dict) else {}
    caps = p.get("capabilities")
    cinfo = p.get("clientInfo")
    client_name = str(cinfo.get("name") or "").strip() if isinstance(cinfo, dict) else ""
    client_version = str(cinfo.get("version") or "").strip() if isinstance(cinfo, dict) else ""

    if isinstance(caps, dict):
        ext = caps.get("extensions") if isinstance(caps.get("extensions"), dict) else {}
        exp = caps.get("experimental") if isinstance(caps.get("experimental"), dict) else {}
        ui_candidates = (
            ext.get("io.modelcontextprotocol/ui"),
            exp.get("io.modelcontextprotocol/ui"),
            ext.get("com.google.antigravity/ui"),
            exp.get("com.google.antigravity/ui"),
            caps.get("ui"),
        )
        if any(_is_truthy_ui_capability(val) for val in ui_candidates):
            return {
                "initialized": True,
                "ui_supported": True,
                "client_name": client_name,
                "client_version": client_version,
                "reason": "ui_capability_declared",
            }

    name_cf = client_name.casefold()
    if "antigravity" in name_cf and "cli" not in name_cf:
        parsed_ver = _parse_semver_tuple(client_version)
        if parsed_ver is not None and parsed_ver < MIN_ANTIGRAVITY_MCP_APP_VERSION:
            return {
                "initialized": True,
                "ui_supported": False,
                "client_name": client_name,
                "client_version": client_version,
                "reason": "antigravity_version_below_min",
            }

    if not isinstance(caps, dict) or (caps == {} and not client_name):
        return {
            "initialized": True,
            "ui_supported": None,
            "client_name": client_name,
            "client_version": client_version,
            "reason": "unknown_or_minimal_handshake",
        }

    return {
        "initialized": True,
        "ui_supported": False,
        "client_name": client_name,
        "client_version": client_version,
        "reason": "no_ui_capability_declared",
    }


def _stat_token(path: Path) -> str:
    try:
        if not path.exists():
            return "missing"
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "error"


def _history_signature(history_path: Path) -> str:
    if not history_path.is_file():
        return "missing"
    return _stat_token(history_path)


def _compute_hist_source_sig(store_dir: Path, history_path: Path) -> str:
    parts = [
        _stat_token(history_path),
        _stat_token(store_dir / "active_captures.json"),
        _stat_token(store_dir / "session_meta.json"),
        _stat_token(store_dir / "ignored.json"),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _compute_pending_source_sig(
    brain_dir: Path,
    store_dir: Path,
    history_path: Path,
) -> str:
    items: List[str] = [
        f"hist:{_stat_token(history_path)}",
        f"cap:{_stat_token(store_dir / 'active_captures.json')}",
        f"ign:{_stat_token(store_dir / 'ignored.json')}",
        f"meta:{_stat_token(store_dir / 'session_meta.json')}",
    ]
    if brain_dir.is_dir():
        try:
            for cdir in sorted(brain_dir.iterdir(), key=lambda p: p.name):
                if not cdir.is_dir():
                    continue
                tp = cdir / ".system_generated" / "logs" / "transcript.jsonl"
                if tp.is_file():
                    items.append(f"{cdir.name}:{_stat_token(tp)}")
        except OSError:
            items.append("brain-err")
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:12]


def _compute_audits_source_sig(hist_src_sig: str) -> str:
    roots = discover_audit_roots()
    items: List[str] = [f"h:{hist_src_sig}"]
    for r in roots:
        rpath = r["path"]
        items.append(f"r:{r['root_id']}")
        try:
            for fpath in sorted(rpath.rglob("*.md")):
                if not fpath.is_file() or fpath.name.startswith("_"):
                    continue
                items.append(f"{fpath.name}:{_stat_token(fpath)}")
        except OSError:
            continue
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:12]


def _state_cache_key(store_dir: Path, history_path: Path, brain_dir: Path) -> Tuple[str, ...]:
    return (
        str(store_dir),
        str(history_path),
        str(brain_dir),
        os.environ.get("GRAVITY_AUDITS_DIR", "").strip(),
    )


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


def _pending_signature(pending: List[Dict[str, Any]], ignored_pending: List[Dict[str, Any]]) -> str:
    compact = [
        (
            p.get("conversation_id"),
            p.get("llm_calls"),
            p.get("current_phase"),
            p.get("project"),
            p.get("updated_at"),
            bool(p.get("active_capture")),
            bool(p.get("ignored")),
        )
        for p in list(pending) + list(ignored_pending)
    ]
    raw = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _aggregate_by_project(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Agrège les coûts, pourcentages, sessions et cache moyen par projet canonique."""
    proj_map = _canonical_projects_map(records)
    buckets: Dict[str, Dict[str, Any]] = {}
    total_2026 = 0.0
    total_2027 = 0.0

    for rec in records:
        raw_p = str(rec.get("project") or "").strip()
        canon_p = proj_map.get(raw_p.casefold(), raw_p) if raw_p else "(sans projet)"
        key = canon_p.casefold()
        if key not in buckets:
            buckets[key] = {
                "project": canon_p,
                "sessions": 0,
                "cost_usd": 0.0,
                "cost_2027_usd": 0.0,
                "cache_sum": 0.0,
                "llm_calls": 0,
                "agents": 0,
                "wall_clock_s": 0.0,
                "bridge_sessions": 0,
            }
        b = buckets[key]
        c26 = float(rec.get("cost_usd") or 0.0)
        raw_27 = rec.get("cost_2027_usd")
        c27 = float(raw_27) if raw_27 is not None else c26
        b["sessions"] += 1
        b["cost_usd"] += c26
        b["cost_2027_usd"] += c27
        b["cache_sum"] += float(rec.get("cache_hit_rate_pct") or 0.0)
        b["llm_calls"] += int(rec.get("llm_calls") or 0)
        b["agents"] += int(rec.get("agents") or 0)
        b["wall_clock_s"] += float(rec.get("wall_clock_s") or 0.0)
        if int(rec.get("bridge_children_count") or 0) > 0:
            b["bridge_sessions"] += 1
        total_2026 += c26
        total_2027 += c27

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        sess = b["sessions"]
        out.append(
            {
                "project": b["project"],
                "sessions": sess,
                "cost_usd": round(b["cost_usd"], 6),
                "cost_2027_usd": round(b["cost_2027_usd"], 6),
                "share_pct": round((b["cost_usd"] / total_2026) * 100.0, 2) if total_2026 > 0 else 0.0,
                "share_2027_pct": round((b["cost_2027_usd"] / total_2027) * 100.0, 2) if total_2027 > 0 else 0.0,
                "avg_cache_hit_rate_pct": round(b["cache_sum"] / sess, 2) if sess > 0 else 0.0,
                "llm_calls": b["llm_calls"],
                "agents": b["agents"],
                "wall_clock_s": round(b["wall_clock_s"], 2),
                "bridge_sessions": b["bridge_sessions"],
            }
        )
    out.sort(key=lambda item: (-item["cost_usd"], -item["sessions"], item["project"].casefold()))
    return out


def _audits_signature(
    audits: List[Dict[str, Any]],
    index_audit: Optional[Dict[str, Any]] = None,
) -> str:
    compact = [(a.get("audit_id"), a.get("size_bytes"), a.get("updated_at"), a.get("verdict")) for a in audits]
    if index_audit:
        compact.append((index_audit.get("audit_id"), index_audit.get("size_bytes"), index_audit.get("updated_at"), "INDEX"))
    raw = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _load_state_snapshot(
    store_dir: Path,
    history_path: Path,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    brain_dir = default_brain_dir()
    ckey = _state_cache_key(store_dir, history_path, brain_dir)
    cached = None if force_refresh else _STATE_CACHE.get(ckey)

    hist_src_sig = _compute_hist_source_sig(store_dir, history_path)
    pending_src_sig = _compute_pending_source_sig(brain_dir, store_dir, history_path)
    audits_src_sig = _compute_audits_source_sig(hist_src_sig)

    if cached and cached.get("hist_src_sig") == hist_src_sig:
        hist_data = cached["hist_data"]
        hist_sig = cached["hist_sig"]
    else:
        records = load_history(history_path)
        projects = _collect_projects(records)
        by_project = _aggregate_by_project(records)
        metrics_md_uri = _file_uri_if_exists(store_dir / "METRICS.md")
        active_captures = load_active_captures(store_dir)
        hist_file_sig = _history_signature(history_path)
        hist_sig = hashlib.sha256(
            f"{hist_file_sig}|{len(records)}|{json.dumps(active_captures, sort_keys=True)}".encode("utf-8")
        ).hexdigest()[:12]
        hist_data = {
            "records": records,
            "projects": projects,
            "by_project": by_project,
            "metrics_md_uri": metrics_md_uri,
            "active_captures": active_captures,
        }

    if cached and cached.get("pending_src_sig") == pending_src_sig:
        pending_data = cached["pending_data"]
        pending_sig = cached["pending_sig"]
    else:
        all_pending = list_pending_sessions(
            brain_dir,
            history_path,
            store_dir=store_dir,
            include_ignored=True,
        )
        pending_sessions = [p for p in all_pending if not p.get("ignored")]
        ignored_sessions = [p for p in all_pending if p.get("ignored")]
        pending_sig = _pending_signature(pending_sessions, ignored_sessions)
        pending_data = {
            "pending_sessions": pending_sessions,
            "ignored_sessions": ignored_sessions,
        }

    if cached and cached.get("audits_src_sig") == audits_src_sig:
        audits_data = cached["audits_data"]
        audits_sig = cached["audits_sig"]
    else:
        bundle = list_audits_bundle(records=hist_data["records"])
        audits = bundle["audits"]
        audits_roots = bundle["roots"]
        index_audit = bundle["index_audit"]
        index_audits = bundle["index_audits"]
        audit_links_by_session = build_audit_links_by_session(audits)
        audits_sig = _audits_signature(audits, index_audit)
        audits_data = {
            "audits": audits,
            "audits_roots": audits_roots,
            "audits_count": len(audits),
            "index_audit": index_audit,
            "index_audits": index_audits,
            "audit_links_by_session": audit_links_by_session,
        }

    sig = f"h:{hist_sig}|p:{pending_sig}|a:{audits_sig}"
    _STATE_CACHE[ckey] = {
        "hist_src_sig": hist_src_sig,
        "pending_src_sig": pending_src_sig,
        "audits_src_sig": audits_src_sig,
        "hist_sig": hist_sig,
        "pending_sig": pending_sig,
        "audits_sig": audits_sig,
        "signature": sig,
        "hist_data": hist_data,
        "pending_data": pending_data,
        "audits_data": audits_data,
    }

    return {
        "signature": sig,
        "hist_sig": hist_sig,
        "pending_sig": pending_sig,
        "audits_sig": audits_sig,
        **hist_data,
        **pending_data,
        **audits_data,
    }


def _fast_unchanged_signature(store_dir: Path, history_path: Path, since_sig: str) -> Optional[str]:
    if not since_sig:
        return None
    brain_dir = default_brain_dir()
    ckey = _state_cache_key(store_dir, history_path, brain_dir)
    cached = _STATE_CACHE.get(ckey)
    if not cached or cached.get("signature") != since_sig:
        return None
    if _compute_hist_source_sig(store_dir, history_path) != cached.get("hist_src_sig"):
        return None
    if _compute_pending_source_sig(brain_dir, store_dir, history_path) != cached.get("pending_src_sig"):
        return None
    if _compute_audits_source_sig(cached["hist_src_sig"]) != cached.get("audits_src_sig"):
        return None
    return str(cached["signature"])


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
                        "enum": [
                            "overview",
                            "sessions",
                            "reports",
                            "pending",
                            "compare",
                            "audits",
                            "history",
                            "detail",
                        ],
                        "description": "Espace/vue initial souhaité : overview, sessions, pending, compare, audits, reports, history ou detail (optionnel).",
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
                    "project": {
                        "type": "string",
                        "description": "Nom du projet associé (optionnel, résolu automatiquement si omis).",
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
                        "description": "Forcer le ré-archivage même si la session est déjà archivée.",
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
            "name": "metrics_update_session_meta",
            "title": "Mettre à jour les métadonnées d'une session",
            "description": "Modifie label, projet ou note d'une session archivée sans altérer ses métriques financières.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Label ou UUID de la session archivée.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Nouveau label (optionnel).",
                    },
                    "project": {
                        "type": "string",
                        "description": "Nom du projet (optionnel).",
                    },
                    "note": {
                        "type": "string",
                        "description": "Note libre (optionnel).",
                    },
                },
                "required": ["target"],
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_ignore_session",
            "title": "Ignorer une session en attente",
            "description": "Masque une conversation de la file À archiver dans ignored.json (réversible).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "UUID de la conversation à masquer.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Raison optionnelle.",
                    },
                },
                "required": ["conversation_id"],
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_unignore_session",
            "title": "Restaurer une session ignorée",
            "description": "Retire une conversation de ignored.json pour la réafficher dans À archiver.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "UUID de la conversation à restaurer.",
                    }
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
        {
            "name": "metrics_list_audits",
            "title": "Lister les Audits Agents Markdown",
            "description": "Découvre en lecture seule les fichiers Markdown des dossiers audits/agents/ (onglet Audits Agents, hors audits/code/ et hors README.md index) et leurs liens de sessions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace": {
                        "type": "string",
                        "description": "Filtrer par nom de workspace (optionnel).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Filtre texte sur titre, chemin ou verdict (optionnel).",
                    },
                },
            },
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
        {
            "name": "metrics_read_audit",
            "title": "Lire un Audit Agent Markdown",
            "description": "Lit en lecture seule un fichier .md autorisé d'un dossier audits/agents/ (onglet Audits Agents, hors audits/code/, plafond 256 Ko).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "audit_id": {
                        "type": "string",
                        "description": "Identifiant de l'audit (<root_id>:<rel_path>).",
                    }
                },
                "required": ["audit_id"],
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


def _state_payload(snap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signature": snap["signature"],
        "hist_sig": snap["hist_sig"],
        "pending_sig": snap["pending_sig"],
        "audits_sig": snap["audits_sig"],
        "records": snap["records"],
        "projects": snap["projects"],
        "by_project": snap["by_project"],
        "pending_sessions": snap["pending_sessions"],
        "ignored_sessions": snap["ignored_sessions"],
        "active_captures": snap["active_captures"],
        "audits_count": snap["audits_count"],
        "index_audit": snap["index_audit"],
        "audit_links_by_session": snap["audit_links_by_session"],
        "metrics_md_uri": snap["metrics_md_uri"],
    }


def _format_host_client_desc(host_state: Dict[str, Any]) -> str:
    cname = str(host_state.get("client_name") or "").strip()
    cver = str(host_state.get("client_version") or "").strip()
    if cname and cver:
        return f"{cname} {cver}"
    if cname:
        return cname
    return "client inconnu"


def _handle_open_metrics_dashboard(
    args: Dict[str, Any],
    host_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    store_dir, history_path = resolve_store_paths()
    brain_dir = default_brain_dir()
    catalog_path = PLUGIN_ROOT / "config" / "pricing_catalog.json"

    snap = _load_state_snapshot(store_dir, history_path)
    records = snap["records"]
    proj_map = _canonical_projects_map(records)
    raw_project = str(args.get("project") or "").strip()
    initial_project = proj_map.get(raw_project.casefold(), raw_project) if raw_project else ""
    initial_scope = "project" if initial_project else "global"
    raw_target = str(args.get("target") or "").strip()
    req_view = str(args.get("view") or "").strip().lower()

    initial_target = ""
    initial_view = req_view if req_view in VALID_DASHBOARD_VIEWS else "overview"
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
            if req_view in ("", "detail", "reports", "sessions"):
                initial_view = "reports" if req_view == "reports" else "detail"
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
            if initial_view == "detail":
                initial_view = "overview"
    elif initial_view == "detail":
        initial_view = "overview"

    structured: Dict[str, Any] = _state_payload(snap)
    structured.update(
        {
            "initial_scope": initial_scope,
            "initial_project": initial_project,
            "initial_view": initial_view,
            "initial_target": initial_target,
        }
    )

    if snap["audits"]:
        cand_aud = dict(structured)
        cand_aud["audits"] = snap["audits"]
        if len(json.dumps(cand_aud, ensure_ascii=False).encode("utf-8")) <= MAX_TRAJECTORY_BYTES:
            structured = cand_aud

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

    active_state = host_state if host_state is not None else _HOST_CLIENT_STATE
    if active_state.get("initialized") and active_state.get("ui_supported") is False:
        client_desc = _format_host_client_desc(active_state)
        summary_txt = (
            f"{summary_txt}\n"
            f"[WARN UI] Client MCP sans support MCP Apps détecté ({client_desc}) : "
            "affichage texte uniquement ; Antigravity App v2.34.0+ requis pour l'interface graphique."
        )

    return _tool_ok(summary_txt, structured)


def _handle_metrics_get_state(args: Dict[str, Any]) -> Dict[str, Any]:
    store_dir, history_path = resolve_store_paths()
    since_sig = str(args.get("since_signature") or "").strip()
    fast_sig = _fast_unchanged_signature(store_dir, history_path, since_sig)
    if fast_sig is not None:
        return _tool_ok("Inchangé.", {"changed": False, "signature": fast_sig})

    snap = _load_state_snapshot(store_dir, history_path)
    sig = snap["signature"]
    if since_sig and since_sig == sig:
        return _tool_ok("Inchangé.", {"changed": False, "signature": sig})
    payload = _state_payload(snap)
    payload["changed"] = True
    return _tool_ok(
        f"{len(snap['records'])} session(s) chargée(s), {len(snap['pending_sessions'])} en attente, {snap['audits_count']} audit(s).",
        payload,
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
    project = str(args.get("project") or "").strip()
    note = str(args.get("note") or "").strip()
    full = bool(args.get("full", False))
    force = bool(args.get("force", False))
    profile = str(args.get("profile") or "2026_nov_standard").strip() or "2026_nov_standard"

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
        project=project,
        force=force,
    )

    snap = _load_state_snapshot(store_dir, history_path, force_refresh=True)
    already = bool(saved_rec.get("already_archived"))
    msg = (
        f"Session '{saved_rec.get('label')}' déjà archivée (immuable)."
        if already
        else f"Session '{saved_rec.get('label')}' archivée."
    )
    payload = _state_payload(snap)
    payload.update({"archived": saved_rec, "already_archived": already})
    return _tool_ok(msg, payload)


def _handle_metrics_update_session_meta(args: Dict[str, Any]) -> Dict[str, Any]:
    target = str(args.get("target") or "").strip()
    if not target:
        return _tool_err("Le paramètre 'target' est requis.")
    store_dir, history_path = resolve_store_paths()
    label_val = str(args["label"]).strip() if "label" in args and args["label"] is not None else None
    proj_val = str(args["project"]).strip() if "project" in args and args["project"] is not None else None
    note_val = str(args["note"]).strip() if "note" in args and args["note"] is not None else None

    try:
        updated_rec, _ = update_session_metadata(
            target,
            store_dir=store_dir,
            history_path=history_path,
            label=label_val,
            project=proj_val,
            note=note_val,
        )
    except (KeyError, ValueError) as exc:
        return _tool_err(str(exc))

    snap = _load_state_snapshot(store_dir, history_path, force_refresh=True)
    payload = _state_payload(snap)
    payload["updated"] = updated_rec
    return _tool_ok(f"Métadonnées de '{updated_rec.get('label')}' mises à jour.", payload)


def _handle_metrics_ignore_session(args: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(args.get("conversation_id") or "").strip()
    if not cid:
        return _tool_err("Le paramètre 'conversation_id' est requis.")
    reason = str(args.get("reason") or "").strip()
    store_dir, history_path = resolve_store_paths()
    ignore_session(cid, reason=reason, store_dir=store_dir)
    snap = _load_state_snapshot(store_dir, history_path, force_refresh=True)
    return _tool_ok(f"Session '{cid[:8]}' masquée.", _state_payload(snap))


def _handle_metrics_unignore_session(args: Dict[str, Any]) -> Dict[str, Any]:
    cid = str(args.get("conversation_id") or "").strip()
    if not cid:
        return _tool_err("Le paramètre 'conversation_id' est requis.")
    store_dir, history_path = resolve_store_paths()
    unignore_session(cid, store_dir=store_dir)
    snap = _load_state_snapshot(store_dir, history_path, force_refresh=True)
    return _tool_ok(f"Session '{cid[:8]}' restaurée.", _state_payload(snap))


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
    snap = _load_state_snapshot(store_dir, history_path, force_refresh=True)
    payload = _state_payload(snap)
    payload.update({"removed": removed, "remaining": remaining})
    return _tool_ok(
        f"Session '{removed.get('label')}' supprimée ({remaining} restante(s)).",
        payload,
    )


def _handle_metrics_list_audits(args: Dict[str, Any]) -> Dict[str, Any]:
    _, history_path = resolve_store_paths()
    records = load_history(history_path)
    bundle = list_audits_bundle(records=records)
    audits = bundle["audits"]
    roots = bundle["roots"]
    index_audit = bundle["index_audit"]
    index_audits = bundle["index_audits"]

    ws_filter = str(args.get("workspace") or "").strip().casefold()
    query = str(args.get("query") or "").strip().casefold()
    filtered = []
    for audit in audits:
        if ws_filter and str(audit.get("workspace") or "").strip().casefold() != ws_filter:
            continue
        if query:
            hay = " ".join(
                [
                    str(audit.get("title") or ""),
                    str(audit.get("rel_path") or ""),
                    str(audit.get("workspace") or ""),
                    str(audit.get("verdict") or ""),
                    str(audit.get("excerpt") or ""),
                ]
            ).casefold()
            if query not in hay:
                continue
        filtered.append(audit)

    links = build_audit_links_by_session(audits)
    return _tool_ok(
        f"{len(filtered)} audit(s) Markdown découvert(s).",
        {
            "audits": filtered,
            "roots": roots,
            "count": len(filtered),
            "index_audit": index_audit,
            "index_audits": index_audits,
            "audit_links_by_session": links,
        },
    )


def _handle_metrics_read_audit(args: Dict[str, Any]) -> Dict[str, Any]:
    audit_id = str(args.get("audit_id") or "").strip()
    if not audit_id:
        return _tool_err("Le paramètre 'audit_id' est requis.")
    _, history_path = resolve_store_paths()
    records = load_history(history_path)
    try:
        audit_data = read_audit(audit_id, records=records)
    except (ValueError, FileNotFoundError, OSError) as exc:
        return _tool_err(str(exc))
    return _tool_ok(
        f"Audit '{audit_data.get('title') or audit_id}' chargé.",
        {"audit": audit_data},
    )


_TOOL_DISPATCH = {
    "open_metrics_dashboard": _handle_open_metrics_dashboard,
    "metrics_get_state": _handle_metrics_get_state,
    "metrics_get_session": _handle_metrics_get_session,
    "metrics_archive_session": _handle_metrics_archive_session,
    "metrics_update_session_meta": _handle_metrics_update_session_meta,
    "metrics_ignore_session": _handle_metrics_ignore_session,
    "metrics_unignore_session": _handle_metrics_unignore_session,
    "metrics_remove_session": _handle_metrics_remove_session,
    "metrics_list_audits": _handle_metrics_list_audits,
    "metrics_read_audit": _handle_metrics_read_audit,
}


def handle_jsonrpc_message(
    msg: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
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

    state = session_state if session_state is not None else _HOST_CLIENT_STATE

    try:
        if method == "initialize":
            state.clear()
            state.update(_inspect_client_ui_support(params))
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
                if tool_name == "open_metrics_dashboard":
                    result = _handle_open_metrics_dashboard(tool_args, host_state=state)
                else:
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
