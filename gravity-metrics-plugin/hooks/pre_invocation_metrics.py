#!/usr/bin/env python3
"""Hook PreInvocation Gravity Metrics : capture `/gravity-metrics` avant le LLM et déclenche l'ouverture du widget MCP App."""

import json
from pathlib import Path
import re
import shlex
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


def _split_tokens(raw_args: str) -> List[str]:
    text = (raw_args or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _parse_capture_args(tokens: List[str]) -> Dict[str, Any]:
    label = ""
    note = ""
    profile = "2026_nov_standard"
    full = False
    positionals: List[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-l", "--label"):
            if i + 1 >= len(tokens):
                raise ValueError(f"Option {tok} sans valeur.")
            label = tokens[i + 1]
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
        elif tok.startswith("-"):
            raise ValueError(f"Option inconnue : `{tok}`.")
        else:
            positionals.append(tok)
            i += 1

    if not label and positionals:
        label = " ".join(positionals)

    return {
        "label": label.strip(),
        "note": note.strip(),
        "profile": profile.strip(),
        "full": full,
    }


def _resolve_transcript_path(
    payload: Dict[str, Any], brain_dir: Path, cid: Optional[str]
) -> Optional[Path]:
    tpath_raw = first_not_none(payload.get("transcriptPath"), payload.get("transcript_path"))
    if isinstance(tpath_raw, str) and tpath_raw.strip():
        return Path(tpath_raw.strip()).expanduser()
    if cid:
        return brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
    return None


def _is_handoff_already_active(
    payload: Dict[str, Any],
    brain_dir: Path,
    cid: Optional[str],
    metrics_marker: str,
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
            if metrics_marker in content or (
                isinstance(meta_obj, dict)
                and metrics_marker in json.dumps(meta_obj, ensure_ascii=False)
            ):
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
) -> Optional[Tuple[str, str]]:
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))

    from metrics import (
        METRICS_MARKER,
        collect_run_metrics,
        default_brain_dir,
        read_gravity_profile_summary,
        record_session_metrics,
        render_chat_summary_markdown,
        resolve_store_paths,
    )

    tokens = _split_tokens(rest)
    opts = _parse_capture_args(tokens)
    store_dir, history_path = resolve_store_paths()
    brain_dir = resolve_brain_dir(payload) or default_brain_dir()
    cid = resolve_conversation_id(payload)

    if _is_handoff_already_active(payload, brain_dir, cid, METRICS_MARKER):
        return None

    ws_root = resolve_workspace_root(payload)
    project = ws_root.name if ws_root else ""
    gravity_profile = read_gravity_profile_summary(ws_root)

    if not cid:
        tool_args: Dict[str, Any] = {"view": "history"}
        if project:
            tool_args["project"] = project
        md_out = (
            "### Gravity Metrics — Ouverture du widget\n\n"
            "Identifiant de conversation absent du contexte ; ouverture du tableau de bord."
        )
        sys_msg = _build_handoff_system_message(
            METRICS_MARKER,
            summary_line="Aucune capture possible sans `conversationId`.",
            tool_args=tool_args,
        )
        return md_out, sys_msg

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
        pricing_profile=opts["profile"],
        label=opts["label"],
        gravity_window=not opts["full"],
        current_prompt_is_metrics=True,
        current_prompt=raw_input,
        full_conversation=opts["full"],
        project=project,
        gravity_profile=gravity_profile,
    )

    if metrics["summary"]["total_llm_calls"] == 0:
        tool_args = {"view": "history"}
        if project:
            tool_args["project"] = project
        scope_note = f" sur le projet `{project}`" if project else ""
        md_out = (
            "### Gravity Metrics — Ouverture du widget\n\n"
            f"Aucune nouvelle activité LLM à capturer depuis la dernière collecte ; "
            f"ouverture du tableau de bord{scope_note}."
        )
        sys_msg = _build_handoff_system_message(
            METRICS_MARKER,
            summary_line="Aucune nouvelle activité LLM à enregistrer.",
            tool_args=tool_args,
        )
        return md_out, sys_msg

    saved_rec, _ = record_session_metrics(
        metrics,
        store_dir=store_dir,
        history_path=history_path,
        label=opts["label"],
        note=opts["note"],
        auto_label=not bool(opts["label"]),
    )
    md_out = render_chat_summary_markdown(
        metrics,
        saved_rec,
        store_dir,
        reports_written=True,
    )
    tool_args = {"target": saved_rec["label"], "view": "detail"}
    if project:
        tool_args["project"] = project
    sys_msg = _build_handoff_system_message(
        METRICS_MARKER,
        summary_line=f"Session `{saved_rec['label']}` déjà capturée et résumée à l'écran.",
        tool_args=tool_args,
        repeat_warning="sans répéter le tableau déjà affiché",
    )
    return md_out, sys_msg


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
    try:
        result = _run_metrics_command(payload, raw_input, rest)
    except Exception as exc:
        emit_user_markdown(
            f"### Gravity Metrics — Erreur\n\n"
            f"`{exc}`\n\n"
            f"Syntaxe : `/gravity-metrics [label] [-n \"note\"] [--full]`"
        )
        return 0

    if result is None:
        emit_passthrough()
        return 0

    markdown_out, sys_msg = result
    emit_handoff_markdown(markdown_out, system_message=sys_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
