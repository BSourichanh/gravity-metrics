#!/usr/bin/env python3
"""Opérations transactionnelles sur le store centralisé Gravity Metrics (100 % stdlib)."""

from collections import Counter
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collector import collect_run_metrics, slugify_label, suggest_auto_label
from .history import (
    history_lock,
    load_history,
    remove_session,
    resolve_session_index,
    save_history,
    to_session_record,
    upsert_session,
)
from .markdown_report import render_metrics_markdown, render_session_detail_markdown
from .pricing import default_store_dir


def resolve_store_paths(
    history_path: Optional[Path] = None,
    store_dir: Optional[Path] = None,
) -> Tuple[Path, Path]:
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
    """Régénère METRICS.md dans `store_dir`."""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "METRICS.md").write_text(render_metrics_markdown(records), encoding="utf-8")
    return store_dir


def write_session_details(store_dir: Path, slug: str, metrics: Dict[str, Any]) -> Path:
    """Persiste `<slug>.json` et `<slug>.md` dans `<store_dir>/reports/`."""
    reports_dir = store_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{slug}.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (reports_dir / f"{slug}.md").write_text(
        render_session_detail_markdown(metrics), encoding="utf-8"
    )
    return reports_dir


def purge_session_details(store_dir: Path, rec: Dict[str, Any]) -> None:
    """Supprime les rapports détaillés d'une session dans `<store_dir>/reports/`."""
    slug = rec.get("detail_slug") or slugify_label(
        str(rec.get("label") or ""),
        f"session-{str(rec.get('conversation_id') or '')[:8]}",
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


def record_session_metrics(
    metrics: Dict[str, Any],
    *,
    store_dir: Path,
    history_path: Path,
    label: str = "",
    note: str = "",
    summary_only: bool = False,
    auto_label: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Enregistre une session sous verrou dans `history.jsonl` et régénère les vues."""
    explicit_label = label.strip()
    metrics_to_write = dict(metrics)

    with history_lock(history_path):
        records = load_history(history_path)
        harmonized_proj = _harmonize_project_name(str(metrics_to_write.get("project") or ""), records)
        metrics_to_write["project"] = harmonized_proj
        metrics["project"] = harmonized_proj
        if explicit_label:
            resolved_label = explicit_label
        elif auto_label:
            win = metrics_to_write.get("window") or {}
            resolved_label = suggest_auto_label(
                project=str(metrics_to_write.get("project") or ""),
                first_prompt=str(win.get("first_prompt") or ""),
                started_at=metrics_to_write.get("summary", {}).get("first_ts"),
                existing_labels=[r.get("label") or "" for r in records],
            )
        else:
            resolved_label = str(metrics_to_write.get("label") or "").strip()

        if resolved_label:
            metrics_to_write["label"] = resolved_label
            metrics["label"] = resolved_label

        rec = to_session_record(metrics_to_write, label=resolved_label, note=note)
        updated = upsert_session(records, rec)
        save_history(history_path, updated)
        regenerate_views(store_dir, updated)
        if not summary_only:
            write_session_details(store_dir, rec["detail_slug"], metrics_to_write)

    saved_rec = next(
        (
            r
            for r in updated
            if r.get("label") == rec["label"]
            and r.get("conversation_id") == rec["conversation_id"]
        ),
        rec,
    )
    return saved_rec, updated


def load_session_for_inspect(
    target: str,
    *,
    store_dir: Path,
    history_path: Path,
    brain_dir: Path,
    catalog_path: Path,
    pricing_profile: str = "2026_nov_standard",
) -> Tuple[Dict[str, Any], str]:
    """Charge une session depuis `reports/<slug>.json` (hors-ligne) ou la recalcule depuis `brain_dir`."""
    records = load_history(history_path)
    cid = target.strip()
    label = ""
    slug = ""
    if records:
        try:
            idx = resolve_session_index(records, target)
            rec = records[idx]
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
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=catalog_path,
        pricing_profile=pricing_profile,
        label=label,
    )
    return metrics, slug


def apply_remove(
    target: str,
    *,
    store_dir: Path,
    history_path: Path,
    purge_reports: bool = False,
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
