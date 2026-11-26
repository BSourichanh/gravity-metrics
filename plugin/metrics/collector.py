#!/usr/bin/env python3
"""Façade d'agrégation et réexportations publiques du collecteur Gravity Metrics (100 % stdlib)."""

from collections import Counter, defaultdict
import importlib
import math
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_tr_reader = importlib.import_module("_shared.transcript_reader")
_shared_discover_known_repos = _tr_reader.discover_known_repos
_shared_normalize_worktree_path = _tr_reader.normalize_worktree_path
parse_iso_ts = _tr_reader.parse_iso_ts

from .agent_tree import (
    _detect_execution_mode,
    _extract_slash_commands,
    _is_direct_child_in_window,
    _is_spawn_in_window,
    discover_agent_tree,
    discover_workflow_children,
    is_bridge_child_conversation,
    list_gravity_conversations,
    load_known_child_conversation_ids,
    resolve_conversation_id_in_brain,
)
from .lifecycle import load_session_meta
from .milestone_analyzer import (
    _compute_pending_milestones,
    _compute_session_milestones,
    list_pending_sessions,
    resolve_gravity_window,
)
from .pricing import (
    CATEGORY_ORDER,
    SCHEMA_VERSION,
    _add_cost,
    _empty_cost_acc,
    _round_cost,
    load_pricing_catalog,
)
from .prompt_classifier import (
    METRICS_MARKER,
    METRICS_OPEN_MARKER,
    _clean_user_prompt,
    _extract_label_keywords,
    _fmt_iso_utc,
    _parse_iso_ts,
    classify_metrics_prompt,
    extract_user_request_text,
    is_gravity_metrics_capture_prompt,
    is_gravity_metrics_prompt,
    is_gravity_session_prompt,
    parse_architect_archive_prompt,
    read_gravity_profile_summary,
    slugify_label,
    suggest_auto_label,
)
from .transcript_index import (
    _get_transcript_index,
    parse_agent_transcript,
)

try:
    from .technical_signals import aggregate_technical_signals
except ImportError:
    def _pct_sorted(vals: List[int], pct: float) -> int:
        if not vals:
            return 0
        return int(vals[min(len(vals) - 1, max(0, math.ceil(pct * len(vals)) - 1))])

    def aggregate_technical_signals(agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Agrège les signaux techniques de tous les agents d'une session."""
        t_calls = t_chars = t_est = compactions = 0
        samples_ms: List[int] = []
        fb_avg: List[float] = []
        fb_p50: List[int] = []
        fb_p95: List[int] = []
        for ag in agents:
            if not isinstance(ag, dict):
                continue
            t_calls += int(ag.get("thinking_calls") or 0)
            t_chars += int(ag.get("thinking_chars") or 0)
            t_est += int(ag.get("thinking_est_tokens") or 0)
            compactions += int(ag.get("compaction_count") or 0)
            raw_s = ag.get("_latency_samples_ms")
            if isinstance(raw_s, list) and raw_s:
                samples_ms.extend(int(v) for v in raw_s if isinstance(v, (int, float)) and v > 0)
            elif int(ag.get("latency_p95_ms") or 0) > 0:
                fb_avg.append(float(ag.get("latency_avg_ms") or 0.0))
                fb_p50.append(int(ag.get("latency_p50_ms") or 0))
                fb_p95.append(int(ag.get("latency_p95_ms") or 0))
        if samples_ms:
            s_ms = sorted(samples_ms)
            return {
                "thinking_calls": t_calls, "thinking_chars": t_chars, "thinking_est_tokens": t_est,
                "compaction_count": compactions,
                "latency_avg_ms": round(sum(s_ms) / len(s_ms), 1),
                "latency_p50_ms": _pct_sorted(s_ms, 0.50),
                "latency_p95_ms": _pct_sorted(s_ms, 0.95),
                "latency_samples_count": len(s_ms),
            }
        if fb_p95:
            return {
                "thinking_calls": t_calls, "thinking_chars": t_chars, "thinking_est_tokens": t_est,
                "compaction_count": compactions,
                "latency_avg_ms": round(sum(fb_avg) / len(fb_avg), 1),
                "latency_p50_ms": _pct_sorted(sorted(fb_p50), 0.50),
                "latency_p95_ms": max(fb_p95),
                "latency_samples_count": len(fb_p95),
            }
        return {
            "thinking_calls": t_calls, "thinking_chars": t_chars, "thinking_est_tokens": t_est,
            "compaction_count": compactions,
            "latency_avg_ms": 0.0, "latency_p50_ms": 0, "latency_p95_ms": 0, "latency_samples_count": 0,
        }


try:
    from .workspace_resolver import (
        classify_workspace_kind,
        discover_known_repos,
        resolve_agent_workspace,
    )
except ImportError:
    _SPEC_TASK_RE = re.compile(r"(?:^|/)(?:specs|tasks)/", re.IGNORECASE)

    def discover_known_repos(
        root_workspace: str = "",
        root_workspace_path: Optional[Union[Path, str]] = None,
    ) -> Set[str]:
        return _shared_discover_known_repos(root_workspace, root_workspace_path)

    def classify_workspace_kind(
        workspace: str,
        *,
        root_workspace: str = "",
        root_workspace_path: Optional[Union[Path, str]] = None,
        workspace_path: Optional[Union[Path, str]] = None,
        has_atelier_markers: Optional[bool] = None,
    ) -> str:
        ws_clean, root_clean = str(workspace or "").strip(), str(root_workspace or "").strip()
        if not ws_clean:
            return "code"
        if workspace_path is not None:
            wp = Path(workspace_path)
            if wp.is_dir():
                if root_clean and ws_clean != root_clean:
                    return "code"
                return "atelier" if ((wp / "projects.json").is_file() or (wp / "specs").is_dir()) else "code"
        if root_clean and ws_clean != root_clean:
            return "code"
        if has_atelier_markers is not None:
            return "atelier" if has_atelier_markers else "code"
        if root_workspace_path is not None:
            rp = Path(root_workspace_path)
            if rp.is_dir():
                return "atelier" if ((rp / "projects.json").is_file() or (rp / "specs").is_dir()) else "code"
        return "atelier" if ws_clean.lower() == "gravity-studio" else "code"

    def resolve_agent_workspace(
        node: Dict[str, Any],
        ws_signals: Optional[Dict[str, Any]] = None,
        *,
        root_workspace: str = "root",
        root_workspace_path: Optional[Union[Path, str]] = None,
        known_repos: Optional[Set[str]] = None,
    ) -> str:
        repos = known_repos if known_repos is not None else discover_known_repos(root_workspace, root_workspace_path)
        root_ws = str(root_workspace or "").strip() or "root"
        bridge_ws = str(node.get("bridge_workspace") or "").strip()
        if bridge_ws:
            return _shared_normalize_worktree_path(
                bridge_ws, root_workspace=root_ws, root_workspace_path=root_workspace_path,
                known_repos=repos, is_workspace_uri=True,
            ) or bridge_ws
        sigs = ws_signals or {}
        w_votes: Counter = Counter()
        for p in list(sigs.get("write_paths") or []) + list(sigs.get("shell_dirs") or []):
            repo = _shared_normalize_worktree_path(
                str(p), root_workspace=root_ws, root_workspace_path=root_workspace_path, known_repos=repos,
            )
            if repo:
                w_votes[repo] += 1
        if w_votes:
            return min(w_votes.items(), key=lambda kv: (-kv[1], kv[0] == root_ws, kv[0]))[0]
        r_votes: Counter = Counter()
        for p in sigs.get("read_paths") or []:
            p_str = str(p)
            repo = _shared_normalize_worktree_path(
                p_str, root_workspace=root_ws, root_workspace_path=root_workspace_path, known_repos=repos,
            )
            if repo and not (repo == root_ws and _SPEC_TASK_RE.search(p_str.replace("\\", "/").strip())):
                r_votes[repo] += 1
        if r_votes:
            return min(r_votes.items(), key=lambda kv: (-kv[1], kv[0] == root_ws, kv[0]))[0]
        for uri in node.get("workspace_uris") or node.get("workspaceUris") or []:
            if isinstance(uri, str) and uri.strip():
                repo = _shared_normalize_worktree_path(
                    uri, root_workspace=root_ws, root_workspace_path=root_workspace_path,
                    known_repos=repos, is_workspace_uri=True,
                )
                if repo and repo != root_ws:
                    return repo
        return root_ws


def _collect_subtree_ids(start_id: str, children_map: Dict[str, List[str]]) -> Set[str]:
    result: Set[str] = set()
    stack = [start_id]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        stack.extend(children_map.get(cur, []))
    return result


def _aggregate_bucket(agents_subset: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw_cost, raw_proj = _empty_cost_acc(), _empty_cost_acc()
    in_tok = cache_tok = out_tok = calls = tools = errs = invs = shell_nz = 0
    ts_starts: List[str] = []
    ts_ends: List[str] = []
    for ag in agents_subset:
        _add_cost(raw_cost, ag["_raw_cost"])
        _add_cost(raw_proj, ag["_raw_proj"])
        in_tok += ag["input_tokens"]
        cache_tok += ag["cache_read_tokens"]
        out_tok += ag["output_tokens"]
        calls += ag["llm_calls"]
        tools += ag["tool_calls_total"]
        errs += ag["error_steps"]
        invs += ag["invalid_steps"]
        shell_nz += ag["shell_nonzero_steps"]
        if ag["first_ts"]:
            ts_starts.append(ag["first_ts"])
        if ag["last_ts"]:
            ts_ends.append(ag["last_ts"])
    tot_prompt = in_tok + cache_tok
    cache_rate = round((cache_tok / tot_prompt) * 100.0, 2) if tot_prompt > 0 else 0.0
    t0 = min(ts_starts) if ts_starts else None
    t1 = max(ts_ends) if ts_ends else None
    wall_span_s = 0.0
    if t0 and t1:
        dt0, dt1 = parse_iso_ts(t0), parse_iso_ts(t1)
        if dt0 and dt1:
            wall_span_s = round((dt1 - dt0).total_seconds(), 2)
    return {
        "agents": len(agents_subset),
        "llm_calls": calls,
        "tool_calls": tools,
        "error_steps": errs,
        "invalid_steps": invs,
        "shell_nonzero_steps": shell_nz,
        "total_anomalies": errs + invs + shell_nz,
        "input_tokens": in_tok,
        "cache_read_tokens": cache_tok,
        "output_tokens": out_tok,
        "total_tokens": tot_prompt + out_tok,
        "cache_hit_rate_pct": cache_rate,
        "cost_usd": _round_cost(raw_cost, 6),
        "cost_usd_projection": _round_cost(raw_proj, 6),
        "first_ts": t0,
        "last_ts": t1,
        "wall_span_s": wall_span_s,
    }


def collect_run_metrics(
    root_id: str,
    brain_dir: Union[Path, str],
    pricing_catalog_path: Union[Path, str],
    pricing_profile: str = "2026_nov_standard",
    secondary_profile: Optional[str] = "2027_standard",
    label: str = "",
    *,
    gravity_window: bool = False,
    current_prompt_is_metrics: bool = False,
    current_prompt: Optional[str] = None,
    full_conversation: bool = False,
    from_step: Optional[int] = None,
    to_step: Optional[int] = None,
    project: str = "",
    gravity_profile: str = "",
    bridge_db_path: Optional[Union[Path, str]] = None,
    store_dir: Optional[Union[Path, str]] = None,
    self_archiving: bool = False,
) -> Dict[str, Any]:
    """Collecte et agrège les métriques et coûts complets d'un arbre de conversation."""
    _ = bridge_db_path
    catalog = load_pricing_catalog(pricing_catalog_path)
    nodes = discover_agent_tree(root_id, brain_dir)
    if not nodes:
        raise ValueError(f"Aucun nœud découvert pour {root_id!r} dans {brain_dir}")

    apply_window = gravity_window or current_prompt_is_metrics or from_step is not None or to_step is not None
    root_node = nodes[0]
    meta_entry = load_session_meta(store_dir).get(root_id) or {}
    resolved_project = str(project or "").strip() or str(meta_entry.get("project") or "").strip()
    raw_cwd = str(meta_entry.get("cwd") or "").strip()
    root_ws_path = Path(raw_cwd) if (raw_cwd and (not resolved_project or Path(raw_cwd).name == resolved_project)) else None
    known_repos = discover_known_repos(resolved_project, root_ws_path)

    if apply_window:
        window_info = resolve_gravity_window(
            root_node["transcript_path"],
            current_prompt_is_metrics=current_prompt_is_metrics,
            current_prompt=current_prompt,
            full_conversation=full_conversation,
            from_step=from_step,
            to_step=to_step,
        )
        nodes_by_raw = {n["conversation_id"]: n for n in nodes}
        raw_children_map = {n["conversation_id"]: list(n["children_ids"]) for n in nodes}
        accepted_direct: List[str] = []
        kept_ids: Set[str] = {root_id}
        for cid in root_node["children_ids"]:
            cnode = nodes_by_raw.get(cid)
            if cnode is not None and _is_direct_child_in_window(cnode, window_info):
                accepted_direct.append(cid)
                kept_ids.update(_collect_subtree_ids(cid, raw_children_map))
        root_node["children_ids"] = accepted_direct
        nodes = [n for n in nodes if n["conversation_id"] in kept_ids]
    else:
        window_info = {
            "mode": "full_conversation", "start_line_idx": 0, "end_line_idx": None,
            "start_step_index": None, "end_step_index": None, "start_ts": None, "end_ts": None,
            "first_prompt": "", "excluded_before_steps": 0, "excluded_after_steps": 0,
            "window_empty": False, "has_gravity_anchor": False,
        }

    enriched_agents: List[Dict[str, Any]] = []
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    children_map: Dict[str, List[str]] = defaultdict(list)
    unmatched_models: Set[str] = set()
    missing_transcripts: List[str] = []
    malformed_lines_total = 0
    root_workspace = resolved_project or "root"

    for node in nodes:
        is_root = node["conversation_id"] == root_id
        parsed = parse_agent_transcript(
            node["transcript_path"],
            catalog=catalog,
            profile=pricing_profile,
            secondary_profile=secondary_profile,
            start_line_idx=window_info["start_line_idx"] if is_root else 0,
            end_line_idx=window_info["end_line_idx"] if is_root else None,
            self_archiving=bool(self_archiving and is_root),
            allow_cli_self_archive=is_root,
            conversation_id=node["conversation_id"],
            subagent_state=node.get("subagent_state"),
        )
        unmatched_models.update(parsed["unmatched_models"])
        if parsed["transcript_missing"]:
            missing_transcripts.append(node["conversation_id"])
        malformed_lines_total += parsed["malformed_lines"]

        ag_workspace = (
            root_workspace
            if (is_root and resolved_project)
            else resolve_agent_workspace(
                node, parsed.get("_workspace_signals"),
                root_workspace=root_workspace, root_workspace_path=root_ws_path, known_repos=known_repos,
            )
        )
        ag_ws_kind = classify_workspace_kind(ag_workspace, root_workspace=root_workspace, root_workspace_path=root_ws_path)
        merged = {**node, **parsed, "workspace": ag_workspace, "workspace_kind": ag_ws_kind}
        enriched_agents.append(merged)
        nodes_by_id[node["conversation_id"]] = merged
        if node["parent_id"]:
            children_map[node["parent_id"]].append(node["conversation_id"])

    sum_cost, sum_proj = _empty_cost_acc(), _empty_cost_acc()
    total_in = total_cache = total_out = 0
    total_llm_calls = total_tool_calls = total_user_inputs = total_error_steps = total_steps = 0
    total_invalid_steps = total_shell_nonzero_steps = 0
    sum_lifespans = sum_active = 0.0
    max_depth = 0
    all_t0: List[str] = []
    all_t1: List[str] = []
    model_agg: Dict[str, Dict[str, Any]] = {}
    tool_counter: Counter = Counter()
    error_reason_counter: Counter = Counter()

    for ag in enriched_agents:
        _add_cost(sum_cost, ag["_raw_cost"])
        _add_cost(sum_proj, ag["_raw_proj"])
        total_in += ag["input_tokens"]
        total_cache += ag["cache_read_tokens"]
        total_out += ag["output_tokens"]
        total_llm_calls += ag["llm_calls"]
        total_tool_calls += ag["tool_calls_total"]
        total_user_inputs += ag["user_inputs"]
        total_error_steps += ag["error_steps"]
        total_invalid_steps += ag["invalid_steps"]
        total_shell_nonzero_steps += ag["shell_nonzero_steps"]
        total_steps += ag["total_steps"]
        sum_lifespans += ag["lifespan_s"]
        sum_active += ag["active_s"]
        if ag["depth"] > max_depth:
            max_depth = ag["depth"]
        if ag["first_ts"]:
            all_t0.append(ag["first_ts"])
        if ag["last_ts"]:
            all_t1.append(ag["last_ts"])
        for tname, cnt in ag["tool_calls_by_name"].items():
            tool_counter[tname] += cnt
        for er in ag["error_reasons"]:
            error_reason_counter[er["reason"]] += er["count"]
        for m_name, m_data in ag["by_model"].items():
            entry = model_agg.setdefault(
                m_name,
                {
                    "calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0,
                    "_raw_cost": _empty_cost_acc(), "_raw_proj": _empty_cost_acc(),
                    "is_fallback": m_data["is_fallback"], "agents_using": 0,
                },
            )
            entry["calls"] += m_data["calls"]
            entry["input_tokens"] += m_data["input_tokens"]
            entry["cache_read_tokens"] += m_data["cache_read_tokens"]
            entry["output_tokens"] += m_data["output_tokens"]
            _add_cost(entry["_raw_cost"], m_data["_raw_cost"])
            _add_cost(entry["_raw_proj"], m_data["_raw_proj"])
            entry["agents_using"] += 1

    total_prompt = total_in + total_cache
    total_tokens = total_prompt + total_out
    cache_hit_rate = round((total_cache / total_prompt) * 100.0, 2) if total_prompt > 0 else 0.0
    t0_str = min(all_t0) if all_t0 else None
    t1_str = max(all_t1) if all_t1 else None
    wall_clock_s = 0.0
    if t0_str and t1_str:
        dt0, dt1 = parse_iso_ts(t0_str), parse_iso_ts(t1_str)
        if dt0 and dt1:
            wall_clock_s = round((dt1 - dt0).total_seconds(), 2)

    error_denom = total_tool_calls if total_tool_calls > 0 else max(total_llm_calls, 1)
    error_rate_pct = round((total_error_steps / error_denom) * 100.0, 2) if total_steps > 0 else 0.0
    total_cost_float = sum_cost["total_usd"]

    by_category: Dict[str, Any] = {}
    cat_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        cat_groups[ag["category"]].append(ag)
    for cat in CATEGORY_ORDER:
        subset = cat_groups.get(cat, [])
        if not subset and cat == "other":
            continue
        agg = _aggregate_bucket(subset)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in subset) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_category[cat] = agg

    by_model: Dict[str, Any] = {}
    for m_name, m_data in sorted(model_agg.items(), key=lambda kv: (-kv[1]["calls"], -kv[1]["_raw_cost"]["total_usd"])):
        m_prompt = m_data["input_tokens"] + m_data["cache_read_tokens"]
        by_model[m_name] = {
            "calls": m_data["calls"],
            "agents_using": m_data["agents_using"],
            "input_tokens": m_data["input_tokens"],
            "cache_read_tokens": m_data["cache_read_tokens"],
            "output_tokens": m_data["output_tokens"],
            "total_tokens": m_prompt + m_data["output_tokens"],
            "cost_usd": _round_cost(m_data["_raw_cost"], 6),
            "cost_usd_projection": _round_cost(m_data["_raw_proj"], 6),
            "cost_share_pct": round((m_data["_raw_cost"]["total_usd"] / total_cost_float) * 100.0, 2) if total_cost_float > 0 else 0.0,
            "is_fallback": m_data["is_fallback"],
        }

    pivot_node = next((ag for ag in enriched_agents if ag["type_name"] == "teamwork_orchestrator"), nodes_by_id[root_id])
    pivot_children = [nodes_by_id[cid] for cid in children_map.get(pivot_node["conversation_id"], []) if cid in nodes_by_id]
    pivot_children.sort(key=lambda a: (a["first_ts"] or "9999", a["spawn_step_index"] or 0, a["conversation_id"]))
    role_counts = Counter(ch["role"] for ch in pivot_children)

    phase_records: List[Tuple[str, List[Dict[str, Any]]]] = []
    assigned_ids: Set[str] = set()
    for ch in pivot_children:
        subtree_ids = _collect_subtree_ids(ch["conversation_id"], children_map)
        assigned_ids.update(subtree_ids)
        phase_name = f"{ch['role']} ({ch['conversation_id'][:8]})" if role_counts[ch["role"]] > 1 else ch["role"]
        phase_records.append((phase_name, [nodes_by_id[sid] for sid in subtree_ids if sid in nodes_by_id]))

    overhead_agents = [ag for ag in enriched_agents if ag["conversation_id"] not in assigned_ids]
    if overhead_agents:
        phase_records.append(("Orchestration & Cadrage (Overhead)", overhead_agents))
    phase_records.sort(key=lambda item: (min((a["first_ts"] for a in item[1] if a["first_ts"]), default="9999"), item[0]))

    by_phase: Dict[str, Any] = {}
    for pname, pagents in phase_records:
        agg = _aggregate_bucket(pagents)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in pagents) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_phase[pname] = agg

    ws_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        ws_groups[ag["workspace"]].append(ag)
    by_workspace: Dict[str, Any] = {}
    for ws_name, subset in sorted(
        ws_groups.items(),
        key=lambda kv: (kv[0] != root_workspace, -sum(a["_raw_cost"]["total_usd"] for a in kv[1]), kv[0]),
    ):
        agg = _aggregate_bucket(subset)
        agg["workspace_kind"] = classify_workspace_kind(ws_name, root_workspace=root_workspace, root_workspace_path=root_ws_path)
        agg["cost_share_pct"] = (
            round((sum(a["_raw_cost"]["total_usd"] for a in subset) / total_cost_float) * 100.0, 2)
            if total_cost_float > 0
            else 0.0
        )
        by_workspace[ws_name] = agg

    tech_agg = aggregate_technical_signals(enriched_agents)

    dup_groups_map: Dict[Tuple[Optional[str], str, str], List[Dict[str, Any]]] = defaultdict(list)
    for ag in enriched_agents:
        if ag["parent_id"] is not None and ag.get("source") != "bridge":
            dup_groups_map[(ag["parent_id"], ag["type_name"], ag["role"])].append(ag)

    dup_groups_out: List[Dict[str, Any]] = []
    dup_agent_count = dup_calls = 0
    dup_cost_raw = 0.0
    for (pid, tname, rl), grp in dup_groups_map.items():
        if len(grp) <= 1:
            continue
        grp_sorted = sorted(grp, key=lambda a: (a["first_ts"] or "9999", a["spawn_step_index"] or 0, a["conversation_id"]))
        remediation_excluded = 0
        if str(tname or "").strip().lower() == "reviewer":
            dups = []
            for idx in range(1, len(grp_sorted)):
                cur, prev_rev = grp_sorted[idx], grp_sorted[idx - 1]
                if idx == 1 and cur.get("source") == "workflow" and prev_rev.get("source") == "workflow" and prev_rev.get("_reviewer_verdict_failed"):
                    remediation_excluded += 1
                    continue
                dups.append(cur)
        else:
            dups = grp_sorted[1:]
        if not dups:
            continue
        d_calls = sum(d["llm_calls"] for d in dups)
        d_cost = sum(d["_raw_cost"]["total_usd"] for d in dups)
        dup_agent_count += len(dups)
        dup_calls += d_calls
        dup_cost_raw += d_cost
        parent_role = nodes_by_id[pid]["role"] if pid in nodes_by_id else (pid or "")
        dup_groups_out.append(
            {
                "parent_role": parent_role, "type_name": tname, "role": rl,
                "spawn_count": len(grp_sorted) - remediation_excluded,
                "duplicate_count": len(dups), "duplicate_calls": d_calls,
                "duplicate_cost_usd": round(d_cost, 6),
            }
        )

    rewind_calls_total = sum(a["rewind_calls"] for a in enriched_agents)
    rewind_cost_total = sum(a["_raw_rewind_cost_usd"] for a in enriched_agents)
    cold_cache_calls_total = sum(a["cold_cache_calls"] for a in enriched_agents)
    cold_cache_in_total = sum(a["cold_cache_input_tokens"] for a in enriched_agents)
    cold_cache_cost_total = sum(a["_raw_cold_cache_cost_usd"] for a in enriched_agents)
    cold_cache_share_pct = round((cold_cache_cost_total / total_cost_float) * 100.0, 2) if total_cost_float > 0 else 0.0
    interrupted = [a for a in enriched_agents if not a["clean_termination"]]
    interrupted_cost = sum(a["_raw_cost"]["total_usd"] for a in interrupted)
    interrupted_calls = sum(a["llm_calls"] for a in interrupted)
    max_ctx_overall = max((a["max_context_tokens"] for a in enriched_agents), default=0)

    verif_cost = sum(a["_raw_cost"]["total_usd"] for a in cat_groups.get("verification", []))
    build_plus_test = sum(a["_raw_cost"]["total_usd"] for a in cat_groups.get("build", [])) + sum(
        a["_raw_cost"]["total_usd"] for a in cat_groups.get("testing", [])
    )
    verif_to_build_ratio = round(verif_cost / build_plus_test, 2) if build_plus_test > 0 else (None if verif_cost > 0 else 0.0)
    execution_mode, features_used = _detect_execution_mode(enriched_agents, tool_counter, root_id)

    agents_clean: List[Dict[str, Any]] = []
    for ag in sorted(enriched_agents, key=lambda x: -x["_raw_cost"]["total_usd"]):
        clean_models = {
            m_name: {k: v for k, v in m_val.items() if not k.startswith("_")}
            for m_name, m_val in ag["by_model"].items()
        }
        ag_out = {k: v for k, v in ag.items() if not k.startswith("_")}
        ag_out["by_model"] = clean_models
        agents_clean.append(ag_out)

    milestones = _compute_session_milestones(
        root_id=root_id,
        root_transcript_path=root_node["transcript_path"],
        window_info=window_info,
        enriched_agents=enriched_agents,
        bridge_pending_jobs=[],
        total_cost_float=total_cost_float,
        is_archived=False,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "label": label or f"run-{root_id[:8]}",
        "root_conversation_id": root_id,
        "project": resolved_project,
        "gravity_profile": str(gravity_profile or "").strip(),
        "window": {
            "mode": window_info["mode"],
            "start_step_index": window_info["start_step_index"],
            "end_step_index": window_info["end_step_index"],
            "excluded_before_steps": window_info["excluded_before_steps"],
            "excluded_after_steps": window_info["excluded_after_steps"],
            "has_gravity_anchor": window_info["has_gravity_anchor"],
            "first_prompt": window_info["first_prompt"],
        },
        "pricing_catalog_version": str(catalog.get("catalog_version", "unknown")),
        "pricing_profile": pricing_profile,
        "secondary_pricing_profile": (
            secondary_profile if (secondary_profile and secondary_profile in catalog.get("profiles", {})) else None
        ),
        "summary": {
            "total_agents": len(enriched_agents),
            "max_tree_depth": max_depth,
            "total_steps": total_steps,
            "total_llm_calls": total_llm_calls,
            "total_tool_calls": total_tool_calls,
            "total_user_inputs": total_user_inputs,
            "total_error_steps": total_error_steps,
            "total_invalid_steps": total_invalid_steps,
            "total_shell_nonzero_steps": total_shell_nonzero_steps,
            "total_anomalies": total_error_steps + total_invalid_steps + total_shell_nonzero_steps,
            "error_rate_pct": error_rate_pct,
            "input_tokens": total_in,
            "cache_read_tokens": total_cache,
            "output_tokens": total_out,
            "total_prompt_tokens": total_prompt,
            "total_tokens": total_tokens,
            "cache_hit_rate_pct": cache_hit_rate,
            "cost_usd": _round_cost(sum_cost, 6),
            "cost_usd_projection": _round_cost(sum_proj, 6),
            "first_ts": t0_str,
            "last_ts": t1_str,
            "wall_clock_s": wall_clock_s,
            "sum_agent_lifespans_s": round(sum_lifespans, 2),
            "sum_agent_active_s": round(sum_active, 2),
            "parallelism_factor": round(sum_lifespans / wall_clock_s, 2) if wall_clock_s > 0 else 1.0,
            "execution_mode": execution_mode,
            "features_used": features_used,
            "thinking_calls": tech_agg["thinking_calls"],
            "thinking_chars": tech_agg["thinking_chars"],
            "thinking_est_tokens": tech_agg["thinking_est_tokens"],
            "compaction_count": tech_agg["compaction_count"],
            "latency_avg_ms": tech_agg["latency_avg_ms"],
            "latency_p50_ms": tech_agg["latency_p50_ms"],
            "latency_p95_ms": tech_agg["latency_p95_ms"],
        },
        "technical_signals": tech_agg,
        "by_category": by_category,
        "by_model": by_model,
        "by_phase": by_phase,
        "by_workspace": by_workspace,
        "milestones": milestones,
        "bridge_pending_jobs": [],
        "bridge": {"children_count": 0, "workspaces": [], "pending_jobs": []},
        "tool_usage": {
            "total_calls": total_tool_calls,
            "by_tool": dict(tool_counter.most_common()),
            "top_errors": [{"reason": r, "count": c} for r, c in error_reason_counter.most_common(10)],
        },
        "efficiency_and_waste": {
            "verification_to_build_cost_ratio": verif_to_build_ratio,
            "duplicate_role_spawns": {
                "duplicate_agent_count": dup_agent_count,
                "duplicate_calls": dup_calls,
                "duplicate_cost_usd": round(dup_cost_raw, 6),
                "groups": sorted(dup_groups_out, key=lambda g: -g["duplicate_cost_usd"]),
            },
            "rewind_waste": {"rewind_calls": rewind_calls_total, "rewind_cost_usd": round(rewind_cost_total, 6)},
            "cold_cache_waste": {
                "calls": cold_cache_calls_total,
                "input_tokens": cold_cache_in_total,
                "cost_usd": round(cold_cache_cost_total, 6),
                "share_of_total_cost_pct": cold_cache_share_pct,
            },
            "interrupted_agents": {
                "count": len(interrupted),
                "total_llm_calls": interrupted_calls,
                "total_cost_usd": round(interrupted_cost, 6),
            },
            "context_bloat": {
                "max_context_tokens_overall": max_ctx_overall,
                "calls_over_100k_ctx": sum(a["calls_over_100k_ctx"] for a in enriched_agents),
                "calls_over_128k_ctx": sum(a["calls_over_128k_ctx"] for a in enriched_agents),
                "calls_over_200k_ctx": sum(a["calls_over_200k_ctx"] for a in enriched_agents),
            },
        },
        "agents": agents_clean,
        "data_quality": {
            "unmatched_models": sorted(unmatched_models),
            "missing_transcripts": missing_transcripts,
            "malformed_lines": malformed_lines_total,
        },
    }
