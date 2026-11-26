#!/usr/bin/env python3
"""Opérations transactionnelles sur le store centralisé Gravity Metrics (100 % stdlib)."""

from collections import Counter
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_atomic_io = importlib.import_module("_shared.atomic_io")
_signal_bus = importlib.import_module("_shared.signal_bus")

from .collector import (
    collect_run_metrics,
    resolve_conversation_id_in_brain,
    resolve_gravity_window,
    slugify_label,
    suggest_auto_label,
)
from .history import (
    find_archived_session,
    history_lock,
    load_history,
    remove_session,
    resolve_session_index,
    save_history,
    to_session_record,
    upsert_session,
)
from .lifecycle import load_active_captures, pop_active_capture, unignore_session
from .markdown_report import render_metrics_markdown, render_session_detail_markdown
from .pricing import default_store_dir


class PendingBridgeJobsError(RuntimeError):
    """Temporary compat stub until W12 after W8-W10 drop callers."""


def _normalize_signal_slug(project: str) -> str:
    raw = str(project or "").strip() or "global"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-") or "default"


def _resolve_signal_gravity_home(store_dir: Path) -> Optional[Path]:
    if os.environ.get("GRAVITY_HOME", "").strip():
        return None
    resolved_store = Path(store_dir).expanduser().resolve()
    return resolved_store.parent if resolved_store.name == "metrics" else resolved_store


def _safe_float(val: Any) -> float:
    if isinstance(val, bool) or val is None:
        return 0.0
    try:
        num = float(val)
        return num if math.isfinite(num) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    if isinstance(val, bool) or val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _publish_session_metrics_signal(store_dir: Path, records: List[Dict[str, Any]], rec: Dict[str, Any]) -> None:
    """Émet le signal `signal_v1` (`session-metrics`) via `_shared.signal_bus` (`SPI-3`)."""
    slug = _normalize_signal_slug(str(rec.get("project") or ""))
    matched = [
        (str(r.get("ended_at") or r.get("archived_at") or r.get("added_at") or r.get("timestamp") or ""), idx, r)
        for idx, r in enumerate(records)
        if isinstance(r, dict)
        and str(r.get("status") or "archived").strip().lower() == "archived"
        and _normalize_signal_slug(str(r.get("project") or "")) == slug
    ]
    matched.sort(key=lambda item: (item[0], item[1]))
    window_limit = 5
    selected = [item[2] for item in matched[-window_limit:]] or [rec]
    sample_count = len(selected)
    total_cost = sum(max(0.0, _safe_float(r.get("cost_usd"))) for r in selected)
    total_out_cost = sum(max(0.0, _safe_float(r.get("cost_output_usd"))) for r in selected)
    out_ratio = round(max(0.0, min(1.0, total_out_cost / total_cost)), 4) if total_cost > 0.0 else 0.0
    payload = {
        "avg_error_rate_pct": round(sum(max(0.0, _safe_float(r.get("error_rate_pct"))) for r in selected) / sample_count, 2),
        "avg_cache_hit_rate_pct": round(sum(max(0.0, _safe_float(r.get("cache_hit_rate_pct"))) for r in selected) / sample_count, 2),
        "max_context_tokens": max(max(0, _safe_int(r.get("max_context_tokens"))) for r in selected),
        "recent_duplicate_spawns": sum(max(0, _safe_int(r.get("duplicate_spawns"))) for r in selected),
        "recent_rewind_calls": sum(max(0, _safe_int(r.get("rewind_calls"))) for r in selected),
        "recent_interrupted_agents": sum(max(0, _safe_int(r.get("interrupted_agents"))) for r in selected),
        "recent_bridge_sessions": 0,
        "high_output_cost_ratio": out_ratio,
    }
    _signal_bus.publish_signal(
        "session-metrics", slug, "metrics", payload,
        sample_count=sample_count, window_size=window_limit,
        gravity_home=_resolve_signal_gravity_home(store_dir),
    )


def resolve_store_paths(history_path: Optional[Path] = None, store_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Résout `(store_dir, history_path)` en gardant la rétrocompatibilité avec `--history`."""
    if store_dir is not None:
        s_dir = Path(store_dir).expanduser()
        h_path = Path(history_path).expanduser() if history_path is not None else (s_dir / "data" / "history.jsonl")
        return s_dir, h_path
    if history_path is not None:
        h_path = Path(history_path).expanduser()
        s_dir = h_path.parent.parent if h_path.parent.name == "data" else h_path.parent
        return s_dir, h_path
    s_dir = default_store_dir()
    return s_dir, s_dir / "data" / "history.jsonl"


def regenerate_views(store_dir: Path, records: List[Dict[str, Any]]) -> Path:
    """Régénère atomiquement METRICS.md dans `store_dir`."""
    _atomic_io.write_text_atomic(store_dir / "METRICS.md", render_metrics_markdown(records))
    return store_dir


def write_session_details(store_dir: Path, slug: str, metrics: Dict[str, Any]) -> Path:
    """Persiste atomiquement `<slug>.json` et `<slug>.md` dans `<store_dir>/reports/`."""
    reports_dir = store_dir / "reports"
    _atomic_io.write_json_atomic(reports_dir / f"{slug}.json", metrics, indent=2, ensure_ascii=False)
    _atomic_io.write_text_atomic(reports_dir / f"{slug}.md", render_session_detail_markdown(metrics))
    return reports_dir


def purge_session_details(store_dir: Path, rec: Dict[str, Any]) -> None:
    """Supprime les rapports détaillés d'une session dans `<store_dir>/reports/`."""
    slug = rec.get("detail_slug") or slugify_label(
        str(rec.get("label") or ""), f"session-{str(rec.get('conversation_id') or '')[:8]}",
    )
    reports_dir = store_dir / "reports"
    for ext in (".json", ".md", ".html"):
        fpath = reports_dir / f"{slug}{ext}"
        if fpath.is_file():
            fpath.unlink()


def _harmonize_project_name(project: str, records: List[Dict[str, Any]]) -> str:
    """Réutilise la casse existante d'un projet connu (comparaison insensible à la casse)."""
    clean = project.strip()
    if not clean:
        return ""
    cf = clean.casefold()
    counts: Counter = Counter()
    for rec in records:
        p = str(rec.get("project") or "").strip()
        if p and p.casefold() == cf:
            counts[p] += 1
    if not counts:
        return clean
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0] != kv[0].lower(), kv[0]))[0]


def find_existing_recorded_session(
    records: List[Dict[str, Any]], conversation_id: str, *, label: str = "",
    from_step: Optional[int] = None, candidate_step: Optional[int] = None,
    allow_latest_fallback: bool = False,
) -> Optional[Dict[str, Any]]:
    """Résout une session enregistrée existante (priorité `label`, puis tolérance `±2` steps)."""
    cid = str(conversation_id or "").strip()
    if not cid or not records:
        return None
    clean_lbl = str(label or "").strip()
    archived = [
        r for r in records
        if r.get("conversation_id") == cid and str(r.get("status") or "archived").lower() == "archived"
    ]
    if not archived:
        return None
    if clean_lbl:
        for rec in archived:
            if str(rec.get("label") or "").strip() == clean_lbl:
                return dict(rec)
        low_lbl = clean_lbl.lower()
        for rec in archived:
            if str(rec.get("label") or "").strip().lower() == low_lbl:
                return dict(rec)
    step_probe = from_step if (isinstance(from_step, int) and not isinstance(from_step, bool)) else candidate_step
    if step_probe is not None:
        matched = find_archived_session(archived, cid, step_probe)
        if matched is not None:
            return matched
    return dict(archived[-1]) if allow_latest_fallback else None


def resolve_sealed_update_target(
    history_path: Path, conversation_id: str, *, label: str = "", project: str = "",
    from_step: Optional[int] = None, full_conversation: bool = False,
    transcript_path: Optional[Path] = None,
) -> Tuple[Optional[int], bool, str, str, Optional[Dict[str, Any]]]:
    """Résout `(from_step, full_conversation, label, project, existing)` pour `update_sealed=True`."""
    eff_from, eff_full = from_step, bool(full_conversation)
    eff_label, eff_project = str(label or "").strip(), str(project or "").strip()
    records = load_history(history_path)
    candidate_step: Optional[int] = None
    if not eff_full and eff_from is None and transcript_path is not None and transcript_path.is_file():
        cand = resolve_gravity_window(transcript_path, current_prompt_is_metrics=False).get("start_step_index")
        if isinstance(cand, int) and not isinstance(cand, bool):
            candidate_step = cand
    existing = find_existing_recorded_session(
        records, conversation_id, label=eff_label, from_step=eff_from,
        candidate_step=candidate_step, allow_latest_fallback=not eff_full,
    )
    if existing is None:
        return eff_from, eff_full, eff_label, eff_project, None
    if not eff_full and eff_from is None:
        ex_wstep = existing.get("window_start_step")
        if ex_wstep is None and existing.get("window_mode") == "full_conversation":
            eff_full = True
        elif isinstance(ex_wstep, int) and not isinstance(ex_wstep, bool):
            eff_from = ex_wstep
    if not eff_label and existing.get("label"):
        eff_label = str(existing["label"]).strip()
    if not eff_project and existing.get("project"):
        eff_project = str(existing["project"]).strip()
    return eff_from, eff_full, eff_label, eff_project, existing


def record_session_metrics(
    metrics: Dict[str, Any], *, store_dir: Path, history_path: Path, label: str = "",
    note: str = "", summary_only: bool = False, auto_label: bool = False,
    force: bool = False, update_sealed: bool = False, self_archiving: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Enregistre/archive une session de manière immuable sous verrou dans `history.jsonl`."""
    _ = self_archiving
    explicit_label = label.strip()
    metrics_to_write = dict(metrics)

    with history_lock(history_path):
        records = load_history(history_path)
        cid = str(metrics_to_write.get("root_conversation_id") or "").strip()
        win = metrics_to_write.get("window") or {}
        w_start = win.get("start_step_index")
        existing = (
            find_existing_recorded_session(records, cid, label=explicit_label, from_step=w_start, allow_latest_fallback=False)
            if (update_sealed or force) and explicit_label
            else find_archived_session(records, cid, w_start)
        )
        if existing is not None and not (force or update_sealed):
            ret = dict(existing)
            ret["already_archived"] = True
            return ret, records

        harmonized_proj = _harmonize_project_name(str(metrics_to_write.get("project") or ""), records)
        if not harmonized_proj and existing is not None and existing.get("project"):
            harmonized_proj = _harmonize_project_name(str(existing["project"]), records)
        metrics_to_write["project"] = harmonized_proj
        metrics["project"] = harmonized_proj
        if explicit_label:
            resolved_label = explicit_label
        elif existing is not None and existing.get("label"):
            resolved_label = str(existing["label"]).strip()
        elif auto_label:
            resolved_label = suggest_auto_label(
                project=str(metrics_to_write.get("project") or ""),
                first_prompt=str(win.get("first_prompt") or ""),
                started_at=metrics_to_write.get("summary", {}).get("first_ts"),
                existing_labels=[r.get("label") or "" for r in records],
            )
        else:
            resolved_label = str(metrics_to_write.get("label") or "").strip()

        resolved_note = note.strip()
        if not resolved_note and existing is not None and (update_sealed or force) and existing.get("note"):
            resolved_note = str(existing["note"]).strip()

        if resolved_label:
            metrics_to_write["label"] = resolved_label
            metrics["label"] = resolved_label

        ms = metrics_to_write.get("milestones")
        if isinstance(ms, dict) and isinstance(ms.get("phases"), list):
            new_phases = [{**ph, "status": "done"} for ph in ms["phases"] if isinstance(ph, dict)]
            metrics_to_write["milestones"] = {
                "mode": ms.get("mode") or "2_phases",
                "current_phase": "Clôture / Enregistrée",
                "phases": new_phases,
            }
            metrics["milestones"] = metrics_to_write["milestones"]

        rec = to_session_record(metrics_to_write, label=resolved_label, note=resolved_note)
        updated = upsert_session(records, rec)
        save_history(history_path, updated)
        regenerate_views(store_dir, updated)
        if not summary_only:
            if existing is not None:
                old_slug = existing.get("detail_slug") or slugify_label(
                    str(existing.get("label") or ""), f"session-{cid[:8]}",
                )
                if old_slug and old_slug != rec["detail_slug"]:
                    purge_session_details(store_dir, existing)
            write_session_details(store_dir, rec["detail_slug"], metrics_to_write)
        if cid:
            pop_active_capture(cid, store_dir=store_dir)
            unignore_session(cid, store_dir=store_dir)
        _publish_session_metrics_signal(store_dir, updated, rec)

    saved_rec = next(
        (r for r in updated if r.get("label") == rec["label"] and r.get("conversation_id") == rec["conversation_id"]),
        rec,
    )
    return saved_rec, updated


def archive_conversation(
    target: str, *, store_dir: Path, history_path: Path, brain_dir: Path,
    catalog_path: Path, label: str = "", note: str = "",
    pricing_profile: str = "2026_nov_standard", gravity_window: bool = True,
    full_conversation: bool = False, from_step: Optional[int] = None,
    project: str = "", gravity_profile: str = "", summary_only: bool = False,
    force: bool = False, update_sealed: bool = False, self_archiving: bool = False,
    bridge_db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """Résout un UUID ou préfixe, collecte la session et l'enregistre avec garde-fous."""
    _ = bridge_db_path  # temporary compat stub until W12 after W8-W10 drop callers
    cid = resolve_conversation_id_in_brain(target, brain_dir)
    eff_from_step, eff_full, eff_label, eff_project = from_step, bool(full_conversation), label, project
    if not eff_full and eff_from_step is None:
        active_cap = load_active_captures(store_dir).get(cid)
        if isinstance(active_cap, dict):
            if isinstance(active_cap.get("start_step"), int):
                eff_from_step = active_cap["start_step"]
            if not eff_label.strip() and active_cap.get("label"):
                eff_label = str(active_cap["label"]).strip()
            if not eff_project.strip() and active_cap.get("project"):
                eff_project = str(active_cap["project"]).strip()

    if update_sealed and not eff_full and eff_from_step is None:
        tpath = brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
        eff_from_step, eff_full, eff_label, eff_project, _ = resolve_sealed_update_target(
            history_path, cid, label=eff_label, project=eff_project,
            from_step=eff_from_step, full_conversation=eff_full, transcript_path=tpath,
        )

    metrics = collect_run_metrics(
        root_id=cid, brain_dir=brain_dir, pricing_catalog_path=catalog_path,
        pricing_profile=pricing_profile, label=eff_label,
        gravity_window=bool(gravity_window and not eff_full),
        full_conversation=eff_full, from_step=eff_from_step if not eff_full else None,
        project=eff_project, gravity_profile=gravity_profile,
        store_dir=store_dir, self_archiving=self_archiving,
    )
    saved_rec, records = record_session_metrics(
        metrics, store_dir=store_dir, history_path=history_path, label=eff_label,
        note=note, summary_only=summary_only, auto_label=not bool(eff_label.strip()),
        force=force, update_sealed=update_sealed, self_archiving=self_archiving,
    )
    return saved_rec, records, metrics


def load_session_for_inspect(
    target: str, *, store_dir: Path, history_path: Path, brain_dir: Path,
    catalog_path: Path, pricing_profile: str = "2026_nov_standard",
    bridge_db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], str]:
    """Charge une session depuis `reports/<slug>.json` (hors-ligne) ou la recalcule depuis `brain_dir`."""
    _ = bridge_db_path  # temporary compat stub until W12 after W8-W10 drop callers
    records = load_history(history_path)
    cid, label, slug = target.strip(), "", ""
    if records:
        try:
            rec = records[resolve_session_index(records, target)]
            cid = rec["conversation_id"]
            label = rec.get("label") or ""
            slug = rec.get("detail_slug") or slugify_label(label, f"session-{cid[:8]}")
        except KeyError:
            pass

    if slug:
        cached_json = store_dir / "reports" / f"{slug}.json"
        if cached_json.is_file():
            return json.loads(cached_json.read_text(encoding="utf-8")), slug

    conv_dir = brain_dir / cid
    if not conv_dir.is_dir():
        raise FileNotFoundError(f"Conversation introuvable : {cid} dans {brain_dir}")

    metrics = collect_run_metrics(
        root_id=cid, brain_dir=brain_dir, pricing_catalog_path=catalog_path,
        pricing_profile=pricing_profile, label=label, store_dir=store_dir,
    )
    return metrics, slug


def update_session_metadata(
    target: str, *, store_dir: Path, history_path: Path,
    label: Optional[str] = None, project: Optional[str] = None, note: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Met à jour label, projet ou note d'une session archivée sans modifier ses métriques financières."""
    with history_lock(history_path):
        records = load_history(history_path)
        if not records:
            raise ValueError("Historique vide.")
        idx = resolve_session_index(records, target)
        rec = dict(records[idx])
        cid = str(rec.get("conversation_id") or "")
        old_slug = rec.get("detail_slug") or slugify_label(str(rec.get("label") or ""), f"session-{cid[:8]}")

        if label is not None and label.strip():
            new_label = label.strip()
            if any(i != idx and str(r.get("label") or "").strip() == new_label for i, r in enumerate(records)):
                raise ValueError(f"Un autre rapport porte déjà le label {new_label!r}.")
            rec["label"] = new_label
            new_slug = slugify_label(new_label, f"session-{cid[:8]}")
        else:
            new_slug = old_slug

        if project is not None:
            rec["project"] = _harmonize_project_name(project, [r for i, r in enumerate(records) if i != idx])
        if note is not None:
            rec["note"] = note.strip()

        rec["detail_slug"] = new_slug
        records[idx] = rec

        reports_dir = store_dir / "reports"
        old_json, old_md = reports_dir / f"{old_slug}.json", reports_dir / f"{old_slug}.md"
        if old_json.is_file():
            try:
                det = json.loads(old_json.read_text(encoding="utf-8"))
                if isinstance(det, dict):
                    det["label"] = rec["label"]
                    det["project"] = rec.get("project") or ""
                    if new_slug != old_slug:
                        old_json.unlink(missing_ok=True)
                        old_md.unlink(missing_ok=True)
                    write_session_details(store_dir, new_slug, det)
            except Exception:
                pass

        save_history(history_path, records)
        regenerate_views(store_dir, records)
    return dict(rec), records


def apply_remove(
    target: str, *, store_dir: Path, history_path: Path, purge_reports: bool = False,
) -> Tuple[Dict[str, Any], int]:
    """Retire une session sous verrou, purge optionnellement `reports/` et régénère les vues."""
    with history_lock(history_path):
        records = load_history(history_path)
        if not records:
            raise ValueError("Historique vide.")
        idx = resolve_session_index(records, target)
        target_rec = records[idx]
        updated = remove_session(records, target)
        if purge_reports:
            purge_session_details(store_dir, target_rec)
        save_history(history_path, updated)
        regenerate_views(store_dir, updated)
    return target_rec, len(updated)
