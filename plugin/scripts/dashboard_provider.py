#!/usr/bin/env python3
"""Fournisseur SPI-1 pour Gravity Dashboard (vues metrics/session-detail/audit-detail & actions, 100 % stdlib)."""

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_hook_rt = importlib.import_module("_shared.hook_runtime")
_path_safety = importlib.import_module("_shared.path_safety")
_tr_reader = importlib.import_module("_shared.transcript_reader")

from metrics import (  # noqa: E402
    apply_remove, archive_conversation, default_brain_dir, discover_audit_roots,
    ignore_session, list_audits_bundle, list_pending_sessions, load_active_captures,
    load_ignored_sessions, load_session_meta, read_audit,
    resolve_conversation_id_in_brain, resolve_store_paths, slugify_label,
    unignore_session, update_session_metadata,
)

DEFAULT_LIMIT, MAX_LIMIT, MAX_PAYLOAD_BYTES, MAX_AGENTS_IN_DETAIL = 100, 500, 450_000, 200
_HEX_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8,}(?:-[0-9a-fA-F]{0,4}){0,4}$")
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRAJECTORY_KEYS = frozenset(
    {"trajectory", "raw_trajectory", "steps", "transcript", "raw_transcript", "messages", "events_raw"}
)


def _clamp_limit(limit: Any) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT

def _stat_token(path: Optional[Path]) -> str:
    try:
        st = Path(path).stat() if path is not None else None
        return f"{st.st_mtime_ns}:{st.st_size}" if st else "missing"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"

def _short_digest(parts: Sequence[Any]) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]

def _file_uri_if_exists(path: Path) -> str:
    try:
        return path.resolve().as_uri() if path.is_file() and not _path_safety.is_symlink_or_junction(path) else ""
    except OSError:
        return ""

def _read_json_ro(path: Path) -> Tuple[Any, Optional[str]]:
    if _path_safety.is_symlink_or_junction(path):
        return None, f"Refused symlink or junction: {path.name}."
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"Failed to read {path.name}: {exc}"
    warn = f"UTF-8 BOM detected in {path.name}." if raw.startswith(b"\xef\xbb\xbf") else None
    try:
        return json.loads((raw[3:] if warn else raw).decode("utf-8")), warn
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"Invalid JSON in {path.name}: {exc}"

def _enforce_payload_budget(payload: Dict[str, Any], list_keys: Sequence[str]) -> Dict[str, Any]:
    _size = lambda: len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if _size() < MAX_PAYLOAD_BYTES:
        return payload
    payload["truncated"] = True
    for key in list_keys:
        items = payload.get(key)
        while _size() >= MAX_PAYLOAD_BYTES and isinstance(items, list) and items:
            items = items[: max(0, len(items) // 2)]
            payload[key] = items
    return payload

def _raw_metrics_home(*, metrics_home: Optional[str] = None, gravity_home: Optional[str] = None) -> Path:
    if metrics_home:
        return Path(os.path.abspath(os.fspath(Path(metrics_home).expanduser())))
    if env_m := os.environ.get("GRAVITY_METRICS_HOME", "").strip():
        return Path(os.path.abspath(os.fspath(Path(env_m).expanduser())))
    if gravity_home:
        return Path(os.path.abspath(os.fspath(Path(gravity_home).expanduser() / "metrics")))
    if env_g := os.environ.get("GRAVITY_HOME", "").strip():
        return Path(os.path.abspath(os.fspath(Path(env_g).expanduser() / "metrics")))
    return Path(os.path.abspath(os.fspath(_path_safety.safe_home_dir() / ".gemini" / "gravity" / "metrics")))


def _resolve_metrics_home(*, metrics_home: Optional[str] = None, gravity_home: Optional[str] = None) -> Path:
    return _raw_metrics_home(metrics_home=metrics_home, gravity_home=gravity_home).resolve()


def _load_history_ro(history_path: Path, warnings: List[str]) -> List[Dict[str, Any]]:
    if not history_path.is_file() or _path_safety.is_symlink_or_junction(history_path):
        return []
    try:
        text = history_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"Failed to read history.jsonl: {exc}")
        return []
    records: List[Dict[str, Any]] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            warnings.append(f"Ignored corrupted JSONL line {idx} in history.jsonl.")
            continue
        if not isinstance(obj, dict) or not obj.get("conversation_id"):
            warnings.append(f"Ignored invalid history.jsonl record at line {idx}.")
            continue
        rec = dict(obj)
        rec["status"] = str(rec.get("status") or "archived")
        if rec.get("bridge_children_count") is None:
            rec["bridge_children_count"] = 0
        if not rec.get("detail_slug"):
            rec["detail_slug"] = slugify_label(str(rec.get("label") or ""), f"session-{str(rec['conversation_id'])[:8]}")
        records.append(rec)
    return records


def _canonical_projects_map(records: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    grouped: Dict[str, Counter] = defaultdict(Counter)
    for rec in records:
        if proj := str(rec.get("project") or "").strip():
            grouped[proj.casefold()][proj] += 1
    return {cf: min(cnts.items(), key=lambda kv: (-kv[1], kv[0] != kv[0].lower(), kv[0]))[0] for cf, cnts in grouped.items()}


def _aggregate_by_project(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    proj_map = _canonical_projects_map(records)
    buckets: Dict[str, Dict[str, Any]] = {}
    tot_26 = tot_27 = 0.0
    for rec in records:
        raw_p = str(rec.get("project") or "").strip()
        canon_p = proj_map.get(raw_p.casefold(), raw_p) if raw_p else "(sans projet)"
        b = buckets.setdefault(canon_p.casefold(), {
            "project": canon_p, "sessions": 0, "cost_usd": 0.0, "cost_2027_usd": 0.0,
            "cache_sum": 0.0, "llm_calls": 0, "agents": 0, "wall_clock_s": 0.0,
        })
        c26 = float(rec.get("cost_usd") or 0.0)
        c27 = float(rec["cost_2027_usd"]) if rec.get("cost_2027_usd") is not None else c26
        b["sessions"] += 1
        b["cost_usd"] += c26
        b["cost_2027_usd"] += c27
        b["cache_sum"] += float(rec.get("cache_hit_rate_pct") or 0.0)
        b["llm_calls"] += int(rec.get("llm_calls") or 0)
        b["agents"] += int(rec.get("agents") or 0)
        b["wall_clock_s"] += float(rec.get("wall_clock_s") or 0.0)
        tot_26 += c26
        tot_27 += c27
    out = [
        {
            "project": b["project"], "sessions": b["sessions"],
            "cost_usd": round(b["cost_usd"], 6), "cost_2027_usd": round(b["cost_2027_usd"], 6),
            "share_pct": round((b["cost_usd"] / tot_26) * 100.0, 2) if tot_26 > 0 else 0.0,
            "share_2027_pct": round((b["cost_2027_usd"] / tot_27) * 100.0, 2) if tot_27 > 0 else 0.0,
            "avg_cache_hit_rate_pct": round(b["cache_sum"] / b["sessions"], 2) if b["sessions"] else 0.0,
            "llm_calls": b["llm_calls"], "agents": b["agents"],
            "wall_clock_s": round(b["wall_clock_s"], 2), "bridge_sessions": 0,
        }
        for b in buckets.values()
    ]
    out.sort(key=lambda item: (-item["cost_usd"], -item["sessions"], item["project"].casefold()))
    return out


def _compute_kpis(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    cnt = len(records)
    cost_usd = sum(float(r.get("cost_usd") or 0.0) for r in records)
    cost_2027 = sum(float(r["cost_2027_usd"]) if r.get("cost_2027_usd") is not None else float(r.get("cost_usd") or 0.0) for r in records)
    return {
        "sessions_count": cnt,
        "archived_count": sum(1 for r in records if str(r.get("status") or "archived").strip().lower() == "archived"),
        "active_count": sum(1 for r in records if str(r.get("status") or "").strip().lower() == "active"),
        "cost_usd": round(cost_usd, 6), "cost_2027_usd": round(cost_2027, 6),
        "cache_savings_usd": round(sum(float(r.get("cache_savings_usd") or 0.0) for r in records), 6),
        "avg_cache_hit_rate_pct": round(sum(float(r.get("cache_hit_rate_pct") or 0.0) for r in records) / cnt, 2) if cnt else 0.0,
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in records),
        "useful_tokens": sum(int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0) for r in records),
        "total_llm_calls": sum(int(r.get("llm_calls") or 0) for r in records),
        "bridge_sessions_count": 0, "bridge_children_total": 0,
    }


def _matches_query(rec: Dict[str, Any], q_cf: str) -> bool:
    if not q_cf:
        return True
    fields = ("label", "project", "conversation_id", "note", "primary_model", "execution_mode", "current_phase")
    return any(isinstance(rec.get(f), str) and q_cf in rec[f].casefold() for f in fields)


def _has_brain_transcript(brain_dir: Optional[Path], cid: str) -> bool:
    if brain_dir is None or not cid or "/" in cid or "\\" in cid or ".." in cid:
        return False
    conv_dir = brain_dir / cid
    tpath = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
    return conv_dir.is_dir() and tpath.is_file() and not _path_safety.is_symlink_or_junction(tpath)


def _derive_pending_states_ro(
    *, records: Sequence[Dict[str, Any]], active_captures: Dict[str, Dict[str, Any]],
    ignored_map: Dict[str, Dict[str, Any]], session_meta: Dict[str, Dict[str, Any]],
    brain_dir: Optional[Path] = None, m_home: Optional[Path] = None, history_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    archived_cids = {str(r.get("conversation_id") or "").strip() for r in records if str(r.get("status") or "archived").strip().lower() == "archived"}
    active_hist = {
        str(r.get("conversation_id") or "").strip(): r for r in records
        if str(r.get("conversation_id") or "").strip() and str(r.get("status") or "").strip().lower() == "active"
    }
    pending: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []

    def _build_item(cid: str, *, lp_item: Optional[Dict[str, Any]] = None, brain_exists: bool, orphan: bool) -> Dict[str, Any]:
        lp = lp_item or {}
        cap, ign = active_captures.get(cid), ignored_map.get(cid)
        meta, hist_a = session_meta.get(cid) or {}, active_hist.get(cid) or {}
        raw_step = (cap or {}).get("start_step")
        if not isinstance(raw_step, int) or isinstance(raw_step, bool):
            w_step = lp.get("window_start_step")
            raw_step = w_step if isinstance(w_step, int) and not isinstance(w_step, bool) else None
        return {
            "conversation_id": cid,
            "label": str((cap or {}).get("label") or hist_a.get("label") or lp.get("title") or f"session-{cid[:8]}"),
            "project": str(lp.get("project") or (cap or {}).get("project") or meta.get("project") or hist_a.get("project") or ""),
            "cwd": str(meta.get("cwd") or ""),
            "started_at": str(lp.get("started_at") or (cap or {}).get("started_at") or meta.get("started_at") or hist_a.get("started_at") or ""),
            "updated_at": str(lp.get("updated_at") or (cap or {}).get("updated_at") or meta.get("updated_at") or hist_a.get("ended_at") or ""),
            "active_capture": cap is not None,
            "start_step": raw_step,
            "ignored": ign is not None or bool(lp.get("ignored")),
            "ignored_reason": str((ign or {}).get("reason") or lp.get("ignored_reason") or ""),
            "ignored_at": str((ign or {}).get("ignored_at") or lp.get("ignored_at") or ""),
            "current_phase": str(lp.get("current_phase") or hist_a.get("current_phase") or ""),
            "llm_calls": int(lp.get("llm_calls") or hist_a.get("llm_calls") or 0),
            "bridge_children_count": 0,
            "brain_exists": brain_exists,
            "orphan": orphan,
        }

    if brain_dir is not None and brain_dir.is_dir():
        eff_history = history_path if history_path is not None else (m_home / "data" / "history.jsonl" if m_home is not None else None)
        live_cids = set()
        for lp in list_pending_sessions(brain_dir, eff_history, store_dir=m_home, include_ignored=True):
            cid = str(lp.get("conversation_id") or "").strip()
            if not cid:
                continue
            live_cids.add(cid)
            item = _build_item(cid, lp_item=lp, brain_exists=True, orphan=False)
            (ignored if item["ignored"] else pending).append(item)
        for cid in sorted(c for c in ((set(active_captures) | set(ignored_map)) - live_cids - archived_cids) if c):
            if _has_brain_transcript(brain_dir, cid):
                continue
            item = _build_item(cid, brain_exists=False, orphan=True)
            (ignored if item["ignored"] else pending).append(item)
        for cid in sorted(c for c in (set(active_hist) - live_cids - archived_cids - set(active_captures) - set(ignored_map)) if c):
            pending.append(_build_item(cid, brain_exists=_has_brain_transcript(brain_dir, cid), orphan=False))
    else:
        for cid in sorted(c for c in ((set(active_captures) | set(ignored_map) | set(active_hist)) - archived_cids) if c):
            item = _build_item(cid, brain_exists=False, orphan=(cid in active_captures or cid in ignored_map))
            (ignored if item["ignored"] else pending).append(item)

    sort_k = lambda e: (str(e.get("updated_at") or e.get("ignored_at") or ""), str(e.get("conversation_id") or ""))
    pending.sort(key=sort_k, reverse=True)
    ignored.sort(key=sort_k, reverse=True)
    return pending, ignored


def _workspace_dirs_from_meta(session_meta: Dict[str, Dict[str, Any]]) -> List[Path]:
    dirs, seen = [], set()
    for meta in session_meta.values():
        cwd = str(meta.get("cwd") or "").strip()
        if cwd and (key := os.path.normcase(cwd)) not in seen:
            seen.add(key)
            dirs.append(Path(cwd).expanduser())
    return dirs


def _coerce_audits_dirs(raw: Any) -> Optional[List[Path]]:
    if isinstance(raw, (str, Path)):
        return [Path(raw).expanduser()]
    if isinstance(raw, list):
        return [Path(p).expanduser() for p in raw if isinstance(p, (str, Path)) and str(p).strip()]
    return None


def _audit_stat_tokens(audits_dirs: Optional[List[Path]], ws_dirs: List[Path]) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    for r in discover_audit_roots(audits_dir=audits_dirs, workspace_dirs=ws_dirs if audits_dirs is None else None, include_cwd=False):
        rpath = r["path"]
        try:
            for fp in sorted(rpath.rglob("*.md")):
                if fp.is_file() and not fp.name.startswith("_") and not _path_safety.is_symlink_or_junction(fp):
                    rel = fp.resolve().relative_to(rpath.resolve()).as_posix()
                    if "code/" not in rel.lower():
                        tokens.append((f"{r['root_id']}:{rel}", _stat_token(fp)))
        except (OSError, ValueError):
            continue
    return tokens


def get_metrics_view(
    *, project: str = "", limit: int = DEFAULT_LIMIT, q: str = "", since_signature: str = "",
    m_home: Path, audits_dirs: Optional[List[Path]] = None, brain_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    eff_limit, project_clean, q_clean = _clamp_limit(limit), project.strip(), q.strip()
    eff_brain = Path(brain_dir).expanduser().resolve() if brain_dir is not None else None
    history_path, md_path = m_home / "data" / "history.jsonl", m_home / "METRICS.md"
    session_meta = load_session_meta(m_home)
    ws_dirs = _workspace_dirs_from_meta(session_meta)
    sig = _short_digest([
        str(m_home), str(eff_brain) if eff_brain is not None else "", project_clean.casefold(), eff_limit, q_clean.casefold(),
        _stat_token(history_path), _stat_token(m_home / "active_captures.json"),
        _stat_token(m_home / "ignored.json"), _stat_token(m_home / "session_meta.json"),
        _stat_token(md_path), _stat_token(eff_brain), *_audit_stat_tokens(audits_dirs, ws_dirs),
    ])
    if since_signature and since_signature == sig:
        return {"changed": False, "unchanged": True, "signature": sig}

    warnings: List[str] = []
    all_records = _load_history_ro(history_path, warnings)
    active_captures, ignored_map = load_active_captures(m_home), load_ignored_sessions(m_home)
    audits_bundle = list_audits_bundle(
        records=all_records, audits_dir=audits_dirs,
        workspace_dirs=ws_dirs if audits_dirs is None else None, include_cwd=False,
    )
    proj_cf, q_cf = project_clean.casefold(), q_clean.casefold()
    filtered = [
        r for r in all_records
        if (not proj_cf or str(r.get("project") or "").strip().casefold() == proj_cf) and _matches_query(r, q_cf)
    ]
    pending_sessions, ignored_sessions = _derive_pending_states_ro(
        records=all_records, active_captures=active_captures, ignored_map=ignored_map,
        session_meta=session_meta, brain_dir=eff_brain, m_home=m_home, history_path=history_path,
    )
    payload: Dict[str, Any] = {
        "available": True, "changed": True, "unchanged": False, "signature": sig,
        "metrics_home": str(m_home), "metrics_md_uri": _file_uri_if_exists(md_path),
        "filters": {"project": project_clean, "limit": eff_limit, "q": q_clean},
        "total_records": len(all_records), "total_matching": len(filtered), "truncated": False,
        "kpis": _compute_kpis(filtered),
        "projects": sorted(_canonical_projects_map(all_records).values(), key=lambda s: (s.casefold(), s)),
        "by_project": _aggregate_by_project(filtered),
        "records": list(reversed(filtered))[:eff_limit],
        "active_captures": active_captures, "pending_sessions": pending_sessions, "ignored_sessions": ignored_sessions,
        "audits": audits_bundle["audits"], "index_audit": audits_bundle["index_audit"],
        "index_audits": audits_bundle["index_audits"], "warnings": warnings,
    }
    return _enforce_payload_budget(payload, ("records", "audits", "pending_sessions", "ignored_sessions"))


def _resolve_target_record(records: Sequence[Dict[str, Any]], target: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    clean = target.strip()
    if not clean:
        return None, "Target is required."
    for rec in records:
        if str(rec.get("detail_slug") or "") == clean or str(rec.get("label") or "").strip() == clean:
            return rec, None
    cf = clean.casefold()
    if hits := [r for r in records if str(r.get("label") or "").strip().casefold() == cf]:
        return hits[-1], None
    low = clean.lower()
    for rec in records:
        if str(rec.get("conversation_id") or "").strip().lower() == low:
            return rec, None
    if len(clean) >= 8 and _HEX_PREFIX_RE.match(clean):
        matches = {str(r.get("conversation_id") or "").strip(): r for r in records if str(r.get("conversation_id") or "").strip().lower().startswith(low)}
        if len(matches) == 1:
            return next(iter(matches.values())), None
        if len(matches) > 1:
            return None, f"Ambiguous conversation_id prefix {target!r}."
    return None, f"Session not found: {target!r}."


def _strip_raw_trajectories(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    cleaned: Dict[str, Any] = {}
    for k, v in data.items():
        if k in _TRAJECTORY_KEYS:
            continue
        cleaned[k] = [
            {ak: av for ak, av in ag.items() if ak not in _TRAJECTORY_KEYS} if isinstance(ag, dict) else ag
            for ag in v[:MAX_AGENTS_IN_DETAIL]
        ] if k == "agents" and isinstance(v, list) else v
    return cleaned


def get_session_detail_view(target: str, *, m_home: Path) -> Dict[str, Any]:
    clean_target = str(target or "").strip()
    if not clean_target:
        return {"found": False, "error": "Target is required.", "warnings": []}
    warnings: List[str] = []
    rec, err = _resolve_target_record(_load_history_ro(m_home / "data" / "history.jsonl", warnings), clean_target)
    if rec is None:
        return {"found": False, "target": clean_target, "error": err or "Session not found.", "warnings": warnings}
    cid = str(rec.get("conversation_id") or "")
    label = str(rec.get("label") or cid[:8])
    slug = str(rec.get("detail_slug") or slugify_label(label, f"session-{cid[:8]}")).strip()
    reports_dir, detail_data, source, report_md_uri = (m_home / "reports").resolve(), dict(rec), "history_fallback", ""

    if not _SAFE_SLUG_RE.match(slug):
        warnings.append(f"Unsafe detail_slug {slug!r} ignored; using history fallback.")
    elif reports_dir.is_dir() and not _path_safety.is_symlink_or_junction(reports_dir):
        json_cand = reports_dir / f"{slug}.json"
        if _path_safety.is_symlink_or_junction(json_cand) or not _path_safety.is_within_root(reports_dir, json_cand):
            warnings.append(f"Confined report path check failed for {slug!r}; using history fallback.")
        elif json_cand.is_file():
            loaded, warn = _read_json_ro(json_cand)
            if warn:
                warnings.append(warn)
            if isinstance(loaded, dict):
                detail_data, source = _strip_raw_trajectories(loaded), "report_json"
            elif loaded is not None:
                warnings.append(f"Invalid structure in {json_cand.name}; using history fallback.")
        if _path_safety.is_within_root(reports_dir, md_cand := reports_dir / f"{slug}.md"):
            report_md_uri = _file_uri_if_exists(md_cand)

    result = {
        "found": True, "source": source, "target": clean_target, "slug": slug,
        "conversation_id": cid, "label": label, "report_md_uri": report_md_uri,
        "detail": detail_data, "record": rec, "warnings": warnings,
    }
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) >= MAX_PAYLOAD_BYTES and isinstance(detail_data.get("agents"), list):
        detail_data["agents"] = detail_data["agents"][:20]
    return result


def get_audit_detail_view(rel_path: str, *, m_home: Path, audits_dirs: Optional[List[Path]]) -> Dict[str, Any]:
    clean = str(rel_path or "").strip()
    try:
        validated = _path_safety.validate_rel_member_path(clean, label="audit target")
    except ValueError as exc:
        return {"found": False, "path": clean, "error": str(exc)}
    low = validated.lower()
    if low == "code" or low.startswith("code/") or "/code/" in low or low.startswith("audits/code/"):
        return {"found": False, "path": validated, "error": "Access to audits/code/ is forbidden."}
    warnings: List[str] = []
    records = _load_history_ro(m_home / "data" / "history.jsonl", warnings)
    ws_dirs = _workspace_dirs_from_meta(load_session_meta(m_home))
    try:
        audit = read_audit(
            validated, records=records, audits_dir=audits_dirs,
            workspace_dirs=ws_dirs if audits_dirs is None else None, include_cwd=False,
        )
        return {"found": True, "path": validated, **audit, "warnings": warnings}
    except (ValueError, FileNotFoundError, OSError) as exc:
        return {"found": False, "path": validated, "error": str(exc), "warnings": warnings}


def _resolve_cid_for_lifecycle(target: str, brain_dir: Path, m_home: Path) -> str:
    raw = str(target or "").strip()
    if not raw:
        raise ValueError("Conversation target is required.")
    if brain_dir.is_dir():
        try:
            return resolve_conversation_id_in_brain(raw, brain_dir)
        except FileNotFoundError:
            pass
    known = sorted(set(load_session_meta(m_home)) | set(load_active_captures(m_home)) | set(load_ignored_sessions(m_home)))
    if raw in known:
        return raw
    pref = [k for k in known if k.lower().startswith(raw.lower())]
    if len(pref) == 1:
        return pref[0]
    if len(pref) > 1:
        raise ValueError(f"Ambiguous conversation prefix: {raw!r}")
    return raw


def run_action(action: str, params: Dict[str, Any], *, m_home: Path, brain_dir: Path) -> Dict[str, Any]:
    clean_act = action.strip().lower()
    store_dir, history_path = resolve_store_paths(store_dir=m_home)
    target = str(params.get("target") or params.get("conversation_id") or params.get("slug") or "").strip()
    try:
        if clean_act in ("record", "archive", "update"):
            cid_target = str(params.get("conversation_id") or target).strip()
            if not cid_target:
                raise ValueError("Target or conversation_id is required.")
            ws_raw = str(params.get("workspace") or "").strip()
            proj = str(params.get("project") or "").strip() or (Path(ws_raw).expanduser().name if ws_raw else "")
            full = bool(params.get("full", False))
            saved_rec, _, _ = archive_conversation(
                cid_target, store_dir=store_dir, history_path=history_path, brain_dir=brain_dir,
                catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
                label=str(params.get("label") or "").strip(), note=str(params.get("note") or "").strip(),
                gravity_window=not full, full_conversation=full, project=proj,
                force=bool(params.get("force", False)), update_sealed=(clean_act == "update" or bool(params.get("update", False))),
            )
            already = bool(saved_rec.get("already_archived"))
            msg = f"Session '{saved_rec.get('label')}' déjà enregistrée." if already else f"Session '{saved_rec.get('label')}' enregistrée."
            return {"ok": True, "action": clean_act, "conversation_id": saved_rec.get("conversation_id"), "already_archived": already, "record": saved_rec, "message": msg}
        if clean_act == "update-meta":
            updated_rec, _ = update_session_metadata(
                target, store_dir=store_dir, history_path=history_path,
                label=str(params["label"]) if params.get("label") is not None else None,
                project=str(params["project"]) if params.get("project") is not None else None,
                note=str(params["note"]) if params.get("note") is not None else None,
            )
            return {"ok": True, "action": clean_act, "record": updated_rec, "message": f"Métadonnées de '{updated_rec.get('label')}' mises à jour."}
        if clean_act == "ignore":
            cid = _resolve_cid_for_lifecycle(str(params.get("conversation_id") or target), brain_dir, m_home)
            return {"ok": True, "action": clean_act, "conversation_id": cid, "entry": ignore_session(cid, reason=str(params.get("reason") or ""), store_dir=store_dir), "message": f"Session '{cid[:8]}' masquée."}
        if clean_act == "unignore":
            cid = _resolve_cid_for_lifecycle(str(params.get("conversation_id") or target), brain_dir, m_home)
            return {"ok": True, "action": clean_act, "conversation_id": cid, "removed": unignore_session(cid, store_dir=store_dir), "message": f"Session '{cid[:8]}' restaurée."}
        if clean_act == "remove":
            cid_param = str(params.get("conversation_id") or "").strip()
            removed_rec, remaining = apply_remove(
                target,
                store_dir=store_dir,
                history_path=history_path,
                conversation_id=cid_param,
                purge_reports=bool(params.get("purge_reports", True)),
                purge_state=True,
                if_exists=True,
            )
            already_absent = bool(removed_rec.get("already_absent", False))
            cleaned_sources = dict(removed_rec.get("cleaned_sources") or {})
            lbl = removed_rec.get("label") or target
            msg = f"Session '{lbl}' déjà absente." if already_absent else f"Session '{lbl}' supprimée."
            return {
                "ok": True,
                "action": clean_act,
                "removed": removed_rec,
                "remaining": remaining,
                "already_absent": already_absent,
                "cleaned_sources": cleaned_sources,
                "message": msg,
            }
        raise ValueError(f"Unsupported action: {action!r}")
    except (FileNotFoundError, KeyError, ValueError, RuntimeError, TimeoutError) as exc:
        return {"ok": False, "action": clean_act, "error": str(exc), "message": str(exc)}


def _read_request_payload(payload_arg: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    raw_text = payload_arg.strip() if payload_arg.strip() else _hook_rt.read_stdin_text(timeout_s=0.5)
    try:
        parsed = json.loads(raw_text) if raw_text else {}
    except ValueError:
        return {}, {}
    if not isinstance(parsed, dict):
        return {}, {}
    if "params" in parsed or "context" in parsed:
        p = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        c = parsed.get("context") if isinstance(parsed.get("context"), dict) else {}
        return dict(p), dict(c)
    return dict(parsed), {}


def _validate_jsonl_ro(path: Path) -> Tuple[int, Optional[str]]:
    if _path_safety.is_symlink_or_junction(path):
        return 0, f"{path} est un symlink (refusé en lecture seule)."
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, f"{path} illisible ({exc})."
    count = 0
    for idx, raw_ln in enumerate(text.splitlines(), start=1):
        line = raw_ln.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            return count, f"{path} ligne {idx} corrompue ({exc})."
        if not isinstance(obj, dict):
            return count, f"{path} ligne {idx} n'est pas un objet JSON."
        count += 1
    return count, None


def get_health_view(*, m_home: Path, raw_m_home: Optional[Path] = None) -> Dict[str, Any]:
    """Inspecte la santé du stockage metrics en lecture seule stricte (zéro symlink, zéro écriture)."""
    history_path, metrics_md = m_home / "data" / "history.jsonl", m_home / "METRICS.md"
    candidates = [raw_m_home] if raw_m_home is not None else []
    candidates.extend((m_home, history_path.parent, history_path, metrics_md))
    sym_hit = next((p for p in candidates if _path_safety.is_symlink_or_junction(p)), None)
    if sym_hit is not None:
        return {
            "ok": True,
            "status": "WARN",
            "summary": "symlink refusé",
            "warnings": [f"Module 'metrics' : {sym_hit} est un symlink ou une jonction (refusé en lecture seule)."],
        }
    try:
        files_ok = history_path.is_file() and metrics_md.is_file()
    except OSError:
        files_ok = False
    if not files_ok:
        return {
            "ok": True,
            "status": "WARN",
            "summary": "history.jsonl/METRICS.md absent(s)",
            "warnings": [
                f"Module 'metrics' actif mais {history_path} ou {metrics_md} absent : "
                "exécutez /gravity-metrics pour initialiser la télémétrie."
            ],
        }
    sessions, jerr = _validate_jsonl_ro(history_path)
    if jerr:
        return {
            "ok": True,
            "status": "WARN",
            "summary": "history.jsonl invalide",
            "warnings": [f"Module 'metrics' : {jerr}"],
        }
    return {"ok": True, "status": "OK", "summary": f"{sessions} session(s)", "warnings": []}


def get_live_meta_view(
    *, m_home: Path, since_signature: str = "", raw_m_home: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retourne `active_captures` et `session_meta` (worktrees filtrés) en RO strict avec signature compatible."""
    cap_path, meta_path = m_home / "active_captures.json", m_home / "session_meta.json"
    sig = _short_digest([str(m_home), _stat_token(cap_path), _stat_token(meta_path)])
    if since_signature and since_signature == sig:
        return {"ok": True, "changed": False, "unchanged": True, "signature": sig}

    warnings: List[str] = []
    sym_home = next(
        (p for p in (raw_m_home, m_home) if p is not None and _path_safety.is_symlink_or_junction(p)),
        None,
    )
    if sym_home is not None:
        warnings.append(f"Refused symlink or junction: {sym_home.name}.")
        return {
            "ok": True,
            "changed": True,
            "unchanged": False,
            "active_captures": {},
            "session_meta": {},
            "signature": sig,
            "warnings": warnings,
        }

    def _load_map_ro(path: Path, key: str) -> Dict[str, Dict[str, Any]]:
        if _path_safety.is_symlink_or_junction(path):
            warnings.append(f"Refused symlink or junction: {path.name}.")
            return {}
        if not path.is_file():
            return {}
        raw, warn = _read_json_ro(path)
        if warn:
            warnings.append(warn)
        bucket = raw.get(key) if isinstance(raw, dict) and isinstance(raw.get(key), dict) else {}
        return {
            str(k): dict(v)
            for k, v in bucket.items()
            if isinstance(k, str) and k.strip() and isinstance(v, dict)
        }

    active_captures = _load_map_ro(cap_path, "captures")
    raw_sessions = _load_map_ro(meta_path, "sessions")
    session_meta = {
        cid: meta
        for cid, meta in raw_sessions.items()
        if not _tr_reader.is_worktree_path(str(meta.get("cwd") or ""))
    }
    return {
        "ok": True,
        "changed": True,
        "unchanged": False,
        "active_captures": active_captures,
        "session_meta": session_meta,
        "signature": sig,
        "warnings": warnings,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    _hook_rt.configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Gravity Metrics SPI-1 dashboard provider")
    for name, kw in (
        ("--view", {"default": ""}), ("--since-signature", {"default": ""}),
        ("--project", {"default": None}), ("--limit", {"type": int, "default": None}),
        ("--q", {"default": None}), ("--target", {"default": None}), ("--path", {"default": None}),
        ("--action", {"default": ""}), ("--payload", {"default": ""}),
        ("--metrics-home", {"default": None}), ("--gravity-home", {"default": None}),
        ("--brain-dir", {"default": None}), ("--json", {"action": "store_true"}),
    ):
        parser.add_argument(name, **kw)
    args = parser.parse_args(argv)
    params, ctx = _read_request_payload(args.payload)
    raw_m_home = _raw_metrics_home(
        metrics_home=args.metrics_home or (str(ctx["metrics_home"]) if ctx.get("metrics_home") else None),
        gravity_home=args.gravity_home,
    )
    m_home = raw_m_home.resolve()
    brain_dir = (
        Path(args.brain_dir).expanduser().resolve() if args.brain_dir
        else (Path(str(ctx["brain_dir"])).expanduser().resolve() if ctx.get("brain_dir") else default_brain_dir())
    )
    audits_dirs = _coerce_audits_dirs(ctx.get("audits_dirs"))

    if args.action:
        if args.target is not None:
            params["target"] = args.target
        out = run_action(args.action, params, m_home=m_home, brain_dir=brain_dir)
    elif args.view == "health":
        out = get_health_view(m_home=m_home, raw_m_home=raw_m_home)
    elif args.view == "live-meta":
        out = get_live_meta_view(
            m_home=m_home,
            since_signature=args.since_signature or str(params.get("since_signature") or ""),
            raw_m_home=raw_m_home,
        )
    elif args.view == "session-detail":
        out = get_session_detail_view(args.target if args.target is not None else str(params.get("target") or ""), m_home=m_home)
    elif args.view == "audit-detail":
        out = get_audit_detail_view(args.path or args.target or str(params.get("path") or params.get("audit_id") or ""), m_home=m_home, audits_dirs=audits_dirs)
    elif args.view in ("metrics", ""):
        out = get_metrics_view(
            project=args.project if args.project is not None else str(params.get("project") or ""),
            limit=args.limit if args.limit is not None else params.get("limit", DEFAULT_LIMIT),
            q=args.q if args.q is not None else str(params.get("q") or ""),
            since_signature=args.since_signature, m_home=m_home, audits_dirs=audits_dirs, brain_dir=brain_dir,
        )
    else:
        out = {"available": False, "error": f"Unknown view: {args.view!r}"}

    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
