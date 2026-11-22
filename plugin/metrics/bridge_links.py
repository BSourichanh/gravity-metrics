#!/usr/bin/env python3
"""Découverte en lecture seule des liens inter-workspaces Gravity Bridge (100 % stdlib)."""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

ACTIVE_BRIDGE_JOB_STATUSES = ("queued", "claimed", "dispatched")
_THREAD_ID_RE = re.compile(r'"thread_id"\s*:\s*"(thr-[A-Za-z0-9_-]+)"')
_TARGET_CID_RE = re.compile(r'"target_conversation_id"\s*:\s*"([A-Za-z0-9_-]+)"')


def default_bridge_db_path() -> Path:
    """Retourne le chemin par défaut vers bridge.db selon la priorité Gravity."""
    env_bridge = os.environ.get("GRAVITY_BRIDGE_HOME", "").strip()
    if env_bridge:
        return Path(env_bridge).expanduser() / "bridge.db"
    env_gravity = os.environ.get("GRAVITY_HOME", "").strip()
    if env_gravity:
        return Path(env_gravity).expanduser() / "bridge" / "bridge.db"
    return Path.home() / ".gemini" / "gravity" / "bridge" / "bridge.db"


def resolve_bridge_db_path(bridge_db: Optional[Union[Path, str]] = None) -> Path:
    """Résout le chemin vers bridge.db (`--bridge-db` > `$GRAVITY_BRIDGE_HOME` > `$GRAVITY_HOME/bridge`)."""
    if bridge_db is not None and str(bridge_db).strip():
        return Path(bridge_db).expanduser()
    return default_bridge_db_path()


def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    """Ouvre SQLite en lecture seule stricte (`?mode=ro`), sans jamais créer ni écrire."""
    if not db_path.is_file():
        return None
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, OSError, ValueError):
        return None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> Set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(r[1]) for r in rows if len(r) > 1}
    except sqlite3.Error:
        return set()


def load_bridge_target_conversation_ids(
    bridge_db: Optional[Union[Path, str]] = None,
) -> Set[str]:
    """Retourne tous les `target_conversation_id` connus dans `bridge.db` (threads et jobs)."""
    db_path = resolve_bridge_db_path(bridge_db)
    conn = _connect_ro(db_path)
    if conn is None:
        return set()
    targets: Set[str] = set()
    try:
        with closing(conn):
            for tbl in ("threads", "jobs"):
                cols = _table_columns(conn, tbl)
                if "target_conversation_id" not in cols:
                    continue
                for row in conn.execute(
                    f"SELECT DISTINCT target_conversation_id FROM {tbl} "
                    "WHERE target_conversation_id IS NOT NULL AND TRIM(target_conversation_id) != ''"
                ):
                    val = str(row[0] or "").strip()
                    if val:
                        targets.add(val)
    except sqlite3.Error:
        return set()
    return targets


def _decode_json_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                parsed = json.loads(s)
                if isinstance(parsed, str):
                    return parsed.strip()
            except ValueError:
                pass
        return s
    return str(raw).strip()


def _decode_json_obj(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    s = raw.strip()
    if not s:
        return {}
    try:
        parsed = json.loads(s)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        return {}
    return {}


def _extract_bridge_call(tc: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Identifie un appel MCP `gravity-bridge_bridge` (`send_message` ou `dispatch_thread`)."""
    name = str(tc.get("name") or "").strip()
    args = tc.get("args") if isinstance(tc.get("args"), dict) else {}

    if name == "call_mcp_tool":
        server = _decode_json_str(args.get("ServerName") or args.get("server_name") or "").lower()
        tool = _decode_json_str(args.get("ToolName") or args.get("tool_name") or "").lower()
        if server in ("gravity-bridge_bridge", "gravity-bridge") and tool in (
            "send_message",
            "dispatch_thread",
        ):
            call_args = _decode_json_obj(args.get("Arguments") or args.get("arguments"))
            return tool, call_args
        return None

    lowered = name.lower()
    for suffix in ("send_message", "dispatch_thread"):
        if lowered == suffix or lowered.endswith(f"_{suffix}"):
            if suffix == "send_message" and "to_workspace" in args and "msg_type" in args:
                return suffix, {
                    k: (_decode_json_obj(v) if k == "Arguments" else v)
                    for k, v in args.items()
                }
            if suffix == "dispatch_thread" and "thread_id" in args:
                return suffix, dict(args)
    return None


def _extract_response_payloads(generic_contents: List[str]) -> List[Dict[str, Any]]:
    """Extrait les objets JSON retournés par les appels Bridge dans les étapes GENERIC suivantes."""
    payloads: List[Dict[str, Any]] = []
    for text in generic_contents:
        if not text:
            continue
        start = text.find("{")
        end = text.rfind("}")
        parsed: Optional[Dict[str, Any]] = None
        if start >= 0 and end > start:
            try:
                cand = json.loads(text[start : end + 1])
                if isinstance(cand, dict) and "thread_id" in cand:
                    parsed = cand
            except ValueError:
                parsed = None
        if parsed is not None:
            payloads.append(parsed)
            continue
        for tid in _THREAD_ID_RE.findall(text):
            tcid_m = _TARGET_CID_RE.search(text)
            payloads.append(
                {
                    "thread_id": tid,
                    "target_conversation_id": tcid_m.group(1) if tcid_m else "",
                }
            )
    return payloads


def _parse_transcript_bridge_calls(transcript_path: Path) -> List[Dict[str, Any]]:
    """Extrait les appels `send_message`/`dispatch_thread` depuis le transcript parent (repli v0.1.0)."""
    if not transcript_path.is_file():
        return []
    entries: List[Dict[str, Any]] = []
    for raw_line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)

    discovered: List[Dict[str, Any]] = []
    for idx, entry in enumerate(entries):
        if entry.get("type") != "PLANNER_RESPONSE":
            continue
        tcs = entry.get("tool_calls") or []
        if not isinstance(tcs, list):
            continue
        bridge_calls: List[Tuple[str, Dict[str, Any]]] = []
        for tc in tcs:
            if isinstance(tc, dict):
                extracted = _extract_bridge_call(tc)
                if extracted is not None:
                    bridge_calls.append(extracted)
        if not bridge_calls:
            continue

        generic_texts: List[str] = []
        for nxt in entries[idx + 1 :]:
            ntype = nxt.get("type")
            if ntype in ("PLANNER_RESPONSE", "USER_INPUT"):
                break
            if ntype == "GENERIC":
                generic_texts.append(str(nxt.get("content") or ""))
        resp_payloads = _extract_response_payloads(generic_texts)
        used_resp: Set[int] = set()

        step_idx = entry.get("step_index")
        spawn_step = step_idx if isinstance(step_idx, int) else None
        created_at = entry.get("created_at")

        for tool_name, call_args in bridge_calls:
            tid = _decode_json_str(call_args.get("thread_id"))
            to_ws = _decode_json_str(call_args.get("to_workspace"))
            msg_type = _decode_json_str(call_args.get("msg_type"))
            dispatched = bool(call_args.get("dispatch")) if tool_name == "send_message" else True
            target_cid = ""

            if tool_name == "send_message":
                matched_idx: Optional[int] = None
                for r_idx, rp in enumerate(resp_payloads):
                    if r_idx in used_resp:
                        continue
                    rp_to = str(rp.get("to_workspace") or "").strip()
                    if to_ws and rp_to and rp_to != to_ws:
                        continue
                    matched_idx = r_idx
                    break
                if matched_idx is not None:
                    used_resp.add(matched_idx)
                    rp = resp_payloads[matched_idx]
                    tid = str(rp.get("thread_id") or tid).strip()
                    to_ws = str(rp.get("to_workspace") or to_ws).strip()
                    msg_type = str(rp.get("msg_type") or msg_type).strip()
                    target_cid = str(rp.get("target_conversation_id") or "").strip()

            if not tid:
                continue
            discovered.append(
                {
                    "thread_id": tid,
                    "to_workspace": to_ws,
                    "msg_type": msg_type,
                    "target_conversation_id": target_cid,
                    "spawn_step_index": spawn_step,
                    "dispatched": dispatched,
                    "created_at": str(created_at) if created_at else None,
                    "from_transcript": True,
                }
            )
    return discovered


def _query_bridge_db_for_threads(
    conn: sqlite3.Connection,
    parent_cid: str,
    thread_ids: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Interroge `threads` et `jobs` par `caller_conversation_id` (v0.2.0+) et par `thread_id`."""
    threads_by_id: Dict[str, Dict[str, Any]] = {}
    latest_job_by_thread: Dict[str, Dict[str, Any]] = {}

    thread_cols = _table_columns(conn, "threads")
    job_cols = _table_columns(conn, "jobs")

    if "caller_conversation_id" in thread_cols:
        for row in conn.execute(
            "SELECT * FROM threads WHERE caller_conversation_id = ?",
            (parent_cid,),
        ):
            r = dict(row)
            tid = str(r.get("id") or "").strip()
            if tid:
                threads_by_id[tid] = r

    if "caller_conversation_id" in job_cols:
        for row in conn.execute(
            "SELECT * FROM jobs WHERE caller_conversation_id = ? ORDER BY created_at ASC",
            (parent_cid,),
        ):
            r = dict(row)
            tid = str(r.get("thread_id") or "").strip()
            if tid:
                latest_job_by_thread[tid] = r
                if tid not in threads_by_id and thread_cols:
                    trow = conn.execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
                    if trow is not None:
                        threads_by_id[tid] = dict(trow)

    all_tids = sorted(set(thread_ids) | set(threads_by_id.keys()))
    if all_tids and thread_cols:
        placeholders = ",".join("?" for _ in all_tids)
        for row in conn.execute(
            f"SELECT * FROM threads WHERE id IN ({placeholders})",
            tuple(all_tids),
        ):
            r = dict(row)
            tid = str(r.get("id") or "").strip()
            if tid:
                threads_by_id[tid] = r

    if all_tids and job_cols:
        placeholders = ",".join("?" for _ in all_tids)
        for row in conn.execute(
            f"SELECT * FROM jobs WHERE thread_id IN ({placeholders}) ORDER BY created_at ASC, id ASC",
            tuple(all_tids),
        ):
            r = dict(row)
            tid = str(r.get("thread_id") or "").strip()
            if tid:
                latest_job_by_thread[tid] = r

    return threads_by_id, latest_job_by_thread


def discover_bridge_links(
    parent_cid: str,
    transcript_path: Union[Path, str],
    bridge_db: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Fusionne les liens Bridge (`bridge.db` v0.2.0+ et repli transcript v0.1.0) sans doublon de `thread_id`."""
    tpath = Path(transcript_path)
    tx_calls = _parse_transcript_bridge_calls(tpath)
    tx_by_tid: Dict[str, Dict[str, Any]] = {}
    for item in tx_calls:
        tid = item["thread_id"]
        if tid not in tx_by_tid:
            tx_by_tid[tid] = dict(item)
        else:
            # Conserver le premier spawn_step_index et compléter si besoin
            if tx_by_tid[tid].get("spawn_step_index") is None and item.get("spawn_step_index") is not None:
                tx_by_tid[tid]["spawn_step_index"] = item["spawn_step_index"]
            if item.get("target_conversation_id") and not tx_by_tid[tid].get("target_conversation_id"):
                tx_by_tid[tid]["target_conversation_id"] = item["target_conversation_id"]

    db_path = resolve_bridge_db_path(bridge_db)
    conn = _connect_ro(db_path)
    threads_by_id: Dict[str, Dict[str, Any]] = {}
    jobs_by_tid: Dict[str, Dict[str, Any]] = {}
    if conn is not None:
        try:
            with closing(conn):
                threads_by_id, jobs_by_tid = _query_bridge_db_for_threads(
                    conn, parent_cid, list(tx_by_tid.keys())
                )
        except sqlite3.Error:
            threads_by_id = {}
            jobs_by_tid = {}

    ordered_tids: List[str] = []
    seen_tids: Set[str] = set()
    for tid in list(tx_by_tid.keys()) + sorted(threads_by_id.keys()):
        if tid not in seen_tids:
            seen_tids.add(tid)
            ordered_tids.append(tid)

    links: List[Dict[str, Any]] = []
    for tid in ordered_tids:
        tx = tx_by_tid.get(tid, {})
        th = threads_by_id.get(tid, {})
        jb = jobs_by_tid.get(tid, {})

        to_workspace = str(
            th.get("to_workspace") or jb.get("target_workspace") or tx.get("to_workspace") or ""
        ).strip()
        msg_type = str(th.get("msg_type") or tx.get("msg_type") or "").strip().lower()
        target_cid = str(
            th.get("target_conversation_id")
            or jb.get("target_conversation_id")
            or tx.get("target_conversation_id")
            or ""
        ).strip()
        job_status = str(jb.get("status") or "").strip().lower()
        thread_status = str(th.get("status") or "").strip().lower()
        has_active_job = job_status in ACTIVE_BRIDGE_JOB_STATUSES
        dispatched = bool(tx.get("dispatched")) or bool(jb) or bool(target_cid)

        if not dispatched and not has_active_job:
            continue

        links.append(
            {
                "thread_id": tid,
                "to_workspace": to_workspace or "unknown",
                "msg_type": msg_type or "handoff",
                "target_conversation_id": target_cid,
                "spawn_step_index": tx.get("spawn_step_index"),
                "thread_status": thread_status or "open",
                "job_id": str(jb.get("id") or ""),
                "job_status": job_status,
                "job_action": str(jb.get("action") or ""),
                "has_active_job": has_active_job,
                "created_at": str(th.get("created_at") or jb.get("created_at") or tx.get("created_at") or "") or None,
            }
        )
    return links
