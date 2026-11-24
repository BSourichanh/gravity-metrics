#!/usr/bin/env python3
"""Opérations transactionnelles sur le store centralisé Gravity Metrics (100 % stdlib)."""

from collections import Counter
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .collector import (
    collect_run_metrics,
    resolve_conversation_id_in_brain,
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
from .lifecycle import (
    load_active_captures,
    pop_active_capture,
    unignore_session,
)
from .markdown_report import render_metrics_markdown, render_session_detail_markdown
from .pricing import default_store_dir


class PendingBridgeJobsError(RuntimeError):
    """Erreur levée lorsqu'un archivage est bloqué par des jobs Gravity Bridge encore actifs."""

    def __init__(self, pending_jobs: List[Dict[str, Any]]) -> None:
        self.pending_jobs = list(pending_jobs)
        details = ", ".join(
            f"{j.get('thread_id') or '?'}->{j.get('to_workspace') or '?'} ({j.get('status') or 'actif'})"
            for j in self.pending_jobs[:5]
        )
        if len(self.pending_jobs) > 5:
            details += f", +{len(self.pending_jobs) - 5} autre(s)"
        super().__init__(
            f"Archivage bloqué : {len(self.pending_jobs)} job(s) Gravity Bridge encore actif(s) "
            f"[{details}]. Attendez leur clôture ou relancez avec --force."
        )


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
    force: bool = False,
    update_sealed: bool = False,
    self_archiving: bool = False,
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
        existing = find_archived_session(records, cid, w_start)
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
        if not resolved_note and existing is not None and update_sealed and existing.get("note"):
            resolved_note = str(existing["note"]).strip()

        if resolved_label:
            metrics_to_write["label"] = resolved_label
            metrics["label"] = resolved_label

        ms = metrics_to_write.get("milestones")
        if isinstance(ms, dict) and isinstance(ms.get("phases"), list):
            new_phases = []
            for ph in ms["phases"]:
                if isinstance(ph, dict):
                    p_copy = dict(ph)
                    p_copy["status"] = "done"
                    new_phases.append(p_copy)
            metrics_to_write["milestones"] = {
                "mode": ms.get("mode") or "2_phases",
                "current_phase": "Clôture / Archivé",
                "phases": new_phases,
            }
            metrics["milestones"] = metrics_to_write["milestones"]

        rec = to_session_record(metrics_to_write, label=resolved_label, note=resolved_note)
        updated = upsert_session(records, rec)
        save_history(history_path, updated)
        regenerate_views(store_dir, updated)
        if not summary_only:
            write_session_details(store_dir, rec["detail_slug"], metrics_to_write)
        if cid:
            pop_active_capture(cid, store_dir=store_dir)
            unignore_session(cid, store_dir=store_dir)

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


def archive_conversation(
    target: str,
    *,
    store_dir: Path,
    history_path: Path,
    brain_dir: Path,
    catalog_path: Path,
    label: str = "",
    note: str = "",
    pricing_profile: str = "2026_nov_standard",
    gravity_window: bool = True,
    full_conversation: bool = False,
    from_step: Optional[int] = None,
    project: str = "",
    gravity_profile: str = "",
    summary_only: bool = False,
    force: bool = False,
    update_sealed: bool = False,
    bridge_db_path: Optional[Path] = None,
    self_archiving: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    """Résout un UUID ou préfixe, collecte la session et l'archive avec garde-fous."""
    _ = bridge_db_path
    cid = resolve_conversation_id_in_brain(target, brain_dir)
    eff_from_step = from_step
    eff_label = label
    eff_project = project
    if not full_conversation and eff_from_step is None:
        active_cap = load_active_captures(store_dir).get(cid)
        if isinstance(active_cap, dict):
            if isinstance(active_cap.get("start_step"), int):
                eff_from_step = active_cap["start_step"]
            if not eff_label.strip() and active_cap.get("label"):
                eff_label = str(active_cap["label"]).strip()
            if not eff_project.strip() and active_cap.get("project"):
                eff_project = str(active_cap["project"]).strip()

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=catalog_path,
        pricing_profile=pricing_profile,
        label=eff_label,
        gravity_window=bool(gravity_window and not full_conversation),
        full_conversation=bool(full_conversation),
        from_step=eff_from_step if not full_conversation else None,
        project=eff_project,
        gravity_profile=gravity_profile,
        store_dir=store_dir,
        self_archiving=self_archiving,
    )
    saved_rec, records = record_session_metrics(
        metrics,
        store_dir=store_dir,
        history_path=history_path,
        label=eff_label,
        note=note,
        summary_only=summary_only,
        auto_label=not bool(eff_label.strip()),
        force=force,
        update_sealed=update_sealed,
        self_archiving=self_archiving,
    )
    return saved_rec, records, metrics


def load_session_for_inspect(
    target: str,
    *,
    store_dir: Path,
    history_path: Path,
    brain_dir: Path,
    catalog_path: Path,
    pricing_profile: str = "2026_nov_standard",
    bridge_db_path: Optional[Path] = None,
) -> Tuple[Dict[str, Any], str]:
    """Charge une session depuis `reports/<slug>.json` (hors-ligne) ou la recalcule depuis `brain_dir`."""
    _ = bridge_db_path
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
        store_dir=store_dir,
    )
    return metrics, slug


def update_session_metadata(
    target: str,
    *,
    store_dir: Path,
    history_path: Path,
    label: Optional[str] = None,
    project: Optional[str] = None,
    note: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Met à jour label, projet ou note d'une session archivée sans modifier ses métriques financières."""
    with history_lock(history_path):
        records = load_history(history_path)
        if not records:
            raise ValueError("Historique vide.")
        idx = resolve_session_index(records, target)
        rec = dict(records[idx])
        cid = str(rec.get("conversation_id") or "")
        old_slug = rec.get("detail_slug") or slugify_label(
            str(rec.get("label") or ""), f"session-{cid[:8]}"
        )

        if label is not None and label.strip():
            new_label = label.strip()
            conflict = any(
                i != idx and str(r.get("label") or "").strip() == new_label
                for i, r in enumerate(records)
            )
            if conflict:
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
        old_json = reports_dir / f"{old_slug}.json"
        old_md = reports_dir / f"{old_slug}.md"
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
