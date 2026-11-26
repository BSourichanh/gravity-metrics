#!/usr/bin/env python3
"""Découverte de l'arbre d'agents (subagents natifs + run_workflow) sans couplage Bridge (100 % stdlib)."""

from collections import Counter
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_tr_reader = importlib.import_module("_shared.transcript_reader")
parse_iso_ts = _tr_reader.parse_iso_ts
read_jsonl_incremental = _tr_reader.read_jsonl_incremental

from .pricing import classify_agent
from .prompt_classifier import _clean_user_prompt, extract_user_request_text
from .transcript_index import _cache_put, _get_transcript_index, _stat_sig

_WORKFLOW_CHILDREN_CACHE: Dict[str, Tuple[Tuple[Any, ...], List[Dict[str, Any]]]] = {}
_SUBAGENTS_CACHE: Dict[str, Tuple[Tuple[Any, ...], List[Dict[str, Any]]]] = {}
_RUNS_JSONL_CACHE: Dict[str, Dict[str, Any]] = {}

_WORKFLOW_AGENT_LINE_RE = re.compile(
    r"^\s*-\s+\[agent:([^\]]+)\]\s+([^:]+?):\s*([^(]+?)\s*"
    r"\(([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)"
)
_TASK_STEP_RE = re.compile(r"^task-(\d+)\.log$", re.IGNORECASE)


def _collect_known_gravity_session_ids(runs_jsonl_paths: Optional[List[Path]]) -> Set[str]:
    ids: Set[str] = set()
    if not runs_jsonl_paths:
        return ids

    def _apply(state: Set[str], obj: Dict[str, Any]) -> None:
        sid = obj.get("conversation_id") or obj.get("session_id")
        if isinstance(sid, str) and sid.strip():
            state.add(sid.strip())

    for rpath in runs_jsonl_paths:
        ids.update(read_jsonl_incremental(rpath, _RUNS_JSONL_CACHE, set, _apply))
    return ids


def _task_log_sort_key(path: Path) -> Tuple[int, int, str]:
    m = _TASK_STEP_RE.match(path.name)
    if m:
        return (0, int(m.group(1)), path.name)
    return (1, 0, path.name)


def _extract_workflow_children_from_text(
    text: str,
    spawn_step_index: Optional[int],
    parent_cid_lower: str,
    by_cid: Dict[str, Dict[str, Any]],
    ordered_keys: List[str],
) -> None:
    """Extrait les lignes `- [agent:<type>] <role>: <state> (<UUID>)` dans `by_cid`."""
    if not text or "[agent:" not in text:
        return
    for line in text.splitlines():
        m = _WORKFLOW_AGENT_LINE_RE.match(line)
        if not m:
            continue
        type_name, role, state, wcid = (m.group(i).strip() for i in range(1, 5))
        key = wcid.lower()
        if not wcid or key == parent_cid_lower:
            continue
        if key not in by_cid:
            by_cid[key] = {
                "conversation_id": wcid,
                "type_name": type_name,
                "role": role,
                "state": state,
                "spawn_step_index": spawn_step_index,
            }
            ordered_keys.append(key)
        elif by_cid[key]["spawn_step_index"] is None and spawn_step_index is not None:
            by_cid[key]["spawn_step_index"] = spawn_step_index


def discover_workflow_children(
    parent_cid: str,
    brain_dir: Union[Path, str],
) -> List[Dict[str, Any]]:
    """Découvre en lecture seule les sous-agents `run_workflow` d'une conversation (cache mtime_ns+size)."""
    clean_parent = str(parent_cid or "").strip()
    if not clean_parent:
        return []
    cdir = Path(brain_dir) / clean_parent
    if not cdir.is_dir():
        return []

    tasks_dir = cdir / ".system_generated" / "tasks"
    msgs_dir = cdir / ".system_generated" / "messages"
    tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"

    task_logs: List[Path] = []
    if tasks_dir.is_dir():
        try:
            task_logs = sorted(
                (p for p in tasks_dir.iterdir() if p.is_file() and p.name.lower().startswith("task-") and p.suffix.lower() == ".log"),
                key=_task_log_sort_key,
            )
        except OSError:
            task_logs = []

    msg_files: List[Path] = []
    if msgs_dir.is_dir():
        try:
            msg_files = [
                p for p in sorted(msgs_dir.iterdir(), key=lambda p: p.name)
                if p.is_file() and p.suffix.lower() == ".json" and p.name.lower() != "read.json"
            ]
        except OSError:
            msg_files = []

    fp = (
        tuple((p.name, _stat_sig(p)) for p in task_logs),
        tuple((p.name, _stat_sig(p)) for p in msg_files),
        _stat_sig(tpath) if tpath.is_file() else None,
    )
    try:
        cache_key = str(cdir.resolve())
    except OSError:
        cache_key = ""

    if cache_key:
        cached = _WORKFLOW_CHILDREN_CACHE.get(cache_key)
        if cached is not None and cached[0] == fp:
            return [dict(item) for item in cached[1]]

    parent_cid_lower = clean_parent.lower()
    by_cid: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []

    for tlog in task_logs:
        m_step = _TASK_STEP_RE.match(tlog.name)
        step_idx = int(m_step.group(1)) if m_step else None
        try:
            content = tlog.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _extract_workflow_children_from_text(content, step_idx, parent_cid_lower, by_cid, ordered_keys)

    for mpath in msg_files:
        try:
            obj = json.loads(mpath.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        sm = obj.get("sourceMetadata")
        tool_meta = sm.get("tool") if isinstance(sm, dict) else None
        tool_call = tool_meta.get("toolCall") if isinstance(tool_meta, dict) else None
        tool_name = str(tool_call.get("name") or "").strip() if isinstance(tool_call, dict) else ""
        content = obj.get("content")
        content_str = content if isinstance(content, str) else ""
        if tool_name != "run_workflow" or "[agent:" not in content_str:
            continue
        raw_step = tool_meta.get("stepIndex") if isinstance(tool_meta, dict) else None
        step_idx = raw_step if isinstance(raw_step, int) and not isinstance(raw_step, bool) else None
        _extract_workflow_children_from_text(content_str, step_idx, parent_cid_lower, by_cid, ordered_keys)

    if tpath.is_file():
        try:
            raw_lines = tpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            raw_lines = []
        active_wf_turn = active_wf_has_other_tools = has_any_wf_call = False
        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            step_type = obj.get("type")
            if step_type == "USER_INPUT":
                active_wf_turn = active_wf_has_other_tools = False
                continue
            if step_type == "PLANNER_RESPONSE":
                raw_tcs = obj.get("tool_calls")
                tc_list = raw_tcs if isinstance(raw_tcs, list) else []
                tc_names = [str(tc.get("name") or "").strip() for tc in tc_list if isinstance(tc, dict)]
                has_wf = "run_workflow" in tc_names
                active_wf_turn = has_wf
                active_wf_has_other_tools = has_wf and any(n != "run_workflow" for n in tc_names)
                has_any_wf_call = has_any_wf_call or has_wf
                continue
            if step_type == "GENERIC":
                content = obj.get("content")
                if not active_wf_turn or not isinstance(content, str) or "[agent:" not in content:
                    continue
                if active_wf_has_other_tools:
                    padded = "\n" + content
                    if any(m in padded for m in ("\nCommand output:", "\nCommand exited ", "\nShowing lines ")) and (
                        "Workflow " not in content and "\nLogs:\n" not in content
                    ):
                        continue
            elif step_type == "SYSTEM_MESSAGE":
                content = obj.get("content")
                if not isinstance(content, str) or "[agent:" not in content:
                    continue
                lowered = content.lower()
                if not (
                    has_any_wf_call
                    or f'task id "{parent_cid_lower}/task-' in lowered
                    or f"sender={parent_cid_lower}/task-" in lowered
                ):
                    continue
            else:
                continue
            raw_step = obj.get("step_index")
            step_idx = raw_step if isinstance(raw_step, int) and not isinstance(raw_step, bool) else None
            _extract_workflow_children_from_text(content, step_idx, parent_cid_lower, by_cid, ordered_keys)

    result = [by_cid[k] for k in ordered_keys]
    if cache_key:
        _cache_put(_WORKFLOW_CHILDREN_CACHE, cache_key, (fp, [dict(item) for item in result]))
    return result


def load_known_child_conversation_ids(
    brain_dir: Union[Path, str],
) -> Set[str]:
    """Agrège tous les identifiants connus de conversations enfants (subagents, workflow)."""
    child_ids: Set[str] = set()
    base = Path(brain_dir)
    if not base.is_dir():
        return child_ids

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        cdir_lower = cdir.name.lower()
        sub_dir = cdir / ".system_generated" / "subagents"
        if sub_dir.is_dir():
            try:
                sub_files = sorted(fn for fn in sub_dir.iterdir() if fn.is_file() and fn.suffix.lower() == ".json")
            except OSError:
                sub_files = []
            sub_fp = tuple((fn.name, _stat_sig(fn)) for fn in sub_files)
            try:
                sub_key = str(sub_dir.resolve())
            except OSError:
                sub_key = ""
            cached_sub = _SUBAGENTS_CACHE.get(sub_key) if sub_key else None
            if cached_sub is not None and cached_sub[0] == sub_fp:
                sub_cids = cached_sub[1]
            else:
                sub_cids = []
                for fn in sub_files:
                    try:
                        sd = json.loads(fn.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not isinstance(sd, dict):
                        continue
                    scid = str(sd.get("conversationId") or "").strip()
                    if not scid or scid == cdir.name or scid.lower() == cdir_lower:
                        continue
                    desc = sd.get("subagentDescriptor")
                    raw_type = (
                        desc.get("typeName")
                        if isinstance(desc, dict) and desc.get("typeName") is not None
                        else sd.get("type_name")
                    )
                    if str(raw_type or "").strip().lower() == "root":
                        continue
                    sub_cids.append(scid)
                if sub_key:
                    _cache_put(_SUBAGENTS_CACHE, sub_key, (sub_fp, list(sub_cids)))
            child_ids.update(sub_cids)

        for w in discover_workflow_children(cdir.name, base):
            wcid = str(w.get("conversation_id") or "").strip()
            if not wcid or wcid == cdir.name or wcid.lower() == cdir_lower:
                continue
            if str(w.get("type_name") or "").strip().lower() == "root":
                continue
            child_ids.add(wcid)

    return child_ids


def is_bridge_child_conversation(
    conversation_id: str,
    *,
    first_prompt_raw: Optional[str] = None,
    bridge_target_ids: Optional[Set[str]] = None,
    brain_dir: Optional[Union[Path, str]] = None,
) -> bool:
    """Retourne True si la conversation porte un marqueur transcript délégué `[GRAVITY-BRIDGE |`."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return False
    if bridge_target_ids and cid in bridge_target_ids:
        return True

    prompt_txt = first_prompt_raw
    if prompt_txt is None and brain_dir is not None:
        tpath = Path(brain_dir) / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if tpath.is_file():
            with tpath.open("r", encoding="utf-8", errors="replace") as fh:
                for idx, raw_line in enumerate(fh):
                    if idx >= 20:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "USER_INPUT":
                        prompt_txt = str(obj.get("content") or "")
                        break
    return bool(prompt_txt and extract_user_request_text(prompt_txt).startswith("[GRAVITY-BRIDGE |"))


def resolve_conversation_id_in_brain(target: str, brain_dir: Union[Path, str]) -> str:
    """Résout un UUID complet ou un préfixe unique d'UUID dans `brain_dir`."""
    clean = str(target or "").strip()
    if not clean:
        raise ValueError("Identifiant de conversation vide.")
    base = Path(brain_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"Répertoire des conversations introuvable : {base}")
    if (base / clean).is_dir():
        return clean
    lower_target = clean.lower()
    matches = sorted(d.name for d in base.iterdir() if d.is_dir() and d.name.lower().startswith(lower_target))
    if not matches:
        raise FileNotFoundError(f"Conversation introuvable : {clean!r} dans {base}")
    if len(matches) > 1:
        raise ValueError(f"Plusieurs conversations correspondent au préfixe {clean!r} ; précisez l'UUID complet.")
    return matches[0]


def list_gravity_conversations(
    brain_dir: Union[Path, str],
    include_all: bool = False,
    runs_jsonl_paths: Optional[List[Path]] = None,
) -> List[Dict[str, Any]]:
    """Liste les conversations racines récentes, en excluant les enfants par défaut."""
    base = Path(brain_dir)
    if not base.is_dir():
        return []

    known_session_ids = _collect_known_gravity_session_ids(runs_jsonl_paths)
    known_child_ids = load_known_child_conversation_ids(base)
    results: List[Dict[str, Any]] = []

    for cdir in base.iterdir():
        if not cdir.is_dir():
            continue
        tpath = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        tx = _get_transcript_index(tpath)
        if not tx["exists"] or tx["first_prompt_raw"] is None:
            continue

        first_prompt_raw = str(tx["first_prompt_raw"])
        is_bridge_child = is_bridge_child_conversation(cdir.name, first_prompt_raw=first_prompt_raw)
        is_child = is_bridge_child or (cdir.name in known_child_ids)
        if not include_all and is_child:
            continue

        is_gravity = (not is_child) and (bool(tx["has_gravity_cmd"]) or (cdir.name in known_session_ids))
        if not include_all and not is_gravity:
            continue

        mtime = float(tx["mtime"] or 0.0)
        updated_at = tx["last_ts"] or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        results.append(
            {
                "conversation_id": cdir.name,
                "title": _clean_user_prompt(first_prompt_raw),
                "updated_at": updated_at,
                "is_gravity": is_gravity,
                "is_bridge_child": is_bridge_child,
                "_mtime": mtime,
            }
        )

    results.sort(key=lambda item: (-item["_mtime"], item["conversation_id"]))
    for item in results:
        item.pop("_mtime", None)
    return results


def _read_first_timestamp(transcript_path: Union[Path, str]) -> Optional[datetime]:
    tpath = Path(transcript_path)
    if not tpath.is_file():
        return None
    with tpath.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("created_at"):
                dt = parse_iso_ts(str(obj["created_at"]))
                if dt is not None:
                    return dt
    return None


def _is_spawn_in_window(
    spawn_step: Optional[int],
    created_at_ts: Optional[str],
    transcript_path: Optional[str],
    window_info: Dict[str, Any],
) -> bool:
    if window_info.get("window_empty"):
        return False
    start_step, end_step = window_info.get("start_step_index"), window_info.get("end_step_index")
    use_step_bounds = isinstance(start_step, int) and (
        end_step is None or (isinstance(end_step, int) and end_step >= start_step)
    )
    if isinstance(spawn_step, int) and use_step_bounds:
        if spawn_step < start_step:
            return False
        if isinstance(end_step, int) and spawn_step >= end_step:
            return False
        return True

    start_dt = parse_iso_ts(window_info.get("start_ts"))
    end_dt = parse_iso_ts(window_info.get("end_ts"))
    child_dt = _read_first_timestamp(transcript_path) if transcript_path else None
    if child_dt is None and created_at_ts:
        child_dt = parse_iso_ts(created_at_ts)
    if child_dt is not None and (start_dt is not None or end_dt is not None):
        if start_dt is not None and child_dt < start_dt:
            return False
        if end_dt is not None and child_dt >= end_dt:
            return False
        return True

    return (
        window_info.get("mode") == "full_conversation"
        and window_info.get("start_line_idx", 0) == 0
        and window_info.get("end_line_idx") is None
    )


def _is_direct_child_in_window(child_node: Dict[str, Any], window_info: Dict[str, Any]) -> bool:
    return _is_spawn_in_window(
        child_node.get("spawn_step_index"),
        child_node.get("bridge_created_at"),
        child_node.get("transcript_path"),
        window_info,
    )


def _discover_direct_subagent_children(conv_dir: Path) -> List[Dict[str, Any]]:
    """Lit les descripteurs `subagents/*.json` directs d'une conversation."""
    sub_dir = conv_dir / ".system_generated" / "subagents"
    if not sub_dir.is_dir():
        return []
    children: List[Dict[str, Any]] = []
    try:
        files = sorted(sub_dir.iterdir())
    except OSError:
        return []
    for fn in files:
        if not fn.is_file() or fn.suffix.lower() != ".json":
            continue
        try:
            sd = json.loads(fn.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(sd, dict):
            continue
        scid = str(sd.get("conversationId") or "").strip()
        if not scid:
            continue
        desc = sd.get("subagentDescriptor") or {}
        if not isinstance(desc, dict):
            desc = {}
        children.append(
            {
                "conversation_id": scid,
                "type_name": str(desc.get("typeName") or sd.get("type_name") or "unknown"),
                "role": str(desc.get("role") or scid[:8]),
                "state": sd.get("state"),
                "spawn_step_index": sd.get("spawnStepIndex"),
                "workspace_uris": [str(u) for u in (sd.get("workspaceUris") or []) if isinstance(u, str) and u.strip()],
            }
        )
    return children


def discover_agent_tree(
    root_id: str,
    brain_dir: Union[Path, str],
) -> List[Dict[str, Any]]:
    """Découvre l'arbre complet (sous-agents natifs + run_workflow) par BFS anti-cycles."""
    base = Path(brain_dir)
    visited: Set[str] = set()
    enqueued: Set[str] = {root_id}
    queue: List[Dict[str, Any]] = [
        {
            "cid": root_id, "parent_id": None, "depth": 0, "type_name": "root", "role": "root",
            "state": None, "spawn_step": None, "source": "root",
            "bridge_thread_id": None, "bridge_workspace": None, "bridge_job_status": None,
            "bridge_created_at": None, "workspace_uris": [],
        }
    ]
    nodes: List[Dict[str, Any]] = []

    while queue:
        item = queue.pop(0)
        cid = item["cid"]
        if cid in visited:
            continue
        visited.add(cid)

        cdir = base / cid
        transcript_path = cdir / ".system_generated" / "logs" / "transcript.jsonl"
        children_ids: List[str] = []
        depth = item["depth"]
        cur_bridge_ws = item["bridge_workspace"]

        for sub in _discover_direct_subagent_children(cdir):
            scid = sub["conversation_id"]
            if scid in visited:
                continue
            if scid not in children_ids:
                children_ids.append(scid)
            if scid not in enqueued:
                enqueued.add(scid)
                queue.append(
                    {
                        "cid": scid, "parent_id": cid, "depth": depth + 1,
                        "type_name": sub["type_name"], "role": sub["role"], "state": sub["state"],
                        "spawn_step": sub["spawn_step_index"], "source": "subagent",
                        "bridge_thread_id": None, "bridge_workspace": cur_bridge_ws,
                        "bridge_job_status": None, "bridge_created_at": None,
                        "workspace_uris": sub.get("workspace_uris") or [],
                    }
                )

        for w in discover_workflow_children(cid, base):
            wcid = str(w.get("conversation_id") or "").strip()
            if not wcid or wcid in visited:
                continue
            if wcid not in children_ids:
                children_ids.append(wcid)
            if wcid not in enqueued:
                enqueued.add(wcid)
                queue.append(
                    {
                        "cid": wcid, "parent_id": cid, "depth": depth + 1,
                        "type_name": w["type_name"], "role": w["role"], "state": w["state"],
                        "spawn_step": w["spawn_step_index"], "source": "workflow",
                        "bridge_thread_id": None, "bridge_workspace": cur_bridge_ws,
                        "bridge_job_status": None, "bridge_created_at": None,
                        "workspace_uris": [],
                    }
                )

        nodes.append(
            {
                "conversation_id": cid,
                "parent_id": item["parent_id"],
                "depth": depth,
                "type_name": item["type_name"],
                "role": item["role"],
                "category": classify_agent(item["type_name"], item["role"]),
                "subagent_state": item["state"],
                "spawn_step_index": item["spawn_step"],
                "source": item["source"],
                "bridge_thread_id": item["bridge_thread_id"],
                "bridge_workspace": cur_bridge_ws,
                "bridge_job_status": item["bridge_job_status"],
                "bridge_created_at": item["bridge_created_at"],
                "workspace_uris": item.get("workspace_uris") or [],
                "children_ids": children_ids,
                "transcript_path": str(transcript_path),
                "transcript_exists": transcript_path.is_file(),
            }
        )

    return nodes


def _extract_slash_commands(raw_prompts: List[str]) -> Set[str]:
    cmds: Set[str] = set()
    for raw in raw_prompts:
        txt = extract_user_request_text(raw)
        m = re.match(r"^/([a-z0-9_:-]+)(?:\s|$)", txt, re.IGNORECASE)
        if m:
            cmd = m.group(1).lower()
            if ":" in cmd:
                cmd = cmd.split(":", 1)[0]
            cmds.add(cmd)
    return cmds


def _detect_execution_mode(
    enriched_agents: List[Dict[str, Any]],
    tool_counter: Counter,
    root_id: str,
) -> Tuple[str, List[str]]:
    """Détermine le mode d'orchestration principal et les capacités détectées."""
    type_names = {str(a.get("type_name") or "").lower() for a in enriched_agents}
    root_agent = next((a for a in enriched_agents if a["conversation_id"] == root_id), enriched_agents[0])
    cmds = _extract_slash_commands(root_agent.get("_user_prompts") or [])

    is_teamwork = any(tn.startswith("teamwork") for tn in type_names) or "teamwork" in cmds
    is_swarm = bool(tool_counter.get("swarm")) or "swarm" in cmds
    is_workflow = bool(tool_counter.get("run_workflow")) or any(a.get("source") == "workflow" for a in enriched_agents)
    is_bridge = any(
        a.get("source") == "bridge" or str(a.get("type_name") or "").lower().startswith("bridge")
        for a in enriched_agents
    )
    is_subagents = len(enriched_agents) > 1 or bool(tool_counter.get("invoke_subagent"))
    is_plan = bool(tool_counter.get("exit_plan_mode")) or "plan" in cmds
    is_gravity = bool(cmds & {"gravity", "gravity-workflow", "gravity-architect", "gravity-bridge"})

    features_used = [
        name for flag, name in (
            (is_teamwork, "teamwork"), (is_swarm, "swarm"), (is_workflow, "workflow"),
            (is_bridge, "bridge"), (is_subagents, "subagents"), (is_plan, "plan"), (is_gravity, "gravity"),
        ) if flag
    ] or ["single"]

    if is_teamwork:
        mode = "teamwork"
    elif is_swarm:
        mode = "swarm"
    elif is_workflow:
        mode = "workflow"
    elif is_bridge:
        mode = "bridge"
    elif is_subagents:
        mode = "subagents"
    else:
        mode = "single"
    return mode, features_used
