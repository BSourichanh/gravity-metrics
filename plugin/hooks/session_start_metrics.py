#!/usr/bin/env python3
"""Hook SessionStart Gravity Metrics : enregistre la provenance workspace/projet (0 token, exit 0 garanti)."""

import importlib
import json
from pathlib import Path
import sys

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_hook_rt = importlib.import_module("_shared.hook_runtime")
_tr_reader = importlib.import_module("_shared.transcript_reader")


def _is_subagent_worktree(ws_path_str: str) -> bool:
    """Retourne True si le chemin résolu correspond à un worktree de sous-agent Antigravity."""
    return bool(_tr_reader.is_worktree_path(ws_path_str))


def main() -> int:
    try:
        from metrics.lifecycle import record_session_meta
        from metrics.store import resolve_store_paths
    except Exception:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
        return 0

    _hook_rt.configure_utf8_stdio()
    try:
        raw = _hook_rt.read_stdin_text()
        if not raw:
            _hook_rt.emit_passthrough()
            return 0
        try:
            payload = json.loads(raw)
        except Exception:
            _hook_rt.emit_passthrough()
            return 0
        if not isinstance(payload, dict):
            _hook_rt.emit_passthrough()
            return 0

        cid = _hook_rt.resolve_conversation_id(payload)
        ws_root = _hook_rt.resolve_workspace_root(payload)
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
        _hook_rt.emit_passthrough()
    except Exception:
        try:
            sys.stdout.write("{}\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
