#!/usr/bin/env python3
"""Hook PreInvocation Gravity Metrics : cycle hybride /gravity-metrics & capture SPI-2."""

import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_hook_rt = importlib.import_module("_shared.hook_runtime")
_path_safety = importlib.import_module("_shared.path_safety")

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
_CONSULT_HEAD_RE = re.compile(
    r"^(?:pourquoi|comment|combien|explique(?:-moi)?|qu['’]est-ce|est-ce\s+que|"
    r"quel(?:le)?s?|list(?:e|er)?|montre(?:-moi)?|affiche|r[eé]sume|"
    r"what|why|how|explain|show|summarize)\b",
    re.IGNORECASE,
)
_ACTION_OVERRIDE_RE = re.compile(
    r"\b(?:specs?|tasks?|handoff|cr[eé]er|cr[eé]e|impl[eé]menter|impl[eé]mente|"
    r"modifier|modifie|[eé]diter|[eé]dite|corriger|corrige|refactor(?:er|iser)?|"
    r"ajouter|ajoute|supprimer|supprime|migrer|migre|d[eé]ployer|d[eé]ploie|"
    r"construire|construis|[eé]crire|[eé]cris|create|implement|modify|edit|"
    r"patch|fix|add|remove|delete|build|write|deploy)\b",
    re.IGNORECASE,
)
_LABEL_STOPWORDS = frozenset(
    {
        "a", "an", "and", "au", "aux", "avec", "ce", "ces", "dans", "de", "des",
        "du", "en", "et", "for", "in", "la", "le", "les", "of", "on", "or",
        "ou", "par", "pour", "sur", "the", "to", "un", "une",
    }
)


def _resolve_gravity_home() -> Path:
    env_home = os.environ.get("GRAVITY_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    return _path_safety.safe_home_dir() / ".gemini" / "gravity"


def _resolve_transcript_path(
    payload: Dict[str, Any], brain_dir: Path, cid: Optional[str]
) -> Optional[Path]:
    tpath_raw = _hook_rt.first_not_none(
        payload.get("transcriptPath"), payload.get("transcript_path")
    )
    if isinstance(tpath_raw, str) and tpath_raw.strip():
        return Path(tpath_raw.strip()).expanduser()
    if cid:
        return brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
    return None


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
            meta_dump = (
                json.dumps(meta_obj, ensure_ascii=False)
                if isinstance(meta_obj, dict)
                else ""
            )
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

    inv_num = _hook_rt.first_not_none(payload.get("invocationNum"), payload.get("invocation_num"))
    return isinstance(inv_num, int) and not isinstance(inv_num, bool) and inv_num > 0


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
            "Appelle immédiatement l'outil MCP `open_gravity_dashboard` du serveur "
            f"`gravity_dashboard` avec exactement ces arguments JSON : {args_json} "
            "(sans lire `METRICS.md`, `history.jsonl` ni les scripts du hook au préalable)."
        ),
        "Réponds ensuite en une seule phrase courte confirmant l'ouverture du tableau de bord"
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

    from metrics import (
        METRICS_MARKER,
        METRICS_OPEN_MARKER,
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
    from metrics.lifecycle import next_step_index_in_transcript
    from metrics.store import resolve_sealed_update_target

    parsed = classify_metrics_prompt(raw_input)
    action = parsed.get("action")
    if action == "passthrough":
        return None
    if action == "error":
        raise ValueError(parsed.get("error") or "Syntaxe invalide.")

    store_dir, history_path = resolve_store_paths()
    brain_dir = _hook_rt.resolve_brain_dir(payload) or default_brain_dir()
    cid = _hook_rt.resolve_conversation_id(payload)

    if _is_handoff_already_active(payload, brain_dir, cid, (METRICS_MARKER, METRICS_OPEN_MARKER)):
        return None

    ws_root = _hook_rt.resolve_workspace_root(payload)
    project = str(parsed.get("project") or "").strip() or (ws_root.name if ws_root else "")
    gravity_profile = read_gravity_profile_summary(ws_root)

    if action == "open":
        records = load_history(history_path)
        pending = list_pending_sessions(brain_dir, history_path, store_dir=store_dir)
        active_cap = load_active_captures(store_dir).get(cid) if cid else None
        tool_args: Dict[str, Any] = {"view": "metrics", "metrics_sub": "overview"}
        if project:
            tool_args["project"] = project
        cap_info = ""
        if isinstance(active_cap, dict):
            cap_lbl = active_cap.get("label") or "capture en cours"
            cap_info = (
                f"\n- **Capture active :** `{cap_lbl}` (depuis step `{active_cap.get('start_step', 0)}`) "
                "— utilisez `/gravity-metrics stop` pour enregistrer."
            )
        scope_txt = f" · Projet : `{project}`" if project else ""
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Tableau de bord (lecture seule)\n\n"
            f"- **Sessions enregistrées :** `{len(records)}` · **À enregistrer :** `{len(pending)}`{scope_txt}"
            f"{cap_info}\n"
            "- Commandes capture : `/gravity-metrics start [label]` · `/gravity-metrics stop [label]` "
            "· `/gravity-metrics record <uuid>` · `/gravity-metrics pending`"
        )
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line=(
                f"Ouverture du tableau de bord Gravity Metrics en lecture seule "
                f"({len(records)} enregistrée(s), {len(pending)} à enregistrer)."
            ),
            tool_args=tool_args,
        )
        return "handoff", md_out, sys_msg

    if action == "start":
        if not cid:
            raise ValueError("Impossible de démarrer une capture sans `conversationId`.")
        tpath = _resolve_transcript_path(payload, brain_dir, cid)
        start_step = next_step_index_in_transcript(tpath)
        cap = start_active_capture(
            cid,
            start_step=start_step,
            label=str(parsed.get("label") or ""),
            project=project,
            store_dir=store_dir,
            overwrite=True,
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
        tool_args = {"view": "metrics", "metrics_sub": "pending"}
        if project:
            tool_args["project"] = project
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line=f"{len(pending)} session(s) Gravity à enregistrer affichée(s).",
            tool_args=tool_args,
            repeat_warning="sans répéter le tableau déjà affiché",
        )
        return "handoff", md_out, sys_msg

    update_sealed = bool(parsed.get("update"))

    if action in ("record", "archive"):
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
            project=parsed.get("project") or (project if same_conv and not update_sealed else ""),
            gravity_profile=gravity_profile if same_conv else "",
            force=parsed["force"],
            update_sealed=update_sealed,
            self_archiving=same_conv,
        )
        already = bool(saved_rec.get("already_archived"))
        is_update = bool(update_sealed or parsed["force"])
        md_out = render_chat_summary_markdown(
            metrics,
            saved_rec,
            store_dir,
            reports_written=not already,
            already_archived=already,
        )
        tool_args = {"view": "metrics", "metrics_sub": "sessions", "target": saved_rec["label"]}
        if saved_rec.get("project"):
            tool_args["project"] = saved_rec["project"]
        elif project:
            tool_args["project"] = project
        if already:
            rec_summary = f"Session `{saved_rec['label']}` déjà enregistrée (immuable)."
        elif is_update:
            rec_summary = f"Session `{saved_rec['label']}` mise à jour et résumée à l'écran."
        else:
            rec_summary = f"Session `{saved_rec['label']}` enregistrée et résumée à l'écran."
        sys_msg = _build_handoff_system_message(
            METRICS_MARKER,
            summary_line=rec_summary,
            tool_args=tool_args,
            repeat_warning="sans répéter le tableau déjà affiché",
        )
        return "handoff", md_out, sys_msg

    # action == "stop" or action == "capture"
    if not cid:
        tool_args = {"view": "metrics", "metrics_sub": "overview"}
        if project:
            tool_args["project"] = project
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Ouverture du tableau de bord\n\n"
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
    eff_project = str(parsed.get("project") or "").strip()
    eff_full = bool(parsed["full"])
    if isinstance(active_cap, dict):
        if not eff_full and isinstance(active_cap.get("start_step"), int):
            from_step = active_cap["start_step"]
        if not eff_label and active_cap.get("label"):
            eff_label = str(active_cap["label"]).strip()
        if not eff_project and active_cap.get("project"):
            eff_project = str(active_cap["project"]).strip()

    tpath = _resolve_transcript_path(payload, brain_dir, cid)
    if update_sealed and not eff_full and from_step is None:
        from_step, eff_full, eff_label, eff_project, _ = resolve_sealed_update_target(
            history_path,
            cid,
            label=eff_label,
            project=eff_project,
            from_step=from_step,
            full_conversation=eff_full,
            transcript_path=tpath,
        )
    if not eff_project:
        eff_project = project

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=PLUGIN_ROOT / "config" / "pricing_catalog.json",
        pricing_profile=parsed["profile"],
        label=eff_label,
        gravity_window=not eff_full,
        current_prompt_is_metrics=True,
        current_prompt=raw_input,
        full_conversation=eff_full,
        from_step=from_step,
        project=eff_project,
        gravity_profile=gravity_profile,
        store_dir=store_dir,
        self_archiving=True,
    )

    if metrics["summary"]["total_llm_calls"] == 0:
        if action == "stop":
            pop_active_capture(cid, store_dir=store_dir)
        tool_args = {"view": "metrics", "metrics_sub": "overview"}
        if eff_project:
            tool_args["project"] = eff_project
        archived_for_cid = [
            r for r in load_history(history_path) if r.get("conversation_id") == cid
        ]
        if archived_for_cid:
            last_lbl = archived_for_cid[-1].get("label") or cid[:8]
            tool_args = {"view": "metrics", "metrics_sub": "sessions", "target": last_lbl}
            if eff_project:
                tool_args["project"] = eff_project
            md_out = (
                f"{METRICS_OPEN_MARKER}\n"
                "### Gravity Metrics — Session déjà enregistrée\n\n"
                f"Aucune nouvelle activité LLM à capturer depuis l'enregistrement de `{last_lbl}` ; "
                "ouverture du tableau de bord."
            )
            sys_msg = _build_handoff_system_message(
                METRICS_OPEN_MARKER,
                summary_line=f"Session `{last_lbl}` déjà enregistrée sans nouvelle activité LLM.",
                tool_args=tool_args,
            )
            return "handoff", md_out, sys_msg
        scope_note = f" sur le projet `{eff_project}`" if eff_project else ""
        md_out = (
            f"{METRICS_OPEN_MARKER}\n"
            "### Gravity Metrics — Ouverture du tableau de bord\n\n"
            f"Aucune nouvelle activité LLM à capturer depuis la dernière collecte ; "
            f"ouverture du tableau de bord{scope_note}."
        )
        sys_msg = _build_handoff_system_message(
            METRICS_OPEN_MARKER,
            summary_line="Aucune nouvelle activité LLM à enregistrer.",
            tool_args=tool_args,
        )
        return "handoff", md_out, sys_msg

    saved_rec, _ = record_session_metrics(
        metrics,
        store_dir=store_dir,
        history_path=history_path,
        label=eff_label,
        note=parsed["note"],
        auto_label=not bool(eff_label),
        force=parsed["force"],
        update_sealed=update_sealed,
        self_archiving=True,
    )

    already = bool(saved_rec.get("already_archived"))
    is_update = bool(update_sealed or parsed["force"])
    md_out = render_chat_summary_markdown(
        metrics,
        saved_rec,
        store_dir,
        reports_written=not already,
        already_archived=already,
    )
    tool_args = {"view": "metrics", "metrics_sub": "sessions", "target": saved_rec["label"]}
    if saved_rec.get("project"):
        tool_args["project"] = saved_rec["project"]
    elif eff_project:
        tool_args["project"] = eff_project
    if already:
        stop_summary = f"Session `{saved_rec['label']}` déjà enregistrée (immuable)."
    elif is_update:
        stop_summary = f"Session `{saved_rec['label']}` mise à jour et résumée à l'écran."
    else:
        stop_summary = f"Session `{saved_rec['label']}` enregistrée et résumée à l'écran."
    sys_msg = _build_handoff_system_message(
        METRICS_MARKER,
        summary_line=stop_summary,
        tool_args=tool_args,
        repeat_warning="sans répéter le tableau déjà affiché",
    )
    return "handoff", md_out, sys_msg


def _load_capture_policies() -> List[Dict[str, Any]]:
    """Lit dynamiquement les `capture_policy` (`SPI-2`) depuis `$GRAVITY_HOME/core/modules.json`."""
    g_home = _resolve_gravity_home()
    core_dir = g_home / "core"
    reg_path = core_dir / "modules.json"
    if (
        _path_safety.is_symlink_or_junction(g_home)
        or _path_safety.is_symlink_or_junction(core_dir)
        or _path_safety.is_symlink_or_junction(reg_path)
        or not reg_path.is_file()
    ):
        return []
    try:
        raw = reg_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    raw_modules = data.get("modules")
    items: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(raw_modules, dict):
        items = [(str(k), v) for k, v in raw_modules.items() if isinstance(v, dict)]
    elif isinstance(raw_modules, list):
        items = [(str(v.get("id") or ""), v) for v in raw_modules if isinstance(v, dict)]

    policies: List[Dict[str, Any]] = []
    for mod_key, mod in items:
        cp = mod.get("capture_policy")
        if not isinstance(cp, dict):
            continue
        if cp.get("auto_start") is False:
            continue
        raw_cmds = cp.get("commands") or mod.get("commands")
        if not isinstance(raw_cmds, list):
            continue
        commands = [
            c.strip() for c in raw_cmds if isinstance(c, str) and c.strip().startswith("/")
        ]
        if not commands:
            continue
        raw_excl = cp.get("exclude_subcommands") or []
        exclude_subcommands = (
            {s.strip().lower() for s in raw_excl if isinstance(s, str) and s.strip()}
            if isinstance(raw_excl, list)
            else set()
        )
        cb_raw = cp.get("consultative_bypass")
        consultative_bypass = cb_raw if isinstance(cb_raw, bool) else True
        label_prefix = (
            str(cp.get("label_prefix") or mod.get("id") or mod_key or "capture").strip()
            or "capture"
        )
        policies.append(
            {
                "commands": commands,
                "exclude_subcommands": exclude_subcommands,
                "consultative_bypass": consultative_bypass,
                "label_prefix": label_prefix,
            }
        )
    return policies


def _match_capture_policy(cleaned: str) -> Optional[Tuple[str, str]]:
    """Retourne `(label_prefix, args_text)` si `cleaned` déclenche une `capture_policy` SPI-2."""
    if not cleaned.startswith("/"):
        return None
    for policy in _load_capture_policies():
        for cmd in policy["commands"]:
            pattern = re.compile(
                rf"^{re.escape(cmd)}(?::[a-z0-9_-]+)?\s+(.+)$",
                re.IGNORECASE | re.DOTALL,
            )
            m = pattern.match(cleaned)
            if m is None:
                continue
            args_text = m.group(1).strip()
            if not args_text or args_text.startswith("/"):
                continue
            effective_args = args_text
            if policy["consultative_bypass"]:
                while True:
                    parts = effective_args.split(None, 1)
                    if not parts:
                        break
                    if parts[0].strip().lower() in ("evo", "--no-verify"):
                        effective_args = parts[1].strip() if len(parts) > 1 else ""
                        continue
                    break
                if not effective_args:
                    return None
                if effective_args.startswith("/"):
                    continue
            first_tok = effective_args.split(None, 1)[0].strip().lower()
            if first_tok in policy["exclude_subcommands"]:
                return None
            if policy["consultative_bypass"] and _CONSULT_HEAD_RE.match(effective_args):
                if _ACTION_OVERRIDE_RE.search(effective_args) is None:
                    return None
            return policy["label_prefix"], effective_args
    return None


def _derive_capture_label(label_prefix: str, args_text: str, slugify_fn: Any) -> str:
    slug_args = slugify_fn(args_text, fallback="", max_len=0).replace(".", "-")
    kws = [
        tok for tok in re.findall(r"[a-z0-9]+", slug_args)
        if len(tok) >= 2 and tok not in _LABEL_STOPWORDS
    ][:5]
    if kws:
        return str(slugify_fn(f"{label_prefix}-{'-'.join(kws)}", fallback=label_prefix, max_len=64))
    return str(slugify_fn(label_prefix, fallback="capture", max_len=64))


def _maybe_auto_start_policy_capture(payload: Dict[str, Any], cleaned: str) -> None:
    """Auto-démarre une capture idempotente (0 token) selon `capture_policy` (`SPI-2`)."""
    if not _hook_rt.is_initial_invocation(payload):
        return
    matched = _match_capture_policy(cleaned)
    if matched is None:
        return
    cid = _hook_rt.resolve_conversation_id(payload)
    if not cid:
        return
    label_prefix, args_text = matched
    try:
        from metrics import (
            default_brain_dir,
            resolve_store_paths,
            slugify_label,
            start_active_capture,
        )
        from metrics.lifecycle import current_turn_start_step_in_transcript

        store_dir, _ = resolve_store_paths()
        brain_dir = _hook_rt.resolve_brain_dir(payload) or default_brain_dir()
        tpath = _resolve_transcript_path(payload, brain_dir, cid)
        start_step = current_turn_start_step_in_transcript(tpath, expected_prompt=cleaned)
        ws_root = _hook_rt.resolve_workspace_root(payload)
        project = ws_root.name if ws_root else ""
        auto_lbl = _derive_capture_label(label_prefix, args_text, slugify_label)
        start_active_capture(
            cid,
            start_step=start_step,
            label=auto_lbl,
            project=project,
            source=f"{label_prefix}_auto",
            store_dir=store_dir,
            overwrite=False,
        )
    except Exception:
        pass


def main() -> int:
    _hook_rt.configure_utf8_stdio()
    raw = _hook_rt.read_stdin_text()
    if not raw:
        _hook_rt.emit_passthrough()
        return 0
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        _hook_rt.emit_passthrough()
        return 0
    if not isinstance(payload, dict):
        _hook_rt.emit_passthrough()
        return 0

    raw_input = str(
        _hook_rt.first_not_none(
            payload.get("lastUserInput"),
            payload.get("userInput"),
            payload.get("prompt"),
            "",
        )
    )
    cleaned = re.sub(r"\s+", " ", _STRIP_META_RE.sub("", raw_input)).strip()
    match = _METRICS_CMD_RE.match(cleaned)
    if match is None:
        _maybe_auto_start_policy_capture(payload, cleaned)
        _hook_rt.emit_passthrough()
        return 0

    rest = (match.group(1) or "").strip()
    if rest.startswith("/"):
        _hook_rt.emit_passthrough()
        return 0

    try:
        result = _run_metrics_command(payload, raw_input, rest)
    except Exception as exc:
        _hook_rt.emit_user_markdown(
            f"### Gravity Metrics — Erreur\n\n"
            f"`{exc}`\n\n"
            'Syntaxe : `/gravity-metrics` · `/gravity-metrics start [label]` · '
            '`/gravity-metrics stop [label] [-n "note"] [--full] [--update] [--force]` · '
            '`/gravity-metrics record <uuid> [label] [--full] [--update] [--force]` · '
            '`/gravity-metrics pending`'
        )
        return 0

    if result is None:
        _hook_rt.emit_passthrough()
        return 0

    mode, markdown_out, sys_msg = result
    if mode == "terminate":
        _hook_rt.emit_user_markdown(markdown_out, system_message=sys_msg)
    else:
        _hook_rt.emit_handoff_markdown(markdown_out, system_message=sys_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
