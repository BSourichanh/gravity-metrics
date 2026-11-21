#!/usr/bin/env python3
"""Gestion de l'historique multi-sessions JSONL et verrous (100 % stdlib)."""

import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Iterator, List, Optional, Union

from .collector import _fmt_iso_utc, slugify_label
from .pricing import SCHEMA_VERSION


@contextlib.contextmanager
def history_lock(history_path: Union[Path, str]) -> Iterator[None]:
    """Verrou fichier inter-processus léger (Windows msvcrt / POSIX fcntl) pour history.jsonl."""
    lock_path = Path(history_path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            for _ in range(100):
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            fh.close()
        except Exception:
            pass


def to_session_record(
    metrics: Dict[str, Any],
    *,
    label: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """Convertit les métriques complètes d'un run en entrée compacte pour history.jsonl."""
    s = metrics["summary"]
    c = s["cost_usd"]
    p = s.get("cost_usd_projection") or {}
    eff = metrics["efficiency_and_waste"]
    by_model = metrics.get("by_model") or {}
    win = metrics.get("window") or {}

    if by_model:
        primary_model = max(
            by_model.items(),
            key=lambda kv: (kv[1]["calls"], kv[1]["cost_usd"]["total_usd"]),
        )[0]
    else:
        primary_model = "unknown"

    models_compact = {
        m_name: {"calls": m_data["calls"], "cost_usd": m_data["cost_usd"]["total_usd"]}
        for m_name, m_data in by_model.items()
    }
    cat_shares = {
        cat: data["cost_share_pct"]
        for cat, data in (metrics.get("by_category") or {}).items()
    }

    cid = metrics["root_conversation_id"]
    resolved_label = (label or metrics.get("label") or f"run-{cid[:8]}").strip()

    return {
        "schema_version": SCHEMA_VERSION,
        "added_at": _fmt_iso_utc(datetime.now(timezone.utc)),
        "label": resolved_label,
        "detail_slug": slugify_label(resolved_label, f"session-{cid[:8]}"),
        "conversation_id": cid,
        "note": note.strip(),
        "project": str(metrics.get("project") or ""),
        "gravity_profile": str(metrics.get("gravity_profile") or ""),
        "window_mode": str(win.get("mode") or "full_conversation"),
        "window_start_step": win.get("start_step_index"),
        "pricing_profile": metrics["pricing_profile"],
        "started_at": s["first_ts"],
        "ended_at": s["last_ts"],
        "wall_clock_s": s["wall_clock_s"],
        "parallelism_factor": s.get("parallelism_factor", 1.0),
        "execution_mode": s.get("execution_mode", "single"),
        "features_used": s.get("features_used", ["single"]),
        "primary_model": primary_model,
        "models": models_compact,
        "agents": s["total_agents"],
        "max_depth": s["max_tree_depth"],
        "llm_calls": s["total_llm_calls"],
        "tool_calls": s["total_tool_calls"],
        "user_inputs": s["total_user_inputs"],
        "error_steps": s["total_error_steps"],
        "error_rate_pct": s["error_rate_pct"],
        "input_tokens": s["input_tokens"],
        "cache_read_tokens": s["cache_read_tokens"],
        "output_tokens": s["output_tokens"],
        "total_tokens": s["total_tokens"],
        "cache_hit_rate_pct": s["cache_hit_rate_pct"],
        "max_context_tokens": eff["context_bloat"]["max_context_tokens_overall"],
        "cost_usd": c["total_usd"],
        "cost_input_usd": c["input_usd"],
        "cost_cache_usd": c["cache_read_usd"],
        "cost_output_usd": c["output_usd"],
        "cache_savings_usd": c["cache_savings_usd"],
        "cost_2027_usd": p.get("total_usd", 0.0),
        "category_share_pct": cat_shares,
        "duplicate_spawns": eff["duplicate_role_spawns"]["duplicate_agent_count"],
        "rewind_calls": eff["rewind_waste"]["rewind_calls"],
        "interrupted_agents": eff["interrupted_agents"]["count"],
    }


def load_history(history_path: Union[Path, str]) -> List[Dict[str, Any]]:
    """Charge la liste chronologique des sessions depuis history.jsonl."""
    hpath = Path(history_path)
    if not hpath.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for raw_line in hpath.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("conversation_id"):
            records.append(obj)
    return records


def save_history(history_path: Union[Path, str], records: List[Dict[str, Any]]) -> None:
    """Sauvegarde la liste des sessions dans history.jsonl (UTF-8)."""
    hpath = Path(history_path)
    hpath.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        hpath.write_text("", encoding="utf-8")
        return
    payload = "\n".join(json.dumps(rec, ensure_ascii=False) for rec in records) + "\n"
    hpath.write_text(payload, encoding="utf-8")


def resolve_session_index(records: List[Dict[str, Any]], key: str) -> int:
    """Trouve l'indice d'une session par label exact, UUID exact ou préfixe unique d'UUID."""
    target = key.strip()
    if not target:
        raise KeyError("Clé de session vide.")

    for idx, rec in enumerate(records):
        if rec.get("label") == target or rec.get("conversation_id") == target:
            return idx

    matches = [
        idx
        for idx, rec in enumerate(records)
        if str(rec.get("conversation_id", "")).lower().startswith(target.lower())
        or str(rec.get("label", "")).lower() == target.lower()
    ]
    if not matches:
        raise KeyError(f"Session introuvable : {key!r}")
    if len(matches) > 1:
        raise ValueError(f"Plusieurs sessions correspondent à {key!r} ; précisez le label complet.")
    return matches[0]


def upsert_session(records: List[Dict[str, Any]], record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ajoute ou met à jour une session dans la liste historique."""
    updated = [dict(r) for r in records]
    new_rec = dict(record)
    new_cid = new_rec.get("conversation_id")
    new_wstep = new_rec.get("window_start_step")

    match_idx: Optional[int] = None
    for idx, r in enumerate(updated):
        if r.get("conversation_id") == new_cid and r.get("window_start_step") == new_wstep:
            match_idx = idx
            break
    if match_idx is None and new_rec.get("label"):
        for idx, r in enumerate(updated):
            if r.get("label") == new_rec.get("label"):
                match_idx = idx
                break

    if match_idx is not None:
        updated[match_idx] = new_rec
    else:
        updated.append(new_rec)
    return updated


def remove_session(records: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Supprime une session par label ou préfixe d'UUID."""
    idx = resolve_session_index(records, key)
    return [dict(r) for i, r in enumerate(records) if i != idx]
