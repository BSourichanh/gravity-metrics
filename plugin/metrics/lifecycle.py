#!/usr/bin/env python3
"""Gestion atomique des états de cycle de vie sous $GRAVITY_METRICS_HOME (100 % stdlib)."""

import contextlib
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterator, Optional, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_atomic_io = importlib.import_module("_shared.atomic_io")
_file_lock = importlib.import_module("_shared.file_lock")
_tr_reader = importlib.import_module("_shared.transcript_reader")

from .pricing import default_store_dir

MAX_SESSION_META_ENTRIES = 500


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def state_file_lock(
    target_path: Union[Path, str],
    timeout_s: float = 5.0,
) -> Iterator[None]:
    """Verrou fichier inter-processus fail-closed (Windows/POSIX) via _shared.file_lock."""
    lock_path = Path(target_path).with_suffix(".lock")
    with _file_lock.exclusive_file_lock(lock_path, timeout_s=timeout_s):
        yield


def _read_json_dict(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_io.write_json_atomic(path, payload, indent=2, ensure_ascii=False)


def _resolve_dir(store_dir: Optional[Union[Path, str]]) -> Path:
    return Path(store_dir).expanduser() if store_dir is not None else default_store_dir()


def _is_worktree_path(cwd: str) -> bool:
    """Retourne True si `cwd` correspond à un répertoire worktree temporaire."""
    return bool(_tr_reader.is_worktree_path(cwd))


def load_session_meta(store_dir: Optional[Union[Path, str]] = None) -> Dict[str, Dict[str, Any]]:
    """Retourne la carte `{conversation_id: {cwd, project, started_at, updated_at}}`."""
    s_dir = _resolve_dir(store_dir)
    raw = _read_json_dict(s_dir / "session_meta.json")
    sessions = raw.get("sessions")
    if not isinstance(sessions, dict):
        return {}
    return {
        str(k): dict(v)
        for k, v in sessions.items()
        if isinstance(k, str)
        and k.strip()
        and isinstance(v, dict)
        and not _is_worktree_path(str(v.get("cwd") or ""))
    }


def record_session_meta(
    conversation_id: str,
    *,
    cwd: str = "",
    project: str = "",
    store_dir: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Enregistre ou met à jour la provenance workspace d'une conversation dans `session_meta.json`."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return {}
    clean_cwd = str(cwd or "").strip()
    if _is_worktree_path(clean_cwd):
        return {}
    s_dir = _resolve_dir(store_dir)
    meta_path = s_dir / "session_meta.json"
    now_iso = _now_iso_utc()
    clean_proj = str(project or "").strip()
    if not clean_proj and clean_cwd:
        clean_proj = Path(clean_cwd).name

    with state_file_lock(meta_path):
        raw = _read_json_dict(meta_path)
        raw_sessions = raw.get("sessions") if isinstance(raw.get("sessions"), dict) else {}
        sessions = {
            str(k): v
            for k, v in raw_sessions.items()
            if isinstance(k, str)
            and k.strip()
            and isinstance(v, dict)
            and not _is_worktree_path(str(v.get("cwd") or ""))
        }
        prev = sessions.get(cid) if isinstance(sessions.get(cid), dict) else {}
        entry = {
            "cwd": clean_cwd or str(prev.get("cwd") or ""),
            "project": clean_proj or str(prev.get("project") or ""),
            "started_at": str(prev.get("started_at") or now_iso),
            "updated_at": now_iso,
        }
        sessions[cid] = entry
        if len(sessions) > MAX_SESSION_META_ENTRIES:
            ordered = sorted(
                sessions.items(),
                key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
                reverse=True,
            )[:MAX_SESSION_META_ENTRIES]
            sessions = dict(ordered)
        _atomic_write_json(meta_path, {"schema_version": 1, "sessions": sessions})
    return entry


def next_step_index_in_transcript(
    transcript_path: Optional[Union[Path, str]],
) -> int:
    """Retourne `max(step_index) + 1` depuis un `transcript.jsonl` (repli `0` si absent/illisible)."""
    return int(_tr_reader.next_step_index(transcript_path))


def _normalize_user_prompt(text: Optional[str]) -> str:
    """Normalise un prompt utilisateur en retirant métadonnées/balises et en compressant les espaces."""
    return str(_tr_reader.normalize_user_prompt(text))


def current_turn_start_step_in_transcript(
    transcript_path: Optional[Union[Path, str]],
    *,
    expected_prompt: Optional[str] = None,
) -> int:
    """Retourne le `step_index` du `USER_INPUT` courant (ancré par `expected_prompt` si fourni)."""
    return int(_tr_reader.current_turn_start_step(transcript_path, expected_prompt=expected_prompt))


def load_active_captures(store_dir: Optional[Union[Path, str]] = None) -> Dict[str, Dict[str, Any]]:
    """Retourne les captures actives démarrées via `/gravity-metrics start`."""
    s_dir = _resolve_dir(store_dir)
    raw = _read_json_dict(s_dir / "active_captures.json")
    captures = raw.get("captures")
    if not isinstance(captures, dict):
        return {}
    return {
        str(k): dict(v)
        for k, v in captures.items()
        if isinstance(k, str) and k.strip() and isinstance(v, dict)
    }


def start_active_capture(
    conversation_id: str,
    *,
    start_step: int,
    label: str = "",
    project: str = "",
    source: str = "manual",
    store_dir: Optional[Union[Path, str]] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Enregistre une fenêtre de capture active pour `conversation_id` (idempotente si `overwrite=False`)."""
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id requis pour démarrer une capture.")
    s_dir = _resolve_dir(store_dir)
    cap_path = s_dir / "active_captures.json"
    now_iso = _now_iso_utc()
    entry = {
        "start_step": max(0, int(start_step)),
        "label": str(label or "").strip(),
        "project": str(project or "").strip(),
        "source": str(source or "manual").strip() or "manual",
        "started_at": now_iso,
        "updated_at": now_iso,
    }
    with state_file_lock(cap_path):
        raw = _read_json_dict(cap_path)
        captures = raw.get("captures") if isinstance(raw.get("captures"), dict) else {}
        if not overwrite and isinstance(captures.get(cid), dict):
            return dict(captures[cid])
        captures[cid] = entry
        _atomic_write_json(cap_path, {"schema_version": 1, "captures": captures})
    return entry


def pop_active_capture(
    conversation_id: str,
    *,
    store_dir: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Retire et retourne la capture active de `conversation_id` si elle existe."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return None
    s_dir = _resolve_dir(store_dir)
    cap_path = s_dir / "active_captures.json"
    if not cap_path.is_file():
        return None
    with state_file_lock(cap_path):
        raw = _read_json_dict(cap_path)
        captures = raw.get("captures") if isinstance(raw.get("captures"), dict) else {}
        removed = captures.pop(cid, None)
        if removed is not None:
            _atomic_write_json(cap_path, {"schema_version": 1, "captures": captures})
    return dict(removed) if isinstance(removed, dict) else None


def load_ignored_sessions(store_dir: Optional[Union[Path, str]] = None) -> Dict[str, Dict[str, Any]]:
    """Retourne les sessions masquées depuis `ignored.json`."""
    s_dir = _resolve_dir(store_dir)
    raw = _read_json_dict(s_dir / "ignored.json")
    ignored = raw.get("ignored")
    if not isinstance(ignored, dict):
        return {}
    return {
        str(k): dict(v)
        for k, v in ignored.items()
        if isinstance(k, str) and k.strip() and isinstance(v, dict)
    }


def ignore_session(
    conversation_id: str,
    *,
    reason: str = "",
    store_dir: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Ajoute `conversation_id` dans `ignored.json`."""
    cid = str(conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id requis pour ignorer une session.")
    s_dir = _resolve_dir(store_dir)
    ign_path = s_dir / "ignored.json"
    entry = {
        "ignored_at": _now_iso_utc(),
        "reason": str(reason or "").strip(),
    }
    with state_file_lock(ign_path):
        raw = _read_json_dict(ign_path)
        ignored = raw.get("ignored") if isinstance(raw.get("ignored"), dict) else {}
        ignored[cid] = entry
        _atomic_write_json(ign_path, {"schema_version": 1, "ignored": ignored})
    return entry


def unignore_session(
    conversation_id: str,
    *,
    store_dir: Optional[Union[Path, str]] = None,
) -> bool:
    """Retire `conversation_id` de `ignored.json`. Retourne True si présent."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return False
    s_dir = _resolve_dir(store_dir)
    ign_path = s_dir / "ignored.json"
    if not ign_path.is_file():
        return False
    with state_file_lock(ign_path):
        raw = _read_json_dict(ign_path)
        ignored = raw.get("ignored") if isinstance(raw.get("ignored"), dict) else {}
        removed = ignored.pop(cid, None)
        if removed is not None:
            _atomic_write_json(ign_path, {"schema_version": 1, "ignored": ignored})
            return True
    return False
