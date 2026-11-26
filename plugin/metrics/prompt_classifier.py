#!/usr/bin/env python3
"""Classification des prompts, profils workflow, labels et commandes CLI (100 % stdlib)."""

from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import unicodedata

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_tr_reader = importlib.import_module("_shared.transcript_reader")
normalize_user_prompt = _tr_reader.normalize_user_prompt
parse_iso_ts = _tr_reader.parse_iso_ts

try:
    from .workspace_resolver import (
        _extract_shell_command_str,
        _is_atelier_doc_path,
        _parse_tool_call_args,
    )
except ImportError:
    _ATELIER_DOC_PATH_RE = re.compile(r"(?:^|/)(?:specs|tasks|tracking)/", re.IGNORECASE)

    def _parse_tool_call_args(tc: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait le dictionnaire d'arguments d'un tool_call (`args` ou `arguments`)."""
        raw_args = tc.get("args") if tc.get("args") is not None else tc.get("arguments")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except Exception:
                return {"CommandLine": raw_args}
        return raw_args if isinstance(raw_args, dict) else {}

    def _extract_shell_command_str(tc: Dict[str, Any]) -> str:
        """Extrait la commande shell depuis un tool_call (`CommandLine`/`command`/`cmd`)."""
        args = _parse_tool_call_args(tc)
        raw = args.get("CommandLine") or args.get("command") or args.get("command_line") or args.get("cmd") or ""
        cmd = str(raw).strip()
        if len(cmd) >= 2 and cmd[0] == '"' and cmd[-1] == '"':
            try:
                unwrapped = json.loads(cmd)
                if isinstance(unwrapped, str):
                    cmd = unwrapped.strip()
            except Exception:
                pass
        return cmd

    def _is_atelier_doc_path(raw_path: str) -> bool:
        """Retourne True si le chemin pointe vers `specs/`, `tasks/` ou `tracking/`."""
        return bool(raw_path and _ATELIER_DOC_PATH_RE.search(raw_path.replace("\\", "/").strip()))


METRICS_MARKER = "<!-- gravity-metrics:recorded -->"
METRICS_OPEN_MARKER = "<!-- gravity-metrics:opened -->"

_ARCHITECT_ARCHIVE_RE = re.compile(
    r"^/gravity-architect(?::gravity-architect)?\s+archive\s+([^\s]+)",
    re.IGNORECASE,
)
_GRAVITY_METRICS_CMD_RE = re.compile(
    r"^/gravity-metrics(?::gravity-metrics)?(?:\s|$)", re.IGNORECASE
)
_GRAVITY_METRICS_ARGS_RE = re.compile(
    r"^/gravity-metrics(?::gravity-metrics)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL
)
_SWARM_CMD_RE = re.compile(r"^/swarm(?::swarm)?(?:\s|$)", re.IGNORECASE)
_GRAVITY_CMD_RE = re.compile(
    r"^/(?:gravity-workflow(?::gravity-workflow)?|gravity-architect(?::gravity-architect)?|gravity-bridge(?::gravity-bridge)?|gravity(?::gravity)?)(?:\s|$)",
    re.IGNORECASE,
)
_GRAVITY_LEADING_AXES_RE = re.compile(
    r"^/(?:gravity-workflow|gravity-architect|gravity-bridge|gravity|boost|grill-me|plan|caveman)(?::[a-z0-9_-]+)?(?=\s|$)"
    r"(?:\s+(?:quick|standard|deep|evo|evolutive|low|medium|high|ultra|slow|fast|safe|unsafe|auto)(?=\s|$))*"
    r"(?:\s+(?:init|add|sync|status|evals|modules|doctor|dashboard|help|spec|handoff|archive|audit|workspaces|link|send|read|take|reply|done|prune)(?=\s|$))?"
    r"(?:\s+(?:quick|standard|deep|evo|evolutive|low|medium|high|ultra|slow|fast|safe|unsafe|auto)(?=\s|$))*\s*",
    re.IGNORECASE,
)
_STOPWORDS = {
    "a", "an", "and", "au", "aux", "avec", "ce", "ces", "dans", "de", "des", "du",
    "elle", "en", "et", "for", "il", "in", "je", "la", "le", "les", "me", "ne",
    "no", "non", "of", "ok", "on", "or", "ou", "oui", "par", "pas", "peux", "pour",
    "sur", "te", "the", "to", "tu", "un", "une", "vas", "vazi", "veux", "yes",
}
_CLI_VALUE_FLAGS = frozenset(
    {
        "-l", "--label", "-n", "--note", "--profile", "--workspace",
        "--brain-dir", "--bridge-db", "--catalog", "--store", "--history",
    }
)


def _parse_iso_ts(ts_str: Optional[str]) -> Optional[datetime]:
    return parse_iso_ts(ts_str)


def _fmt_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_user_request_text(raw: str) -> str:
    """Extrait le texte utilisateur nettoyé d'un payload USER_INPUT Antigravity."""
    if raw is None:
        return ""
    return normalize_user_prompt(raw if isinstance(raw, str) else str(raw))


def _clean_user_prompt(raw: str, max_len: int = 90) -> str:
    txt = extract_user_request_text(raw)
    if len(txt) > max_len:
        return txt[: max_len - 3] + "..."
    return txt or "(sans titre)"


def _clean_pending_title(first_prompt: str, fallback_prompt: str) -> str:
    txt = extract_user_request_text(first_prompt)
    stripped = _strip_leading_gravity_commands(txt)
    if stripped:
        return stripped if len(stripped) <= 90 else stripped[:87] + "..."
    return _clean_user_prompt(fallback_prompt or first_prompt)


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
    update = False
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
        elif tok in ("--update", "--update-sealed"):
            update = True
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
            "update": update,
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
    """Analyse un prompt `/gravity-metrics` : passthrough, open, start, stop, capture, record/archive, pending ou error."""
    txt = extract_user_request_text(raw)
    if not _GRAVITY_METRICS_CMD_RE.match(txt):
        return {"action": "passthrough"}
    match = _GRAVITY_METRICS_ARGS_RE.match(txt)
    if not match:
        return {"action": "passthrough"}
    rest = (match.group(1) or "").strip()
    if not rest:
        return {"action": "open", "project": ""}
    if rest.startswith("/"):
        return {"action": "passthrough"}

    tokens = _split_metrics_tokens(rest)
    if not tokens:
        return {"action": "open", "project": ""}

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

    if head in ("start", "stop", "finish", "record", "archive"):
        try:
            opts, positionals, has_explicit_label, _ = _parse_metrics_flags(tokens[1:])
        except ValueError as exc:
            return {"action": "error", "error": str(exc)}
        if head == "start":
            return {
                "action": "start",
                "label": opts["label"] or " ".join(positionals).strip(),
                "project": opts["project"],
                "has_explicit_label": has_explicit_label,
            }
        if head in ("record", "archive"):
            if not positionals:
                return {
                    "action": "error",
                    "error": f'Syntaxe : `/gravity-metrics {head} <uuid_ou_prefixe> [label] [-n "note"] [--full] [--update] [--force]`',
                }
            return {
                "action": head,
                "target": positionals[0],
                "label": opts["label"] or " ".join(positionals[1:]).strip(),
                "has_explicit_label": has_explicit_label,
                **{k: opts[k] for k in ("note", "profile", "project", "full", "force", "update")},
            }
        return {
            "action": "stop",
            "label": opts["label"] or " ".join(positionals).strip(),
            "has_explicit_label": has_explicit_label,
            **{k: opts[k] for k in ("note", "profile", "project", "full", "force", "update")},
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
        return {"action": "open", "project": opts["project"]}
    return {
        "action": "capture",
        "label": resolved_label.strip(),
        "has_explicit_label": has_explicit_label,
        **{k: opts[k] for k in ("note", "profile", "project", "full", "force", "update")},
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

    slug = re.sub(r"[^a-z0-9]+", "-", resolved.name.lower()).strip("-")[:32] or "workspace"
    norm_digest8 = hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8")).hexdigest()[:8]
    hashes: List[str] = [f"{slug}-{norm_digest8}"]
    for raw_s in {str(resolved), os.path.normcase(str(resolved))}:
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
    """Lit en lecture seule `$GRAVITY_HOME/workflow/workspaces/<slug>-<hash>/profile.json` > `.agents/gravity-workflow/profile.json` > `.agents/gravity/profile.json`."""
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
    norm = (
        unicodedata.normalize("NFKD", str(label or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9._-]+", "-", norm.strip().lower())
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


def _strip_leading_gravity_commands(text: str) -> str:
    """Retire itérativement les commandes Gravity/pilotage en tête jusqu'à point fixe."""
    current = str(text or "").strip()
    while current:
        nxt = _GRAVITY_LEADING_AXES_RE.sub("", current, count=1).strip()
        if nxt == current:
            break
        current = nxt
    return current


def _extract_label_keywords(raw_prompt: str, project_slug: str = "") -> List[str]:
    """Extrait les mots-clés significatifs d'un prompt (ignore commandes `/gravity` et stopwords)."""
    cleaned = extract_user_request_text(raw_prompt)
    cleaned = _strip_leading_gravity_commands(cleaned)
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
    max_len: int = 64,
) -> str:
    """Génère un label court, lisible et unique à partir du projet, prompt et horodatage."""
    proj_slug = slugify_label(project, fallback="")[:18].strip("-")
    words = _extract_label_keywords(first_prompt, proj_slug)[:5]

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

    base = slugify_label(base, fallback="session", max_len=max_len)
    existing_set = {str(lbl).strip().lower() for lbl in existing_labels if lbl}
    if base.lower() not in existing_set:
        return base

    idx = 2
    while True:
        suffix = f"-{idx}"
        if max_len > 0:
            prefix = base[: max(1, max_len - len(suffix))].strip("-") or "session"
            candidate = f"{prefix}{suffix}"[:max_len]
        else:
            candidate = f"{base}{suffix}"
        if candidate.lower() not in existing_set:
            return candidate
        idx += 1


def _split_shell_segments(cmd: str) -> List[str]:
    """Découpe une ligne shell en segments sur `;`, `|`, `&` et retours ligne hors quotes."""
    segments: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    escaped = False
    for ch in cmd:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote == '"':
            buf.append(ch)
            escaped = True
            continue
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in (";", "|", "&", "\r", "\n"):
            seg = "".join(buf).strip()
            if seg:
                segments.append(seg)
            buf.clear()
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def _parse_cli_segment(seg: str) -> Optional[Tuple[str, List[str], bool]]:
    """Extrait `(subcmd, positionals, has_latest)` si `seg` appelle `cli.py record|archive|add`."""
    try:
        raw_tokens = shlex.split(seg, posix=False)
    except ValueError:
        raw_tokens = seg.split()
    tokens = [
        t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'") else t
        for t in raw_tokens
        if t
    ]
    if not tokens:
        return None
    if Path(tokens[0].replace("\\", "/")).name.lower() in ("git", "echo", "printf", "grep", "rg"):
        return None
    cli_idx = next(
        (idx for idx, tok in enumerate(tokens) if Path(tok.replace("\\", "/")).name.lower() == "cli.py"),
        None,
    )
    if cli_idx is None or cli_idx + 1 >= len(tokens):
        return None
    subcmd = tokens[cli_idx + 1].lower()
    if subcmd not in ("record", "archive", "add"):
        return None

    positionals: List[str] = []
    has_latest = False
    j = cli_idx + 2
    while j < len(tokens):
        tok = tokens[j]
        if tok in _CLI_VALUE_FLAGS:
            j += 2
            continue
        if tok.startswith("-"):
            if tok == "--latest":
                has_latest = True
            j += 1
            continue
        positionals.append(tok)
        j += 1
    return subcmd, positionals, has_latest


def _segment_is_cli_record_or_archive(seg: str) -> bool:
    """Retourne True si un segment shell invoque `cli.py record|archive|add`."""
    parsed = _parse_cli_segment(seg)
    if parsed is None:
        return False
    subcmd, positionals, has_latest = parsed
    if subcmd in ("record", "archive"):
        return bool(positionals)
    return bool(positionals) or has_latest


def _segment_is_cli_self_archive(seg: str, current_cid: str) -> bool:
    """Vérifie si un segment invoque directement `cli.py record|archive|add` pour `current_cid`."""
    parsed = _parse_cli_segment(seg)
    if parsed is None:
        return False
    subcmd, positionals, has_latest = parsed
    target = positionals[0] if positionals else ""
    cid_low = current_cid.strip().lower()
    if subcmd in ("record", "archive"):
        if not target:
            return False
        return not cid_low or cid_low.startswith(target.lower())
    if target:
        return not cid_low or cid_low.startswith(target.lower())
    return has_latest


def _is_cli_archive_tool_call(
    step_obj: Optional[Dict[str, Any]],
    current_cid: str = "",
) -> bool:
    """Détecte un step PLANNER_RESPONSE appelant directement `cli.py archive` ou `cli.py add` sur `current_cid`."""
    if not isinstance(step_obj, dict) or step_obj.get("type") != "PLANNER_RESPONSE":
        return False
    if step_obj.get("status") in ("ERROR", "INVALID"):
        return False
    raw_tcs = step_obj.get("tool_calls")
    if not isinstance(raw_tcs, (list, tuple)):
        return False
    for tc in raw_tcs:
        if not isinstance(tc, dict):
            continue
        tname = str(tc.get("name") or "").strip().lower()
        if tname not in ("run_command", "execute_command", "bash", "shell"):
            continue
        cmd = _extract_shell_command_str(tc)
        if not cmd or "cli.py" not in cmd.lower():
            continue
        if any(_segment_is_cli_self_archive(seg, current_cid) for seg in _split_shell_segments(cmd)):
            return True
    return False


def _review_dict_is_failed(obj: Dict[str, Any]) -> Optional[bool]:
    """Retourne True/False si `obj` porte un verdict Reviewer structuré, sinon None."""
    if not any(k in obj for k in ("verdict", "blockers", "majors")):
        return None
    if str(obj.get("verdict") or "").strip().upper() == "FAIL":
        return True
    for key in ("blockers", "majors"):
        val = obj.get(key)
        if isinstance(val, list) and any(str(x).strip() for x in val):
            return True
        if (
            isinstance(val, str)
            and val.strip()
            and val.strip().lower() not in ("[]", "none", "null", "aucun", "aucune", "0", "n/a")
        ):
            return True
    return False


def _is_reviewer_verdict_failed(step_obj: Optional[Dict[str, Any]]) -> bool:
    """Détermine si la réponse finale d'un Reviewer retourne `FAIL`, des `blockers` ou des `majors`."""
    if not isinstance(step_obj, dict) or step_obj.get("type") != "PLANNER_RESPONSE":
        return False
    for tc in step_obj.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        res = _review_dict_is_failed(_parse_tool_call_args(tc))
        if res is not None:
            return res

    content = step_obj.get("content")
    if not isinstance(content, str) or not content.strip():
        return False

    decoder = json.JSONDecoder()
    pos = 0
    saw_review_json = False
    while True:
        idx = content.find("{", pos)
        if idx < 0:
            break
        try:
            parsed, end_idx = decoder.raw_decode(content, idx)
        except ValueError:
            pos = idx + 1
            continue
        pos = max(idx + 1, end_idx)
        if isinstance(parsed, dict):
            res = _review_dict_is_failed(parsed)
            if res is not None:
                saw_review_json = True
                if res:
                    return True
    if saw_review_json:
        return False

    if re.search(r"(?i)\bverdict(?:\s+global)?\b\s*(?:\*{1,2})?\s*[:=]\s*(?:\*{1,2})?\s*fail\b", content):
        return True
    if re.search(r"\[(?:BLOCKER|MAJOR)\]\s*\S", content):
        return True
    return bool(
        re.search(
            r"(?im)^\s*(?:[-*]\s*)?(?:\*{1,2}|#{1,4}\s*)?(?:blockers?|majors?)(?:\*{1,2})?\s*:"
            r"\s*(?!\s*(?:\[\s*\]|none|null|aucun[e]?|0|n/?a|-)\s*$)\S",
            content,
        )
    )
