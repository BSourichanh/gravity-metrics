#!/usr/bin/env python3
"""Hook PreInvocation Gravity Metrics : cycle hybride /gravity-metrics."""

import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from hook_runtime import (  # noqa: E402
    configure_utf8_stdio,
    emit_handoff_markdown,
    emit_passthrough,
    emit_user_markdown,
    first_not_none,
    read_stdin_text,
    resolve_brain_dir,
    resolve_conversation_id,
    resolve_workspace_root,
)

_STRIP_META_RE = re.compile(
    r"<ADDITIONAL_METADATA>.*?(?:</ADDITIONAL_METADATA>|$)"
    r"|<USER_SETTINGS_CHANGE>.*?(?:</USER_SETTINGS_CHANGE>|$)"
    r"|</?USER_REQUEST>",
    re.DOTALL,
)
_METRICS_CMD_RE = re.compile(
    r"^/gravity-metrics(?::gravity-metrics)?(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)


def _resolve_transcript_path(
    payload: Dict[str, Any], brain_dir: Path, cid: Optional[str]
) -> Optional[Path]:
    tpath_raw = first_not_none(payload.get("transcriptPath"), payload.get("transcript_path"))
    if isinstance(tpath_raw, str) and tpath_raw.strip():
        return Path(tpath_raw.strip()).expanduser()
    if cid:
        return brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
    return None


def _next_step_index_in_transcript(tpath: Optional[Path]) -> int:
    if tpath is None or not tpath.is_file():
        return 0
    max_step = -1
    try:
        for raw_line in tpath.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("step_index"), int):
                if obj["step_index"] > max_step:
                    max_step = obj["step_index"]
    except OSError:
        return 0
    return max_step + 1


def _is_handoff_already_active(
    payload: Dict[str, Any],
    brain_dir: Path,
    cid: Optional[str],
    markers: Tuple[str, ...],
) -> bool:
    """Évite de ré-injecter le handoff sur la 2e invocation LLM d'un même tour `/gravity-metrics`."""
    tpath = _resolve_transcript_path(payload, brain_dir, cid)
    if tpath is None or not tpath.is_file():
        return False

    entries: List[Dict[str, Any]] = []
    for raw_line in tpath.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            entries.append(obj)

    last_user_idx = -1
    for idx, entry in enumerate(entries):
        if entry.get("type") == "USER_INPUT":
            last_user_idx = idx
    if last_user_idx < 0:
        return False

    last_user_raw = str(entries[last_user_idx].get("content") or "")
    last_user_clean = re.sub(r"\s+", " ", _STRIP_META_RE.sub("", last_user_raw)).strip()
    last_user_is_metrics = _METRICS_CMD_RE.match(last_user_clean) is not None

    tail = entries[last_user_idx + 1 :]
    has_marker = False
    planners: List[Dict[str, Any]] = []
    for entry in tail:
        stype = entry.get("type")
        if stype == "SYSTEM_MESSAGE":
            content = str(entry.get("content") or "")
            meta_obj = entry.get("metadata")
            meta_dump = json.dumps(meta_obj, ensure_ascii=False) if isinstance(meta_obj, dict) else ""
            if any(m in content or (meta_dump and m in meta_dump) for m in markers):
                has_marker = True
        elif stype == "PLANNER_RESPONSE":
            planners.append(entry)

    if planners:
        last_p = planners[-1]
        turn_finished = last_p.get("status") == "DONE" and not (last_p.get("tool_calls") or [])
        if has_marker:
            return not turn_finished
        if last_user_is_metrics and not turn_finished:
            return True
        return False

    if not has_marker:
        return False

    inv_num = payload.get("invocationNum")
    return isinstance(inv_num, int) and inv_num > 0


def _build_handoff_system_message(
    marker: str,
    *,
    summary_line: str,
    tool_args: Dict[str, Any],
    repeat_warning: str = "",
) -> str:
    args_json = json.dumps(tool_args, ensure_ascii=False)
    lines = [
        marker,
        summary_line,
        (
            "Appelle immédiatement l'outil MCP `open_metrics_dashboard` du serveur "
            f"`gravity-metrics_dashboard` avec exactement ces arguments JSON : {args_json} "
            "(sans lire `METRICS.md`, `history.jsonl` ni les scripts du hook au préalable)."
        ),
        "Réponds ensuite en une seule phrase courte confirmant l'ouverture du widget"
        + (f" ({repeat_warning})" if repeat_warning else "")
        + ".",
    ]
    return "\n".join(lines)


def _run_metrics_command(
    payload: Dict[str, Any], raw_input: str, rest: str
) -> Optional[Tuple[str, str, str]]:
    """Retourne `(mode: 'handoff'|'terminate', markdown_out, sys_msg)` ou None pour passthrough."""
    if rest.startswith("/"):
        return None

    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))

    from metrics import (
        METRICS_MARKER,
        METRICS_OPEN_MARKER,
        PendingBridgeJobsError,
        archive_conversation,
        classify_metrics_prompt,
        collect_run_metrics,
        default_brain_dir,
        list_pending_sessions,
        load_active_captures,
        load_history,
        pop_active_capture,
        read_gravity_profile_summary,
        record_session_metrics,
        render_chat_summary_markdown,
        render_pending_markdown,
        resolve_conversation_id_in_brain,
        resolve_store_paths,
        start_active_capture,
    )

    parsed = classify_metrics_prompt(raw_input)
    action = parsed.get("action")
    if action == "passthrough":
        return None
    if action == "error":
        raise ValueError(parsed.get("error") or "Syntaxe invalide.")

    store_dir, history_path = resolve_store_paths()
    brain_dir = resolve_brain_dir(payload) or default_brain_dir()
    cid = resolve_conversation_id(payload)

    if _is_handoff_already_active(payload, brain_dir, cid, (METRICS_MARKER, METRICS_OPEN_MARKER)):
        return None

    ws_root = resolve_workspace_root(payload)
    project = str(parsed.get("project") or "").strip() or (ws_root.name if ws_root else "")
    gravity_profile = read_gravity_profile_summary(ws_root)

    if action == "open":
        records = load_history(history_path)
        pending = list_pending_sessions(brain_dir, history_path, store_dir=store_dir)
        active_cap = load_active_captures(store_dir).get(cid) if cid else None
        tool_args: Dict[str, Any] = {"view": "overview"}
        if project:
            tool_args["project"] = project
        cap_info = ""
        if isinstance(active_cap, dict):
            cap_lbl = active_cap.get("label") or "capture en cours"
            cap_info = f"\n- **Capture active :** `{cap_lbl}` (depuis step `{active_cap.get('start_step', 0)}`) — utilisez `/gravity-metrics stop` pour archiver."
        scope_txt = f" · Projet : `{project}`" if project else ""
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Tableau de bord (lecture seule)\n\n"
            f"- **Sessions archivées :** `{len(records)}` · **À archiver :** `{len(pending)}`{scope_txt}"
            f"{cap_info}\n"
            "- Commandes capture : `/gravity-metrics start [label]` · `/gravity-metrics stop [label]` · `/gravity-metrics pending`"
        )
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line=(
                f"Ouverture du tableau de bord Gravity Metrics en lecture seule "
                f"({len(records)} archivée(s), {len(pending)} en attente)."
            ),
            tool_args=tool_args,
        )
        return "handoff", md_out, sys_msg

    if action == "start":
        if not cid:
            raise ValueError("Impossible de démarrer une capture sans `conversationId`.")
        tpath = _resolve_transcript_path(payload, brain_dir, cid)
        start_step = _next_step_index_in_transcript(tpath)
        cap = start_active_capture(
            cid,
            start_step=start_step,
            label=str(parsed.get("label") or ""),
            project=project,
            store_dir=store_dir,
        )
        lbl_part = f" (`{cap['label']}`)" if cap.get("label") else ""
        proj_part = f" · projet `{cap['project']}`" if cap.get("project") else ""
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            f"### Gravity Metrics — Capture démarrée{lbl_part}\n\n"
            f"- **Conversation :** `{cid[:8]}` · **Départ step :** `{cap['start_step']}`{proj_part}\n"
            "- Terminez la fenêtre avec `/gravity-metrics stop [label]` (0 token au démarrage)."
        )
        return "terminate", md_out, ""

    if action == "pending":
        pending = list_pending_sessions(brain_dir, history_path, store_dir=store_dir)
        md_out = render_pending_markdown(pending)
        tool_args = {"view": "pending"}
        if project:
            tool_args["project"] = project
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line=f"{len(pending)} session(s) Gravity en attente d'archivage affichée(s).",
            tool_args=tool_args,
            repeat_warning="sans répéter le tableau déjà affiché",
        )
        return "handoff", md_out, sys_msg

    if action == "archive":
        target_cid = resolve_conversation_id_in_brain(parsed["target"], brain_dir)
        same_conv = bool(cid and target_cid == cid)
        saved_rec, _, metrics = archive_conversation(
            target_cid,
            store_dir=store_dir,
            history_path=history_path,
            brain_dir=brain_dir,
            catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
            label=parsed["label"],
            note=parsed["note"],
            pricing_profile=parsed["profile"],
            gravity_window=not parsed["full"],
            full_conversation=parsed["full"],
            project=parsed.get("project") or (project if same_conv else ""),
            gravity_profile=gravity_profile if same_conv else "",
            force=parsed["force"],
        )
        already = bool(saved_rec.get("already_archived"))
        md_out = render_chat_summary_markdown(
            metrics,
            saved_rec,
            store_dir,
            reports_written=not already,
            already_archived=already,
        )
        tool_args = {"target": saved_rec["label"], "view": "detail"}
        if saved_rec.get("project"):
            tool_args["project"] = saved_rec["project"]
        elif project:
            tool_args["project"] = project
        sys_msg = _build_handoff_system_message(
            METRICS_MARKER,
            summary_line=(
                f"Session `{saved_rec['label']}` déjà archivée (immuable)."
                if already
                else f"Session `{saved_rec['label']}` archivée et résumée à l'écran."
            ),
            tool_args=tool_args,
            repeat_warning="sans répéter le tableau déjà affiché",
        )
        return "handoff", md_out, sys_msg

    # action == "stop" or action == "capture"
    if not cid:
        tool_args = {"view": "overview"}
        if project:
            tool_args["project"] = project
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Ouverture du widget\n\n"
            "Identifiant de conversation absent du contexte ; ouverture du tableau de bord."
        )
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line="Aucune capture possible sans `conversationId`.",
            tool_args=tool_args,
        )
        return "handoff", md_out, sys_msg

    active_cap = load_active_captures(store_dir).get(cid)
    from_step = None
    eff_label = str(parsed.get("label") or "").strip()
    eff_project = project
    if isinstance(active_cap, dict):
        if not parsed["full"] and isinstance(active_cap.get("start_step"), int):
            from_step = active_cap["start_step"]
        if not eff_label and active_cap.get("label"):
            eff_label = str(active_cap["label"]).strip()
        if not eff_project and active_cap.get("project"):
            eff_project = str(active_cap["project"]).strip()

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
        pricing_profile=parsed["profile"],
        label=eff_label,
        gravity_window=not parsed["full"],
        current_prompt_is_metrics=True,
        current_prompt=raw_input,
        full_conversation=parsed["full"],
        from_step=from_step,
        project=eff_project,
        gravity_profile=gravity_profile,
        store_dir=store_dir,
    )

    if metrics["summary"]["total_llm_calls"] == 0:
        if action == "stop":
            pop_active_capture(cid, store_dir=store_dir)
        tool_args = {"view": "overview"}
        if eff_project:
            tool_args["project"] = eff_project
        archived_for_cid = [
            r for r in load_history(history_path) if r.get("conversation_id") == cid
        ]
        if archived_for_cid:
            last_lbl = archived_for_cid[-1].get("label") or cid[:8]
            tool_args = {"target": last_lbl, "view": "detail"}
            if eff_project:
                tool_args["project"] = eff_project
            md_out = (
                f"{METRICS_OPEN_MARKER}\n"
                "### Gravity Metrics — Session déjà archivée\n\n"
                f"Aucune nouvelle activité LLM à capturer depuis l'archivage de `{last_lbl}` ; "
                "ouverture du tableau de bord."
            )
            sys_msg = _build_handoff_system_message(
                METRICS_OPEN_MARKER,
                summary_line=f"Session `{last_lbl}` déjà archivée sans nouvelle activité LLM.",
                tool_args=tool_args,
            )
            return "handoff", md_out, sys_msg
        scope_note = f" sur le projet `{eff_project}`" if eff_project else ""
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Ouverture du widget\n\n"
            f"Aucune nouvelle activité LLM à capturer depuis la dernière collecte ; "
            f"ouverture du tableau de bord{scope_note}."
        )
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line="Aucune nouvelle activité LLM à enregistrer.",
            tool_args=tool_args,
        )
        return "handoff", md_out, sys_msg

    try:
        saved_rec, _ = record_session_metrics(
            metrics,
            store_dir=store_dir,
            history_path=history_path,
            label=eff_label,
            note=parsed["note"],
            auto_label=not bool(eff_label),
            force=parsed["force"],
        )
    except PendingBridgeJobsError:
        raise

    already = bool(saved_rec.get("already_archived"))
    md_out = render_chat_summary_markdown(
        metrics,
        saved_rec,
        store_dir,
        reports_written=not already,
        already_archived=already,
    )
    tool_args = {"target": saved_rec["label"], "view": "detail"}
    if saved_rec.get("project"):
        tool_args["project"] = saved_rec["project"]
    elif eff_project:
        tool_args["project"] = eff_project
    sys_msg = _build_handoff_system_message(
        METRICS_MARKER,
        summary_line=(
            f"Session `{saved_rec['label']}` déjà archivée (immuable)."
            if already
            else f"Session `{saved_rec['label']}` déjà capturée et résumée à l'écran."
        ),
        tool_args=tool_args,
        repeat_warning="sans répéter le tableau déjà affiché",
    )
    return "handoff", md_out, sys_msg


def main() -> int:
    configure_utf8_stdio()
    raw = read_stdin_text()
    if not raw:
        emit_passthrough()
        return 0
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        emit_passthrough()
        return 0
    if not isinstance(payload, dict):
        emit_passthrough()
        return 0

    raw_input = str(
        first_not_none(
            payload.get("lastUserInput"),
            payload.get("userInput"),
            payload.get("prompt"),
            "",
        )
    )
    cleaned = re.sub(r"\s+", " ", _STRIP_META_RE.sub("", raw_input)).strip()
    match = _METRICS_CMD_RE.match(cleaned)
    if match is None:
        emit_passthrough()
        return 0

    rest = (match.group(1) or "").strip()
    if rest.startswith("/"):
        emit_passthrough()
        return 0

    try:
        result = _run_metrics_command(payload, raw_input, rest)
    except Exception as exc:
        if exc.__class__.__name__ == "PendingBridgeJobsError":
            emit_user_markdown(
                f"### Gravity Metrics — Archivage bloqué (Bridge actif)\n\n{exc}"
            )
            return 0
        emit_user_markdown(
            f"### Gravity Metrics — Erreur\n\n"
            f"`{exc}`\n\n"
            'Syntaxe : `/gravity-metrics` · `/gravity-metrics start [label]` · '
            '`/gravity-metrics stop [label] [-n "note"] [--full] [--force]` · '
            '`/gravity-metrics archive <uuid> [label] [--full] [--force]` · '
            '`/gravity-metrics pending`'
        )
        return 0

    if result is None:
        emit_passthrough()
        return 0

    mode, markdown_out, sys_msg = result
    if mode == "terminate":
        emit_user_markdown(markdown_out, system_message=sys_msg)
    else:
        emit_handoff_markdown(markdown_out, system_message=sys_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
