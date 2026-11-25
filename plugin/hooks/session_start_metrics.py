#!/usr/bin/env python3
"""Hook SessionStart Gravity Metrics : enregistre la provenance workspace/projet (0 token, exit 0 garanti)."""

import json
from pathlib import Path
import sys

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
for _p in (str(HOOKS_DIR), str(PLUGIN_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _is_subagent_worktree(ws_path_str: str) -> bool:
    """Retourne True si le chemin résolu correspond à un worktree de sous-agent Antigravity."""
    norm_raw = ws_path_str.lower()
    norm_posix = (ws_path_str.replace("\\", "/").rstrip("/") + "/").lower()
    return (
        "/.gemini/antigravity/worktrees/" in norm_posix
        or "/gravity-worktrees/" in norm_posix
        or "/worktrees/" in norm_posix
        or "\\worktrees\\" in (norm_raw + "\\")
    )


def main() -> int:
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_passthrough,
            read_stdin_text,
            resolve_conversation_id,
            resolve_workspace_root,
        )
        from metrics.lifecycle import record_session_meta
        from metrics.store import resolve_store_paths
    except Exception:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
        return 0

    configure_utf8_stdio()
    try:
        raw = read_stdin_text()
        if not raw:
            emit_passthrough()
            return 0
        try:
            payload = json.loads(raw)
        except Exception:
            emit_passthrough()
            return 0
        if not isinstance(payload, dict):
            emit_passthrough()
            return 0

        cid = resolve_conversation_id(payload)
        ws_root = resolve_workspace_root(payload)
        if cid and ws_root is not None:
            try:
                cwd_str = str(ws_root.resolve())
            except OSError:
                cwd_str = str(ws_root)
            if not _is_subagent_worktree(cwd_str):
                proj_name = ws_root.name or Path(cwd_str).name
                store_dir, _ = resolve_store_paths()
                record_session_meta(
                    cid,
                    cwd=cwd_str,
                    project=proj_name,
                    store_dir=store_dir,
                )
        emit_passthrough()
    except Exception:
        try:
            sys.stdout.write("{}\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
