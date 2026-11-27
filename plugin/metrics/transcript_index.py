#!/usr/bin/env python3
"""Indexation incrémentale des transcripts, résolution de fenêtre et parsing par agent (100 % stdlib)."""

from collections import Counter
from datetime import datetime
import importlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_tr_reader = importlib.import_module("_shared.transcript_reader")
extract_shell_nonzero_code = _tr_reader.extract_shell_nonzero_code
parse_iso_ts = _tr_reader.parse_iso_ts

from .pricing import (
    _add_cost,
    _empty_cost_acc,
    _round_cost,
    compute_call_cost,
    resolve_model_pricing,
)
from .prompt_classifier import (
    METRICS_MARKER,
    METRICS_OPEN_MARKER,
    _extract_shell_command_str,
    _fmt_iso_utc,
    _is_atelier_doc_path,
    _is_cli_archive_tool_call,
    _is_reviewer_verdict_failed,
    _parse_tool_call_args,
    _segment_is_cli_record_or_archive,
    _split_shell_segments,
    classify_metrics_prompt,
    extract_user_request_text,
    is_gravity_metrics_prompt,
    is_gravity_session_prompt,
    parse_architect_archive_prompt,
)

try:
    from .technical_signals import TechnicalSignalAccumulator, _percentile_sorted
except ImportError:
    _COMPACT_CMD_RE = re.compile(r"^/compact(?::[a-z0-9_-]+)?(?:\s|$)", re.IGNORECASE)

    def _percentile_sorted(sorted_vals: List[int], pct: float) -> int:
        if not sorted_vals:
            return 0
        idx = min(len(sorted_vals) - 1, max(0, math.ceil(pct * len(sorted_vals)) - 1))
        return int(sorted_vals[idx])

    def _extract_thinking_chars(raw_thinking: Any) -> int:
        if isinstance(raw_thinking, str):
            return len(raw_thinking)
        if isinstance(raw_thinking, list):
            return sum(
                len(it) if isinstance(it, str) else len(str(it.get("text") or it.get("thinking") or ""))
                for it in raw_thinking
                if isinstance(it, (str, dict))
            )
        if isinstance(raw_thinking, dict):
            return len(str(raw_thinking.get("text") or raw_thinking.get("thinking") or ""))
        return 0

    class TechnicalSignalAccumulator:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.thinking_calls = 0
            self.thinking_chars = 0
            self.compaction_count = 0
            self._prev_ctx = 0
            self._just_compacted = False
            self._prev_dt: Optional[datetime] = None
            self.latency_samples_ms: List[int] = []

        def feed(self, obj: Dict[str, Any], dt: Optional[datetime] = None) -> None:
            eff_dt = dt if dt is not None else parse_iso_ts(obj.get("created_at"))
            if eff_dt is not None:
                if self._prev_dt is not None:
                    delta_s = (eff_dt - self._prev_dt).total_seconds()
                    if 0.0 < delta_s <= 600.0:
                        self.latency_samples_ms.append(int(round(delta_s * 1000.0)))
                self._prev_dt = eff_dt
            stype = obj.get("type")
            if stype == "USER_INPUT":
                if _COMPACT_CMD_RE.match(extract_user_request_text(str(obj.get("content") or ""))):
                    self.compaction_count += 1
                    self._just_compacted = True
                return
            if stype != "PLANNER_RESPONSE":
                return
            t_chars = _extract_thinking_chars(obj.get("thinking"))
            if t_chars > 0:
                self.thinking_calls += 1
                self.thinking_chars += t_chars
            ctx = int(obj.get("input_tokens") or 0) + int(obj.get("cache_read_tokens") or 0)
            if ctx > 0:
                if self._prev_ctx > 40_000 and ctx < 0.55 * self._prev_ctx and not self._just_compacted:
                    self.compaction_count += 1
                self._just_compacted = False
                self._prev_ctx = ctx

        def finalize(self, *, include_samples: bool = True) -> Dict[str, Any]:
            sorted_ms = sorted(self.latency_samples_ms)
            out: Dict[str, Any] = {
                "thinking_calls": self.thinking_calls,
                "thinking_chars": self.thinking_chars,
                "thinking_est_tokens": self.thinking_chars // 4,
                "compaction_count": self.compaction_count,
                "latency_avg_ms": round(sum(sorted_ms) / len(sorted_ms), 1) if sorted_ms else 0.0,
                "latency_p50_ms": _percentile_sorted(sorted_ms, 0.50),
                "latency_p95_ms": _percentile_sorted(sorted_ms, 0.95),
            }
            if include_samples:
                out["_latency_samples_ms"] = list(self.latency_samples_ms)
            return out


try:
    from .workspace_resolver import WorkspaceSignalAccumulator
except ImportError:
    import shlex as _shlex

    _WRITE_TN = frozenset({"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"})
    _READ_TN = frozenset({"read_file", "view_file", "list_dir", "grep_search", "find_by_name"})
    _SHELL_TN = frozenset({"run_command", "execute_command", "bash", "shell"})

    def _extract_tool_path(args: Dict[str, Any]) -> str:
        for k in ("Path", "TargetFile", "AbsolutePath", "DirectoryPath", "SearchPath", "SearchDirectory", "path", "file_path", "target_file"):
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    class WorkspaceSignalAccumulator:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.write_paths: List[str] = []
            self.shell_dirs: List[str] = []
            self.read_paths: List[str] = []

        def feed_tool_call(self, tc: Dict[str, Any]) -> None:
            tname = str(tc.get("name") or "").strip().lower()
            if not tname:
                return
            args = _parse_tool_call_args(tc)
            if tname in _WRITE_TN:
                p = _extract_tool_path(args)
                if p:
                    self.write_paths.append(p)
            elif tname in _SHELL_TN:
                cwd_arg = args.get("Cwd") or args.get("cwd")
                if isinstance(cwd_arg, str) and cwd_arg.strip():
                    self.shell_dirs.append(cwd_arg.strip())
                cmd = _extract_shell_command_str(tc)
                for seg in _split_shell_segments(cmd):
                    try:
                        toks = _shlex.split(seg, posix=True)
                    except ValueError:
                        toks = seg.split()
                    if toks and toks[0] == "cd" and len(toks) >= 2 and toks[1] not in ("-", "~"):
                        self.shell_dirs.append(toks[1])
                    elif toks and Path(toks[0]).name.lower() == "git":
                        for idx, tok in enumerate(toks[1:], start=1):
                            if tok == "-C" and idx + 1 < len(toks):
                                self.shell_dirs.append(toks[idx + 1])
                            elif tok.startswith("-C") and len(tok) > 2:
                                self.shell_dirs.append(tok[2:])
            elif tname in _READ_TN:
                p = _extract_tool_path(args)
                if p:
                    self.read_paths.append(p)

        def finalize(self) -> Dict[str, List[str]]:
            return {"write_paths": self.write_paths, "shell_dirs": self.shell_dirs, "read_paths": self.read_paths}


_CACHE_MAX_ENTRIES = 512
_TRANSCRIPT_INDEX_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}
_SWARM_CMD_RE = re.compile(r"^/swarm(?::swarm)?(?:\s|$)", re.IGNORECASE)
_GIT_PUSH_RE = re.compile(r"\bgit\b[^\n;|&]*\bpush\b", re.IGNORECASE)
_DIRECT_EDIT_TOOL_NAMES = frozenset(
    {"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"}
)
_ECOSYSTEM_DOC_PATH_RE = re.compile(
    r"(?:^|/)(?:(?:docs|audits)/|(?:ECOSYSTEM|ROADMAP|CHANGELOG)\.md$)",
    re.IGNORECASE,
)


def _is_excluded_edit_doc_path(raw_path: str) -> bool:
    """Retourne True pour les docs atelier/écosystème exclus de `has_direct_edit_tool`."""
    if not raw_path:
        return False
    if _is_atelier_doc_path(raw_path):
        return True
    return bool(_ECOSYSTEM_DOC_PATH_RE.search(raw_path.replace("\\", "/").strip()))
_WORK_TOOL_NAMES = _DIRECT_EDIT_TOOL_NAMES | frozenset({"run_workflow", "invoke_subagent", "swarm"})
_VERIF_CMD_TOKENS = ("git push", "validate.py", "unittest", "pytest", "audit_ecosystem.py", "ecosystem_install.py")
_CLEAN_TERMINAL_TOOLS = frozenset({"finish", "post_message", "swarm_wait"})
_extract_shell_nonzero_code = extract_shell_nonzero_code


def _cache_put(cache: Dict[str, Any], key: str, value: Any) -> None:
    if len(cache) >= _CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)))
    cache[key] = value


def _stat_sig(path: Path) -> Tuple[int, int]:
    try:
        st = path.stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, -1)


def _get_transcript_index(transcript_path: Union[Path, str]) -> Dict[str, Any]:
    """Indexe en une passe (avec cache mtime_ns+size) un `transcript.jsonl` racine."""
    tpath = Path(transcript_path)
    if not tpath.is_file():
        return {
            "exists": False,
            "mtime": 0.0,
            "first_prompt_raw": None,
            "has_gravity_cmd": False,
            "last_ts": None,
            "max_step_index": None,
            "entries": [],
        }

    try:
        st = tpath.stat()
        sig = (int(st.st_mtime_ns), int(st.st_size))
        mtime = float(st.st_mtime)
        cache_key = str(tpath.resolve())
    except OSError:
        sig, mtime, cache_key = (0, -1), 0.0, ""

    if cache_key:
        cached = _TRANSCRIPT_INDEX_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return cached[1]

    first_prompt_raw: Optional[str] = None
    has_gravity_cmd = False
    last_ts: Optional[str] = None
    max_step_index: Optional[int] = None
    entries: List[Dict[str, Any]] = []

    try:
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for line_idx, raw_line in enumerate(fh):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                step_type = str(obj.get("type") or "")
                raw_step = obj.get("step_index")
                step_idx = raw_step if isinstance(raw_step, int) and not isinstance(raw_step, bool) else None
                if step_idx is not None and (max_step_index is None or step_idx > max_step_index):
                    max_step_index = step_idx
                created_at = obj.get("created_at")
                if created_at:
                    last_ts = str(created_at)
                content = str(obj.get("content") or "")
                is_user = step_type == "USER_INPUT"
                metrics_action = ""
                is_metrics = is_gravity = is_architect = is_architect_archive = is_swarm = False
                clean_prompt = ""
                if is_user:
                    if first_prompt_raw is None:
                        first_prompt_raw = content
                    clean_prompt = extract_user_request_text(content)[:160]
                    is_metrics = is_gravity_metrics_prompt(content)
                    if is_metrics:
                        metrics_action = str(classify_metrics_prompt(content).get("action") or "")
                    is_gravity = is_gravity_session_prompt(content)
                    if not has_gravity_cmd and is_gravity:
                        has_gravity_cmd = True
                    is_architect = clean_prompt.lower().startswith("/gravity-architect")
                    is_architect_archive = parse_architect_archive_prompt(content) is not None
                    is_swarm = bool(_SWARM_CMD_RE.match(clean_prompt))

                meta_obj = obj.get("metadata")
                meta_str = json.dumps(meta_obj, ensure_ascii=False) if isinstance(meta_obj, dict) else ""
                has_marker = step_type == "SYSTEM_MESSAGE" and (METRICS_MARKER in content or METRICS_MARKER in meta_str)
                has_open_marker = step_type == "SYSTEM_MESSAGE" and (METRICS_OPEN_MARKER in content or METRICS_OPEN_MARKER in meta_str)

                tool_names: List[str] = []
                has_push_or_verif = has_cli_record_or_archive = has_swarm_or_subagent_tool = False
                has_direct_edit_tool = has_work_activity = False
                if step_type == "PLANNER_RESPONSE":
                    for tc in obj.get("tool_calls") or []:
                        if not isinstance(tc, dict):
                            continue
                        tname = str(tc.get("name") or "").strip()
                        if tname:
                            tool_names.append(tname)
                            if tname in _WORK_TOOL_NAMES:
                                has_work_activity = True
                            if tname in ("swarm", "invoke_subagent"):
                                has_swarm_or_subagent_tool = True
                            if tname in _DIRECT_EDIT_TOOL_NAMES:
                                targs = _parse_tool_call_args(tc)
                                tpath_arg = str(
                                    targs.get("Path") or targs.get("TargetFile")
                                    or targs.get("path") or targs.get("file_path") or targs.get("target_file") or ""
                                ).strip()
                                if not _is_excluded_edit_doc_path(tpath_arg):
                                    has_direct_edit_tool = True
                        if tname.lower() in ("run_command", "execute_command", "bash", "shell"):
                            cmd_raw = _extract_shell_command_str(tc)
                            cmd_str = cmd_raw.lower()
                            if any(tok in cmd_str for tok in _VERIF_CMD_TOKENS) or _GIT_PUSH_RE.search(cmd_raw):
                                has_push_or_verif = True
                            if "cli.py" in cmd_str and any(_segment_is_cli_record_or_archive(seg) for seg in _split_shell_segments(cmd_raw)):
                                has_cli_record_or_archive = True

                entries.append(
                    {
                        "line_idx": line_idx,
                        "step_index": step_idx,
                        "type": step_type,
                        "status": str(obj.get("status") or ""),
                        "created_at": created_at,
                        "is_user": is_user,
                        "is_metrics": is_metrics,
                        "metrics_action": metrics_action,
                        "is_gravity": is_gravity,
                        "is_architect": is_architect,
                        "is_architect_archive": is_architect_archive,
                        "is_swarm": is_swarm,
                        "is_planner": step_type == "PLANNER_RESPONSE",
                        "has_marker": has_marker,
                        "has_open_marker": has_open_marker,
                        "has_push_or_verif": has_push_or_verif,
                        "has_cli_record_or_archive": has_cli_record_or_archive,
                        "has_swarm_or_subagent_tool": has_swarm_or_subagent_tool,
                        "has_direct_edit_tool": has_direct_edit_tool,
                        "has_work_activity": has_work_activity,
                        "tool_names": tool_names,
                        "clean_prompt": clean_prompt,
                    }
                )
    except OSError:
        pass

    result = {
        "exists": True,
        "mtime": mtime,
        "first_prompt_raw": first_prompt_raw,
        "has_gravity_cmd": has_gravity_cmd,
        "last_ts": last_ts,
        "max_step_index": max_step_index,
        "entries": entries,
    }
    if cache_key:
        _cache_put(_TRANSCRIPT_INDEX_CACHE, cache_key, (sig, result))
    return result


def _has_clean_terminal_tool_call(
    step_obj: Optional[Dict[str, Any]],
    tool_names: FrozenSet[str] = _CLEAN_TERMINAL_TOOLS,
) -> bool:
    """Vérifie si un step PLANNER_RESPONSE invoque un outil terminal propre."""
    if not isinstance(step_obj, dict) or step_obj.get("type") != "PLANNER_RESPONSE":
        return False
    raw_tcs = step_obj.get("tool_calls")
    if not isinstance(raw_tcs, (list, tuple)):
        return False
    return any(isinstance(tc, dict) and str(tc.get("name") or "").strip() in tool_names for tc in raw_tcs)


def _has_finish_tool_call(step_obj: Optional[Dict[str, Any]]) -> bool:
    """Vérifie si un step PLANNER_RESPONSE invoque l'outil terminal `finish`."""
    return _has_clean_terminal_tool_call(step_obj, frozenset({"finish"}))


def parse_agent_transcript(
    transcript_path: Union[Path, str],
    catalog: Dict[str, Any],
    profile: str,
    secondary_profile: Optional[str] = "2027_standard",
    active_gap_cap_s: float = 120.0,
    start_line_idx: int = 0,
    end_line_idx: Optional[int] = None,
    *,
    self_archiving: bool = False,
    allow_cli_self_archive: bool = True,
    conversation_id: Optional[str] = None,
    subagent_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Extrait les métriques d'exécution, tokens, coûts et qualité d'un transcript (optionnellement fenêtré)."""
    tpath = Path(transcript_path)
    has_secondary = bool(secondary_profile and secondary_profile in catalog.get("profiles", {}))

    total_steps = malformed_lines = llm_calls = user_inputs = system_messages = 0
    error_steps = invalid_steps = shell_nonzero_steps = running_status_steps = tool_calls_total = 0
    tool_calls_by_name: Counter = Counter()
    error_reasons: Counter = Counter()

    input_tokens = cache_read_tokens = output_tokens = 0
    ctx_list: List[int] = []
    calls_over_100k = calls_over_128k = calls_over_200k = 0
    cost_acc, proj_acc = _empty_cost_acc(), _empty_cost_acc()
    model_stats: Dict[str, Dict[str, Any]] = {}
    unmatched_models: Set[str] = set()

    seen_planner_steps: Set[int] = set()
    rewind_calls = rewind_in = rewind_cache = rewind_out = 0
    rewind_cost_raw = 0.0
    cold_cache_calls = cold_cache_input_tokens = 0
    cold_cache_cost_raw = 0.0

    timestamps: List[datetime] = []
    prev_non_sys: Optional[Dict[str, Any]] = None
    last_non_sys: Optional[Dict[str, Any]] = None
    last_planner_resp: Optional[Dict[str, Any]] = None
    trailing_after_planner: List[Dict[str, Any]] = []
    last_step_index: Optional[int] = None
    user_prompt_texts: List[str] = []
    step_costs: List[Dict[str, Any]] = []
    tech_acc = TechnicalSignalAccumulator()
    ws_acc = WorkspaceSignalAccumulator()

    if tpath.is_file():
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
                    malformed_lines += 1
                    continue
                if not isinstance(obj, dict):
                    malformed_lines += 1
                    continue

                total_steps += 1
                raw_step_idx = obj.get("step_index")
                if isinstance(raw_step_idx, int) and not isinstance(raw_step_idx, bool):
                    if last_step_index is None or raw_step_idx > last_step_index:
                        last_step_index = raw_step_idx
                step_type, status = obj.get("type"), obj.get("status")
                dt = parse_iso_ts(obj.get("created_at"))
                if dt is not None:
                    timestamps.append(dt)
                tech_acc.feed(obj, dt)

                if step_type != "SYSTEM_MESSAGE":
                    prev_non_sys, last_non_sys = last_non_sys, obj
                    if step_type != "PLANNER_RESPONSE" and last_planner_resp is not None:
                        trailing_after_planner.append(obj)
                else:
                    system_messages += 1

                if step_type == "USER_INPUT":
                    user_inputs += 1
                    user_prompt_texts.append(str(obj.get("content") or "")[:500])
                if status == "ERROR":
                    error_steps += 1
                    err_txt = str(obj.get("error") or obj.get("content") or "unknown").strip()
                    error_reasons[err_txt.splitlines()[0][:120] if err_txt else "unknown"] += 1
                elif status == "INVALID":
                    invalid_steps += 1
                    err_txt = str(obj.get("error") or obj.get("content") or "INVALID").strip()
                    err_short = err_txt.splitlines()[0][:120] if err_txt else "INVALID"
                    error_reasons[f"[INVALID] {err_short}"[:120]] += 1
                elif status == "RUNNING":
                    running_status_steps += 1

                if step_type == "GENERIC" and status not in ("ERROR", "INVALID"):
                    shell_code = extract_shell_nonzero_code(obj.get("content"))
                    if shell_code is not None:
                        shell_nonzero_steps += 1
                        error_reasons[f"[shell exit={shell_code}] Command exited with code {shell_code}"[:120]] += 1

                for tc in obj.get("tool_calls") or []:
                    if isinstance(tc, dict):
                        tool_calls_total += 1
                        tool_calls_by_name[str(tc.get("name") or "unknown")] += 1
                        ws_acc.feed_tool_call(tc)

                if step_type == "PLANNER_RESPONSE":
                    last_planner_resp, trailing_after_planner = obj, []
                    llm_calls += 1
                    in_t = int(obj.get("input_tokens") or 0)
                    cache_t = int(obj.get("cache_read_tokens") or 0)
                    out_t = int(obj.get("output_tokens") or 0)
                    model_name = str(obj.get("model") or "unknown")
                    input_tokens += in_t
                    cache_read_tokens += cache_t
                    output_tokens += out_t

                    ctx = in_t + cache_t
                    ctx_list.append(ctx)
                    calls_over_100k += int(ctx > 100_000)
                    calls_over_128k += int(ctx > 128_000)
                    calls_over_200k += int(ctx > 200_000)

                    pricing, is_fb = resolve_model_pricing(model_name, catalog, profile)
                    if is_fb:
                        unmatched_models.add(model_name)
                    call_cost = compute_call_cost(in_t, cache_t, out_t, pricing)
                    _add_cost(cost_acc, call_cost)

                    if (cache_t == 0 and in_t > 10_000) or (
                        in_t > 50_000 and ctx > 0 and (cache_t / ctx) < 0.25
                    ):
                        cold_cache_calls += 1
                        cold_cache_input_tokens += in_t
                        cold_cache_cost_raw += float(call_cost["total_usd"])

                    if has_secondary and secondary_profile:
                        p2, _ = resolve_model_pricing(model_name, catalog, secondary_profile)
                        c2 = compute_call_cost(in_t, cache_t, out_t, p2)
                        _add_cost(proj_acc, c2)
                    else:
                        c2 = _empty_cost_acc()

                    m_entry = model_stats.setdefault(
                        model_name,
                        {
                            "calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
                            "_raw_cost": _empty_cost_acc(), "_raw_proj": _empty_cost_acc(), "is_fallback": is_fb,
                        },
                    )
                    m_entry["calls"] += 1
                    m_entry["input_tokens"] += in_t
                    m_entry["cache_read_tokens"] += cache_t
                    m_entry["output_tokens"] += out_t
                    _add_cost(m_entry["_raw_cost"], call_cost)
                    _add_cost(m_entry["_raw_proj"], c2)

                    step_idx = obj.get("step_index")
                    step_costs.append(
                        {
                            "line_idx": line_idx,
                            "step_index": step_idx if isinstance(step_idx, int) else None,
                            "tokens": in_t + cache_t + out_t,
                            "cost_usd": float(call_cost["total_usd"]),
                            "cost_2027_usd": float(c2["total_usd"]),
                        }
                    )
                    if isinstance(step_idx, int):
                        if step_idx in seen_planner_steps:
                            rewind_calls += 1
                            rewind_in += in_t
                            rewind_cache += cache_t
                            rewind_out += out_t
                            rewind_cost_raw += call_cost["total_usd"]
                        else:
                            seen_planner_steps.add(step_idx)

    total_prompt = input_tokens + cache_read_tokens
    total_tokens = total_prompt + output_tokens
    cache_hit_rate = round((cache_read_tokens / total_prompt) * 100.0, 2) if total_prompt > 0 else 0.0
    sorted_ctx = sorted(ctx_list)
    max_ctx = sorted_ctx[-1] if sorted_ctx else 0
    avg_ctx = round(sum(sorted_ctx) / len(sorted_ctx), 1) if sorted_ctx else 0.0
    p95_ctx = _percentile_sorted(sorted_ctx, 0.95)

    eff_cid = str(conversation_id or "").strip()
    if not eff_cid and tpath.parent.name == "logs" and tpath.parent.parent.name == ".system_generated":
        eff_cid = tpath.parents[2].name

    sub_state_error = str(subagent_state or "").strip().upper() in ("SUBAGENT_STATE_ERROR", "SUBAGENT_STATE_FAILED")
    clean_term = False
    last_non_sys_type: Optional[str] = None
    if last_non_sys is not None:
        last_non_sys_type, last_status = last_non_sys.get("type"), last_non_sys.get("status")
        has_terminal_error = (
            sub_state_error
            or last_status in ("ERROR", "INVALID")
            or (last_non_sys_type == "GENERIC" and extract_shell_nonzero_code(last_non_sys.get("content")) is not None)
        )
        if has_terminal_error:
            clean_term = False
        elif self_archiving:
            clean_term = True
        elif last_non_sys_type == "PLANNER_RESPONSE":
            clean_term = (
                (not bool(last_non_sys.get("tool_calls")))
                or (last_status == "DONE" and _has_clean_terminal_tool_call(last_non_sys))
                or (allow_cli_self_archive and _is_cli_archive_tool_call(last_non_sys, eff_cid))
            )
        elif last_non_sys_type == "GENERIC":
            if allow_cli_self_archive and last_status in ("DONE", "RUNNING", None, "") and _is_cli_archive_tool_call(prev_non_sys, eff_cid):
                clean_term = True
            else:
                trailing_all_clean = bool(trailing_after_planner) and all(
                    s.get("type") == "GENERIC" and s.get("status") == "DONE" and extract_shell_nonzero_code(s.get("content")) is None
                    for s in trailing_after_planner
                )
                clean_term = (
                    last_planner_resp is not None
                    and last_planner_resp.get("status") == "DONE"
                    and _has_clean_terminal_tool_call(last_planner_resp)
                    and trailing_all_clean
                )

    ts_sorted = sorted(timestamps)
    first_dt, last_dt = (ts_sorted[0], ts_sorted[-1]) if ts_sorted else (None, None)
    lifespan_s = round((last_dt - first_dt).total_seconds(), 2) if first_dt and last_dt else 0.0
    deltas = [max(0.0, (ts_sorted[i] - ts_sorted[i - 1]).total_seconds()) for i in range(1, len(ts_sorted))]
    active_s = round(sum(min(d, active_gap_cap_s) for d in deltas), 2)
    idle_gap_s = round(max(0.0, lifespan_s - active_s), 2)
    max_gap_s = round(max(deltas), 2) if deltas else 0.0

    models_out = {
        m_name: {
            "calls": m_data["calls"],
            "input_tokens": m_data["input_tokens"],
            "cache_read_tokens": m_data["cache_read_tokens"],
            "output_tokens": m_data["output_tokens"],
            "cost_usd": _round_cost(m_data["_raw_cost"], 6),
            "cost_usd_projection": _round_cost(m_data["_raw_proj"], 6),
            "is_fallback": m_data["is_fallback"],
            "_raw_cost": m_data["_raw_cost"],
            "_raw_proj": m_data["_raw_proj"],
        }
        for m_name, m_data in model_stats.items()
    }

    return {
        "transcript_missing": not tpath.is_file(),
        "malformed_lines": malformed_lines,
        "total_steps": total_steps,
        "llm_calls": llm_calls,
        "user_inputs": user_inputs,
        "system_messages": system_messages,
        "error_steps": error_steps,
        "invalid_steps": invalid_steps,
        "shell_nonzero_steps": shell_nonzero_steps,
        "total_anomalies": error_steps + invalid_steps + shell_nonzero_steps,
        "running_status_steps": running_status_steps,
        "tool_calls_total": tool_calls_total,
        "tool_calls_by_name": dict(tool_calls_by_name.most_common()),
        "error_reasons": [{"reason": r, "count": c} for r, c in error_reasons.most_common(5)],
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
        "total_prompt_tokens": total_prompt,
        "total_tokens": total_tokens,
        "cache_hit_rate_pct": cache_hit_rate,
        "max_context_tokens": max_ctx,
        "avg_context_tokens": avg_ctx,
        "p95_context_tokens": p95_ctx,
        "calls_over_100k_ctx": calls_over_100k,
        "calls_over_128k_ctx": calls_over_128k,
        "calls_over_200k_ctx": calls_over_200k,
        "cost_usd": _round_cost(cost_acc, 6),
        "cost_usd_projection": _round_cost(proj_acc, 6),
        "by_model": models_out,
        "unmatched_models": sorted(unmatched_models),
        "rewind_calls": rewind_calls,
        "rewind_input_tokens": rewind_in,
        "rewind_cache_read_tokens": rewind_cache,
        "rewind_output_tokens": rewind_out,
        "rewind_cost_usd": round(rewind_cost_raw, 6),
        "_raw_rewind_cost_usd": rewind_cost_raw,
        "cold_cache_calls": cold_cache_calls,
        "cold_cache_input_tokens": cold_cache_input_tokens,
        "cold_cache_cost_usd": round(cold_cache_cost_raw, 6),
        "_raw_cold_cache_cost_usd": cold_cache_cost_raw,
        "clean_termination": clean_term,
        "last_non_system_type": last_non_sys_type,
        "last_step_index": last_step_index,
        "first_ts": _fmt_iso_utc(first_dt),
        "last_ts": _fmt_iso_utc(last_dt),
        "lifespan_s": lifespan_s,
        "active_s": active_s,
        "idle_gap_s": idle_gap_s,
        "max_gap_s": max_gap_s,
        "_raw_cost": cost_acc,
        "_raw_proj": proj_acc,
        "_user_prompts": user_prompt_texts,
        "_step_costs": step_costs,
        "_reviewer_verdict_failed": _is_reviewer_verdict_failed(last_planner_resp),
        "_workspace_signals": ws_acc.finalize(),
        **tech_acc.finalize(),
    }
