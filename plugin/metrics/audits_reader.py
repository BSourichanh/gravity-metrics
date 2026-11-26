#!/usr/bin/env python3
"""Découverte et lecture sécurisée en lecture seule des dossiers audits/agents/ (100 % stdlib)."""

from datetime import datetime, timezone
import importlib
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_path_safety = importlib.import_module("_shared.path_safety")

MAX_AUDIT_BYTES = 262_144

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_PREFIX_RE = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){0,3}\b")
_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
_VERDICT_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*]\s+)?(?:\*\*|__)?(?:Verdict(?:\s+global)?|Statut(?:\s+global)?|Status|Conclusion|Résultat|Result)(?:\*\*|__)?\s*[:：]\s*(?:\*\*|__)?\s*(.+)",
    re.IGNORECASE,
)
_VERDICT_HEADING_RE = re.compile(
    r"(?:^|\n)\s*#{1,4}\s+(?:Verdict(?:\s+global)?|Conclusion|Statut(?:\s+global)?)\s*\n+([^\n#]+)",
    re.IGNORECASE,
)
_CANONICAL_VERDICT_RE = re.compile(
    r"\b(NON[\s-]+BLOQUANT|BLOQUANT|PASS|PARTIAL|PARTIEL|FAIL|UNTESTED|WARN(?:ING)?|OK|KO)\b",
    re.IGNORECASE,
)
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _norm_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _slugify_root_id(raw: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (raw or "").strip().lower()).strip("-")
    return slug or "audits"


def _split_env_dirs(raw: str) -> List[Path]:
    parts = [p.strip() for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p).expanduser() for p in parts]


def _is_audits_code_dir(path: Path, *, is_direct: bool = False) -> bool:
    """Retourne True si le chemin désigne un dossier `audits/code` ou `code` direct."""
    low_name = path.name.lower()
    if is_direct and low_name == "code":
        return True
    if low_name == "code" and path.parent.name.lower() == "audits":
        return True
    return any(
        path.parts[idx].lower() == "audits" and path.parts[idx + 1].lower() == "code"
        for idx in range(len(path.parts) - 1)
    )


def _is_code_audit_path(
    root_resolved: Path,
    file_path: Path,
    resolved_file: Optional[Path] = None,
) -> bool:
    """Exclut tout fichier situé sous un répertoire `code/` ou `audits/code/`."""
    resolved = resolved_file or file_path.resolve()
    for candidate in (file_path, resolved):
        try:
            rel_parts = candidate.relative_to(root_resolved).parts
        except ValueError:
            return True
        if any(part.lower() == "code" for part in rel_parts[:-1]):
            return True
    dir_parts = [part.lower() for part in resolved.parts[:-1]]
    return any(
        dir_parts[idx] == "audits" and dir_parts[idx + 1] == "code"
        for idx in range(len(dir_parts) - 1)
    )


def _workspace_label(ws_path: Path, explicit_name: str = "") -> str:
    if explicit_name:
        return explicit_name
    if ws_path.name:
        return ws_path.name
    try:
        return ws_path.resolve().name or "workspace"
    except OSError:
        return "workspace"


def discover_audit_roots(
    *,
    audits_dir: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
    workspace_dirs: Optional[Sequence[Union[Path, str]]] = None,
    bridge_db: Optional[Union[Path, str]] = None,
    include_cwd: bool = True,
) -> List[Dict[str, Any]]:
    """Découvre les dossiers `audits/agents/` en lecture seule selon la priorité Gravity."""
    _ = bridge_db
    candidates: List[Tuple[str, Path, str, bool]] = []

    if audits_dir is not None:
        if isinstance(audits_dir, (str, Path)):
            raw_list = _split_env_dirs(str(audits_dir)) if isinstance(audits_dir, str) else [audits_dir]
        else:
            raw_list = [Path(p).expanduser() for p in audits_dir]
        for p in raw_list:
            candidates.append(("", p, "explicit", True))
    else:
        env_audits = os.environ.get("GRAVITY_AUDITS_DIR", "").strip()
        if env_audits:
            for p in _split_env_dirs(env_audits):
                candidates.append(("", p, "env", True))
        else:
            if workspace_dirs:
                for ws in workspace_dirs:
                    ws_path = Path(ws).expanduser()
                    candidates.append(
                        (_workspace_label(ws_path), ws_path / "audits" / "agents", "workspace", False)
                    )
            if include_cwd:
                cwd = Path.cwd()
                candidates.append(
                    (_workspace_label(cwd), cwd / "audits" / "agents", "workspace", False)
                )

    roots: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    used_ids: Set[str] = set()

    for ws_label_hint, root_path, source, is_direct in candidates:
        try:
            abs_path = Path(os.path.abspath(str(root_path)))
            if _is_audits_code_dir(abs_path, is_direct=is_direct):
                continue
            if _path_safety.is_symlink_or_junction(root_path) or not root_path.is_dir():
                continue
            resolved = root_path.resolve()
            if _is_audits_code_dir(resolved, is_direct=is_direct):
                continue
            if is_direct:
                agents_sub = resolved / "agents"
                if agents_sub.is_dir() and not _path_safety.is_symlink_or_junction(agents_sub):
                    agents_resolved = agents_sub.resolve()
                    if _is_audits_code_dir(agents_sub) or _is_audits_code_dir(agents_resolved):
                        continue
                    resolved = agents_resolved
                if resolved.name.lower() == "agents" and resolved.parent.name.lower() == "audits":
                    ws_label = resolved.parent.parent.name or resolved.parent.name or resolved.name or "audits"
                else:
                    ws_label = resolved.parent.name or resolved.name or "audits"
            else:
                ws_label = ws_label_hint or "workspace"
            key = _norm_key(resolved)
        except OSError:
            continue
        if key in seen_paths:
            continue
        seen_paths.add(key)

        base_id = _slugify_root_id(ws_label)
        root_id = base_id
        suffix = 2
        while root_id in used_ids:
            root_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(root_id)

        roots.append(
            {
                "root_id": root_id,
                "workspace": ws_label,
                "path": resolved,
                "source": source,
            }
        )
    return roots


def _classify_verdict_tone(verdict_text: str) -> str:
    up = verdict_text.upper()
    if re.search(r"\bUNTESTED\b", up):
        return "neutral"
    if re.search(r"(?<!NON[\s-])\bBLOQUANT\b", up):
        return "danger"
    if any(tok in up for tok in ("FAIL", "REJECT", "REFUS", "CRITIQUE", "ERROR")):
        return "danger"
    if re.search(r"\bKO\b", up):
        return "danger"
    if any(tok in up for tok in ("PARTIAL", "PARTIEL", "WARN", "ATTENTION", "CORRIGER", "CONDITIONAL", "MITIG")):
        return "warn"
    if "NON BLOQUANT" in up or "NON-BLOQUANT" in up:
        return "ok"
    if any(tok in up for tok in ("PASS", "VALID", "APPROUV", "APPROVED", "CLEAN", "SUCC")):
        return "ok"
    if re.search(r"\bOK\b", up):
        return "ok"
    return "neutral"


def _clean_md_inline(text: str) -> str:
    cleaned = re.sub(r"[*_`]+", "", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _extract_verdict(text: str) -> Tuple[str, str]:
    head_chunk = text[:16_384]
    match = _VERDICT_LINE_RE.search(head_chunk)
    raw_verdict = ""
    if match:
        raw_verdict = match.group(1)
    else:
        h_match = _VERDICT_HEADING_RE.search(head_chunk)
        if h_match:
            raw_verdict = h_match.group(1)

    if not raw_verdict:
        return "", "neutral"
    cleaned_raw = _clean_md_inline(raw_verdict)
    prefix = re.split(r"\s*(?:\||—|–|\s-\s)\s*", cleaned_raw, maxsplit=1)[0].strip()
    if not prefix:
        prefix = cleaned_raw
    canon_m = _CANONICAL_VERDICT_RE.search(prefix)
    if canon_m:
        token = re.sub(r"\s+", " ", canon_m.group(1).upper())
        if token == "WARNING":
            token = "WARN"
        val = token
    else:
        val = prefix
        if len(val) > 40:
            val = val[:37].rstrip() + "..."
    return val, _classify_verdict_tone(val) if val else "neutral"


def _extract_title(text: str, fallback: str) -> str:
    match = _TITLE_RE.search(text[:8_192])
    if match:
        title = _clean_md_inline(match.group(1))
        if title:
            return title
    return fallback


def _extract_excerpt(text: str) -> str:
    for line in text[:4_096].splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---") or s.startswith("|") or s.startswith("<!--"):
            continue
        cleaned = _clean_md_inline(s)
        if len(cleaned) >= 12:
            return cleaned if len(cleaned) <= 160 else cleaned[:157].rstrip() + "..."
    return ""


def _extract_linked_sessions(text: str, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not records or not text:
        return []
    by_cid: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        cid = str(rec.get("conversation_id") or "").strip().lower()
        if cid:
            by_cid.setdefault(cid, []).append(rec)
    if not by_cid:
        return []

    all_cids = list(by_cid.keys())
    matched: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for full_uuid in _UUID_RE.findall(text):
        key = full_uuid.lower()
        for rec in by_cid.get(key, []):
            cid = str(rec.get("conversation_id") or "")
            lbl = str(rec.get("label") or cid[:8])
            matched.setdefault(
                (cid, lbl),
                {
                    "conversation_id": cid,
                    "label": lbl,
                    "project": str(rec.get("project") or ""),
                    "cost_usd": float(rec.get("cost_usd") or 0.0),
                    "cost_2027_usd": float(rec.get("cost_2027_usd") or 0.0),
                    "cache_hit_rate_pct": float(rec.get("cache_hit_rate_pct") or 0.0),
                    "started_at": rec.get("started_at") or rec.get("added_at") or "",
                    "matched_by": "uuid",
                },
            )

    text_no_full = _UUID_RE.sub(" ", text)
    for tok in _HEX_PREFIX_RE.findall(text_no_full):
        low = tok.lower()
        if len(low) < 8:
            continue
        cid_hits = [c for c in all_cids if c.startswith(low)]
        if len(cid_hits) != 1:
            continue
        for rec in by_cid[cid_hits[0]]:
            cid = str(rec.get("conversation_id") or "")
            lbl = str(rec.get("label") or cid[:8])
            matched.setdefault(
                (cid, lbl),
                {
                    "conversation_id": cid,
                    "label": lbl,
                    "project": str(rec.get("project") or ""),
                    "cost_usd": float(rec.get("cost_usd") or 0.0),
                    "cost_2027_usd": float(rec.get("cost_2027_usd") or 0.0),
                    "cache_hit_rate_pct": float(rec.get("cache_hit_rate_pct") or 0.0),
                    "started_at": rec.get("started_at") or rec.get("added_at") or "",
                    "matched_by": "prefix",
                },
            )

    return sorted(
        matched.values(),
        key=lambda item: (str(item.get("started_at") or ""), str(item.get("label") or "")),
        reverse=True,
    )


def _read_bounded_markdown(path: Path) -> Tuple[str, int, bool]:
    stat = path.stat()
    size_bytes = int(stat.st_size)
    with path.open("rb") as fh:
        raw = fh.read(MAX_AUDIT_BYTES + 1)
    truncated = len(raw) > MAX_AUDIT_BYTES
    if truncated:
        raw = raw[:MAX_AUDIT_BYTES]
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    return text, size_bytes, truncated


def _build_audit_record(
    root: Dict[str, Any],
    file_path: Path,
    *,
    records: Sequence[Dict[str, Any]] = (),
    include_content: bool = False,
) -> Dict[str, Any]:
    root_resolved = root["path"].resolve()
    file_resolved = file_path.resolve()
    rel_posix = file_resolved.relative_to(root_resolved).as_posix()
    audit_id = f"{root['root_id']}:{rel_posix}"

    text, size_bytes, truncated = _read_bounded_markdown(file_resolved)
    stat = file_resolved.stat()
    updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_m = _DATE_PREFIX_RE.match(file_resolved.name)
    date_str = date_m.group(1) if date_m else updated_at[:10]

    is_index = rel_posix.lower() == "readme.md"
    title = _extract_title(text, file_resolved.stem)
    verdict, verdict_tone = _extract_verdict(text)
    linked_sessions = _extract_linked_sessions(text, records)

    entry: Dict[str, Any] = {
        "audit_id": audit_id,
        "root_id": root["root_id"],
        "workspace": root["workspace"],
        "filename": file_resolved.name,
        "rel_path": rel_posix,
        "is_index": is_index,
        "title": title,
        "verdict": verdict,
        "verdict_tone": verdict_tone,
        "date": date_str,
        "updated_at": updated_at,
        "size_bytes": size_bytes,
        "truncated": truncated,
        "excerpt": _extract_excerpt(text),
        "linked_sessions": linked_sessions,
        "uri": file_resolved.as_uri(),
    }
    if include_content:
        entry["content"] = text
    return entry


def _is_safe_rel_posix(rel_part: str) -> bool:
    if not rel_part or "\x00" in rel_part or "\\" in rel_part:
        return False
    if rel_part.startswith("/") or _WIN_ABS_RE.match(rel_part):
        return False
    posix = PurePosixPath(rel_part)
    if posix.is_absolute():
        return False
    return ".." not in posix.parts


def _resolve_audit_target(
    audit_id: str,
    roots: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Path]:
    raw = str(audit_id or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("Identifiant d'audit vide ou invalide.")
    if raw.startswith(("/", "\\")) or _WIN_ABS_RE.match(raw):
        raise ValueError("Chemin absolu interdit pour un audit.")

    if ":" in raw:
        root_key, rel_part = raw.split(":", 1)
        if not root_key or not _is_safe_rel_posix(rel_part):
            raise ValueError(f"Chemin d'audit invalide ou traversal détecté : {audit_id!r}")
        matched_roots = [r for r in roots if r["root_id"] == root_key]
        if not matched_roots:
            raise FileNotFoundError(f"Racine d'audits introuvable pour {audit_id!r}")
    else:
        if not _is_safe_rel_posix(raw):
            raise ValueError(f"Chemin d'audit invalide ou traversal détecté : {audit_id!r}")
        rel_part = raw
        matched_roots = roots

    for root in matched_roots:
        root_resolved = root["path"].resolve()
        if _path_safety.is_symlink_or_junction(root["path"]) or _path_safety.is_symlink_or_junction(root_resolved):
            raise ValueError(f"Lien symbolique ou jonction interdit : {audit_id!r}")
        unresolved_candidate = root_resolved / rel_part
        try:
            _path_safety.ensure_no_symlink_or_junction_under(root_resolved, unresolved_candidate)
        except ValueError as exc:
            raise ValueError(f"Chemin d'audit invalide ou lien symbolique interdit ({audit_id!r}): {exc}") from exc
        candidate = unresolved_candidate.resolve()
        if not _path_safety.is_within_root(root_resolved, candidate):
            raise ValueError(f"Path traversal détecté : {audit_id!r}")
        if _is_code_audit_path(root_resolved, unresolved_candidate, candidate):
            raise ValueError(f"Accès aux audits de code interdit : {audit_id!r}")
        if candidate.suffix.lower() != ".md":
            raise ValueError("Seuls les fichiers Markdown (.md) peuvent être lus.")
        if candidate.name.startswith("_"):
            raise ValueError(f"Fichier modèle/interne exclu des audits : {audit_id!r}")
        if candidate.is_file():
            return root, candidate

    # Dernière vérification traversal si un suffixe non-.md a été passé sans ":"
    if not rel_part.lower().endswith(".md"):
        raise ValueError("Seuls les fichiers Markdown (.md) peuvent être lus.")
    raise FileNotFoundError(f"Audit introuvable : {audit_id!r}")


def list_audits_bundle(
    *,
    records: Sequence[Dict[str, Any]] = (),
    audits_dir: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
    workspace_dirs: Optional[Sequence[Union[Path, str]]] = None,
    bridge_db: Optional[Union[Path, str]] = None,
    include_cwd: bool = True,
) -> Dict[str, Any]:
    """Découvre les audits `.md` (`audits/agents/`) en séparant `README.md` (`index_audit`) et excluant `_*`."""
    roots = discover_audit_roots(
        audits_dir=audits_dir,
        workspace_dirs=workspace_dirs,
        bridge_db=bridge_db,
        include_cwd=include_cwd,
    )
    regular_raw: List[Dict[str, Any]] = []
    index_candidates: List[Dict[str, Any]] = []
    for root in roots:
        root_resolved = root["path"].resolve()
        try:
            candidates = sorted(root_resolved.rglob("*"))
        except OSError:
            continue
        for fpath in candidates:
            try:
                if _path_safety.is_symlink_or_junction(fpath) or not fpath.is_file() or fpath.suffix.lower() != ".md":
                    continue
                if fpath.name.startswith("_"):
                    continue
                _path_safety.ensure_no_symlink_or_junction_under(root_resolved, fpath)
                resolved_file = fpath.resolve()
                if resolved_file.name.startswith("_"):
                    continue
                if not _path_safety.is_within_root(root_resolved, resolved_file):
                    continue
                if _is_code_audit_path(root_resolved, fpath, resolved_file):
                    continue
                rec = _build_audit_record(root, resolved_file, records=records, include_content=False)
                if rec.get("is_index"):
                    index_candidates.append(rec)
                else:
                    regular_raw.append(rec)
            except (OSError, ValueError):
                continue

    index_candidates.sort(
        key=lambda a: (
            str(a.get("workspace") or "").casefold(),
            str(a.get("rel_path") or "").casefold(),
        ),
    )
    regular = sorted(
        regular_raw,
        key=lambda a: (
            str(a.get("date") or ""),
            str(a.get("updated_at") or ""),
            str(a.get("rel_path") or ""),
        ),
        reverse=True,
    )
    roots_meta = [
        {
            "root_id": r["root_id"],
            "workspace": r["workspace"],
            "source": r["source"],
        }
        for r in roots
    ]
    return {
        "audits": regular,
        "roots": roots_meta,
        "index_audit": index_candidates[0] if index_candidates else None,
        "index_audits": index_candidates,
    }


def list_audits(
    *,
    records: Sequence[Dict[str, Any]] = (),
    audits_dir: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
    workspace_dirs: Optional[Sequence[Union[Path, str]]] = None,
    bridge_db: Optional[Union[Path, str]] = None,
    include_cwd: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Liste les fichiers `.md` découverts dans les dossiers `audits/agents/` (hors `audits/code/`, `README.md` et `_*`)."""
    bundle = list_audits_bundle(
        records=records,
        audits_dir=audits_dir,
        workspace_dirs=workspace_dirs,
        bridge_db=bridge_db,
        include_cwd=include_cwd,
    )
    return bundle["audits"], bundle["roots"]


def read_audit(
    audit_id: str,
    *,
    records: Sequence[Dict[str, Any]] = (),
    audits_dir: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
    workspace_dirs: Optional[Sequence[Union[Path, str]]] = None,
    bridge_db: Optional[Union[Path, str]] = None,
    include_cwd: bool = True,
) -> Dict[str, Any]:
    """Lit en toute sécurité un audit Markdown (anti-traversal, .md only, plafond 256 Ko)."""
    roots = discover_audit_roots(
        audits_dir=audits_dir,
        workspace_dirs=workspace_dirs,
        bridge_db=bridge_db,
        include_cwd=include_cwd,
    )
    if not roots:
        raise FileNotFoundError("Aucun dossier audits/agents/ découvert.")
    root, target = _resolve_audit_target(audit_id, roots)
    return _build_audit_record(root, target, records=records, include_content=True)


def build_audit_links_by_session(audits: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Indexe les audits associés par `conversation_id` pour liens croisés bidirectionnels (hors index)."""
    links: Dict[str, List[Dict[str, Any]]] = {}
    for audit in audits:
        if audit.get("is_index"):
            continue
        compact = {
            "audit_id": audit.get("audit_id") or "",
            "title": audit.get("title") or audit.get("filename") or "",
            "verdict": audit.get("verdict") or "",
            "verdict_tone": audit.get("verdict_tone") or "neutral",
            "workspace": audit.get("workspace") or "",
            "is_index": False,
            "date": audit.get("date") or "",
        }
        for sess in audit.get("linked_sessions") or []:
            cid = str(sess.get("conversation_id") or "").strip()
            if not cid:
                continue
            bucket = links.setdefault(cid, [])
            if not any(x.get("audit_id") == compact["audit_id"] for x in bucket):
                bucket.append(compact)
    return links
