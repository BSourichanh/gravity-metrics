#!/usr/bin/env python3
"""Hook Stop Gravity Metrics : filet d'auto-clôture silencieuse en Validation (fail-safe, exit 0)."""

import importlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_hook_rt = importlib.import_module("_shared.hook_runtime")
_tr_reader = importlib.import_module("_shared.transcript_reader")

_ERROR_STOP_REASONS = ("error", "fail", "abort", "cancel", "interrupt")
_VALIDATION_CMD_TOKENS = ("validate.py", "audit_ecosystem.py", "ecosystem_install.py")


def _resolve_transcript_path(
    payload: Dict[str, Any], brain_dir: Path, cid: str
) -> Optional[Path]:
    tpath_raw = _hook_rt.first_not_none(
        payload.get("transcriptPath"), payload.get("transcript_path")
    )
    if isinstance(tpath_raw, str) and tpath_raw.strip():
        return Path(tpath_raw.strip()).expanduser()
    return brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"


def _payload_has_error(payload: Dict[str, Any]) -> bool:
    if payload.get("error") or payload.get("hasError") is True or payload.get("isError") is True:
        return True
    status = str(payload.get("status") or "").strip().upper()
    if status in ("ERROR", "FAILED", "INVALID", "CANCELLED", "ABORTED"):
        return True
    reason = str(
        _hook_rt.first_not_none(payload.get("stopReason"), payload.get("stop_reason"), "")
    ).strip().lower()
    return any(tok in reason for tok in _ERROR_STOP_REASONS)


def _window_error_or_validation_state(
    tpath: Path,
    *,
    cid: str,
    brain_dir: Path,
    start_line_idx: int,
    end_line_idx: Optional[int],
    win_info: Dict[str, Any],
) -> tuple[bool, bool]:
    """Retourne `(window_clean, has_validation_marker)` en un passage sur le transcript."""
    from metrics.agent_tree import (
        _discover_direct_subagent_children,
        _is_spawn_in_window,
        discover_workflow_children,
    )
    from metrics.milestone_analyzer import _analyze_milestones_window, _is_prod_subagent
    from metrics.prompt_classifier import (
        _extract_shell_command_str,
        _is_reviewer_verdict_failed,
    )
    from metrics.transcript_index import _get_transcript_index

    win_entries = [
        e for e in (_get_transcript_index(tpath).get("entries") or [])
        if e["line_idx"] >= start_line_idx
        and (end_line_idx is None or e["line_idx"] < end_line_idx)
    ]
    if not any(e.get("is_planner") for e in win_entries):
        return False, False
    if any(
        e.get("status") in ("ERROR", "INVALID") or e.get("type") == "ERROR_MESSAGE"
        for e in win_entries
    ):
        return False, False

    apply_win = bool(
        win_info.get("has_gravity_anchor") or win_info.get("mode") != "full_conversation"
    )
    wf_children = [
        c for c in discover_workflow_children(cid, brain_dir)
        if not apply_win or _is_spawn_in_window(c.get("spawn_step_index"), None, None, win_info)
    ]
    subagent_children = [
        s for s in _discover_direct_subagent_children(brain_dir / cid)
        if not apply_win
        or _is_spawn_in_window(
            s.get("spawn_step_index"),
            None,
            str(brain_dir / s["conversation_id"] / ".system_generated" / "logs" / "transcript.jsonl"),
            win_info,
        )
    ]
    prod_subagents = [s for s in subagent_children if _is_prod_subagent(s)]
    sig = _analyze_milestones_window(
        win_entries,
        root_links=[],
        bridge_agents=[],
        wf_agents=wf_children,
        prod_subagents=prod_subagents,
        pending_jobs=[],
        subagent_agents=subagent_children,
    )
    last_prod_step = sig.get("last_prod_step")

    last_planner_raw: Optional[Dict[str, Any]] = None
    last_generic_raw: Optional[Dict[str, Any]] = None
    explicit_validation_cmd = False
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
                continue
            if not isinstance(obj, dict):
                continue
            stype = obj.get("type")
            if stype == "PLANNER_RESPONSE":
                last_planner_raw = obj
                for tc in obj.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    tname = str(tc.get("name") or "").strip().lower()
                    if tname in ("run_command", "execute_command", "bash", "shell"):
                        cmd = _extract_shell_command_str(tc).lower()
                        if any(tok in cmd for tok in _VALIDATION_CMD_TOKENS) or (
                            "sync_tracking.py" in cmd and "--ack-editorial" in cmd
                        ):
                            explicit_validation_cmd = True
            elif stype == "GENERIC":
                last_generic_raw = obj
                si = obj.get("step_index")
                if (
                    isinstance(last_prod_step, int)
                    and isinstance(si, int)
                    and si > last_prod_step
                    and _tr_reader.extract_shell_nonzero_code(obj.get("content")) is not None
                ):
                    return False, False

    if _is_reviewer_verdict_failed(last_planner_raw):
        return False, False
    if last_generic_raw is not None and _tr_reader.extract_shell_nonzero_code(last_generic_raw.get("content")) is not None:
        return False, False

    return True, bool(sig.get("has_verif_activity") or explicit_validation_cmd)


def _maybe_auto_record(payload: Dict[str, Any]) -> None:
    fully_idle = _hook_rt.first_not_none(payload.get("fullyIdle"), payload.get("fully_idle"))
    if fully_idle is not True or _payload_has_error(payload):
        return
    cid = _hook_rt.resolve_conversation_id(payload)
    if not cid:
        return

    from metrics import (
        archive_conversation,
        default_brain_dir,
        load_active_captures,
        resolve_gravity_window,
        resolve_store_paths,
    )

    store_dir, history_path = resolve_store_paths()
    active_cap = load_active_captures(store_dir).get(cid)
    if not isinstance(active_cap, dict):
        return

    brain_dir = _hook_rt.resolve_brain_dir(payload) or default_brain_dir()
    tpath = _resolve_transcript_path(payload, brain_dir, cid)
    if tpath is None or not tpath.is_file():
        return

    raw_step = active_cap.get("start_step")
    from_step = raw_step if isinstance(raw_step, int) and not isinstance(raw_step, bool) else None
    win = resolve_gravity_window(tpath, current_prompt_is_metrics=False, from_step=from_step)
    if win.get("window_empty"):
        return

    start_line_idx = int(win.get("start_line_idx") or 0)
    end_line_idx = win.get("end_line_idx")
    clean_ok, has_validation = _window_error_or_validation_state(
        tpath,
        cid=cid,
        brain_dir=brain_dir,
        start_line_idx=start_line_idx,
        end_line_idx=end_line_idx,
        win_info=win,
    )
    if not (clean_ok and has_validation):
        return

    archive_conversation(
        cid,
        store_dir=store_dir,
        history_path=history_path,
        brain_dir=brain_dir,
        catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
        gravity_window=True,
        update_sealed=True,
    )


def main() -> int:
    _hook_rt.configure_utf8_stdio()
    try:
        raw = _hook_rt.read_stdin_text(timeout_s=0.5)
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                _maybe_auto_record(payload)
    except Exception:
        pass
    try:
        _hook_rt.emit_ok()
    except Exception:
        try:
            sys.stdout.write("{}\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
