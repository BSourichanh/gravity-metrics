#!/usr/bin/env python3
"""Hook PreInvocation Gravity Metrics : exécute `/gravity metrics` en 0 token (100 % stdlib)."""

import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Dict, List, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from hook_runtime import (  # noqa: E402
    configure_utf8_stdio,
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
    r"^/(?:gravity(?::gravity)?)\s+metrics(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)

_HELP_TEXT = """### Gravity Metrics — Commandes `/gravity metrics` *(0 token)*

- `/gravity metrics [label] [-n "note"] [--full] [--summary-only] [--no-html]` : mesure la fenêtre `[début /gravity → juste avant /gravity metrics]` et l'ajoute à `~/.gemini/gravity-metrics/`.
- `/gravity metrics list [--regen]` : affiche l'historique consolidé.
- `/gravity metrics inspect <label_ou_uuid>` : affiche le détail complet d'une session.
- `/gravity metrics remove <label_ou_uuid> [--purge-reports]` : retire une session de l'historique.
- `/gravity metrics add <UUID> [label] [-n "note"] [--full]` : collecte une session externe par UUID.
- `/gravity metrics help` : affiche cette aide.
"""


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
    summary_only = False
    no_html = False
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
        elif tok == "--summary-only":
            summary_only = True
            i += 1
        elif tok == "--no-html":
            no_html = True
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
        "summary_only": summary_only,
        "no_html": no_html,
    }


def _parse_simple_flags(tokens: List[str], allowed_flags: Tuple[str, ...]) -> Tuple[List[str], set]:
    pos: List[str] = []
    flags: set = set()
    for tok in tokens:
        if tok in allowed_flags:
            flags.add(tok)
        elif tok.startswith("-"):
            raise ValueError(f"Option inconnue : `{tok}`.")
        else:
            pos.append(tok)
    return pos, flags


def _run_metrics_command(payload: Dict[str, Any], raw_input: str, rest: str) -> Tuple[str, str]:
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))

    from metrics import (
        METRICS_MARKER,
        apply_remove,
        collect_run_metrics,
        default_brain_dir,
        history_lock,
        load_history,
        load_session_for_inspect,
        read_gravity_profile_summary,
        record_session_metrics,
        regenerate_views,
        render_chat_inspect_markdown,
        render_chat_list_markdown,
        render_chat_summary_markdown,
        resolve_store_paths,
    )

    tokens = _split_tokens(rest)
    sub = tokens[0].lower() if tokens else ""
    catalog_path = PLUGIN_ROOT / "config" / "pricing_catalog.json"
    store_dir, history_path = resolve_store_paths()
    brain_dir = resolve_brain_dir(payload) or default_brain_dir()

    if sub in ("help", "-h", "--help"):
        return _HELP_TEXT, ""

    if sub == "list":
        pos, flags = _parse_simple_flags(tokens[1:], ("--regen", "--no-html"))
        if pos:
            raise ValueError("Usage : `/gravity metrics list [--regen]`")
        regen = "--regen" in flags
        no_html = "--no-html" in flags
        if regen:
            with history_lock(history_path):
                records = load_history(history_path)
                regenerate_views(store_dir, records, no_html=no_html)
        else:
            records = load_history(history_path)
        return render_chat_list_markdown(records, store_dir), ""

    if sub == "inspect":
        pos, _ = _parse_simple_flags(tokens[1:], ())
        if not pos:
            raise ValueError("Usage : `/gravity metrics inspect <label_ou_uuid>`")
        target = " ".join(pos)
        metrics, slug = load_session_for_inspect(
            target,
            store_dir=store_dir,
            history_path=history_path,
            brain_dir=brain_dir,
            catalog_path=catalog_path,
        )
        return render_chat_inspect_markdown(metrics, store_dir, detail_slug=slug), ""

    if sub == "remove":
        pos, flags = _parse_simple_flags(
            tokens[1:], ("--purge-reports", "--purge-details", "--no-html")
        )
        if not pos:
            raise ValueError("Usage : `/gravity metrics remove <label_ou_uuid> [--purge-reports]`")
        purge = ("--purge-reports" in flags) or ("--purge-details" in flags)
        removed, remaining = apply_remove(
            " ".join(pos),
            store_dir=store_dir,
            history_path=history_path,
            purge_reports=purge,
            no_html="--no-html" in flags,
        )
        purge_note = " *(rapports `reports/` purgés)*" if purge else " *(rapports `reports/` conservés)*"
        return (
            f"### Gravity Metrics — Session retirée\n\n"
            f"- Supprimée : **`{removed['label']}`** (`{removed['conversation_id'][:8]}`){purge_note}\n"
            f"- Sessions restantes : **{remaining}**",
            "",
        )

    if sub == "add":
        if len(tokens) < 2:
            raise ValueError("Usage : `/gravity metrics add <UUID> [label] [-n \"note\"] [--full]`")
        target_cid = tokens[1]
        opts = _parse_capture_args(tokens[2:])
        current_cid = resolve_conversation_id(payload)
        ws_root = resolve_workspace_root(payload) if target_cid == current_cid else None
        project = ws_root.name if ws_root else ""
        gravity_profile = read_gravity_profile_summary(ws_root)
        metrics = collect_run_metrics(
            root_id=target_cid,
            brain_dir=brain_dir,
            pricing_catalog_path=catalog_path,
            pricing_profile=opts["profile"],
            label=opts["label"],
            gravity_window=not opts["full"],
            current_prompt_is_metrics=(target_cid == current_cid),
            current_prompt=raw_input if target_cid == current_cid else None,
            full_conversation=opts["full"],
            project=project,
            gravity_profile=gravity_profile,
        )
        if metrics["summary"]["total_llm_calls"] == 0:
            return (
                f"{METRICS_MARKER}\n"
                f"### Gravity Metrics — Aucune nouvelle activité à collecter\n\n"
                f"La fenêtre mesurée pour `{target_cid[:8]}` ne contient aucun appel LLM.",
                "",
            )
        saved_rec, _ = record_session_metrics(
            metrics,
            store_dir=store_dir,
            history_path=history_path,
            label=opts["label"],
            note=opts["note"],
            summary_only=opts["summary_only"],
            no_html=opts["no_html"],
            auto_label=not bool(opts["label"]),
        )
        return (
            render_chat_summary_markdown(
                metrics,
                saved_rec,
                store_dir,
                reports_written=not opts["summary_only"],
                html_written=not opts["no_html"],
            ),
            METRICS_MARKER if target_cid == current_cid else "",
        )

    opts = _parse_capture_args(tokens)
    cid = resolve_conversation_id(payload)
    if not cid:
        raise ValueError(
            "Impossible de déterminer `conversationId` depuis le contexte du hook. "
            "Utilisez `/gravity metrics add <UUID>`."
        )
    ws_root = resolve_workspace_root(payload)
    project = ws_root.name if ws_root else ""
    gravity_profile = read_gravity_profile_summary(ws_root)

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=catalog_path,
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
        return (
            f"{METRICS_MARKER}\n"
            "### Gravity Metrics — Aucune nouvelle activité à collecter\n\n"
            "La fenêtre mesurée ne contient aucun appel LLM depuis la dernière collecte "
            "(utilisez `--full` pour forcer la conversation entière).",
            "",
        )

    saved_rec, _ = record_session_metrics(
        metrics,
        store_dir=store_dir,
        history_path=history_path,
        label=opts["label"],
        note=opts["note"],
        summary_only=opts["summary_only"],
        no_html=opts["no_html"],
        auto_label=not bool(opts["label"]),
    )
    return (
        render_chat_summary_markdown(
            metrics,
            saved_rec,
            store_dir,
            reports_written=not opts["summary_only"],
            html_written=not opts["no_html"],
        ),
        METRICS_MARKER,
    )


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
    sys_msg = ""
    try:
        markdown_out, sys_msg = _run_metrics_command(payload, raw_input, rest)
    except Exception as exc:
        markdown_out = (
            f"### Gravity Metrics — Erreur\n\n"
            f"`{exc}`\n\n"
            f"Lancez `/gravity metrics help` pour la syntaxe des commandes."
        )
    emit_user_markdown(markdown_out, system_message=sys_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
