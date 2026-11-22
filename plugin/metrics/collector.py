#!/usr/bin/env python3
"""Collecte et agrégation des métriques depuis les transcripts Antigravity (100 % stdlib)."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from .bridge_links import (
    discover_bridge_links,
    load_bridge_target_conversation_ids,
)
from .pricing import (
    CATEGORY_ORDER,
    SCHEMA_VERSION,
    _add_cost,
    _empty_cost_acc,
    _round_cost,
    classify_agent,
    compute_call_cost,
    load_pricing_catalog,
    resolve_model_pricing,
)

METRICS_MARKER = "<!-- gravity-metrics:recorded -->"

_GRAVITY_METRICS_CMD_RE = re.compile(
    r"^/gravity-metrics(?::gravity-metrics)?(?:\s|$)", re.IGNORECASE
)
_GRAVITY_METRICS_ARGS_RE = re.compile(
    r"^/gravity-metrics(?::gravity-metrics)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL
)
_NON_CAPTURE_METRICS_SUBCOMMANDS: Tuple[str, ...] = ("archive", "pending")
_GRAVITY_CMD_RE = re.compile(
    r"^/(?:gravity-workflow(?::gravity-workflow)?|gravity-architect(?::gravity-architect)?|gravity-bridge(?::gravity-bridge)?|gravity(?::gravity)?)(?:\s|$)",
    re.IGNORECASE,
)
_GRAVITY_LEADING_AXES_RE = re.compile(
    r"^/(?:gravity-workflow(?::gravity-workflow)?|gravity-architect(?::gravity-architect)?|gravity-bridge(?::gravity-bridge)?|gravity(?::gravity)?)"
    r"(?:\s+(?:low|medium|high|ultra|evo|evolutive|slow|fast|safe|unsafe|auto|init|status|evals|modules|doctor|sync|spec|handoff|archive|workspaces|link|send|read|take|reply|done|prune))*\s*",
    re.IGNORECASE,
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "dans",
    "de",
    "des",
    "du",
    "elle",
    "en",
    "et",
    "for",
    "il",
    "in",
    "je",
    "la",
    "le",
    "les",
    "me",
    "ne",
    "no",
    "non",
    "of",
    "ok",
    "on",
    "or",
    "ou",
    "oui",
    "par",
    "pas",
    "peux",
    "pour",
    "sur",
    "te",
    "the",
    "to",
    "tu",
    "un",
    "une",
    "vas",
    "vazi",
    "veux",
    "yes",
}


def _parse_iso_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_user_request_text(raw: str) -> str:
    """Extrait le texte utilisateur nettoyé d'un payload USER_INPUT Antigravity."""
    txt = str(raw or "")
    txt = re.sub(
        r"<ADDITIONAL_METADATA>.*?(?:</ADDITIONAL_METADATA>|$)",
        "",
        txt,
        flags=re.DOTALL,
    )
    txt = re.sub(
        r"<USER_SETTINGS_CHANGE>.*?(?:</USER_SETTINGS_CHANGE>|$)",
        "",
        txt,
        flags=re.DOTALL,
    )
    txt = re.sub(r"</?USER_REQUEST>", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _clean_user_prompt(raw: str, max_len: int = 90) -> str:
    txt = extract_user_request_text(raw)
    if len(txt) > max_len:
        return txt[: max_len - 3] + "..."
    return txt or "(sans titre)"


def is_gravity_metrics_prompt(raw: str) -> bool:
    """Retourne True uniquement si le prompt invoque `/gravity-metrics`."""
    txt = extract_user_request_text(raw)
    return bool(_GRAVITY_METRICS_CMD_RE.match(txt))


def _split_metrics_tokens(raw_args: str) -> List[str]:
    text = (raw_args or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _parse_metrics_flags(tokens: List[str]) -> Tuple[Dict[str, Any], List[str], bool]:
    """Parse les options communes de `/gravity-metrics` sans lever d'exception sur texte long."""
    label = ""
    note = ""
    profile = "2026_nov_standard"
    full = False
    force = False
    has_explicit_label = False
    positionals: List[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-l", "--label"):
            if i + 1 >= len(tokens):
                raise ValueError(f"Option {tok} sans valeur.")
            label = tokens[i + 1]
            has_explicit_label = True
            i += 2
        elif tok in ("-n", "--note"):
            if i + 1 >= len(tokens):
                raise ValueError(f"Option {tok} sans valeur.")
            note = tokens[i + 1]
            i += 2
        elif tok == "--profile":
            if i + 1 >= len(tokens):
                raise ValueError("Option --profile sans valeur.")
            profile = tokens[i + 1]
            i += 2
        elif tok == "--full":
            full = True
            i += 1
        elif tok == "--force":
            force = True
            i += 1
        elif tok.startswith("-"):
            raise ValueError(f"Option inconnue : `{tok}`.")
        else:
            positionals.append(tok)
            i += 1

    return (
        {
            "label": label.strip(),
            "note": note.strip(),
            "profile": profile.strip(),
            "full": full,
            "force": force,
        },
        positionals,
        has_explicit_label,
    )


def classify_metrics_prompt(raw: str) -> Dict[str, Any]:
    """Analyse un prompt `/gravity-metrics` et décide : passthrough, capture, archive, pending ou error."""
    txt = extract_user_request_text(raw)
    if not _GRAVITY_METRICS_CMD_RE.match(txt):
        return {"action": "passthrough"}
    match = _GRAVITY_METRICS_ARGS_RE.match(txt)
    if not match:
        return {"action": "passthrough"}
    rest = (match.group(1) or "").strip()
    if not rest:
        return {
            "action": "capture",
            "label": "",
            "note": "",
            "profile": "2026_nov_standard",
            "full": False,
            "force": False,
            "has_explicit_label": False,
        }
    if rest.startswith("/"):
        return {"action": "passthrough"}

    tokens = _split_metrics_tokens(rest)
    if not tokens:
        return {
            "action": "capture",
            "label": "",
            "note": "",
            "profile": "2026_nov_standard",
            "full": False,
            "force": False,
            "has_explicit_label": False,
        }

    head = tokens[0].lower()
    if head == "pending":
        for tok in tokens[1:]:
            if tok.startswith("-"):
                return {"action": "error", "error": f"Option inconnue pour `pending` : `{tok}`."}
        return {"action": "pending"}

    if head == "archive":
        try:
            opts, positionals, has_explicit_label = _parse_metrics_flags(tokens[1:])
        except ValueError as exc:
            return {"action": "error", "error": str(exc)}
        if not positionals:
            return {
                "action": "error",
                "error": 'Syntaxe : `/gravity-metrics archive <uuid_ou_prefixe> [label] [-n "note"] [--full] [--force]`',
            }
        target = positionals[0]
        label = opts["label"] or " ".join(positionals[1:]).strip()
        return {
            "action": "archive",
            "target": target,
            "label": label,
            "note": opts["note"],
            "profile": opts["profile"],
            "full": opts["full"],
            "force": opts["force"],
            "has_explicit_label": has_explicit_label,
        }

    try:
        opts, positionals, has_explicit_label = _parse_metrics_flags(tokens)
    except ValueError as exc:
        if len(rest) > 60 or len(tokens) > 6:
            return {"action": "passthrough"}
        return {"action": "error", "error": str(exc)}

    positional_text = " ".join(positionals).strip()
    if not has_explicit_label and (len(positional_text) > 60 or len(positional_text.split()) > 6):
        return {"action": "passthrough"}

    resolved_label = opts["label"] or positional_text
    return {
        "action": "capture",
        "label": resolved_label.strip(),
        "note": opts["note"],
        "profile": opts["profile"],
        "full": opts["full"],
        "force": opts["force"],
        "has_explicit_label": has_explicit_label,
    }


def is_gravity_metrics_capture_prompt(raw: str) -> bool:
    """Retourne True uniquement pour une commande valide de capture `/gravity-metrics` (hors passthrough/pending/archive)."""
    return classify_metrics_prompt(raw).get("action") == "capture"


def is_gravity_session_prompt(raw: str) -> bool:
    """Retourne True si le prompt lance `/gravity`, `/gravity-workflow`, `/gravity-architect` ou `/gravity-bridge`."""
    txt = extract_user_request_text(raw)
    return bool(_GRAVITY_CMD_RE.match(txt)) and not is_gravity_metrics_prompt(txt)


def read_gravity_profile_summary(
    workspace_root: Optional[Union[Path, str]],
) -> str:
    """Lit en lecture seule `.agents/gravity/profile.json` s'il existe dans le projet cible."""
    if not workspace_root:
        return ""
    profile_path = Path(workspace_root) / ".agents" / "gravity" / "profile.json"
    if not profile_path.is_file():
        return ""
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    mode = str(data.get("mode") or "").strip().lower()
    level = str(data.get("current_level") or "").strip().lower()
    pace = str(data.get("pace") or "").strip().lower()
    verif = str(data.get("verification") or "").strip().lower()
    if not mode and not level:
        return ""

    head = f"evo→{level or 'medium'}" if mode == "evo" else (level or mode)
    parts = [head]
    if pace:
        parts.append(pace)
    if verif:
        parts.append(verif)
    return " · ".join(parts)


def slugify_label(label: str, fallback: str = "session", max_len: int = 64) -> str:
    """Normalise un label en nom de fichier sûr ([a-z0-9._-]) borné à `max_len` caractères."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", (label or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip(".-_")
    if not slug:
        slug = fallback
    if max_len > 0 and len(slug) > max_len:
        digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8]
        if max_len <= len(digest) + 2:
            return slug[:max_len]
        keep = max_len - 1 - len(digest)
        prefix = slug[:keep].rstrip(".-_") or slug[:keep]
        return f"{prefix}-{digest}"
    return slug


def _extract_label_keywords(raw_prompt: str, project_slug: str = "") -> List[str]:
    """Extrait les mots-clés significatifs d'un prompt (ignore commandes `/gravity` et stopwords)."""
    cleaned = extract_user_request_text(raw_prompt)
    cleaned = _GRAVITY_LEADING_AXES_RE.sub("", cleaned).strip()
    words: List[str] = []
    for tok in re.findall(r"[a-z0-9]+", slugify_label(cleaned, fallback="", max_len=0).replace(".", "-")):
        if len(tok) < 2 or tok in _STOPWORDS or (project_slug and tok == project_slug):
            continue
        words.append(tok)
    return words


def suggest_auto_label(
    project: str = "",
    first_prompt: str = "",
    started_at: Optional[str] = None,
    existing_labels: Iterable[str] = (),
) -> str:
    """Génère un label court, lisible et unique à partir du projet, prompt et horodatage."""
    proj_slug = slugify_label(project, fallback="")[:18].strip("-")
    words = _extract_label_keywords(first_prompt, proj_slug)[:3]

    dt = _parse_iso_ts(started_at)
    ts_tag = dt.astimezone(timezone.utc).strftime("%Y%m%d-%H%M") if dt else "session"

    if proj_slug and words:
        base = f"{proj_slug}-{'-'.join(words)}"
    elif proj_slug:
        base = f"{proj_slug}-{ts_tag}"
    elif words:
        base = "-".join(words)
    else:
        base = f"gravity-{ts_tag}"

    base = base[:42].strip("-") or "session"
    existing_set = {str(lbl).strip().lower() for lbl in existing_labels if lbl}
    if base.lower() not in existing_set:
        return base

    idx = 2
    while True:
        candidate = f"{base[:36].strip('-')}-{idx}"
        if candidate.lower() not in existing_set:
            return candidate
        idx += 1


def _collect_known_gravity_session_ids(runs_jsonl_paths: Optional[List[Path]]) -> Set[str]:
    ids: Set[str] = set()
    if not runs_jsonl_paths:
        return ids
    for rpath in runs_jsonl_paths:
        if not rpath.is_file():
            continue
        for raw in rpath.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            sid = obj.get("conversation_id") or obj.get("session_id")
            if isinstance(sid, str) and sid.strip():
                ids.add(sid.strip())
    return ids


def _has_gravity_workspace_uri(conv_dir: Path) -> bool:
    sub_dir = conv_dir / ".system_generated" / "subagents"
    if not sub_dir.is_dir():
        return False
    for fn in sub_dir.iterdir():
        if not fn.is_file() or fn.suffix.lower() != ".json":
            continue
        try:
            sd = json.loads(fn.read_text(encoding="utf-8"))
        except Exception:
            continue
        for uri in sd.get("workspaceUris") or []:
            if isinstance(uri, str) and "gravity" in uri.lower():
                return True
    return False


def is_bridge_child_conversation(
    conversation_id: str,
    *,
    first_prompt_raw: Optional[str] = None,
    bridge_target_ids: Optional[Set[str]] = None,
    brain_dir: Optional[Union[Path, str]] = None,
    bridge_db_path: Optional[Union[Path, str]] = None,
) -> bool:
    """Retourne True si la conversation est un enfant délégué via Gravity Bridge."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return False
    if bridge_target_ids is None:
        bridge_target_ids = load_bridge_target_conversation_ids(bridge_db_path)
    if cid in bridge_target_ids:
        return True

    prompt_txt = first_prompt_raw
    if prompt_txt is None and brain_dir is not None:
        tpath = Path(brain_dir) / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if tpath.is_file():
            with tpath.open("r", encoding="utf-8", errors="replace") as fh:
                for idx, raw_line in enumerate(fh):
                    if idx >= 20:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "USER_INPUT":
                        prompt_txt = str(obj.get("content") or "")
                        break
    if prompt_txt:
        cleaned = extract_user_request_text(prompt_txt)
        if cleaned.startswith("[GRAVITY-BRIDGE |"):
            return True
    return False


def resolve_conversation_id_in_brain(target: str, brain_dir: Union[Path, str]) -> str:
    """Résout un UUID complet ou un préfixe unique d'UUID dans `brain_dir`."""
    clean = str(target or "").strip()
    if not clean:
        raise ValueError("Identifiant de conversation vide.")
    base = Path(brain_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"Répertoire des conversations introuvable : {base}")
    exact = base / clean
    if exact.is_dir():
        return clean
    lower_target = clean.lower()
    matches = sorted(
        d.name
        for d in base.iterdir()
        if d.is_dir() and d.name.lower().startswith(lower_target)
    )
    if not matches:
        raise FileNotFoundError(f"Conversation introuvable : {clean!r} dans {base}")
    if len(matches) > 1:
        raise ValueError(
            f"Plusieurs conversations correspondent au préfixe {clean!r} ; précisez l'UUID complet."
        )
    return matches[0]


def list_gravity_conversations(
    brain_dir: Union[Path, str],
    include_all: bool = False,
    runs_jsonl_paths: Optional[List[Path]] = None,
    bridge_db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Liste les conversations racines récentes, en excluant les enfants Bridge par défaut."""
    base = Path(brain_dir)
    if not base.is_dir():
        return []

    known_session_ids = _collect_known_gravity_session_ids(runs_jsonl_paths)
    bridge_target_ids = load_bridge_target_conversation_ids(bridge_db_path)
    results: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        if not tpath.is_file():
            continue

        first_prompt_raw: Optional[str] = None
        has_gravity_cmd = False
        last_ts: Optional[str] = None
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("created_at"):
                    last_ts = str(obj["created_at"])
                if obj.get("type") == "USER_INPUT":
                    content = str(obj.get("content") or "")
                    if first_prompt_raw is None:
                        first_prompt_raw = content
                    if is_gravity_session_prompt(content):
                        has_gravity_cmd = True

        if first_prompt_raw is None:
            continue

        is_bridge_child = is_bridge_child_conversation(
            cdir.name,
            first_prompt_raw=first_prompt_raw,
            bridge_target_ids=bridge_target_ids,
        )
        if not include_all and is_bridge_child:
            continue

        title = _clean_user_prompt(first_prompt_raw)
        is_gravity = (not is_bridge_child) and (
            has_gravity_cmd
            or "/gravity" in first_prompt_raw.lower()
            or "gravity" in title.lower()
            or "gravity" in first_prompt_raw.lower()
            or cdir.name in known_session_ids
            or _has_gravity_workspace_uri(cdir)
        )
        if not include_all and not is_gravity:
            continue

        mtime = tpath.stat().st_mtime
        updated_at = last_ts or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        results.append(
            {
                "conversation_id": cdir.name,
                "title": title,
                "updated_at": updated_at,
                "is_gravity": is_gravity,
                "is_bridge_child": is_bridge_child,
                "_mtime": mtime,
            }
        )

    results.sort(key=lambda item: (-item["_mtime"], item["conversation_id"]))
    for item in results:
        item.pop("_mtime", None)
    return results


def resolve_gravity_window(
    transcript_path: Union[Path, str],
    *,
    current_prompt_is_metrics: bool = False,
    current_prompt: Optional[str] = None,
    full_conversation: bool = False,
    from_step: Optional[int] = None,
    to_step: Optional[int] = None,
) -> Dict[str, Any]:
    """Résout la fenêtre de lignes [début /gravity → avant /gravity-metrics] sur le transcript racine."""
    tpath = Path(transcript_path)
    entries: List[Dict[str, Any]] = []
    if tpath.is_file():
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for line_idx, raw_line in enumerate(fh):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                step_type = str(obj.get("type") or "")
                raw_step = obj.get("step_index")
                step_idx = raw_step if isinstance(raw_step, int) else None
                content = str(obj.get("content") or "")
                is_user = step_type == "USER_INPUT"
                meta_obj = obj.get("metadata")
                has_marker = step_type == "SYSTEM_MESSAGE" and (
                    METRICS_MARKER in content
                    or (
                        isinstance(meta_obj, dict)
                        and METRICS_MARKER in json.dumps(meta_obj, ensure_ascii=False)
                    )
                )
                entries.append(
                    {
                        "line_idx": line_idx,
                        "step_index": step_idx,
                        "type": step_type,
                        "created_at": obj.get("created_at"),
                        "is_user": is_user,
                        "is_metrics": is_user and is_gravity_metrics_prompt(content),
                        "is_capture": is_user and is_gravity_metrics_capture_prompt(content),
                        "is_gravity": is_user and is_gravity_session_prompt(content),
                        "is_planner": step_type == "PLANNER_RESPONSE",
                        "has_marker": has_marker,
                        "clean_prompt": extract_user_request_text(content)[:160] if is_user else "",
                    }
                )

    if not entries:
        return {
            "mode": "full_conversation" if full_conversation else "empty",
            "start_line_idx": 0,
            "end_line_idx": None,
            "start_step_index": None,
            "end_step_index": None,
            "start_ts": None,
            "end_ts": None,
            "first_prompt": "",
            "excluded_before_steps": 0,
            "excluded_after_steps": 0,
            "window_empty": True,
            "has_gravity_anchor": False,
        }

    capture_positions = [i for i, e in enumerate(entries) if e["is_capture"]]
    has_gravity_anchor = False

    def _is_metrics_entry_pending(idx_in_entries: int) -> bool:
        tail = entries[idx_in_entries + 1 :]
        if any(
            e["type"] in ("PLANNER_RESPONSE", "SYSTEM_MESSAGE", "ERROR_MESSAGE", "USER_INPUT")
            or e["has_marker"]
            for e in tail
        ):
            return False
        if current_prompt is not None:
            cur_clean = extract_user_request_text(current_prompt)[:160]
            if cur_clean != entries[idx_in_entries]["clean_prompt"]:
                return False
        return True

    def _is_completed_capture_boundary(idx_in_entries: int) -> bool:
        turn_tail: List[Dict[str, Any]] = []
        for e in entries[idx_in_entries + 1 :]:
            if e["is_user"]:
                break
            turn_tail.append(e)
        return any(e["has_marker"] or e["is_planner"] for e in turn_tail)

    if from_step is not None or to_step is not None:
        if from_step is not None:
            start_pos = next(
                (i for i, e in enumerate(entries) if e["step_index"] is not None and e["step_index"] >= from_step),
                len(entries),
            )
        else:
            start_pos = 0
        if to_step is not None:
            end_pos = next(
                (
                    i
                    for i in range(start_pos, len(entries))
                    if entries[i]["step_index"] is not None and entries[i]["step_index"] >= to_step
                ),
                len(entries),
            )
        else:
            end_pos = len(entries)
        mode = "custom"
    elif full_conversation:
        start_pos = 0
        end_pos = len(entries)
        if current_prompt_is_metrics and capture_positions:
            last_m = capture_positions[-1]
            if _is_metrics_entry_pending(last_m):
                end_pos = last_m
        mode = "full_conversation"
    else:
        current_m_pos: Optional[int] = None
        prev_m_pos: Optional[int] = None
        if capture_positions:
            if current_prompt_is_metrics:
                last_m = capture_positions[-1]
                if _is_metrics_entry_pending(last_m):
                    current_m_pos = last_m
                    prev_candidates = [j for j in capture_positions[:-1] if _is_completed_capture_boundary(j)]
                    prev_m_pos = prev_candidates[-1] if prev_candidates else None
                else:
                    current_m_pos = None
                    prev_candidates = [j for j in capture_positions if _is_completed_capture_boundary(j)]
                    prev_m_pos = prev_candidates[-1] if prev_candidates else None
            else:
                completed = [j for j in capture_positions if _is_completed_capture_boundary(j)]
                if completed:
                    current_m_pos = completed[-1]
                    prev_m_pos = completed[-2] if len(completed) >= 2 else None
                else:
                    current_m_pos = capture_positions[-1]
                    prev_m_pos = None

        end_pos = current_m_pos if current_m_pos is not None else len(entries)
        if prev_m_pos is not None:
            pos = prev_m_pos + 1
            while pos < end_pos and not entries[pos]["is_user"]:
                pos += 1
            fallback_start_pos = pos
        else:
            fallback_start_pos = 0

        g_pos = next(
            (i for i in range(fallback_start_pos, end_pos) if entries[i]["is_gravity"]),
            None,
        )
        if g_pos is not None:
            start_pos = g_pos
            mode = "gravity_session"
            has_gravity_anchor = True
        else:
            start_pos = fallback_start_pos
            mode = "since_last_metrics" if prev_m_pos is not None else "full_conversation"

    window_empty = start_pos >= end_pos
    start_line_idx = (
        entries[start_pos]["line_idx"]
        if start_pos < len(entries)
        else entries[-1]["line_idx"] + 1
    )
    end_line_idx = entries[end_pos]["line_idx"] if end_pos < len(entries) else None

    slice_entries = entries[start_pos:end_pos]
    start_step_index = next(
        (e["step_index"] for e in slice_entries if e["step_index"] is not None), None
    )
    if start_step_index is None:
        prev_steps = [e["step_index"] for e in entries[:start_pos] if e["step_index"] is not None]
        if prev_steps:
            start_step_index = prev_steps[-1] + 1

    end_step_index = entries[end_pos]["step_index"] if end_pos < len(entries) else None
    start_ts = next((e["created_at"] for e in slice_entries if e.get("created_at")), None)
    end_ts = entries[end_pos].get("created_at") if end_pos < len(entries) else None
    first_prompt = next(
        (
            e["clean_prompt"]
            for e in slice_entries
            if e["is_user"] and e["clean_prompt"] and _extract_label_keywords(e["clean_prompt"])
        ),
        "",
    ) or next(
        (e["clean_prompt"] for e in slice_entries if e["is_user"] and e["clean_prompt"]),
        "",
    )

    return {
        "mode": mode,
        "start_line_idx": start_line_idx,
        "end_line_idx": end_line_idx,
        "start_step_index": start_step_index,
        "end_step_index": end_step_index,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "first_prompt": first_prompt,
        "excluded_before_steps": start_pos,
        "excluded_after_steps": len(entries) - end_pos,
        "window_empty": window_empty,
        "has_gravity_anchor": has_gravity_anchor,
    }


def _read_first_timestamp(transcript_path: Union[Path, str]) -> Optional[datetime]:
    tpath = Path(transcript_path)
    if not tpath.is_file():
        return None
    with tpath.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("created_at"):
                dt = _parse_iso_ts(str(obj["created_at"]))
                if dt is not None:
                    return dt
    return None


def _is_spawn_in_window(
    spawn_step: Optional[int],
    created_at_ts: Optional[str],
    transcript_path: Optional[str],
    window_info: Dict[str, Any],
) -> bool:
    if window_info.get("window_empty"):
        return False

    start_step = window_info.get("start_step_index")
    end_step = window_info.get("end_step_index")

    use_step_bounds = (
        isinstance(start_step, int)
        and (end_step is None or (isinstance(end_step, int) and end_step >= start_step))
    )
    if isinstance(spawn_step, int) and use_step_bounds:
        if spawn_step < start_step:
            return False
        if isinstance(end_step, int) and spawn_step >= end_step:
            return False
        return True

    start_dt = _parse_iso_ts(window_info.get("start_ts"))
    end_dt = _parse_iso_ts(window_info.get("end_ts"))
    child_dt = _read_first_timestamp(transcript_path) if transcript_path else None
    if child_dt is None and created_at_ts:
        child_dt = _parse_iso_ts(created_at_ts)
    if child_dt is not None and (start_dt is not None or end_dt is not None):
        if start_dt is not None and child_dt < start_dt:
            return False
        if end_dt is not None and child_dt >= end_dt:
            return False
        return True

    return (
        window_info.get("mode") == "full_conversation"
        and window_info.get("start_line_idx", 0) == 0
        and window_info.get("end_line_idx") is None
    )


def _is_direct_child_in_window(
    child_node: Dict[str, Any],
    window_info: Dict[str, Any],
) -> bool:
    return _is_spawn_in_window(
        child_node.get("spawn_step_index"),
        child_node.get("bridge_created_at"),
        child_node.get("transcript_path"),
        window_info,
    )


def discover_agent_tree(
    root_id: str,
    brain_dir: Union[Path, str],
    bridge_db_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Découvre l'arbre complet (sous-agents natifs + enfants Gravity Bridge) par BFS anti-cycles."""
    base = Path(brain_dir)
    visited: Set[str] = set()
    enqueued: Set[str] = {root_id}
    queue: List[Dict[str, Any]] = [
        {
            "cid": root_id,
            "parent_id": None,
            "depth": 0,
            "type_name": "root",
            "role": "root",
            "state": None,
            "spawn_step": None,
            "source": "root",
            "bridge_thread_id": None,
            "bridge_workspace": None,
            "bridge_job_status": None,
            "bridge_created_at": None,
        }
    ]
    nodes: List[Dict[str, Any]] = []

    while queue:
        item = queue.pop(0)
        cid = item["cid"]
        if cid in visited:
            continue
        visited.add(cid)

        cdir = base / cid
        sub_dir = cdir / ".system_generated" / "subagents"
        transcript_path = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        children_ids: List[str] = []
        depth = item["depth"]
        cur_bridge_ws = item["bridge_workspace"]

        if sub_dir.is_dir():
            for fn in sorted(sub_dir.iterdir()):
                if not fn.is_file() or fn.suffix.lower() != ".json":
                    continue
                try:
                    sd = json.loads(fn.read_text(encoding="utf-8"))
                except Exception:
                    continue
                scid = sd.get("conversationId")
                if not scid or scid in visited:
                    continue
                if scid not in children_ids:
                    children_ids.append(scid)
                if scid not in enqueued:
                    enqueued.add(scid)
                    desc = sd.get("subagentDescriptor") or {}
                    queue.append(
                        {
                            "cid": scid,
                            "parent_id": cid,
                            "depth": depth + 1,
                            "type_name": str(desc.get("typeName") or "unknown"),
                            "role": str(desc.get("role") or scid[:8]),
                            "state": sd.get("state"),
                            "spawn_step": sd.get("spawnStepIndex"),
                            "source": "subagent",
                            "bridge_thread_id": None,
                            "bridge_workspace": cur_bridge_ws,
                            "bridge_job_status": None,
                            "bridge_created_at": None,
                        }
                    )

        bridge_links = discover_bridge_links(cid, transcript_path, bridge_db=bridge_db_path)
        for link in bridge_links:
            bcid = str(link.get("target_conversation_id") or "").strip()
            if not bcid or bcid in visited:
                continue
            if bcid not in children_ids:
                children_ids.append(bcid)
            if bcid not in enqueued:
                enqueued.add(bcid)
                msg_type = str(link.get("msg_type") or "handoff").strip().lower()
                to_ws = str(link.get("to_workspace") or "unknown").strip()
                queue.append(
                    {
                        "cid": bcid,
                        "parent_id": cid,
                        "depth": depth + 1,
                        "type_name": f"bridge_{msg_type}" if msg_type else "bridge",
                        "role": f"bridge:{to_ws}",
                        "state": link.get("job_status") or link.get("thread_status"),
                        "spawn_step": link.get("spawn_step_index"),
                        "source": "bridge",
                        "bridge_thread_id": link.get("thread_id"),
                        "bridge_workspace": to_ws,
                        "bridge_job_status": link.get("job_status") or "",
                        "bridge_created_at": link.get("created_at"),
                    }
                )

        type_name = item["type_name"]
        role = item["role"]
        nodes.append(
            {
                "conversation_id": cid,
                "parent_id": item["parent_id"],
                "depth": depth,
                "type_name": type_name,
                "role": role,
                "category": classify_agent(type_name, role),
                "subagent_state": item["state"],
                "spawn_step_index": item["spawn_step"],
                "source": item["source"],
                "bridge_thread_id": item["bridge_thread_id"],
                "bridge_workspace": cur_bridge_ws,
                "bridge_job_status": item["bridge_job_status"],
                "bridge_created_at": item["bridge_created_at"],
                "children_ids": children_ids,
                "transcript_path": str(transcript_path),
                "transcript_exists": transcript_path.is_file(),
                "_bridge_links": bridge_links,
            }
        )

    return nodes


def _percentile_sorted(sorted_vals: List[int], pct: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(pct * len(sorted_vals)) - 1))
    return int(sorted_vals[idx])


def parse_agent_transcript(
    transcript_path: Union[Path, str],
    catalog: Dict[str, Any],
    profile: str,
    secondary_profile: Optional[str] = "2027_standard",
    active_gap_cap_s: float = 120.0,
    start_line_idx: int = 0,
    end_line_idx: Optional[int] = None,
) -> Dict[str, Any]:
    """Extrait les métriques d'exécution, tokens, coûts et qualité d'un transcript (optionnellement fenêtré)."""
    tpath = Path(transcript_path)
    has_secondary = bool(secondary_profile and secondary_profile in catalog.get("profiles", {}))

    total_steps = 0
    malformed_lines = 0
    llm_calls = 0
    user_inputs = 0
    system_messages = 0
    error_steps = 0
    running_status_steps = 0
    tool_calls_total = 0
    tool_calls_by_name: Counter = Counter()
    error_reasons: Counter = Counter()

    input_tokens = 0
    cache_read_tokens = 0
    output_tokens = 0
    ctx_list: List[int] = []
    calls_over_100k = 0
    calls_over_128k = 0
    calls_over_200k = 0

    cost_acc = _empty_cost_acc()
    proj_acc = _empty_cost_acc()
    model_stats: Dict[str, Dict[str, Any]] = {}
    unmatched_models: Set[str] = set()

    seen_planner_steps: Set[int] = set()
    rewind_calls = 0
    rewind_in = 0
    rewind_cache = 0
    rewind_out = 0
    rewind_cost_raw = 0.0

    timestamps: List[datetime] = []
    last_non_sys: Optional[Dict[str, Any]] = None
    user_prompt_texts: List[str] = []

    if tpath.is_file():
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for line_idx, raw_line in enumerate(fh):
                if line_idx < start_line_idx:
                    continue
                if end_line_idx is not None and line_idx >= end_line_idx:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    malformed_lines += 1
                    continue
                if not isinstance(obj, dict):
                    malformed_lines += 1
                    continue

                total_steps += 1
                step_type = obj.get("type")
                status = obj.get("status")
                dt = _parse_iso_ts(obj.get("created_at"))
                if dt is not None:
                    timestamps.append(dt)

                if step_type != "SYSTEM_MESSAGE":
                    last_non_sys = obj
                else:
                    system_messages += 1

                if step_type == "USER_INPUT":
                    user_inputs += 1
                    user_prompt_texts.append(str(obj.get("content") or "")[:500])
                if status == "ERROR":
                    error_steps += 1
                    err_txt = str(obj.get("error") or obj.get("content") or "unknown").strip()
                    err_short = err_txt.splitlines()[0][:120] if err_txt else "unknown"
                    error_reasons[err_short] += 1
                elif status == "RUNNING":
                    running_status_steps += 1

                for tc in obj.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        tool_calls_total += 1
                        tool_calls_by_name[str(tc.get("name") or "unknown")] += 1

                if step_type == "PLANNER_RESPONSE":
                    llm_calls += 1
                    in_t = int(obj.get("input_tokens") or 0)
                    cache_t = int(obj.get("cache_read_tokens") or 0)
                    out_t = int(obj.get("output_tokens") or 0)
                    model_name = str(obj.get("model") or "unknown")

                    input_tokens += in_t
                    cache_read_tokens += cache_t
                    output_tokens += out_t

                    ctx = in_t + cache_t
                    ctx_list.append(ctx)
                    if ctx > 100_000:
                        calls_over_100k += 1
                    if ctx > 128_000:
                        calls_over_128k += 1
                    if ctx > 200_000:
                        calls_over_200k += 1

                    pricing, is_fb = resolve_model_pricing(model_name, catalog, profile)
                    if is_fb:
                        unmatched_models.add(model_name)
                    call_cost = compute_call_cost(in_t, cache_t, out_t, pricing)
                    _add_cost(cost_acc, call_cost)

                    if has_secondary and secondary_profile:
                        p2, _ = resolve_model_pricing(model_name, catalog, secondary_profile)
                        c2 = compute_call_cost(in_t, cache_t, out_t, p2)
                        _add_cost(proj_acc, c2)
                    else:
                        c2 = _empty_cost_acc()

                    m_entry = model_stats.setdefault(
                        model_name,
                        {
                            "calls": 0,
                            "input_tokens": 0,
                            "cache_read_tokens": 0,
                            "output_tokens": 0,
                            "_raw_cost": _empty_cost_acc(),
                            "_raw_proj": _empty_cost_acc(),
                            "is_fallback": is_fb,
                        },
                    )
                    m_entry["calls"] += 1
                    m_entry["input_tokens"] += in_t
                    m_entry["cache_read_tokens"] += cache_t
                    m_entry["output_tokens"] += out_t
                    _add_cost(m_entry["_raw_cost"], call_cost)
                    _add_cost(m_entry["_raw_proj"], c2)

                    step_idx = obj.get("step_index")
                    if isinstance(step_idx, int):
                        if step_idx in seen_planner_steps:
                            rewind_calls += 1
                            rewind_in += in_t
                            rewind_cache += cache_t
                            rewind_out += out_t
                            rewind_cost_raw += call_cost["total_usd"]
                        else:
                            seen_planner_steps.add(step_idx)

    total_prompt = input_tokens + cache_read_tokens
    total_tokens = total_prompt + output_tokens
    cache_hit_rate = round((cache_read_tokens / total_prompt) * 100.0, 2) if total_prompt > 0 else 0.0

    sorted_ctx = sorted(ctx_list)
    max_ctx = sorted_ctx[-1] if sorted_ctx else 0
    avg_ctx = round(sum(sorted_ctx) / len(sorted_ctx), 1) if sorted_ctx else 0.0
    p95_ctx = _percentile_sorted(sorted_ctx, 0.95)

    clean_term = False
    last_non_sys_type: Optional[str] = None
    if last_non_sys is not None:
        last_non_sys_type = last_non_sys.get("type")
        clean_term = (last_non_sys_type == "PLANNER_RESPONSE") and (
            not bool(last_non_sys.get("tool_calls"))
        )

    ts_sorted = sorted(timestamps)
    first_dt = ts_sorted[0] if ts_sorted else None
    last_dt = ts_sorted[-1] if ts_sorted else None
    lifespan_s = round((last_dt - first_dt).total_seconds(), 2) if first_dt and last_dt else 0.0
    deltas = [
        max(0.0, (ts_sorted[i] - ts_sorted[i - 1]).total_seconds())
        for i in range(1, len(ts_sorted))
    ]
    active_s = round(sum(min(d, active_gap_cap_s) for d in deltas), 2)
    idle_gap_s = round(max(0.0, lifespan_s - active_s), 2)
    max_gap_s = round(max(deltas), 2) if deltas else 0.0

    models_out: Dict[str, Any] = {}
    for m_name, m_data in model_stats.items():
        models_out[m_name] = {
            "calls": m_data["calls"],
            "input_tokens": m_data["input_tokens"],
            "cache_read_tokens": m_data["cache_read_tokens"],
            "output_tokens": m_data["output_tokens"],
            "cost_usd": _round_cost(m_data["_raw_cost"], 6),
            "cost_usd_projection": _round_cost(m_data["_raw_proj"], 6),
            "is_fallback": m_data["is_fallback"],
            "_raw_cost": m_data["_raw_cost"],
            "_raw_proj": m_data["_raw_proj"],
        }

    return {
        "transcript_missing": not tpath.is_file(),
        "malformed_lines": malformed_lines,
        "total_steps": total_steps,
        "llm_calls": llm_calls,
        "user_inputs": user_inputs,
        "system_messages": system_messages,
        "error_steps": error_steps,
        "running_status_steps": running_status_steps,
        "tool_calls_total": tool_calls_total,
        "tool_calls_by_name": dict(tool_calls_by_name.most_common()),
        "error_reasons": [
            {"reason": r, "count": c} for r, c in error_reasons.most_common(5)
        ],
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
        "total_prompt_tokens": total_prompt,
        "total_tokens": total_tokens,
        "cache_hit_rate_pct": cache_hit_rate,
        "max_context_tokens": max_ctx,
        "avg_context_tokens": avg_ctx,
        "p95_context_tokens": p95_ctx,
        "calls_over_100k_ctx": calls_over_100k,
        "calls_over_128k_ctx": calls_over_128k,
        "calls_over_200k_ctx": calls_over_200k,
        "cost_usd": _round_cost(cost_acc, 6),
        "cost_usd_projection": _round_cost(proj_acc, 6),
        "by_model": models_out,
        "unmatched_models": sorted(unmatched_models),
        "rewind_calls": rewind_calls,
        "rewind_input_tokens": rewind_in,
        "rewind_cache_read_tokens": rewind_cache,
        "rewind_output_tokens": rewind_out,
        "rewind_cost_usd": round(rewind_cost_raw, 6),
        "_raw_rewind_cost_usd": rewind_cost_raw,
        "clean_termination": clean_term,
        "last_non_system_type": last_non_sys_type,
        "first_ts": _fmt_iso_utc(first_dt),
        "last_ts": _fmt_iso_utc(last_dt),
        "lifespan_s": lifespan_s,
        "active_s": active_s,
        "idle_gap_s": idle_gap_s,
        "max_gap_s": max_gap_s,
        "_raw_cost": cost_acc,
        "_raw_proj": proj_acc,
        "_user_prompts": user_prompt_texts,
    }


def _extract_slash_commands(raw_prompts: List[str]) -> Set[str]:
    cmds: Set[str] = set()
    for raw in raw_prompts:
        txt = extract_user_request_text(raw)
        m = re.match(r"^/([a-z0-9_:-]+)(?:\s|$)", txt, re.IGNORECASE)
        if m:
            cmd = m.group(1).lower()
            if ":" in cmd:
                cmd = cmd.split(":", 1)[0]
            cmds.add(cmd)
    return cmds


def _detect_execution_mode(
    enriched_agents: List[Dict[str, Any]],
    tool_counter: Counter,
    root_id: str,
) -> Tuple[str, List[str]]:
    """Détermine le mode d'orchestration principal et les capacités détectées."""
    type_names = {str(a.get("type_name") or "").lower() for a in enriched_agents}
    root_agent = next(
        (a for a in enriched_agents if a["conversation_id"] == root_id),
        enriched_agents[0],
    )
    cmds = _extract_slash_commands(root_agent.get("_user_prompts") or [])

    is_teamwork = any(tn.startswith("teamwork") for tn in type_names) or "teamwork" in cmds
    is_swarm = bool(tool_counter.get("swarm")) or "swarm" in cmds
    is_workflow = bool(tool_counter.get("run_workflow"))
    is_bridge = any(
        a.get("source") == "bridge" or str(a.get("type_name") or "").lower().startswith("bridge")
        for a in enriched_agents
    )
    is_subagents = len(enriched_agents) > 1 or bool(tool_counter.get("invoke_subagent"))
    is_plan = bool(tool_counter.get("exit_plan_mode")) or "plan" in cmds
    is_gravity = bool(cmds & {"gravity", "gravity-workflow", "gravity-architect", "gravity-bridge"})

    features_used: List[str] = []
    for flag, name in (
        (is_teamwork, "teamwork"),
        (is_swarm, "swarm"),
        (is_workflow, "workflow"),
        (is_bridge, "bridge"),
        (is_subagents, "subagents"),
        (is_plan, "plan"),
        (is_gravity, "gravity"),
    ):
        if flag:
            features_used.append(name)
    if not features_used:
        features_used = ["single"]

    if is_teamwork:
        mode = "teamwork"
    elif is_swarm:
        mode = "swarm"
    elif is_workflow:
        mode = "workflow"
    elif is_bridge:
        mode = "bridge"
    elif is_subagents:
        mode = "subagents"
    else:
        mode = "single"
    return mode, features_used


def _collect_subtree_ids(
    start_id: str, children_map: Dict[str, List[str]]
) -> Set[str]:
    result: Set[str] = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        for ch in children_map.get(cur, []):
            stack.append(ch)
    return result


def _aggregate_bucket(agents_subset: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw_cost = _empty_cost_acc()
    raw_proj = _empty_cost_acc()
    in_tok = cache_tok = out_tok = calls = tools = errs = 0
    ts_starts: List[str] = []
    ts_ends: List[str] = []
    for ag in agents_subset:
        _add_cost(raw_cost, ag["_raw_cost"])
        _add_cost(raw_proj, ag["_raw_proj"])
        in_tok += ag["input_tokens"]
        cache_tok += ag["cache_read_tokens"]
        out_tok += ag["output_tokens"]
        calls += ag["llm_calls"]
        tools += ag["tool_calls_total"]
        errs += ag["error_steps"]
        if ag["first_ts"]:
            ts_starts.append(ag["first_ts"])
        if ag["last_ts"]:
            ts_ends.append(ag["last_ts"])
    tot_prompt = in_tok + cache_tok
    cache_rate = round((cache_tok / tot_prompt) * 100.0, 2) if tot_prompt > 0 else 0.0
    t0 = min(ts_starts) if ts_starts else None
    t1 = max(ts_ends) if ts_ends else None
    wall_span_s = 0.0
    if t0 and t1:
        dt0, dt1 = _parse_iso_ts(t0), _parse_iso_ts(t1)
        if dt0 and dt1:
            wall_span_s = round((dt1 - dt0).total_seconds(), 2)
    return {
        "agents": len(agents_subset),
        "llm_calls": calls,
        "tool_calls": tools,
        "error_steps": errs,
        "input_tokens": in_tok,
        "cache_read_tokens": cache_tok,
        "output_tokens": out_tok,
        "total_tokens": tot_prompt + out_tok,
        "cache_hit_rate_pct": cache_rate,
        "cost_usd": _round_cost(raw_cost, 6),
        "cost_usd_projection": _round_cost(raw_proj, 6),
        "first_ts": t0,
        "last_ts": t1,
        "wall_span_s": wall_span_s,
    }


def collect_run_metrics(
    root_id: str,
    brain_dir: Union[Path, str],
    pricing_catalog_path: Union[Path, str],
    pricing_profile: str = "2026_nov_standard",
    secondary_profile: Optional[str] = "2027_standard",
    label: str = "",
    *,
    gravity_window: bool = False,
    current_prompt_is_metrics: bool = False,
    current_prompt: Optional[str] = None,
    full_conversation: bool = False,
    from_step: Optional[int] = None,
    to_step: Optional[int] = None,
    project: str = "",
    gravity_profile: str = "",
    bridge_db_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Collecte et agrège les métriques et coûts complets d'un arbre de conversation."""
    catalog = load_pricing_catalog(pricing_catalog_path)
    nodes = discover_agent_tree(root_id, brain_dir, bridge_db_path=bridge_db_path)
    if not nodes:
        raise ValueError(f"Aucun nœud découvert pour {root_id!r} dans {brain_dir}")

    apply_window = (
        gravity_window
        or current_prompt_is_metrics
        or from_step is not None
        or to_step is not None
    )
    root_node = nodes[0]
    if apply_window:
        window_info = resolve_gravity_window(
            root_node["transcript_path"],
            current_prompt_is_metrics=current_prompt_is_metrics,
            current_prompt=current_prompt,
            full_conversation=full_conversation,
            from_step=from_step,
            to_step=to_step,
        )
        nodes_by_raw = {n["conversation_id"]: n for n in nodes}
        raw_children_map = {n["conversation_id"]: list(n["children_ids"]) for n in nodes}
        accepted_direct: List[str] = []
        kept_ids: Set[str] = {root_id}
        for cid in root_node["children_ids"]:
            cnode = nodes_by_raw.get(cid)
            if cnode is not None and _is_direct_child_in_window(cnode, window_info):
                accepted_direct.append(cid)
                kept_ids.update(_collect_subtree_ids(cid, raw_children_map))
        root_node["children_ids"] = accepted_direct
        nodes = [n for n in nodes if n["conversation_id"] in kept_ids]
    else:
        window_info = {
            "mode": "full_conversation",
            "start_line_idx": 0,
            "end_line_idx": None,
            "start_step_index": None,
            "end_step_index": None,
            "start_ts": None,
            "end_ts": None,
            "first_prompt": "",
            "excluded_before_steps": 0,
            "excluded_after_steps": 0,
            "window_empty": False,
            "has_gravity_anchor": False,
        }

    enriched_agents: List[Dict[str, Any]] = []
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    children_map: Dict[str, List[str]] = defaultdict(list)
    unmatched_models: Set[str] = set()
    missing_transcripts: List[str] = []
    malformed_lines_total = 0
    root_workspace = str(project or "").strip() or "root"

    bridge_pending_jobs: List[Dict[str, Any]] = []
    seen_pending_keys: Set[Tuple[str, str]] = set()

    for node in nodes:
        is_root = node["conversation_id"] == root_id
        for link in node.get("_bridge_links") or []:
            if not link.get("has_active_job"):
                continue
            if is_root and apply_window and not _is_spawn_in_window(
                link.get("spawn_step_index"),
                link.get("created_at"),
                None,
                window_info,
            ):
                continue
            pkey = (str(link.get("thread_id") or ""), str(link.get("job_id") or ""))
            if pkey not in seen_pending_keys:
                seen_pending_keys.add(pkey)
                bridge_pending_jobs.append(
                    {
                        "thread_id": pkey[0],
                        "job_id": pkey[1],
                        "to_workspace": str(link.get("to_workspace") or "unknown"),
                        "status": str(link.get("job_status") or ""),
                        "action": str(link.get("job_action") or ""),
                        "target_conversation_id": str(link.get("target_conversation_id") or ""),
                    }
                )

        parsed = parse_agent_transcript(
            node["transcript_path"],
            catalog=catalog,
            profile=pricing_profile,
            secondary_profile=secondary_profile,
            start_line_idx=window_info["start_line_idx"] if is_root else 0,
            end_line_idx=window_info["end_line_idx"] if is_root else None,
        )
        unmatched_models.update(parsed["unmatched_models"])
        if parsed["transcript_missing"]:
            missing_transcripts.append(node["conversation_id"])
        malformed_lines_total += parsed["malformed_lines"]

        ag_workspace = str(node.get("bridge_workspace") or "").strip() or root_workspace
        merged = {**node, **parsed, "workspace": ag_workspace}
        enriched_agents.append(merged)
        nodes_by_id[node["conversation_id"]] = merged
        if node["parent_id"]:
            children_map[node["parent_id"]].append(node["conversation_id"])

    sum_cost = _empty_cost_acc()
    sum_proj = _empty_cost_acc()
    total_in = total_cache = total_out = 0
    total_llm_calls = total_tool_calls = total_user_inputs = total_error_steps = total_steps = 0
    sum_lifespans = sum_active = 0.0
    max_depth = 0
    all_t0: List[str] = []
    all_t1: List[str] = []
    model_agg: Dict[str, Dict[str, Any]] = {}
    tool_counter: Counter = Counter()
    error_reason_counter: Counter = Counter()

    for ag in enriched_agents:
        _add_cost(sum_cost, ag["_raw_cost"])
        _add_cost(sum_proj, ag["_raw_proj"])
        total_in += ag["input_tokens"]
        total_cache += ag["cache_read_tokens"]
        total_out += ag["output_tokens"]
        total_llm_calls += ag["llm_calls"]
        total_tool_calls += ag["tool_calls_total"]
        total_user_inputs += ag["user_inputs"]
        total_error_steps += ag["error_steps"]
        total_steps += ag["total_steps"]
        sum_lifespans += ag["lifespan_s"]
        sum_active += ag["active_s"]
        if ag["depth"] > max_depth:
            max_depth = ag["depth"]
        if ag["first_ts"]:
            all_t0.append(ag["first_ts"])
        if ag["last_ts"]:
            all_t1.append(ag["last_ts"])
        for tname, cnt in ag["tool_calls_by_name"].items():
            tool_counter[tname] += cnt
        for er in ag["error_reasons"]:
            error_reason_counter[er["reason"]] += er["count"]

        for m_name, m_data in ag["by_model"].items():
            entry = model_agg.setdefault(
                m_name,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "cache_read_tokens": 0,
                    "output_tokens": 0,
                    "_raw_cost": _empty_cost_acc(),
                    "_raw_proj": _empty_cost_acc(),
                    "is_fallback": m_data["is_fallback"],
                    "agents_using": 0,
                },
            )
            entry["calls"] += m_data["calls"]
            entry["input_tokens"] += m_data["input_tokens"]
            entry["cache_read_tokens"] += m_data["cache_read_tokens"]
            entry["output_tokens"] += m_data["output_tokens"]
            _add_cost(entry["_raw_cost"], m_data["_raw_cost"])
            _add_cost(entry["_raw_proj"], m_data["_raw_proj"])
            entry["agents_using"] += 1

    total_prompt = total_in + total_cache
    total_tokens = total_prompt + total_out
    cache_hit_rate = round((total_cache / total_prompt) * 100.0, 2) if total_prompt > 0 else 0.0
    t0_str = min(all_t0) if all_t0 else None
    t1_str = max(all_t1) if all_t1 else None
    wall_clock_s = 0.0
    if t0_str and t1_str:
        dt0, dt1 = _parse_iso_ts(t0_str), _parse_iso_ts(t1_str)
        if dt0 and dt1:
            wall_clock_s = round((dt1 - dt0).total_seconds(), 2)

    error_denom = total_tool_calls if total_tool_calls > 0 else max(total_llm_calls, 1)
    error_rate_pct = round((total_error_steps / error_denom) * 100.0, 2) if total_steps > 0 else 0.0
    total_cost_float = sum_cost["total_usd"]

    by_category: Dict[str, Any] = {}
    cat_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        cat_groups[ag["category"]].append(ag)
    for cat in CATEGORY_ORDER:
        subset = cat_groups.get(cat, [])
        if not subset and cat == "other":
            continue
        agg = _aggregate_bucket(subset)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in subset) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_category[cat] = agg

    by_model: Dict[str, Any] = {}
    for m_name, m_data in sorted(
        model_agg.items(), key=lambda kv: (-kv[1]["calls"], -kv[1]["_raw_cost"]["total_usd"])
    ):
        m_prompt = m_data["input_tokens"] + m_data["cache_read_tokens"]
        by_model[m_name] = {
            "calls": m_data["calls"],
            "agents_using": m_data["agents_using"],
            "input_tokens": m_data["input_tokens"],
            "cache_read_tokens": m_data["cache_read_tokens"],
            "output_tokens": m_data["output_tokens"],
            "total_tokens": m_prompt + m_data["output_tokens"],
            "cost_usd": _round_cost(m_data["_raw_cost"], 6),
            "cost_usd_projection": _round_cost(m_data["_raw_proj"], 6),
            "cost_share_pct": round((m_data["_raw_cost"]["total_usd"] / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0,
            "is_fallback": m_data["is_fallback"],
        }

    pivot_node = next(
        (ag for ag in enriched_agents if ag["type_name"] == "teamwork_orchestrator"),
        nodes_by_id[root_id],
    )
    pivot_id = pivot_node["conversation_id"]
    pivot_children = [nodes_by_id[cid] for cid in children_map.get(pivot_id, []) if cid in nodes_by_id]
    pivot_children.sort(
        key=lambda a: (a["first_ts"] or "9999", a["spawn_step_index"] or 0, a["conversation_id"])
    )
    role_counts = Counter(ch["role"] for ch in pivot_children)

    phase_records: List[Tuple[str, List[Dict[str, Any]]]] = []
    assigned_ids: Set[str] = set()
    for ch in pivot_children:
        subtree_ids = _collect_subtree_ids(ch["conversation_id"], children_map)
        assigned_ids.update(subtree_ids)
        phase_name = (
            f"{ch['role']} ({ch['conversation_id'][:8]})"
            if role_counts[ch["role"]] > 1
            else ch["role"]
        )
        phase_records.append((phase_name, [nodes_by_id[sid] for sid in subtree_ids if sid in nodes_by_id]))

    overhead_agents = [ag for ag in enriched_agents if ag["conversation_id"] not in assigned_ids]
    if overhead_agents:
        phase_records.append(("Orchestration & Cadrage (Overhead)", overhead_agents))

    phase_records.sort(
        key=lambda item: (
            min((a["first_ts"] for a in item[1] if a["first_ts"]), default="9999"),
            item[0],
        )
    )
    by_phase: Dict[str, Any] = {}
    for pname, pagents in phase_records:
        agg = _aggregate_bucket(pagents)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in pagents) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_phase[pname] = agg

    ws_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        ws_groups[ag["workspace"]].append(ag)
    by_workspace: Dict[str, Any] = {}
    for ws_name, subset in sorted(
        ws_groups.items(),
        key=lambda kv: (
            kv[0] != root_workspace,
            -sum(a["_raw_cost"]["total_usd"] for a in kv[1]),
            kv[0],
        ),
    ):
        agg = _aggregate_bucket(subset)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in subset) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_workspace[ws_name] = agg

    dup_groups_map: Dict[Tuple[Optional[str], str, str], List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        if ag["parent_id"] is not None:
            dup_groups_map[(ag["parent_id"], ag["type_name"], ag["role"])].append(ag)

    dup_groups_out: List[Dict[str, Any]] = []
    dup_agent_count = 0
    dup_calls = 0
    dup_cost_raw = 0.0
    for (pid, tname, rl), grp in dup_groups_map.items():
        if len(grp) <= 1:
            continue
        grp_sorted = sorted(
            grp,
            key=lambda a: (a["first_ts"] or "9999", a["spawn_step_index"] or 0, a["conversation_id"]),
        )
        dups = grp_sorted[1:]
        d_calls = sum(d["llm_calls"] for d in dups)
        d_cost = sum(d["_raw_cost"]["total_usd"] for d in dups)
        dup_agent_count += len(dups)
        dup_calls += d_calls
        dup_cost_raw += d_cost
        parent_role = nodes_by_id[pid]["role"] if pid in nodes_by_id else (pid or "")
        dup_groups_out.append(
            {
                "parent_role": parent_role,
                "type_name": tname,
                "role": rl,
                "spawn_count": len(grp_sorted),
                "duplicate_count": len(dups),
                "duplicate_calls": d_calls,
                "duplicate_cost_usd": round(d_cost, 6),
            }
        )

    rewind_calls_total = sum(a["rewind_calls"] for a in enriched_agents)
    rewind_cost_total = sum(a["_raw_rewind_cost_usd"] for a in enriched_agents)
    interrupted = [a for a in enriched_agents if not a["clean_termination"]]
    interrupted_cost = sum(a["_raw_cost"]["total_usd"] for a in interrupted)
    interrupted_calls = sum(a["llm_calls"] for a in interrupted)
    max_ctx_overall = max((a["max_context_tokens"] for a in enriched_agents), default=0)

    verif_cost = sum(a["_raw_cost"]["total_usd"] for a in cat_groups.get("verification", []))
    build_cost = sum(a["_raw_cost"]["total_usd"] for a in cat_groups.get("build", []))
    test_cost = sum(a["_raw_cost"]["total_usd"] for a in cat_groups.get("testing", []))
    build_plus_test = build_cost + test_cost
    verif_to_build_ratio = (
        round(verif_cost / build_plus_test, 2)
        if build_plus_test > 0
        else (None if verif_cost > 0 else 0.0)
    )
    execution_mode, features_used = _detect_execution_mode(enriched_agents, tool_counter, root_id)

    agents_clean: List[Dict[str, Any]] = []
    for ag in sorted(enriched_agents, key=lambda x: -x["_raw_cost"]["total_usd"]):
        clean_models = {
            m_name: {k: v for k, v in m_val.items() if not k.startswith("_")}
            for m_name, m_val in ag["by_model"].items()
        }
        ag_out = {k: v for k, v in ag.items() if not k.startswith("_")}
        ag_out["by_model"] = clean_models
        agents_clean.append(ag_out)

    bridge_agents = [ag for ag in agents_clean if ag.get("source") == "bridge"]
    bridge_workspaces = sorted(
        {
            str(ag.get("bridge_workspace") or ag.get("workspace") or "").strip()
            for ag in bridge_agents
            if str(ag.get("bridge_workspace") or ag.get("workspace") or "").strip()
        }
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "label": label or f"run-{root_id[:8]}",
        "root_conversation_id": root_id,
        "project": str(project or "").strip(),
        "gravity_profile": str(gravity_profile or "").strip(),
        "window": {
            "mode": window_info["mode"],
            "start_step_index": window_info["start_step_index"],
            "end_step_index": window_info["end_step_index"],
            "excluded_before_steps": window_info["excluded_before_steps"],
            "excluded_after_steps": window_info["excluded_after_steps"],
            "has_gravity_anchor": window_info["has_gravity_anchor"],
            "first_prompt": window_info["first_prompt"],
        },
        "pricing_catalog_version": str(catalog.get("catalog_version", "unknown")),
        "pricing_profile": pricing_profile,
        "secondary_pricing_profile": (
            secondary_profile
            if (secondary_profile and secondary_profile in catalog.get("profiles", {}))
            else None
        ),
        "summary": {
            "total_agents": len(enriched_agents),
            "max_tree_depth": max_depth,
            "total_steps": total_steps,
            "total_llm_calls": total_llm_calls,
            "total_tool_calls": total_tool_calls,
            "total_user_inputs": total_user_inputs,
            "total_error_steps": total_error_steps,
            "error_rate_pct": error_rate_pct,
            "input_tokens": total_in,
            "cache_read_tokens": total_cache,
            "output_tokens": total_out,
            "total_prompt_tokens": total_prompt,
            "total_tokens": total_tokens,
            "cache_hit_rate_pct": cache_hit_rate,
            "cost_usd": _round_cost(sum_cost, 6),
            "cost_usd_projection": _round_cost(sum_proj, 6),
            "first_ts": t0_str,
            "last_ts": t1_str,
            "wall_clock_s": wall_clock_s,
            "sum_agent_lifespans_s": round(sum_lifespans, 2),
            "sum_agent_active_s": round(sum_active, 2),
            "parallelism_factor": round(sum_lifespans / wall_clock_s, 2) if wall_clock_s > 0 else 1.0,
            "execution_mode": execution_mode,
            "features_used": features_used,
        },
        "by_category": by_category,
        "by_model": by_model,
        "by_phase": by_phase,
        "by_workspace": by_workspace,
        "bridge_pending_jobs": bridge_pending_jobs,
        "bridge": {
            "children_count": len(bridge_agents),
            "workspaces": bridge_workspaces,
            "pending_jobs": bridge_pending_jobs,
        },
        "tool_usage": {
            "total_calls": total_tool_calls,
            "by_tool": dict(tool_counter.most_common()),
            "top_errors": [
                {"reason": r, "count": c} for r, c in error_reason_counter.most_common(10)
            ],
        },
        "efficiency_and_waste": {
            "verification_to_build_cost_ratio": verif_to_build_ratio,
            "duplicate_role_spawns": {
                "duplicate_agent_count": dup_agent_count,
                "duplicate_calls": dup_calls,
                "duplicate_cost_usd": round(dup_cost_raw, 6),
                "groups": sorted(dup_groups_out, key=lambda g: -g["duplicate_cost_usd"]),
            },
            "rewind_waste": {
                "rewind_calls": rewind_calls_total,
                "rewind_cost_usd": round(rewind_cost_total, 6),
            },
            "interrupted_agents": {
                "count": len(interrupted),
                "total_llm_calls": interrupted_calls,
                "total_cost_usd": round(interrupted_cost, 6),
            },
            "context_bloat": {
                "max_context_tokens_overall": max_ctx_overall,
                "calls_over_100k_ctx": sum(a["calls_over_100k_ctx"] for a in enriched_agents),
                "calls_over_128k_ctx": sum(a["calls_over_128k_ctx"] for a in enriched_agents),
                "calls_over_200k_ctx": sum(a["calls_over_200k_ctx"] for a in enriched_agents),
            },
        },
        "agents": agents_clean,
        "data_quality": {
            "unmatched_models": sorted(unmatched_models),
            "missing_transcripts": missing_transcripts,
            "malformed_lines": malformed_lines_total,
        },
    }


def _load_archived_keys(history_path: Optional[Union[Path, str]]) -> Set[Tuple[str, Optional[int]]]:
    keys: Set[Tuple[str, Optional[int]]] = set()
    if history_path is None:
        return keys
    hpath = Path(history_path)
    if not hpath.is_file():
        return keys
    for raw_line in hpath.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        cid = str(obj.get("conversation_id") or "").strip()
        status = str(obj.get("status") or "archived").strip().lower()
        if cid and status == "archived":
            keys.add((cid, obj.get("window_start_step")))
    return keys


def list_pending_sessions(
    brain_dir: Union[Path, str],
    history_path: Optional[Union[Path, str]] = None,
    *,
    bridge_db_path: Optional[Union[Path, str]] = None,
    runs_jsonl_paths: Optional[List[Path]] = None,
) -> List[Dict[str, Any]]:
    """Liste les sessions Gravity racines ayant de l'activité LLM non encore archivée."""
    base = Path(brain_dir)
    if not base.is_dir():
        return []

    archived_keys = _load_archived_keys(history_path)
    known_session_ids = _collect_known_gravity_session_ids(runs_jsonl_paths)
    bridge_target_ids = load_bridge_target_conversation_ids(bridge_db_path)
    pending: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        if not tpath.is_file():
            continue

        first_prompt_raw: Optional[str] = None
        has_gravity_cmd = False
        raw_lines: List[str] = tpath.read_text(encoding="utf-8", errors="replace").splitlines()
        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "USER_INPUT":
                content = str(obj.get("content") or "")
                if first_prompt_raw is None:
                    first_prompt_raw = content
                if is_gravity_session_prompt(content):
                    has_gravity_cmd = True

        if first_prompt_raw is None:
            continue
        if is_bridge_child_conversation(
            cdir.name,
            first_prompt_raw=first_prompt_raw,
            bridge_target_ids=bridge_target_ids,
        ):
            continue

        is_gravity = (
            has_gravity_cmd
            or "/gravity" in first_prompt_raw.lower()
            or "gravity" in first_prompt_raw.lower()
            or cdir.name in known_session_ids
            or _has_gravity_workspace_uri(cdir)
        )
        if not is_gravity:
            continue

        win = resolve_gravity_window(tpath, current_prompt_is_metrics=False)
        if win.get("window_empty"):
            continue
        if (cdir.name, win.get("start_step_index")) in archived_keys:
            continue
        if win.get("mode") == "full_conversation" and (cdir.name, None) in archived_keys:
            continue

        start_idx = int(win.get("start_line_idx") or 0)
        end_idx = win.get("end_line_idx")
        llm_calls = 0
        last_ts: Optional[str] = None
        for line_idx, raw_line in enumerate(raw_lines):
            if line_idx < start_idx:
                continue
            if end_idx is not None and line_idx >= end_idx:
                break
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("created_at"):
                last_ts = str(obj["created_at"])
            if obj.get("type") == "PLANNER_RESPONSE":
                llm_calls += 1

        if llm_calls == 0:
            continue

        links = discover_bridge_links(cdir.name, tpath, bridge_db=bridge_db_path)
        apply_win = bool(win.get("has_gravity_anchor") or win.get("mode") != "full_conversation")
        active_links = [
            lnk
            for lnk in links
            if not apply_win
            or _is_spawn_in_window(lnk.get("spawn_step_index"), lnk.get("created_at"), None, win)
        ]
        pending_jobs = [lnk for lnk in active_links if lnk.get("has_active_job")]
        bridge_children = [lnk for lnk in active_links if lnk.get("target_conversation_id")]
        bridge_workspaces = sorted(
            {str(lnk.get("to_workspace") or "").strip() for lnk in active_links if lnk.get("to_workspace")}
        )

        mtime = tpath.stat().st_mtime
        updated_at = last_ts or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        title = _clean_user_prompt(win.get("first_prompt") or first_prompt_raw)
        pending.append(
            {
                "conversation_id": cdir.name,
                "title": title,
                "window_mode": win["mode"],
                "window_start_step": win["start_step_index"],
                "started_at": win["start_ts"],
                "updated_at": updated_at,
                "llm_calls": llm_calls,
                "bridge_children_count": len(bridge_children),
                "bridge_pending_jobs_count": len(pending_jobs),
                "has_pending_bridge_jobs": bool(pending_jobs),
                "bridge_workspaces": bridge_workspaces,
                "bridge_pending_jobs": [
                    {
                        "thread_id": j["thread_id"],
                        "to_workspace": j["to_workspace"],
                        "job_id": j["job_id"],
                        "status": j["job_status"],
                    }
                    for j in pending_jobs
                ],
                "_mtime": mtime,
            }
        )

    pending.sort(key=lambda item: (-item["_mtime"], item["conversation_id"]))
    for item in pending:
        item.pop("_mtime", None)
    return pending
