#!/usr/bin/env python3
"""Analyse des jalons de session et détection des sessions en attente d'enregistrement (100 % stdlib)."""

from datetime import datetime, timezone
import importlib
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

parse_transcript_steps = importlib.import_module("_shared.transcript_reader").parse_transcript_steps

from .agent_tree import (
    _collect_known_gravity_session_ids,
    _discover_direct_subagent_children,
    _is_spawn_in_window,
    discover_workflow_children,
    is_bridge_child_conversation,
    load_known_child_conversation_ids,
)
from .lifecycle import (
    load_active_captures,
    load_ignored_sessions,
    load_session_meta,
)
from .pricing import classify_agent
from .prompt_classifier import _clean_pending_title, _extract_label_keywords, extract_user_request_text
from .transcript_index import _get_transcript_index

_CONCEPTION_AGENT_TYPES = frozenset({"research", "discovery", "teamwork_explorer", "teamwork_spec_miner"})
_PLAN_AGENT_TYPES = frozenset({"planner", "teamwork_interviewer"})
_VERIF_AGENT_TYPES = frozenset({"reviewer", "teamwork_reviewer", "teamwork_challenger", "teamwork_auditor"})
_PLAN_PROMPT_RE = re.compile(
    r"^/(?:plan(?::[a-z0-9_-]+)?|gravity-architect(?::gravity-architect)?\s+(?:handoff|tasks|plan))\b",
    re.IGNORECASE,
)


def resolve_gravity_window(
    transcript_path: Union[Path, str],
    *,
    current_prompt_is_metrics: bool = False,
    current_prompt: Optional[str] = None,
    full_conversation: bool = False,
    from_step: Optional[int] = None,
    to_step: Optional[int] = None,
) -> Dict[str, Any]:
    """Résout la fenêtre de lignes [début /gravity → avant /gravity-metrics] sur le transcript racine."""
    tx = _get_transcript_index(Path(transcript_path))
    entries: List[Dict[str, Any]] = tx["entries"]
    if not entries:
        return {
            "mode": "full_conversation" if full_conversation else "empty",
            "start_line_idx": 0,
            "end_line_idx": None,
            "start_step_index": None,
            "end_step_index": None,
            "start_ts": None,
            "end_ts": None,
            "first_prompt": "",
            "excluded_before_steps": 0,
            "excluded_after_steps": 0,
            "window_empty": True,
            "has_gravity_anchor": False,
        }

    def _has_user_after(idx_in_entries: int) -> bool:
        return any(e["is_user"] for e in entries[idx_in_entries + 1 :])

    def _turn_has_recorded_marker(idx_in_entries: int) -> bool:
        for e in entries[idx_in_entries + 1 :]:
            if e["is_user"]:
                break
            if e["has_marker"]:
                return True
        return False

    def _is_capture_boundary_entry(idx_in_entries: int) -> bool:
        e = entries[idx_in_entries]
        act = e.get("metrics_action") or ""
        if act in ("capture", "stop"):
            return True
        if act == "open":
            if _turn_has_recorded_marker(idx_in_entries):
                return True
            if not _has_user_after(idx_in_entries) and not any(x.get("has_open_marker") for x in entries[idx_in_entries + 1 :]):
                return True
        return False

    def _is_metrics_entry_pending(idx_in_entries: int) -> bool:
        tail = entries[idx_in_entries + 1 :]
        if any(
            e["type"] in ("PLANNER_RESPONSE", "SYSTEM_MESSAGE", "ERROR_MESSAGE", "USER_INPUT")
            or e["has_marker"] or e.get("has_open_marker")
            for e in tail
        ):
            return False
        if current_prompt is not None and extract_user_request_text(current_prompt)[:160] != entries[idx_in_entries]["clean_prompt"]:
            return False
        return True

    def _is_completed_capture_boundary(idx_in_entries: int) -> bool:
        for e in entries[idx_in_entries + 1 :]:
            if e["is_user"]:
                break
            if e["has_marker"] or e["is_planner"]:
                return True
        return False

    capture_positions = [i for i in range(len(entries)) if _is_capture_boundary_entry(i)]
    has_gravity_anchor = False

    if from_step is not None or to_step is not None:
        start_pos = (
            next((i for i, e in enumerate(entries) if e["step_index"] is not None and e["step_index"] >= from_step), len(entries))
            if from_step is not None
            else 0
        )
        end_pos = (
            next((i for i in range(start_pos, len(entries)) if entries[i]["step_index"] is not None and entries[i]["step_index"] >= to_step), len(entries))
            if to_step is not None
            else len(entries)
        )
        mode = "custom"
    elif full_conversation:
        start_pos, end_pos = 0, len(entries)
        if current_prompt_is_metrics and capture_positions and _is_metrics_entry_pending(capture_positions[-1]):
            end_pos = capture_positions[-1]
        mode = "full_conversation"
    else:
        current_m_pos: Optional[int] = None
        prev_m_pos: Optional[int] = None
        if capture_positions:
            last_m = capture_positions[-1]
            if current_prompt_is_metrics:
                if _is_metrics_entry_pending(last_m):
                    current_m_pos = last_m
                    prev_candidates = [j for j in capture_positions[:-1] if _is_completed_capture_boundary(j)]
                else:
                    prev_candidates = [j for j in capture_positions if _is_completed_capture_boundary(j)]
                prev_m_pos = prev_candidates[-1] if prev_candidates else None
            elif _has_user_after(last_m):
                prev_m_pos = last_m
            else:
                completed = [j for j in capture_positions if _is_completed_capture_boundary(j)]
                if completed:
                    current_m_pos = completed[-1]
                    prev_m_pos = completed[-2] if len(completed) >= 2 else None
                else:
                    current_m_pos = last_m

        end_pos = current_m_pos if current_m_pos is not None else len(entries)
        if prev_m_pos is not None:
            fallback_start_pos = prev_m_pos + 1
            while fallback_start_pos < end_pos and not entries[fallback_start_pos]["is_user"]:
                fallback_start_pos += 1
        else:
            fallback_start_pos = 0

        start_cmd_pos = None
        for i in range(fallback_start_pos, end_pos):
            if entries[i].get("metrics_action") == "start":
                start_cmd_pos = i
        g_pos = next((i for i in range(fallback_start_pos, end_pos) if entries[i]["is_gravity"]), None)

        if start_cmd_pos is not None and (g_pos is None or start_cmd_pos > g_pos):
            pos = start_cmd_pos + 1
            while pos < end_pos and not entries[pos]["is_user"]:
                pos += 1
            start_pos, mode, has_gravity_anchor = pos, "custom", (g_pos is not None)
        elif g_pos is not None:
            start_pos, mode, has_gravity_anchor = g_pos, "gravity_session", True
        else:
            start_pos = fallback_start_pos
            mode = "since_last_metrics" if prev_m_pos is not None else "full_conversation"

    window_empty = start_pos >= end_pos
    start_line_idx = entries[start_pos]["line_idx"] if start_pos < len(entries) else entries[-1]["line_idx"] + 1
    end_line_idx = entries[end_pos]["line_idx"] if end_pos < len(entries) else None
    slice_entries = entries[start_pos:end_pos]
    start_step_index = next((e["step_index"] for e in slice_entries if e["step_index"] is not None), None)
    if start_step_index is None:
        prev_steps = [e["step_index"] for e in entries[:start_pos] if e["step_index"] is not None]
        if prev_steps:
            start_step_index = prev_steps[-1] + 1
    end_step_index = entries[end_pos]["step_index"] if end_pos < len(entries) else None
    start_ts = next((e["created_at"] for e in slice_entries if e.get("created_at")), None)
    end_ts = entries[end_pos].get("created_at") if end_pos < len(entries) else None
    first_prompt = next(
        (e["clean_prompt"] for e in slice_entries if e["is_user"] and e["clean_prompt"] and _extract_label_keywords(e["clean_prompt"])),
        "",
    ) or next((e["clean_prompt"] for e in slice_entries if e["is_user"] and e["clean_prompt"]), "")

    return {
        "mode": mode,
        "start_line_idx": start_line_idx,
        "end_line_idx": end_line_idx,
        "start_step_index": start_step_index,
        "end_step_index": end_step_index,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "first_prompt": first_prompt,
        "excluded_before_steps": start_pos,
        "excluded_after_steps": len(entries) - end_pos,
        "window_empty": window_empty,
        "has_gravity_anchor": has_gravity_anchor,
    }


def _classify_milestone_phase(ag: Dict[str, Any]) -> str:
    """Classe un agent enfant dans l'une des 4 phases (`conception`, `planification`, `production`, `validation`)."""
    tn = str(ag.get("type_name") or "").strip().lower()
    rl = str(ag.get("role") or "").strip().lower()
    cat = classify_agent(tn, rl)
    if tn in _VERIF_AGENT_TYPES or cat == "verification":
        return "validation"
    if tn in _PLAN_AGENT_TYPES or "planner" in rl:
        return "planification"
    if tn in _CONCEPTION_AGENT_TYPES or cat == "discovery":
        return "conception"
    return "production"


def _is_prod_subagent(ag: Dict[str, Any]) -> bool:
    """Retourne True si un sous-agent relève de `production` ou `validation` (hors conception/planification)."""
    return _classify_milestone_phase(ag) in ("production", "validation")


def _analyze_milestones_window(
    win_entries: List[Dict[str, Any]],
    *,
    root_links: List[Dict[str, Any]],
    bridge_agents: List[Dict[str, Any]],
    wf_agents: List[Dict[str, Any]],
    prod_subagents: List[Dict[str, Any]],
    pending_jobs: List[Dict[str, Any]],
    subagent_agents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Analyse les signaux de jalons d'une fenêtre (partagé entre collecte et pending)."""
    all_child_agents = bridge_agents + wf_agents + (subagent_agents if subagent_agents is not None else prod_subagents)
    plan_agents = [a for a in all_child_agents if _classify_milestone_phase(a) == "planification"]
    prod_agents = [a for a in all_child_agents if _classify_milestone_phase(a) == "production"]
    verif_agents = [a for a in all_child_agents if _classify_milestone_phase(a) == "validation"]

    has_architect = any(e.get("is_architect") for e in win_entries)
    has_bridge = bool(bridge_agents or root_links)
    has_swarm = any(e.get("is_swarm") or "swarm" in (e.get("tool_names") or []) for e in win_entries)
    has_wf = bool(wf_agents) or any("run_workflow" in (e.get("tool_names") or []) for e in win_entries)
    has_direct_edit = any(e.get("has_direct_edit_tool") for e in win_entries)
    is_4_phases = has_bridge or has_architect or has_swarm or bool(prod_subagents) or bool(plan_agents) or has_wf

    plan_child_steps = [
        int(a["spawn_step_index"])
        for a in plan_agents
        if isinstance(a.get("spawn_step_index"), int)
    ]
    plan_entry_steps = [
        int(e["step_index"])
        for e in win_entries
        if isinstance(e.get("step_index"), int)
        and (
            "update_steps" in (e.get("tool_names") or [])
            or (e.get("is_user") and bool(_PLAN_PROMPT_RE.match(str(e.get("clean_prompt") or ""))))
        )
    ]
    all_plan_steps = plan_child_steps + plan_entry_steps
    first_plan_step = min(all_plan_steps) if all_plan_steps else None
    has_plan_activity = bool(plan_agents) or first_plan_step is not None

    link_steps = [int(lnk["spawn_step_index"]) for lnk in root_links if isinstance(lnk.get("spawn_step_index"), int)]
    prod_child_steps = [
        int(a["spawn_step_index"])
        for a in prod_agents
        if isinstance(a.get("spawn_step_index"), int)
    ]
    has_prod_wf_or_none = (not wf_agents) or any(_classify_milestone_phase(a) == "production" for a in wf_agents)
    tool_prod_steps = [
        int(e["step_index"])
        for e in win_entries
        if isinstance(e.get("step_index"), int)
        and (
            e.get("is_swarm")
            or "swarm" in (e.get("tool_names") or [])
            or (has_prod_wf_or_none and "run_workflow" in (e.get("tool_names") or []))
            or e.get("has_direct_edit_tool")
        )
    ]
    all_prod_steps = link_steps + prod_child_steps + tool_prod_steps
    first_prod_step = min(all_prod_steps) if all_prod_steps else None
    last_prod_step = max(all_prod_steps) if all_prod_steps else None

    verif_step: Optional[int] = None
    archive_step: Optional[int] = None
    for e in win_entries:
        st_i = e.get("step_index")
        if (e.get("is_architect_archive") or e.get("has_cli_record_or_archive")) and archive_step is None and isinstance(st_i, int):
            archive_step = st_i
        if not pending_jobs and isinstance(st_i, int):
            after_prod = (
                (last_prod_step is not None and st_i > last_prod_step)
                or (last_prod_step is None and bool(prod_subagents) and bool(e.get("has_push_or_verif")))
            )
            if after_prod and verif_step is None:
                if e.get("has_push_or_verif"):
                    verif_step = st_i
                elif e.get("is_user") and "[GRAVITY-BRIDGE" in str(e.get("clean_prompt") or ""):
                    verif_step = st_i
                elif has_bridge and e.get("is_planner"):
                    verif_step = st_i
    if verif_step is None and not pending_jobs:
        verif_child_steps = [
            int(a["spawn_step_index"])
            for a in verif_agents
            if isinstance(a.get("spawn_step_index"), int)
            and (last_prod_step is None or int(a["spawn_step_index"]) > last_prod_step)
        ]
        if verif_child_steps:
            verif_step = min(verif_child_steps)

    has_close_cmd = any(e.get("is_architect_archive") or e.get("has_cli_record_or_archive") for e in win_entries)
    has_prod_activity = bool(
        first_prod_step is not None or bridge_agents or prod_agents or prod_subagents
        or has_swarm or (has_wf and has_prod_wf_or_none) or has_direct_edit or pending_jobs
    )
    has_verif_activity = bool(verif_agents) or verif_step is not None or has_close_cmd
    return {
        "is_4_phases": is_4_phases,
        "has_bridge": has_bridge,
        "first_plan_step": first_plan_step,
        "has_plan_activity": has_plan_activity,
        "first_prod_step": first_prod_step,
        "last_prod_step": last_prod_step,
        "verif_step": verif_step,
        "archive_step": archive_step,
        "has_close_cmd": has_close_cmd,
        "has_prod_activity": has_prod_activity,
        "has_verif_activity": has_verif_activity,
    }


def _compute_session_milestones(
    *,
    root_id: str,
    root_transcript_path: Union[Path, str],
    window_info: Dict[str, Any],
    enriched_agents: List[Dict[str, Any]],
    bridge_pending_jobs: List[Dict[str, Any]],
    total_cost_float: float,
    is_archived: bool = False,
) -> Dict[str, Any]:
    """Déduit les 4 jalons alignés (`conception`, `planification`, `production`, `validation`) ou repli 2 jalons."""
    tx = _get_transcript_index(root_transcript_path)
    entries = tx.get("entries") or []
    s_line = int(window_info.get("start_line_idx") or 0)
    e_line = window_info.get("end_line_idx")
    win_entries = [e for e in entries if e["line_idx"] >= s_line and (e_line is None or e["line_idx"] < e_line)]

    root_agent = next((a for a in enriched_agents if a["conversation_id"] == root_id), None)
    bridge_agents = [a for a in enriched_agents if a.get("source") == "bridge"]
    wf_agents = [a for a in enriched_agents if a.get("source") == "workflow"]
    subagent_agents = [a for a in enriched_agents if a.get("source") == "subagent"]
    prod_subagents = [a for a in subagent_agents if _is_prod_subagent(a)]
    root_links: List[Dict[str, Any]] = []

    def _empty_phase(pid: str, plabel: str) -> Dict[str, Any]:
        return {
            "id": pid, "label": plabel, "status": "pending",
            "agents": 0, "llm_calls": 0, "tokens": 0,
            "cost_usd": 0.0, "cost_2027_usd": 0.0, "cost_share_pct": 0.0,
        }

    def _add_agent_to_phase(phase: Dict[str, Any], ag: Dict[str, Any]) -> None:
        phase["agents"] += 1
        phase["llm_calls"] += int(ag.get("llm_calls") or 0)
        phase["tokens"] += int(ag.get("total_tokens") or 0)
        phase["cost_usd"] += float((ag.get("_raw_cost") or {}).get("total_usd") or 0.0)
        phase["cost_2027_usd"] += float((ag.get("_raw_proj") or {}).get("total_usd") or 0.0)

    sig = _analyze_milestones_window(
        win_entries,
        root_links=root_links,
        bridge_agents=bridge_agents,
        wf_agents=wf_agents,
        prod_subagents=prod_subagents,
        pending_jobs=bridge_pending_jobs,
        subagent_agents=subagent_agents,
    )

    if not sig["is_4_phases"]:
        p_prod = _empty_phase("production", "Production")
        p_close = _empty_phase("cloture", "Clôture / Enregistrée")
        for ag in enriched_agents:
            _add_agent_to_phase(p_prod, ag)
        p_prod["status"] = "done" if is_archived else "active"
        p_close["status"] = "done" if is_archived else "pending"
        phases = [p_prod, p_close]
        current_phase = p_close["label"] if is_archived else p_prod["label"]
        mode = "2_phases"
    else:
        p_conc = _empty_phase("conception", "Conception")
        p_plan = _empty_phase("planification", "Planification")
        p_prod = _empty_phase("production", "Production")
        p_val = _empty_phase("validation", "Validation")

        first_plan_step = sig["first_plan_step"]
        first_prod_step = sig["first_prod_step"]
        verif_step = sig["verif_step"]
        archive_step = sig["archive_step"]

        if is_archived:
            p_conc["status"] = p_plan["status"] = p_prod["status"] = p_val["status"] = "done"
            current_phase = p_val["label"]
        elif sig["has_verif_activity"]:
            p_conc["status"] = p_plan["status"] = p_prod["status"] = "done"
            p_val["status"] = "active"
            current_phase = p_val["label"]
        elif sig["has_prod_activity"]:
            p_conc["status"] = p_plan["status"] = "done"
            p_prod["status"] = "active"
            current_phase = p_prod["label"]
        elif sig["has_plan_activity"]:
            p_conc["status"] = "done"
            p_plan["status"] = "active"
            current_phase = p_plan["label"]
        else:
            p_conc["status"] = "active"
            current_phase = p_conc["label"]

        if root_agent is not None:
            step_costs = root_agent.get("_step_costs") or []
            if not step_costs:
                if first_prod_step is not None:
                    _add_agent_to_phase(p_prod, root_agent)
                elif first_plan_step is not None:
                    _add_agent_to_phase(p_plan, root_agent)
                else:
                    _add_agent_to_phase(p_conc, root_agent)
            else:
                phase_touched: Set[str] = set()
                for sc in step_costs:
                    si = sc.get("step_index")
                    if (verif_step is not None and isinstance(si, int) and si >= verif_step) or (
                        archive_step is not None and isinstance(si, int) and si >= archive_step
                    ):
                        tgt = p_val
                    elif first_prod_step is not None and isinstance(si, int) and si >= first_prod_step:
                        tgt = p_prod
                    elif first_plan_step is not None and isinstance(si, int) and si >= first_plan_step:
                        tgt = p_plan
                    else:
                        tgt = p_conc
                    tgt["llm_calls"] += 1
                    tgt["tokens"] += int(sc.get("tokens") or 0)
                    tgt["cost_usd"] += float(sc.get("cost_usd") or 0.0)
                    tgt["cost_2027_usd"] += float(sc.get("cost_2027_usd") or 0.0)
                    if tgt["id"] not in phase_touched:
                        tgt["agents"] += 1
                        phase_touched.add(tgt["id"])

        for ag in enriched_agents:
            if ag["conversation_id"] == root_id:
                continue
            src, sp = ag.get("source"), ag.get("spawn_step_index")
            p_kind = _classify_milestone_phase(ag)
            if p_kind == "validation":
                _add_agent_to_phase(p_val, ag)
            elif p_kind == "planification":
                _add_agent_to_phase(p_plan, ag)
            elif p_kind == "conception":
                _add_agent_to_phase(p_conc, ag)
            elif (verif_step is not None and isinstance(sp, int) and sp >= verif_step) or (
                archive_step is not None and isinstance(sp, int) and sp >= archive_step
            ):
                _add_agent_to_phase(p_val, ag)
            elif first_prod_step is not None and isinstance(sp, int) and sp >= first_prod_step:
                _add_agent_to_phase(p_prod, ag)
            elif src in ("bridge", "workflow") or _is_prod_subagent(ag):
                _add_agent_to_phase(p_prod, ag)
            elif first_plan_step is not None and isinstance(sp, int) and sp >= first_plan_step:
                _add_agent_to_phase(p_plan, ag)
            else:
                _add_agent_to_phase(p_conc, ag)

        phases, mode = [p_conc, p_plan, p_prod, p_val], "4_phases"

    for ph in phases:
        ph["cost_usd"] = round(ph["cost_usd"], 6)
        ph["cost_2027_usd"] = round(ph["cost_2027_usd"], 6)
        ph["cost_share_pct"] = round((ph["cost_usd"] / total_cost_float) * 100.0, 2) if total_cost_float > 0 else 0.0

    return {"mode": mode, "current_phase": current_phase, "phases": phases}


def _load_archived_keys(
    history_path: Optional[Union[Path, str]],
) -> Dict[str, List[Tuple[Optional[int], Optional[int]]]]:
    """Charge les plages scellées `(window_start_step, root_last_step_index)` par `conversation_id`."""
    by_cid: Dict[str, List[Tuple[Optional[int], Optional[int]]]] = {}
    for obj in parse_transcript_steps(history_path):
        cid = str(obj.get("conversation_id") or "").strip()
        status = str(obj.get("status") or "archived").strip().lower()
        if cid and status == "archived":
            wstep = obj.get("window_start_step")
            norm_wstep = wstep if isinstance(wstep, int) and not isinstance(wstep, bool) else None
            rend = obj.get("root_last_step_index")
            norm_rend = rend if isinstance(rend, int) and not isinstance(rend, bool) else None
            by_cid.setdefault(cid, []).append((norm_wstep, norm_rend))
    return by_cid


def _is_window_archived(
    cid: str,
    win_wstep: Optional[int],
    win_mode: str,
    archived_by_cid: Dict[str, Any],
    *,
    win_end_step: Optional[int] = None,
) -> bool:
    """Retourne True si la fenêtre `(cid, [win_wstep..win_end_step])` chevauche une plage scellée ou est à `<= 2` steps."""
    raw_spans = archived_by_cid.get(cid)
    if not raw_spans:
        return False
    spans: List[Tuple[Optional[int], Optional[int]]] = [
        item if isinstance(item, tuple) else (item, None)
        for item in raw_spans
    ]
    if any(rec_start is None for rec_start, _ in spans):
        return True
    if win_mode == "full_conversation" and any(rec_start is None for rec_start, _ in spans):
        return True
    if isinstance(win_wstep, int) and not isinstance(win_wstep, bool):
        eff_win_end = (
            win_end_step
            if (isinstance(win_end_step, int) and not isinstance(win_end_step, bool) and win_end_step >= win_wstep)
            else win_wstep
        )
        for rec_start, rec_end in spans:
            if not (isinstance(rec_start, int) and not isinstance(rec_start, bool)):
                continue
            if rec_start == win_wstep or abs(rec_start - win_wstep) <= 2:
                return True
            if isinstance(rec_end, int) and not isinstance(rec_end, bool) and rec_end >= rec_start:
                if win_wstep <= rec_end and eff_win_end >= rec_start:
                    return True
    return False


def _resolve_store_dir_from_history(history_path: Optional[Union[Path, str]]) -> Optional[Path]:
    if history_path is None:
        return None
    hp = Path(history_path).expanduser()
    return hp.parent.parent if hp.parent.name == "data" else hp.parent


def _compute_pending_milestones(
    entries: List[Dict[str, Any]],
    start_line_idx: int,
    end_line_idx: Optional[int],
    active_links: List[Dict[str, Any]],
    pending_jobs: List[Dict[str, Any]],
    has_workflow_children: bool,
    wf_children: Optional[List[Dict[str, Any]]] = None,
    subagent_children: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    win_entries = [
        e for e in entries
        if e["line_idx"] >= start_line_idx and (end_line_idx is None or e["line_idx"] < end_line_idx)
    ]
    prod_subagents = [a for a in (subagent_children or []) if _is_prod_subagent(a)]
    wf_list = list(wf_children or [])
    if has_workflow_children and not wf_list:
        wf_list = [{"spawn_step_index": None}]
    sig = _analyze_milestones_window(
        win_entries,
        root_links=active_links,
        bridge_agents=[],
        wf_agents=wf_list,
        prod_subagents=prod_subagents,
        pending_jobs=pending_jobs,
        subagent_agents=list(subagent_children or []),
    )
    if not sig["is_4_phases"]:
        return {
            "mode": "2_phases",
            "current_phase": "Production",
            "phases": [
                {"id": "production", "label": "Production", "status": "active"},
                {"id": "cloture", "label": "Clôture / Enregistrée", "status": "pending"},
            ],
        }

    if sig["has_verif_activity"]:
        statuses, cur = ("done", "done", "done", "active"), "Validation"
    elif sig["has_prod_activity"]:
        statuses, cur = ("done", "done", "active", "pending"), "Production"
    elif sig["has_plan_activity"]:
        statuses, cur = ("done", "active", "pending", "pending"), "Planification"
    else:
        statuses, cur = ("active", "pending", "pending", "pending"), "Conception"

    labels = (
        ("conception", "Conception"),
        ("planification", "Planification"),
        ("production", "Production"),
        ("validation", "Validation"),
    )
    return {
        "mode": "4_phases",
        "current_phase": cur,
        "phases": [{"id": pid, "label": lbl, "status": st} for (pid, lbl), st in zip(labels, statuses)],
    }


def list_pending_sessions(
    brain_dir: Union[Path, str],
    history_path: Optional[Union[Path, str]] = None,
    *,
    runs_jsonl_paths: Optional[List[Path]] = None,
    store_dir: Optional[Union[Path, str]] = None,
    include_ignored: bool = False,
    include_noise: bool = False,
) -> List[Dict[str, Any]]:
    """Liste les sessions Gravity racines ayant de l'activité LLM non encore archivée."""
    base = Path(brain_dir)
    if not base.is_dir():
        return []

    eff_store = Path(store_dir).expanduser() if store_dir is not None else _resolve_store_dir_from_history(history_path)
    archived_keys = _load_archived_keys(history_path)
    full_archived_cids = {cid for cid, spans in archived_keys.items() if any(s is None for s, _ in spans)}
    session_meta = load_session_meta(eff_store)
    active_captures = load_active_captures(eff_store)
    ignored_map = load_ignored_sessions(eff_store)

    known_session_ids = _collect_known_gravity_session_ids(runs_jsonl_paths)
    known_child_ids = load_known_child_conversation_ids(base)
    pending: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        tx = _get_transcript_index(tpath)
        if not tx["exists"] or tx["first_prompt_raw"] is None:
            continue

        first_prompt_raw = str(tx["first_prompt_raw"])
        is_bridge_child = is_bridge_child_conversation(cdir.name, first_prompt_raw=first_prompt_raw)
        if is_bridge_child or (cdir.name in known_child_ids):
            continue

        is_gravity = bool(tx["has_gravity_cmd"]) or (cdir.name in known_session_ids) or (cdir.name in active_captures)
        if not is_gravity or cdir.name in full_archived_cids:
            continue

        ign_entry = ignored_map.get(cdir.name)
        if ign_entry is not None and not include_ignored:
            continue

        cap_entry = active_captures.get(cdir.name)
        cap_from_step = cap_entry.get("start_step") if isinstance(cap_entry, dict) else None
        win = resolve_gravity_window(
            tpath,
            current_prompt_is_metrics=False,
            from_step=cap_from_step if isinstance(cap_from_step, int) else None,
        )
        if win.get("window_empty"):
            continue
        start_idx, end_idx = int(win.get("start_line_idx") or 0), win.get("end_line_idx")
        entries: List[Dict[str, Any]] = tx["entries"]
        win_end_step = max(
            (
                e["step_index"]
                for e in entries
                if e["line_idx"] >= start_idx
                and (end_idx is None or e["line_idx"] < end_idx)
                and isinstance(e.get("step_index"), int)
            ),
            default=None,
        )
        if _is_window_archived(
            cdir.name,
            win.get("start_step_index"),
            str(win.get("mode") or ""),
            archived_keys,
            win_end_step=win_end_step,
        ):
            continue
        llm_calls = 0
        has_work_activity = False
        last_ts: Optional[str] = None
        last_status = ""
        for e in entries:
            lidx = e["line_idx"]
            if lidx < start_idx:
                continue
            if end_idx is not None and lidx >= end_idx:
                break
            if e.get("created_at"):
                last_ts = str(e["created_at"])
            if e.get("status"):
                last_status = str(e["status"])
            if e.get("is_planner"):
                llm_calls += 1
            if e.get("has_work_activity"):
                has_work_activity = True

        if llm_calls == 0:
            continue

        apply_win = bool(win.get("has_gravity_anchor") or win.get("mode") != "full_conversation")
        wf_children = discover_workflow_children(cdir.name, base)
        subagent_children = _discover_direct_subagent_children(cdir)
        active_subagents = [
            s for s in subagent_children
            if not apply_win or _is_spawn_in_window(
                s.get("spawn_step_index"), None,
                str(base / s["conversation_id"] / ".system_generated" / "logs" / "transcript.jsonl"), win,
            )
        ]
        is_auto_or_uncaptured = cap_entry is None or (
            isinstance(cap_entry, dict) and cap_entry.get("source") == "architect_auto"
        )
        if (
            not include_noise
            and not wf_children
            and not any(_is_prod_subagent(s) for s in active_subagents)
            and is_auto_or_uncaptured
            and not has_work_activity
        ):
            continue
        active_wf = [
            c for c in wf_children
            if not apply_win or _is_spawn_in_window(c.get("spawn_step_index"), None, None, win)
        ]
        milestones = _compute_pending_milestones(
            entries, start_idx, end_idx, [], [], bool(wf_children),
            wf_children=active_wf, subagent_children=active_subagents,
        )

        meta_entry = session_meta.get(cdir.name) or {}
        resolved_proj = (
            str((cap_entry or {}).get("project") or "").strip()
            or str(meta_entry.get("project") or "").strip()
        )
        mtime = float(tx["mtime"] or 0.0)
        updated_at = last_ts or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        item: Dict[str, Any] = {
            "conversation_id": cdir.name,
            "title": _clean_pending_title(win.get("first_prompt") or "", first_prompt_raw),
            "project": resolved_proj,
            "window_mode": win["mode"],
            "window_start_step": win["start_step_index"],
            "started_at": win["start_ts"],
            "updated_at": updated_at,
            "llm_calls": llm_calls,
            "is_active": bool(cap_entry is not None or last_status == "RUNNING"),
            "active_capture": dict(cap_entry) if isinstance(cap_entry, dict) else None,
            "current_phase": milestones["current_phase"],
            "milestones": milestones,
            "bridge_children_count": 0,
            "bridge_pending_jobs_count": 0,
            "has_pending_bridge_jobs": False,
            "bridge_workspaces": [],
            "bridge_pending_jobs": [],
            "ignored": ign_entry is not None,
            "_mtime": mtime,
        }
        if ign_entry is not None:
            item["ignored_at"] = ign_entry.get("ignored_at") or ""
            item["ignored_reason"] = ign_entry.get("reason") or ""
        pending.append(item)

    pending.sort(key=lambda item: (-item["_mtime"], item["conversation_id"]))
    for item in pending:
        item.pop("_mtime", None)
    return pending
