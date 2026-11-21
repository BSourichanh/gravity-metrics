#!/usr/bin/env python3
"""Utilitaires légers pour les hooks Antigravity de Gravity Metrics (100 % stdlib Python 3)."""

import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Dict, List, Optional


def configure_utf8_stdio() -> None:
    """Force stdin/stdout/stderr en UTF-8 (tolère Windows cp1252 / PYTHONUTF8=0)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def read_stdin_text(timeout_s: float = 1.0) -> str:
    """Lit stdin sans bloquer si aucun flux n'est fourni."""
    configure_utf8_stdio()
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    buf: List[str] = []

    def _reader() -> None:
        try:
            buf.append(sys.stdin.read())
        except Exception:
            pass

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()
    reader_thread.join(timeout=timeout_s)
    return buf[0].strip() if buf else ""


def emit_json(payload: Dict[str, Any]) -> None:
    """Émet un objet JSON sur stdout et vide le buffer."""
    configure_utf8_stdio()
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def emit_passthrough() -> None:
    """Laisse l'exécution du modèle se poursuivre sans injection."""
    emit_json({})


def emit_user_markdown(markdown_text: str, *, system_message: str = "") -> None:
    """Affiche du Markdown directement dans le chat sans appeler le LLM."""
    emit_json(
        {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": system_message,
                        "metadata": {"user_markdown": markdown_text},
                    }
                }
            ],
            "terminationBehavior": "terminate",
        }
    )


def first_not_none(*values: Any) -> Any:
    """Retourne le premier argument non nul."""
    for val in values:
        if val is not None:
            return val
    return None


def resolve_workspace_root(data: Optional[Dict[str, Any]]) -> Optional[Path]:
    """Résout le dossier du projet cible depuis le payload hook sans retomber sur le dossier plugin."""
    if isinstance(data, dict):
        cwd = data.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return Path(cwd.strip()).expanduser()
        ws_paths = data.get("workspacePaths")
        if isinstance(ws_paths, list) and ws_paths and isinstance(ws_paths[0], str) and ws_paths[0].strip():
            return Path(ws_paths[0].strip()).expanduser()
    env_ws = os.environ.get("GRAVITY_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser()
    return None


def resolve_brain_dir(data: Optional[Dict[str, Any]]) -> Optional[Path]:
    """Déduit le dossier `brain` depuis `transcriptPath` si disponible (App / CLI / IDE)."""
    if isinstance(data, dict):
        tpath_raw = first_not_none(data.get("transcriptPath"), data.get("transcript_path"))
        if isinstance(tpath_raw, str) and tpath_raw.strip():
            tp = Path(tpath_raw.strip()).expanduser()
            if (
                len(tp.parents) >= 4
                and tp.parent.name == "logs"
                and tp.parent.parent.name == ".system_generated"
            ):
                return tp.parents[3]
    env_brain = os.environ.get("ANTIGRAVITY_BRAIN_DIR", "").strip()
    if env_brain:
        return Path(env_brain).expanduser()
    return None


def resolve_conversation_id(data: Optional[Dict[str, Any]]) -> Optional[str]:
    """Résout l'UUID de conversation depuis le payload ou `transcriptPath`."""
    if isinstance(data, dict):
        cid = first_not_none(
            data.get("conversationId"),
            data.get("sessionId"),
            data.get("conversation_id"),
        )
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
        tpath_raw = first_not_none(data.get("transcriptPath"), data.get("transcript_path"))
        if isinstance(tpath_raw, str) and tpath_raw.strip():
            tp = Path(tpath_raw.strip()).expanduser()
            if (
                len(tp.parents) >= 3
                and tp.parent.name == "logs"
                and tp.parent.parent.name == ".system_generated"
            ):
                return tp.parents[2].name
    return None
