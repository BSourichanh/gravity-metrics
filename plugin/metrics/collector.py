#!/usr/bin/env python3
"""Collecte et agrégation des métriques depuis les transcripts Antigravity (100 % stdlib)."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from .bridge_links import (
    discover_bridge_links,
    load_bridge_caller_conversation_ids,
    load_bridge_target_conversation_ids,
    resolve_project_from_bridge_or_transcript,
)
from .lifecycle import (
    load_active_captures,
    load_ignored_sessions,
    load_session_meta,
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
METRICS_OPEN_MARKER = "<!-- gravity-metrics:opened -->"

_ARCHITECT_ARCHIVE_RE = re.compile(
    r"^/gravity-architect(?::gravity-architect)?\s+archive\s+([^\s]+)",
    re.IGNORECASE,
)
_CACHE_MAX_ENTRIES = 512
_TRANSCRIPT_INDEX_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}
_WORKFLOW_CHILDREN_CACHE: Dict[str, Tuple[Tuple[Any, ...], List[Dict[str, Any]]]] = {}
_SUBAGENTS_CACHE: Dict[str, Tuple[Tuple[Any, ...], List[Dict[str, Any]]]] = {}

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
_WORKFLOW_AGENT_LINE_RE = re.compile(
    r"^\s*-\s+\[agent:([^\]]+)\]\s+([^:]+?):\s*([^(]+?)\s*"
    r"\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)"
)
_TASK_STEP_RE = re.compile(r"^task-(\d+)\.log$", re.IGNORECASE)
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


def _parse_metrics_flags(tokens: List[str]) -> Tuple[Dict[str, Any], List[str], bool, bool]:
    """Parse les options communes de `/gravity-metrics` sans lever d'exception sur texte long."""
    label = ""
    note = ""
    profile = "2026_nov_standard"
    project = ""
    full = False
    force = False
    has_explicit_label = False
    has_capture_flags = False
    positionals: List[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-l", "--label"):
            if i + 1 >= len(tokens):
                raise ValueError(f"Option {tok} sans valeur.")
            label = tokens[i + 1]
            has_explicit_label = True
            has_capture_flags = True
            i += 2
        elif tok in ("-n", "--note"):
            if i + 1 >= len(tokens):
                raise ValueError(f"Option {tok} sans valeur.")
            note = tokens[i + 1]
            has_capture_flags = True
            i += 2
        elif tok == "--profile":
            if i + 1 >= len(tokens):
                raise ValueError("Option --profile sans valeur.")
            profile = tokens[i + 1]
            has_capture_flags = True
            i += 2
        elif tok == "--project":
            if i + 1 >= len(tokens):
                raise ValueError("Option --project sans valeur.")
            project = tokens[i + 1]
            i += 2
        elif tok == "--full":
            full = True
            has_capture_flags = True
            i += 1
        elif tok == "--force":
            force = True
            has_capture_flags = True
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
            "project": project.strip(),
            "full": full,
            "force": force,
        },
        positionals,
        has_explicit_label,
        has_capture_flags,
    )


def parse_architect_archive_prompt(raw: str) -> Optional[str]:
    """Extrait `<slug>` si le prompt est `/gravity-architect archive <slug>`, sinon None."""
    txt = extract_user_request_text(raw)
    m = _ARCHITECT_ARCHIVE_RE.match(txt)
    if not m:
        return None
    slug = m.group(1).strip("`'\",.;:()[]<>")
    return slug or None


def classify_metrics_prompt(raw: str) -> Dict[str, Any]:
    """Analyse un prompt `/gravity-metrics` : passthrough, open, start, stop, capture, archive, pending ou error."""
    txt = extract_user_request_text(raw)
    if not _GRAVITY_METRICS_CMD_RE.match(txt):
        return {"action": "passthrough"}
    match = _GRAVITY_METRICS_ARGS_RE.match(txt)
    if not match:
        return {"action": "passthrough"}
    rest = (match.group(1) or "").strip()
    if not rest:
        return {
            "action": "open",
            "project": "",
        }
    if rest.startswith("/"):
        return {"action": "passthrough"}

    tokens = _split_metrics_tokens(rest)
    if not tokens:
        return {
            "action": "open",
            "project": "",
        }

    head = tokens[0].lower()
    if head == "pending":
        proj_arg = ""
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--project":
                if i + 1 >= len(tokens):
                    return {"action": "error", "error": "Option --project sans valeur."}
                proj_arg = tokens[i + 1].strip()
                i += 2
            elif tok.startswith("-"):
                return {"action": "error", "error": f"Option inconnue pour `pending` : `{tok}`."}
            else:
                i += 1
        return {"action": "pending", "project": proj_arg}

    if head == "start":
        try:
            opts, positionals, has_explicit_label, _ = _parse_metrics_flags(tokens[1:])
        except ValueError as exc:
            return {"action": "error", "error": str(exc)}
        resolved_label = opts["label"] or " ".join(positionals).strip()
        return {
            "action": "start",
            "label": resolved_label,
            "project": opts["project"],
            "has_explicit_label": has_explicit_label,
        }

    if head in ("stop", "finish"):
        try:
            opts, positionals, has_explicit_label, _ = _parse_metrics_flags(tokens[1:])
        except ValueError as exc:
            return {"action": "error", "error": str(exc)}
        resolved_label = opts["label"] or " ".join(positionals).strip()
        return {
            "action": "stop",
            "label": resolved_label,
            "note": opts["note"],
            "profile": opts["profile"],
            "project": opts["project"],
            "full": opts["full"],
            "force": opts["force"],
            "has_explicit_label": has_explicit_label,
        }

    if head == "archive":
        try:
            opts, positionals, has_explicit_label, _ = _parse_metrics_flags(tokens[1:])
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
            "project": opts["project"],
            "full": opts["full"],
            "force": opts["force"],
            "has_explicit_label": has_explicit_label,
        }

    try:
        opts, positionals, has_explicit_label, has_capture_flags = _parse_metrics_flags(tokens)
    except ValueError as exc:
        if len(rest) > 60 or len(tokens) > 6:
            return {"action": "passthrough"}
        return {"action": "error", "error": str(exc)}

    positional_text = " ".join(positionals).strip()
    if not has_explicit_label and (len(positional_text) > 60 or len(positional_text.split()) > 6):
        return {"action": "passthrough"}

    resolved_label = opts["label"] or positional_text
    if not resolved_label and not has_capture_flags:
        return {
            "action": "open",
            "project": opts["project"],
        }
    return {
        "action": "capture",
        "label": resolved_label.strip(),
        "note": opts["note"],
        "profile": opts["profile"],
        "project": opts["project"],
        "full": opts["full"],
        "force": opts["force"],
        "has_explicit_label": has_explicit_label,
    }


def is_gravity_metrics_capture_prompt(raw: str) -> bool:
    """Retourne True uniquement pour une commande de capture (`/gravity-metrics <label>`, `stop`/`finish`)."""
    return classify_metrics_prompt(raw).get("action") in ("capture", "stop")


def is_gravity_session_prompt(raw: str) -> bool:
    """Retourne True si le prompt lance `/gravity`, `/gravity-workflow`, `/gravity-architect` ou `/gravity-bridge`."""
    txt = extract_user_request_text(raw)
    return bool(_GRAVITY_CMD_RE.match(txt)) and not is_gravity_metrics_prompt(txt)


def _default_workflow_home() -> Path:
    env_wf = os.environ.get("GRAVITY_WORKFLOW_HOME", "").strip()
    if env_wf:
        return Path(env_wf).expanduser()
    env_g = os.environ.get("GRAVITY_HOME", "").strip()
    if env_g:
        return Path(env_g).expanduser() / "workflow"
    return Path.home() / ".gemini" / "gravity" / "workflow"


def _find_centralized_workflow_profile(workspace_root: Path) -> Optional[Path]:
    wf_ws_dir = _default_workflow_home() / "workspaces"
    if not wf_ws_dir.is_dir():
        return None
    try:
        resolved = workspace_root.resolve()
    except OSError:
        resolved = workspace_root
    candidates_str = {str(resolved), os.path.normcase(str(resolved))}
    hashes: List[str] = []
    for raw_s in candidates_str:
        digest = hashlib.sha256(raw_s.encode("utf-8")).hexdigest()
        hashes.extend([digest, digest[:16], digest[:12]])
    for h in hashes:
        p = wf_ws_dir / h / "profile.json"
        if p.is_file():
            return p
    norm_target = os.path.normcase(str(resolved)).rstrip("\\/")
    try:
        for sub in sorted(wf_ws_dir.iterdir()):
            if not sub.is_dir():
                continue
            p = sub / "profile.json"
            if not p.is_file():
                continue
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            for key in ("workspace", "workspace_path", "canonical_path", "cwd"):
                val = str(obj.get(key) or "").strip()
                if val and os.path.normcase(os.path.abspath(val)).rstrip("\\/") == norm_target:
                    return p
    except OSError:
        return None
    return None


def read_gravity_profile_summary(
    workspace_root: Optional[Union[Path, str]],
) -> str:
    """Lit en lecture seule `$GRAVITY_HOME/workflow/workspaces/<hash>/profile.json` > `.agents/gravity-workflow/profile.json` > `.agents/gravity/profile.json`."""
    if not workspace_root:
        return ""
    ws_path = Path(workspace_root)
    profile_path = _find_centralized_workflow_profile(ws_path)
    if profile_path is None:
        wf_local = ws_path / ".agents" / "gravity-workflow" / "profile.json"
        legacy_local = ws_path / ".agents" / "gravity" / "profile.json"
        if wf_local.is_file():
            profile_path = wf_local
        elif legacy_local.is_file():
            profile_path = legacy_local
        else:
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


def _task_log_sort_key(path: Path) -> Tuple[int, int, str]:
    m = _TASK_STEP_RE.match(path.name)
    if m:
        return (0, int(m.group(1)), path.name)
    return (1, 0, path.name)


def _extract_workflow_children_from_text(
    text: str,
    spawn_step_index: Optional[int],
    parent_cid_lower: str,
    by_cid: Dict[str, Dict[str, Any]],
    ordered_keys: List[str],
) -> None:
    """Extrait les lignes `- [agent:<type>] <role>: <state> (<UUID>)` dans `by_cid`."""
    if not text or "[agent:" not in text:
        return
    for line in text.splitlines():
        m = _WORKFLOW_AGENT_LINE_RE.match(line)
        if not m:
            continue
        type_name = m.group(1).strip()
        role = m.group(2).strip()
        state = m.group(3).strip()
        wcid = m.group(4).strip()
        key = wcid.lower()
        if not wcid or key == parent_cid_lower:
            continue
        if key not in by_cid:
            by_cid[key] = {
                "conversation_id": wcid,
                "type_name": type_name,
                "role": role,
                "state": state,
                "spawn_step_index": spawn_step_index,
            }
            ordered_keys.append(key)
        elif by_cid[key]["spawn_step_index"] is None and spawn_step_index is not None:
            by_cid[key]["spawn_step_index"] = spawn_step_index


def _cache_put(cache: Dict[str, Any], key: str, value: Any) -> None:
    if len(cache) >= _CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)))
    cache[key] = value


def _stat_sig(path: Path) -> Tuple[int, int]:
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, -1)


def _get_transcript_index(transcript_path: Union[Path, str]) -> Dict[str, Any]:
    """Indexe en une passe (avec cache mtime_ns+size) un `transcript.jsonl` racine."""
    tpath = Path(transcript_path)
    if not tpath.is_file():
        return {
            "exists": False,
            "mtime": 0.0,
            "first_prompt_raw": None,
            "has_gravity_cmd": False,
            "last_ts": None,
            "entries": [],
        }

    try:
        st = tpath.stat()
        sig = (int(st.st_mtime_ns), int(st.st_size))
        mtime = float(st.st_mtime)
        cache_key = str(tpath.resolve())
    except OSError:
        sig = (0, -1)
        mtime = 0.0
        cache_key = ""

    if cache_key:
        cached = _TRANSCRIPT_INDEX_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

    first_prompt_raw: Optional[str] = None
    has_gravity_cmd = False
    last_ts: Optional[str] = None
    entries: List[Dict[str, Any]] = []

    try:
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
                created_at = obj.get("created_at")
                if created_at:
                    last_ts = str(created_at)
                content = str(obj.get("content") or "")
                is_user = step_type == "USER_INPUT"
                metrics_action = ""
                is_metrics = False
                is_gravity = False
                is_architect = False
                is_architect_archive = False
                clean_prompt = ""
                if is_user:
                    if first_prompt_raw is None:
                        first_prompt_raw = content
                    clean_prompt = extract_user_request_text(content)[:160]
                    is_metrics = is_gravity_metrics_prompt(content)
                    if is_metrics:
                        metrics_action = str(classify_metrics_prompt(content).get("action") or "")
                    is_gravity = is_gravity_session_prompt(content)
                    if has_gravity_cmd is False and is_gravity:
                        has_gravity_cmd = True
                    low_clean = clean_prompt.lower()
                    if low_clean.startswith("/gravity-architect"):
                        is_architect = True
                    if parse_architect_archive_prompt(content) is not None:
                        is_architect_archive = True

                meta_obj = obj.get("metadata")
                has_marker = step_type == "SYSTEM_MESSAGE" and (
                    METRICS_MARKER in content
                    or (
                        isinstance(meta_obj, dict)
                        and METRICS_MARKER in json.dumps(meta_obj, ensure_ascii=False)
                    )
                )
                has_open_marker = step_type == "SYSTEM_MESSAGE" and (
                    METRICS_OPEN_MARKER in content
                    or (
                        isinstance(meta_obj, dict)
                        and METRICS_OPEN_MARKER in json.dumps(meta_obj, ensure_ascii=False)
                    )
                )
                tool_names: List[str] = []
                has_push_or_verif = False
                if step_type == "PLANNER_RESPONSE":
                    for tc in obj.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        tname = str(tc.get("name") or "").strip()
                        if tname:
                            tool_names.append(tname)
                        if tname in ("run_command", "execute_command", "bash", "shell"):
                            args_obj = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                            cmd_str = str(args_obj.get("CommandLine") or args_obj.get("command") or "").lower()
                            if any(tok in cmd_str for tok in ("git push", "validate.py", "unittest", "pytest")):
                                has_push_or_verif = True

                entries.append(
                    {
                        "line_idx": line_idx,
                        "step_index": step_idx,
                        "type": step_type,
                        "status": str(obj.get("status") or ""),
                        "created_at": created_at,
                        "is_user": is_user,
                        "is_metrics": is_metrics,
                        "metrics_action": metrics_action,
                        "is_gravity": is_gravity,
                        "is_architect": is_architect,
                        "is_architect_archive": is_architect_archive,
                        "is_planner": step_type == "PLANNER_RESPONSE",
                        "has_marker": has_marker,
                        "has_open_marker": has_open_marker,
                        "has_push_or_verif": has_push_or_verif,
                        "tool_names": tool_names,
                        "clean_prompt": clean_prompt,
                    }
                )
    except OSError:
        pass

    result = {
        "exists": True,
        "mtime": mtime,
        "first_prompt_raw": first_prompt_raw,
        "has_gravity_cmd": has_gravity_cmd,
        "last_ts": last_ts,
        "entries": entries,
    }
    if cache_key:
        _cache_put(_TRANSCRIPT_INDEX_CACHE, cache_key, (sig, result))
    return result


def discover_workflow_children(
    parent_cid: str,
    brain_dir: Union[Path, str],
) -> List[Dict[str, Any]]:
    """Découvre en lecture seule les sous-agents `run_workflow` d'une conversation (cache mtime_ns+size)."""
    clean_parent = str(parent_cid or "").strip()
    if not clean_parent:
        return []
    base = Path(brain_dir)
    cdir = base / clean_parent
    if not cdir.is_dir():
        return []

    tasks_dir = cdir / ".system_generated" / "tasks"
    msgs_dir = cdir / ".system_generated" / "messages"
    tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"

    task_logs: List[Path] = []
    if tasks_dir.is_dir():
        try:
            task_logs = sorted(
                (
                    p
                    for p in tasks_dir.iterdir()
                    if p.is_file()
                    and p.name.lower().startswith("task-")
                    and p.suffix.lower() == ".log"
                ),
                key=_task_log_sort_key,
            )
        except OSError:
            task_logs = []

    msg_files: List[Path] = []
    if msgs_dir.is_dir():
        try:
            msg_files = [
                p
                for p in sorted(msgs_dir.iterdir(), key=lambda p: p.name)
                if p.is_file() and p.suffix.lower() == ".json" and p.name.lower() != "read.json"
            ]
        except OSError:
            msg_files = []

    fp = (
        tuple((p.name, _stat_sig(p)) for p in task_logs),
        tuple((p.name, _stat_sig(p)) for p in msg_files),
        _stat_sig(tpath) if tpath.is_file() else None,
    )
    try:
        cache_key = str(cdir.resolve())
    except OSError:
        cache_key = ""

    if cache_key:
        cached = _WORKFLOW_CHILDREN_CACHE.get(cache_key)
        if cached is not None and cached[0] == fp:
            return [dict(item) for item in cached[1]]

    parent_cid_lower = clean_parent.lower()
    by_cid: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []

    for tlog in task_logs:
        m_step = _TASK_STEP_RE.match(tlog.name)
        step_idx = int(m_step.group(1)) if m_step else None
        try:
            content = tlog.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _extract_workflow_children_from_text(
            content, step_idx, parent_cid_lower, by_cid, ordered_keys
        )

    for mpath in msg_files:
        try:
            obj = json.loads(mpath.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        sm = obj.get("sourceMetadata")
        tool_meta = sm.get("tool") if isinstance(sm, dict) else None
        tool_call = tool_meta.get("toolCall") if isinstance(tool_meta, dict) else None
        tool_name = (
            str(tool_call.get("name") or "").strip() if isinstance(tool_call, dict) else ""
        )
        content = obj.get("content")
        content_str = content if isinstance(content, str) else ""
        if tool_name != "run_workflow" or "[agent:" not in content_str:
            continue
        raw_step = tool_meta.get("stepIndex") if isinstance(tool_meta, dict) else None
        step_idx = (
            raw_step
            if isinstance(raw_step, int) and not isinstance(raw_step, bool)
            else None
        )
        _extract_workflow_children_from_text(
            content_str, step_idx, parent_cid_lower, by_cid, ordered_keys
        )

    if tpath.is_file():
        try:
            raw_lines = tpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raw_lines = []
        active_wf_turn = False
        active_wf_has_other_tools = False
        has_any_wf_call = False
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
            step_type = obj.get("type")
            if step_type == "USER_INPUT":
                active_wf_turn = False
                active_wf_has_other_tools = False
                continue
            if step_type == "PLANNER_RESPONSE":
                raw_tcs = obj.get("tool_calls")
                tc_list = raw_tcs if isinstance(raw_tcs, list) else []
                tc_names = [
                    str(tc.get("name") or "").strip()
                    for tc in tc_list
                    if isinstance(tc, dict)
                ]
                has_wf = "run_workflow" in tc_names
                active_wf_turn = has_wf
                active_wf_has_other_tools = has_wf and any(
                    n != "run_workflow" for n in tc_names
                )
                has_any_wf_call = has_any_wf_call or has_wf
                continue
            if step_type == "GENERIC":
                content = obj.get("content")
                if not active_wf_turn or not isinstance(content, str) or "[agent:" not in content:
                    continue
                if active_wf_has_other_tools:
                    padded = "\n" + content
                    if any(
                        m in padded
                        for m in ("\nCommand output:", "\nCommand exited ", "\nShowing lines ")
                    ) and ("Workflow " not in content and "\nLogs:\n" not in content):
                        continue
            elif step_type == "SYSTEM_MESSAGE":
                content = obj.get("content")
                if not isinstance(content, str) or "[agent:" not in content:
                    continue
                lowered = content.lower()
                if not (
                    has_any_wf_call
                    or f'task id "{parent_cid_lower}/task-' in lowered
                    or f"sender={parent_cid_lower}/task-" in lowered
                ):
                    continue
            else:
                continue
            raw_step = obj.get("step_index")
            step_idx = (
                raw_step
                if isinstance(raw_step, int) and not isinstance(raw_step, bool)
                else None
            )
            _extract_workflow_children_from_text(
                content, step_idx, parent_cid_lower, by_cid, ordered_keys
            )

    result = [by_cid[k] for k in ordered_keys]
    if cache_key:
        _cache_put(_WORKFLOW_CHILDREN_CACHE, cache_key, (fp, [dict(item) for item in result]))
    return result


def load_known_child_conversation_ids(
    brain_dir: Union[Path, str],
    bridge_db_path: Optional[Union[Path, str]] = None,
) -> Set[str]:
    """Agrège tous les identifiants connus de conversations enfants (Bridge, subagents, workflow)."""
    child_ids: Set[str] = set(load_bridge_target_conversation_ids(bridge_db_path))
    base = Path(brain_dir)
    if not base.is_dir():
        return child_ids

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        cdir_lower = cdir.name.lower()
        sub_dir = cdir / ".system_generated" / "subagents"
        if sub_dir.is_dir():
            try:
                sub_files = sorted(
                    fn for fn in sub_dir.iterdir() if fn.is_file() and fn.suffix.lower() == ".json"
                )
            except OSError:
                sub_files = []
            sub_fp = tuple((fn.name, _stat_sig(fn)) for fn in sub_files)
            try:
                sub_key = str(sub_dir.resolve())
            except OSError:
                sub_key = ""
            cached_sub = _SUBAGENTS_CACHE.get(sub_key) if sub_key else None
            if cached_sub is not None and cached_sub[0] == sub_fp:
                sub_cids = cached_sub[1]
            else:
                sub_cids = []
                for fn in sub_files:
                    try:
                        sd = json.loads(fn.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(sd, dict):
                        continue
                    scid = str(sd.get("conversationId") or "").strip()
                    if not scid or scid == cdir.name or scid.lower() == cdir_lower:
                        continue
                    desc = sd.get("subagentDescriptor")
                    raw_type = (
                        desc.get("typeName")
                        if isinstance(desc, dict) and desc.get("typeName") is not None
                        else sd.get("type_name")
                    )
                    if str(raw_type or "").strip().lower() == "root":
                        continue
                    sub_cids.append(scid)
                if sub_key:
                    _cache_put(_SUBAGENTS_CACHE, sub_key, (sub_fp, list(sub_cids)))
            child_ids.update(sub_cids)

        for w in discover_workflow_children(cdir.name, base):
            wcid = str(w.get("conversation_id") or "").strip()
            if not wcid or wcid == cdir.name or wcid.lower() == cdir_lower:
                continue
            if str(w.get("type_name") or "").strip().lower() == "root":
                continue
            child_ids.add(wcid)

    return child_ids


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
    bridge_caller_ids = load_bridge_caller_conversation_ids(bridge_db_path)
    known_child_ids = load_known_child_conversation_ids(base, bridge_db_path=bridge_db_path)
    results: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        tx = _get_transcript_index(tpath)
        if not tx["exists"] or tx["first_prompt_raw"] is None:
            continue

        first_prompt_raw = str(tx["first_prompt_raw"])
        has_gravity_cmd = bool(tx["has_gravity_cmd"])
        last_ts = tx["last_ts"]

        is_bridge_child = is_bridge_child_conversation(
            cdir.name,
            first_prompt_raw=first_prompt_raw,
            bridge_target_ids=bridge_target_ids,
        )
        is_child = is_bridge_child or (cdir.name in known_child_ids)
        if not include_all and is_child:
            continue

        title = _clean_user_prompt(first_prompt_raw)
        is_gravity = (not is_child) and (
            has_gravity_cmd
            or (cdir.name in known_session_ids)
            or (cdir.name in bridge_caller_ids)
        )
        if not include_all and not is_gravity:
            continue

        mtime = float(tx["mtime"] or 0.0)
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
    tx = _get_transcript_index(tpath)
    entries: List[Dict[str, Any]] = tx["entries"]

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

    def _has_user_after(idx_in_entries: int) -> bool:
        return any(e["is_user"] for e in entries[idx_in_entries + 1 :])

    def _turn_has_recorded_marker(idx_in_entries: int) -> bool:
        for e in entries[idx_in_entries + 1 :]:
            if e["is_user"]:
                break
            if e["has_marker"]:
                return True
        return False

    def _is_capture_boundary_entry(idx_in_entries: int) -> bool:
        e = entries[idx_in_entries]
        act = e.get("metrics_action") or ""
        if act in ("capture", "stop"):
            return True
        if act == "open":
            # Bare /gravity-metrics: capture boundary if it recorded METRICS_MARKER (v0.4),
            # or if it is the trailing prompt at the end of the transcript (not followed by another USER_INPUT
            # and without METRICS_OPEN_MARKER).
            if _turn_has_recorded_marker(idx_in_entries):
                return True
            if not _has_user_after(idx_in_entries) and not any(
                x.get("has_open_marker")
                for x in entries[idx_in_entries + 1 :]
            ):
                return True
        return False

    capture_positions = [i for i in range(len(entries)) if _is_capture_boundary_entry(i)]
    has_gravity_anchor = False

    def _is_metrics_entry_pending(idx_in_entries: int) -> bool:
        tail = entries[idx_in_entries + 1 :]
        if any(
            e["type"] in ("PLANNER_RESPONSE", "SYSTEM_MESSAGE", "ERROR_MESSAGE", "USER_INPUT")
            or e["has_marker"]
            or e.get("has_open_marker")
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
                last_m = capture_positions[-1]
                if _has_user_after(last_m):
                    # Activité utilisateur postérieure à la dernière capture :
                    # la capture précédente devient le point de départ, pas la fin.
                    current_m_pos = None
                    prev_m_pos = last_m
                else:
                    completed = [j for j in capture_positions if _is_completed_capture_boundary(j)]
                    if completed:
                        current_m_pos = completed[-1]
                        prev_m_pos = completed[-2] if len(completed) >= 2 else None
                    else:
                        current_m_pos = last_m
                        prev_m_pos = None

        end_pos = current_m_pos if current_m_pos is not None else len(entries)
        if prev_m_pos is not None:
            pos = prev_m_pos + 1
            while pos < end_pos and not entries[pos]["is_user"]:
                pos += 1
            fallback_start_pos = pos
        else:
            fallback_start_pos = 0

        # Honorer un /gravity-metrics start non encore clôturé dans ce segment
        start_cmd_pos = None
        for i in range(fallback_start_pos, end_pos):
            if entries[i].get("metrics_action") == "start":
                start_cmd_pos = i

        g_pos = next(
            (i for i in range(fallback_start_pos, end_pos) if entries[i]["is_gravity"]),
            None,
        )
        if start_cmd_pos is not None and (g_pos is None or start_cmd_pos > g_pos):
            pos = start_cmd_pos + 1
            while pos < end_pos and not entries[pos]["is_user"]:
                pos += 1
            start_pos = pos
            mode = "custom"
            has_gravity_anchor = g_pos is not None
        elif g_pos is not None:
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

        workflow_children = discover_workflow_children(cid, base)
        for w in workflow_children:
            wcid = str(w.get("conversation_id") or "").strip()
            if not wcid or wcid in visited:
                continue
            if wcid not in children_ids:
                children_ids.append(wcid)
            if wcid not in enqueued:
                enqueued.add(wcid)
                queue.append(
                    {
                        "cid": wcid,
                        "parent_id": cid,
                        "depth": depth + 1,
                        "type_name": w["type_name"],
                        "role": w["role"],
                        "state": w["state"],
                        "spawn_step": w["spawn_step_index"],
                        "source": "workflow",
                        "bridge_thread_id": None,
                        "bridge_workspace": cur_bridge_ws,
                        "bridge_job_status": None,
                        "bridge_created_at": None,
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


_SHELL_EXIT_RE = re.compile(r"^Command exited with code (-?\d+)\b", re.MULTILINE)


def _extract_shell_nonzero_code(content: Any) -> Optional[int]:
    """Retourne le code de sortie non nul d'un step GENERIC (hors corps Command output/Showing lines)."""
    if not isinstance(content, str) or "Command exited with code " not in content:
        return None
    header = content.split("\nCommand output:", 1)[0]
    header = header.split("\nShowing lines ", 1)[0]
    if header.lstrip().startswith("Showing lines "):
        return None
    for m in _SHELL_EXIT_RE.finditer(header):
        code = int(m.group(1))
        if code != 0:
            return code
    return None


def _has_finish_tool_call(step_obj: Optional[Dict[str, Any]]) -> bool:
    """Vérifie si un step PLANNER_RESPONSE invoque l'outil terminal `finish`."""
    if not isinstance(step_obj, dict) or step_obj.get("type") != "PLANNER_RESPONSE":
        return False
    raw_tcs = step_obj.get("tool_calls")
    if not isinstance(raw_tcs, (list, tuple)):
        return False
    return any(
        isinstance(tc, dict) and str(tc.get("name") or "").strip() == "finish"
        for tc in raw_tcs
    )


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
    invalid_steps = 0
    shell_nonzero_steps = 0
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
    cold_cache_calls = 0
    cold_cache_input_tokens = 0
    cold_cache_cost_raw = 0.0

    timestamps: List[datetime] = []
    prev_non_sys: Optional[Dict[str, Any]] = None
    last_non_sys: Optional[Dict[str, Any]] = None
    user_prompt_texts: List[str] = []
    step_costs: List[Dict[str, Any]] = []

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
                    prev_non_sys = last_non_sys
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
                elif status == "INVALID":
                    invalid_steps += 1
                    err_txt = str(obj.get("error") or obj.get("content") or "INVALID").strip()
                    err_short = err_txt.splitlines()[0][:120] if err_txt else "INVALID"
                    error_reasons[f"[INVALID] {err_short}"[:120]] += 1
                elif status == "RUNNING":
                    running_status_steps += 1

                if step_type == "GENERIC" and status not in ("ERROR", "INVALID"):
                    shell_code = _extract_shell_nonzero_code(obj.get("content"))
                    if shell_code is not None:
                        shell_nonzero_steps += 1
                        error_reasons[f"[shell exit={shell_code}] Command exited with code {shell_code}"[:120]] += 1

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

                    if (cache_t == 0 and in_t > 10_000) or (
                        in_t > 50_000 and (in_t + cache_t) > 0 and (cache_t / (in_t + cache_t)) < 0.25
                    ):
                        cold_cache_calls += 1
                        cold_cache_input_tokens += in_t
                        cold_cache_cost_raw += float(call_cost["total_usd"])

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
                    step_costs.append(
                        {
                            "line_idx": line_idx,
                            "step_index": step_idx if isinstance(step_idx, int) else None,
                            "tokens": in_t + cache_t + out_t,
                            "cost_usd": float(call_cost["total_usd"]),
                            "cost_2027_usd": float(c2["total_usd"]),
                        }
                    )
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
        if last_non_sys_type == "PLANNER_RESPONSE":
            clean_term = (not bool(last_non_sys.get("tool_calls"))) or (
                last_non_sys.get("status") == "DONE"
                and _has_finish_tool_call(last_non_sys)
            )
        elif last_non_sys_type == "GENERIC":
            clean_term = (
                last_non_sys.get("status") == "DONE"
                and prev_non_sys is not None
                and prev_non_sys.get("status") == "DONE"
                and _has_finish_tool_call(prev_non_sys)
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
        "invalid_steps": invalid_steps,
        "shell_nonzero_steps": shell_nonzero_steps,
        "total_anomalies": error_steps + invalid_steps + shell_nonzero_steps,
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
        "cold_cache_calls": cold_cache_calls,
        "cold_cache_input_tokens": cold_cache_input_tokens,
        "cold_cache_cost_usd": round(cold_cache_cost_raw, 6),
        "_raw_cold_cache_cost_usd": cold_cache_cost_raw,
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
        "_step_costs": step_costs,
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
    is_workflow = bool(tool_counter.get("run_workflow")) or any(
        a.get("source") == "workflow" for a in enriched_agents
    )
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
    in_tok = cache_tok = out_tok = calls = tools = errs = invs = shell_nz = 0
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
        invs += ag["invalid_steps"]
        shell_nz += ag["shell_nonzero_steps"]
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
        "invalid_steps": invs,
        "shell_nonzero_steps": shell_nz,
        "total_anomalies": errs + invs + shell_nz,
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


def _compute_session_milestones(
    *,
    root_id: str,
    root_transcript_path: Union[Path, str],
    window_info: Dict[str, Any],
    enriched_agents: List[Dict[str, Any]],
    bridge_pending_jobs: List[Dict[str, Any]],
    total_cost_float: float,
    is_archived: bool = False,
) -> Dict[str, Any]:
    """Déduit les 4 jalons automatiques (0 token) ou repli 2 jalons en mono-dépôt sans Bridge."""
    tx = _get_transcript_index(root_transcript_path)
    entries = tx.get("entries") or []
    s_line = int(window_info.get("start_line_idx") or 0)
    e_line = window_info.get("end_line_idx")
    win_entries = [
        e
        for e in entries
        if e["line_idx"] >= s_line and (e_line is None or e["line_idx"] < e_line)
    ]

    root_agent = next((a for a in enriched_agents if a["conversation_id"] == root_id), None)
    bridge_agents = [a for a in enriched_agents if a.get("source") == "bridge"]
    wf_agents = [a for a in enriched_agents if a.get("source") == "workflow"]
    root_links = (root_agent.get("_bridge_links") or []) if root_agent else []
    has_architect = any(e.get("is_architect") for e in win_entries)
    has_bridge = bool(bridge_agents or root_links)

    def _empty_phase(pid: str, plabel: str) -> Dict[str, Any]:
        return {
            "id": pid,
            "label": plabel,
            "status": "pending",
            "agents": 0,
            "llm_calls": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "cost_2027_usd": 0.0,
            "cost_share_pct": 0.0,
        }

    def _add_agent_to_phase(phase: Dict[str, Any], ag: Dict[str, Any]) -> None:
        phase["agents"] += 1
        phase["llm_calls"] += int(ag.get("llm_calls") or 0)
        phase["tokens"] += int(ag.get("total_tokens") or 0)
        phase["cost_usd"] += float((ag.get("_raw_cost") or {}).get("total_usd") or 0.0)
        phase["cost_2027_usd"] += float((ag.get("_raw_proj") or {}).get("total_usd") or 0.0)

    has_arch_archive = any(e.get("is_architect_archive") for e in win_entries)

    if not has_bridge and not has_architect:
        # Repli 2 jalons : Production -> Clôture / Archivé
        p_prod = _empty_phase("production", "Production")
        p_close = _empty_phase("cloture", "Clôture / Archivé")
        for ag in enriched_agents:
            _add_agent_to_phase(p_prod, ag)
        p_prod["status"] = "done" if is_archived else "active"
        p_close["status"] = "done" if is_archived else "pending"
        phases = [p_prod, p_close]
        current_phase = p_close["label"] if is_archived else p_prod["label"]
        mode = "2_phases"
    else:
        p_conc = _empty_phase("conception", "Conception")
        p_prod = _empty_phase("production", "Production")
        p_verif = _empty_phase("verification", "Vérification & Push")
        p_close = _empty_phase("cloture", "Clôture / Archivé")

        link_steps = [
            int(lnk["spawn_step_index"])
            for lnk in root_links
            if isinstance(lnk.get("spawn_step_index"), int)
        ]
        child_steps = [
            int(a["spawn_step_index"])
            for a in bridge_agents + wf_agents
            if isinstance(a.get("spawn_step_index"), int)
        ]
        first_prod_step = min(link_steps + child_steps) if (link_steps or child_steps) else None

        # Détecter début Vérification & Push : callback reçu, read_thread post-bridge, ou run_command git push / tests
        verif_step: Optional[int] = None
        archive_step: Optional[int] = None
        for e in win_entries:
            st_i = e.get("step_index")
            if e.get("is_architect_archive") and archive_step is None and isinstance(st_i, int):
                archive_step = st_i
            if first_prod_step is not None and isinstance(st_i, int) and st_i > first_prod_step:
                if e.get("has_push_or_verif") and verif_step is None:
                    verif_step = st_i
                elif not bridge_pending_jobs and e.get("is_user") and "[GRAVITY-BRIDGE" in str(e.get("clean_prompt") or ""):
                    if verif_step is None:
                        verif_step = st_i
                elif not bridge_pending_jobs and e.get("is_planner") and verif_step is None:
                    # Premier tour planner racine post-bridge sans jobs actifs restants
                    if st_i > max(link_steps or [first_prod_step]):
                        verif_step = st_i

        # Statuts des 4 jalons
        if is_archived or has_arch_archive:
            p_conc["status"] = "done"
            p_prod["status"] = "done" if (has_bridge or wf_agents or verif_step is not None) else "done"
            p_verif["status"] = "done"
            p_close["status"] = "done" if is_archived else "active"
            current_phase = p_close["label"]
        elif verif_step is not None:
            p_conc["status"] = "done"
            p_prod["status"] = "done"
            p_verif["status"] = "active"
            p_close["status"] = "pending"
            current_phase = p_verif["label"]
        elif first_prod_step is not None or bridge_agents or wf_agents or bridge_pending_jobs:
            p_conc["status"] = "done"
            p_prod["status"] = "active"
            p_verif["status"] = "pending"
            p_close["status"] = "pending"
            current_phase = p_prod["label"]
        else:
            p_conc["status"] = "active"
            p_prod["status"] = "pending"
            p_verif["status"] = "pending"
            p_close["status"] = "pending"
            current_phase = p_conc["label"]

        # Ventilation tokens & coûts sur les 4 phases (sans double-comptage)
        if root_agent is not None:
            step_costs = root_agent.get("_step_costs") or []
            if not step_costs:
                target_p = p_conc if first_prod_step is None else p_prod
                _add_agent_to_phase(target_p, root_agent)
            else:
                phase_touched: Set[str] = set()
                for sc in step_costs:
                    si = sc.get("step_index")
                    if archive_step is not None and isinstance(si, int) and si >= archive_step:
                        tgt = p_close
                    elif verif_step is not None and isinstance(si, int) and si >= verif_step:
                        tgt = p_verif
                    elif first_prod_step is not None and isinstance(si, int) and si >= first_prod_step:
                        tgt = p_prod
                    else:
                        tgt = p_conc
                    tgt["llm_calls"] += 1
                    tgt["tokens"] += int(sc.get("tokens") or 0)
                    tgt["cost_usd"] += float(sc.get("cost_usd") or 0.0)
                    tgt["cost_2027_usd"] += float(sc.get("cost_2027_usd") or 0.0)
                    if tgt["id"] not in phase_touched:
                        tgt["agents"] += 1
                        phase_touched.add(tgt["id"])

        for ag in enriched_agents:
            if ag["conversation_id"] == root_id:
                continue
            src = ag.get("source")
            sp = ag.get("spawn_step_index")
            if src in ("bridge", "workflow"):
                _add_agent_to_phase(p_prod, ag)
            elif archive_step is not None and isinstance(sp, int) and sp >= archive_step:
                _add_agent_to_phase(p_close, ag)
            elif verif_step is not None and isinstance(sp, int) and sp >= verif_step:
                _add_agent_to_phase(p_verif, ag)
            elif first_prod_step is not None and isinstance(sp, int) and sp >= first_prod_step:
                _add_agent_to_phase(p_prod, ag)
            else:
                _add_agent_to_phase(p_conc, ag)

        phases = [p_conc, p_prod, p_verif, p_close]
        mode = "4_phases"

    for ph in phases:
        ph["cost_usd"] = round(ph["cost_usd"], 6)
        ph["cost_2027_usd"] = round(ph["cost_2027_usd"], 6)
        ph["cost_share_pct"] = (
            round((ph["cost_usd"] / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )

    return {
        "mode": mode,
        "current_phase": current_phase,
        "phases": phases,
    }


def _clean_pending_title(first_prompt: str, fallback_prompt: str) -> str:
    txt = extract_user_request_text(first_prompt)
    stripped = _GRAVITY_LEADING_AXES_RE.sub("", txt).strip()
    if stripped:
        return stripped if len(stripped) <= 90 else stripped[:87] + "..."
    return _clean_user_prompt(fallback_prompt or first_prompt)


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
    store_dir: Optional[Union[Path, str]] = None,
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
    resolved_project = str(project or "").strip()
    if not resolved_project:
        meta_entry = load_session_meta(store_dir).get(root_id) or {}
        resolved_project = str(meta_entry.get("project") or "").strip()
    if not resolved_project:
        resolved_project = resolve_project_from_bridge_or_transcript(
            root_id,
            root_node["transcript_path"],
            bridge_db=bridge_db_path,
        )

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
    root_workspace = resolved_project or "root"

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
    total_invalid_steps = total_shell_nonzero_steps = 0
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
        total_invalid_steps += ag["invalid_steps"]
        total_shell_nonzero_steps += ag["shell_nonzero_steps"]
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
    cold_cache_calls_total = sum(a["cold_cache_calls"] for a in enriched_agents)
    cold_cache_in_total = sum(a["cold_cache_input_tokens"] for a in enriched_agents)
    cold_cache_cost_total = sum(a["_raw_cold_cache_cost_usd"] for a in enriched_agents)
    cold_cache_share_pct = (
        round((cold_cache_cost_total / total_cost_float) * 100.0, 2)
        if total_cost_float > 0
        else 0.0
    )
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
    milestones = _compute_session_milestones(
        root_id=root_id,
        root_transcript_path=root_node["transcript_path"],
        window_info=window_info,
        enriched_agents=enriched_agents,
        bridge_pending_jobs=bridge_pending_jobs,
        total_cost_float=total_cost_float,
        is_archived=False,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "label": label or f"run-{root_id[:8]}",
        "root_conversation_id": root_id,
        "project": resolved_project,
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
            "total_invalid_steps": total_invalid_steps,
            "total_shell_nonzero_steps": total_shell_nonzero_steps,
            "total_anomalies": total_error_steps + total_invalid_steps + total_shell_nonzero_steps,
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
        "milestones": milestones,
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
            "cold_cache_waste": {
                "calls": cold_cache_calls_total,
                "input_tokens": cold_cache_in_total,
                "cost_usd": round(cold_cache_cost_total, 6),
                "share_of_total_cost_pct": cold_cache_share_pct,
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


def _resolve_store_dir_from_history(history_path: Optional[Union[Path, str]]) -> Optional[Path]:
    if history_path is None:
        return None
    hp = Path(history_path).expanduser()
    return hp.parent.parent if hp.parent.name == "data" else hp.parent


def _compute_pending_milestones(
    entries: List[Dict[str, Any]],
    start_line_idx: int,
    end_line_idx: Optional[int],
    active_links: List[Dict[str, Any]],
    pending_jobs: List[Dict[str, Any]],
    has_workflow_children: bool,
) -> Dict[str, Any]:
    win_entries = [
        e
        for e in entries
        if e["line_idx"] >= start_line_idx and (end_line_idx is None or e["line_idx"] < end_line_idx)
    ]
    has_architect = any(e.get("is_architect") for e in win_entries)
    has_bridge = bool(active_links)
    if not has_bridge and not has_architect:
        return {
            "mode": "2_phases",
            "current_phase": "Production",
            "phases": [
                {"id": "production", "label": "Production", "status": "active"},
                {"id": "cloture", "label": "Clôture / Archivé", "status": "pending"},
            ],
        }
    link_steps = [int(lnk["spawn_step_index"]) for lnk in active_links if isinstance(lnk.get("spawn_step_index"), int)]
    first_prod = min(link_steps) if link_steps else None
    has_verif = any(
        e.get("has_push_or_verif")
        and (first_prod is None or (isinstance(e.get("step_index"), int) and e["step_index"] > first_prod))
        for e in win_entries
    )
    if not has_verif and has_bridge and not pending_jobs and link_steps:
        max_b = max(link_steps)
        has_verif = any(
            e.get("is_planner") and isinstance(e.get("step_index"), int) and e["step_index"] > max_b
            for e in win_entries
        )
    has_arch_close = any(e.get("is_architect_archive") for e in win_entries)

    if has_arch_close:
        statuses = ("done", "done", "done", "active")
        cur = "Clôture / Archivé"
    elif has_verif:
        statuses = ("done", "done", "active", "pending")
        cur = "Vérification & Push"
    elif first_prod is not None or has_bridge or has_workflow_children or pending_jobs:
        statuses = ("done", "active", "pending", "pending")
        cur = "Production"
    else:
        statuses = ("active", "pending", "pending", "pending")
        cur = "Conception"

    labels = (
        ("conception", "Conception"),
        ("production", "Production"),
        ("verification", "Vérification & Push"),
        ("cloture", "Clôture / Archivé"),
    )
    return {
        "mode": "4_phases",
        "current_phase": cur,
        "phases": [
            {"id": pid, "label": lbl, "status": st}
            for (pid, lbl), st in zip(labels, statuses)
        ],
    }


def list_pending_sessions(
    brain_dir: Union[Path, str],
    history_path: Optional[Union[Path, str]] = None,
    *,
    bridge_db_path: Optional[Union[Path, str]] = None,
    runs_jsonl_paths: Optional[List[Path]] = None,
    store_dir: Optional[Union[Path, str]] = None,
    include_ignored: bool = False,
) -> List[Dict[str, Any]]:
    """Liste les sessions Gravity racines ayant de l'activité LLM non encore archivée."""
    base = Path(brain_dir)
    if not base.is_dir():
        return []

    eff_store = Path(store_dir).expanduser() if store_dir is not None else _resolve_store_dir_from_history(history_path)
    archived_keys = _load_archived_keys(history_path)
    full_archived_cids = {cid for cid, wstep in archived_keys if wstep is None}
    session_meta = load_session_meta(eff_store)
    active_captures = load_active_captures(eff_store)
    ignored_map = load_ignored_sessions(eff_store)

    known_session_ids = _collect_known_gravity_session_ids(runs_jsonl_paths)
    bridge_target_ids = load_bridge_target_conversation_ids(bridge_db_path)
    bridge_caller_ids = load_bridge_caller_conversation_ids(bridge_db_path)
    known_child_ids = load_known_child_conversation_ids(base, bridge_db_path=bridge_db_path)
    pending: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        tx = _get_transcript_index(tpath)
        if not tx["exists"] or tx["first_prompt_raw"] is None:
            continue

        first_prompt_raw = str(tx["first_prompt_raw"])
        has_gravity_cmd = bool(tx["has_gravity_cmd"])

        is_bridge_child = is_bridge_child_conversation(
            cdir.name,
            first_prompt_raw=first_prompt_raw,
            bridge_target_ids=bridge_target_ids,
        )
        is_child = is_bridge_child or (cdir.name in known_child_ids)
        if is_child:
            continue

        is_gravity = (
            has_gravity_cmd
            or (cdir.name in known_session_ids)
            or (cdir.name in bridge_caller_ids)
            or (cdir.name in active_captures)
        )
        if not is_gravity:
            continue

        if cdir.name in full_archived_cids:
            continue

        ign_entry = ignored_map.get(cdir.name)
        if ign_entry is not None and not include_ignored:
            continue

        cap_entry = active_captures.get(cdir.name)
        cap_from_step = cap_entry.get("start_step") if isinstance(cap_entry, dict) else None
        win = resolve_gravity_window(
            tpath,
            current_prompt_is_metrics=False,
            from_step=cap_from_step if isinstance(cap_from_step, int) else None,
        )
        if win.get("window_empty"):
            continue
        if (cdir.name, win.get("start_step_index")) in archived_keys:
            continue
        if win.get("mode") == "full_conversation" and (cdir.name, None) in archived_keys:
            continue

        start_idx = int(win.get("start_line_idx") or 0)
        end_idx = win.get("end_line_idx")
        entries: List[Dict[str, Any]] = tx["entries"]
        llm_calls = 0
        last_ts: Optional[str] = None
        last_status = ""
        for e in entries:
            lidx = e["line_idx"]
            if lidx < start_idx:
                continue
            if end_idx is not None and lidx >= end_idx:
                break
            if e.get("created_at"):
                last_ts = str(e["created_at"])
            if e.get("status"):
                last_status = str(e["status"])
            if e.get("is_planner"):
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
        wf_children = discover_workflow_children(cdir.name, base)
        milestones = _compute_pending_milestones(
            entries,
            start_idx,
            end_idx,
            active_links,
            pending_jobs,
            bool(wf_children),
        )

        meta_entry = session_meta.get(cdir.name) or {}
        resolved_proj = (
            str((cap_entry or {}).get("project") or "").strip()
            or str(meta_entry.get("project") or "").strip()
            or resolve_project_from_bridge_or_transcript(cdir.name, tpath, bridge_db=bridge_db_path)
        )

        mtime = float(tx["mtime"] or 0.0)
        updated_at = last_ts or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        title = _clean_pending_title(win.get("first_prompt") or "", first_prompt_raw)
        is_active = bool(cap_entry is not None or pending_jobs or last_status == "RUNNING")

        item: Dict[str, Any] = {
            "conversation_id": cdir.name,
            "title": title,
            "project": resolved_proj,
            "window_mode": win["mode"],
            "window_start_step": win["start_step_index"],
            "started_at": win["start_ts"],
            "updated_at": updated_at,
            "llm_calls": llm_calls,
            "is_active": is_active,
            "active_capture": dict(cap_entry) if isinstance(cap_entry, dict) else None,
            "current_phase": milestones["current_phase"],
            "milestones": milestones,
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
            "ignored": ign_entry is not None,
            "_mtime": mtime,
        }
        if ign_entry is not None:
            item["ignored_at"] = ign_entry.get("ignored_at") or ""
            item["ignored_reason"] = ign_entry.get("reason") or ""
        pending.append(item)

    pending.sort(key=lambda item: (-item["_mtime"], item["conversation_id"]))
    for item in pending:
        item.pop("_mtime", None)
    return pending
