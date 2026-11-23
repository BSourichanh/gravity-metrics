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
_BRIDGE_TX_CACHE: Dict[str, Tuple[Tuple[int, int], List[Dict[str, Any]]]] = {}
_BRIDGE_TX_CACHE_MAX = 512


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


def _is_callback_job(job: Any) -> bool:
    """Retourne True si un job Bridge correspond à un callback retour."""
    if not isinstance(job, dict) or not job:
        return False
    direction = str(job.get("direction") or "").strip().lower()
    action = str(job.get("action") or "").strip().lower()
    return direction == "callback" or action == "send_message"


def load_bridge_target_conversation_ids(
    bridge_db: Optional[Union[Path, str]] = None,
) -> Set[str]:
    """Retourne tous les `target_conversation_id` des enfants Bridge connus (hors callbacks)."""
    db_path = resolve_bridge_db_path(bridge_db)
    conn = _connect_ro(db_path)
    if conn is None:
        return set()
    targets: Set[str] = set()
    try:
        with closing(conn):
            tcols = _table_columns(conn, "threads")
            if "target_conversation_id" in tcols:
                for row in conn.execute(
                    "SELECT DISTINCT target_conversation_id FROM threads "
                    "WHERE target_conversation_id IS NOT NULL AND TRIM(target_conversation_id) != ''"
                ):
                    val = str(row[0] or "").strip()
                    if val:
                        targets.add(val)

            jcols = _table_columns(conn, "jobs")
            if "target_conversation_id" in jcols:
                clauses = [
                    "target_conversation_id IS NOT NULL AND TRIM(target_conversation_id) != ''"
                ]
                if "direction" in jcols:
                    clauses.append("COALESCE(LOWER(TRIM(direction)), '') != 'callback'")
                if "action" in jcols:
                    clauses.append("COALESCE(LOWER(TRIM(action)), '') != 'send_message'")
                where_sql = " AND ".join(clauses)
                for row in conn.execute(
                    f"SELECT DISTINCT target_conversation_id FROM jobs WHERE {where_sql}"
                ):
                    val = str(row[0] or "").strip()
                    if val:
                        targets.add(val)
    except sqlite3.Error:
        return set()
    return targets


def load_bridge_caller_conversation_ids(
    bridge_db: Optional[Union[Path, str]] = None,
) -> Set[str]:
    """Retourne tous les `caller_conversation_id` connus dans `threads` de `bridge.db`."""
    db_path = resolve_bridge_db_path(bridge_db)
    conn = _connect_ro(db_path)
    if conn is None:
        return set()
    callers: Set[str] = set()
    try:
        with closing(conn):
            tcols = _table_columns(conn, "threads")
            if "caller_conversation_id" not in tcols:
                return set()
            for row in conn.execute(
                "SELECT DISTINCT caller_conversation_id FROM threads "
                "WHERE caller_conversation_id IS NOT NULL AND TRIM(caller_conversation_id) != ''"
            ):
                val = str(row[0] or "").strip()
                if val:
                    callers.add(val)
    except sqlite3.Error:
        return set()
    return callers


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
    """Extrait les appels `send_message`/`dispatch_thread` depuis le transcript parent (cache mtime_ns+size)."""
    if not transcript_path.is_file():
        return []
    try:
        st = transcript_path.stat()
        cache_key = str(transcript_path.resolve())
        sig = (int(st.st_mtime_ns), int(st.st_size))
        cached = _BRIDGE_TX_CACHE.get(cache_key)
        if cached is not None and cached[0] == sig:
            return [dict(item) for item in cached[1]]
    except OSError:
        cache_key = ""
        sig = (0, 0)

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
    if cache_key:
        if len(_BRIDGE_TX_CACHE) >= _BRIDGE_TX_CACHE_MAX:
            _BRIDGE_TX_CACHE.pop(next(iter(_BRIDGE_TX_CACHE)))
        _BRIDGE_TX_CACHE[cache_key] = (sig, [dict(item) for item in discovered])
    return discovered


def _record_thread_job(
    jobs_by_tid: Dict[str, Dict[str, Dict[str, Any]]],
    tid: str,
    job: Dict[str, Any],
) -> None:
    """Classe un job dans `outbound`, `active_outbound` et `active_callback` par `thread_id`."""
    bucket = jobs_by_tid.setdefault(
        tid,
        {"outbound": {}, "active_outbound": {}, "active_callback": {}},
    )
    status = str(job.get("status") or "").strip().lower()
    is_active = status in ACTIVE_BRIDGE_JOB_STATUSES
    if _is_callback_job(job):
        if is_active:
            bucket["active_callback"] = job
    else:
        bucket["outbound"] = job
        if is_active:
            bucket["active_outbound"] = job


def _query_bridge_db_for_threads(
    conn: sqlite3.Connection,
    parent_cid: str,
    thread_ids: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Dict[str, Any]]]]:
    """Interroge `threads` et `jobs` par `caller_conversation_id` (v0.2.0+) et par `thread_id`."""
    threads_by_id: Dict[str, Dict[str, Any]] = {}
    jobs_by_tid: Dict[str, Dict[str, Dict[str, Any]]] = {}

    thread_cols = _table_columns(conn, "threads")
    job_cols = _table_columns(conn, "jobs")
    order_cols = [c for c in ("created_at", "id") if c in job_cols]
    order_sql = f" ORDER BY {', '.join(f'{c} ASC' for c in order_cols)}" if order_cols else ""

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
            f"SELECT * FROM jobs WHERE caller_conversation_id = ?{order_sql}",
            (parent_cid,),
        ):
            r = dict(row)
            if _is_callback_job(r):
                continue
            tid = str(r.get("thread_id") or "").strip()
            if tid:
                _record_thread_job(jobs_by_tid, tid, r)
                if tid not in threads_by_id and thread_cols:
                    trow = conn.execute("SELECT * FROM threads WHERE id = ?", (tid,)).fetchone()
                    if trow is not None:
                        threads_by_id[tid] = dict(trow)

    all_tids = sorted(set(thread_ids) | set(threads_by_id.keys()) | set(jobs_by_tid.keys()))
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
        for tid in all_tids:
            jobs_by_tid.pop(tid, None)
        placeholders = ",".join("?" for _ in all_tids)
        for row in conn.execute(
            f"SELECT * FROM jobs WHERE thread_id IN ({placeholders}){order_sql}",
            tuple(all_tids),
        ):
            r = dict(row)
            tid = str(r.get("thread_id") or "").strip()
            if tid:
                _record_thread_job(jobs_by_tid, tid, r)

    return threads_by_id, jobs_by_tid


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
    jobs_by_tid: Dict[str, Dict[str, Dict[str, Any]]] = {}
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
    for tid in list(tx_by_tid.keys()) + sorted(set(threads_by_id.keys()) | set(jobs_by_tid.keys())):
        if tid not in seen_tids:
            seen_tids.add(tid)
            ordered_tids.append(tid)

    links: List[Dict[str, Any]] = []
    for tid in ordered_tids:
        tx = tx_by_tid.get(tid, {})
        th = threads_by_id.get(tid, {})
        jb_info = jobs_by_tid.get(tid, {})
        outbound_jb = jb_info.get("outbound") or {}
        active_out_jb = jb_info.get("active_outbound") or {}
        active_cb_jb = jb_info.get("active_callback") or {}

        to_workspace = str(
            th.get("to_workspace") or outbound_jb.get("target_workspace") or tx.get("to_workspace") or ""
        ).strip()
        msg_type = str(th.get("msg_type") or tx.get("msg_type") or "").strip().lower()
        target_cid = str(
            th.get("target_conversation_id")
            or outbound_jb.get("target_conversation_id")
            or tx.get("target_conversation_id")
            or ""
        ).strip()
        has_active_job = bool(active_out_jb or active_cb_jb)
        display_jb = active_out_jb or active_cb_jb or outbound_jb
        job_status = str(display_jb.get("status") or "").strip().lower()
        thread_status = str(th.get("status") or "").strip().lower()
        dispatched = bool(tx.get("dispatched")) or bool(outbound_jb) or bool(target_cid)

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
                "job_id": str(display_jb.get("id") or ""),
                "job_status": job_status,
                "job_action": str(display_jb.get("action") or ""),
                "has_active_job": has_active_job,
                "created_at": str(
                    th.get("created_at") or outbound_jb.get("created_at") or tx.get("created_at") or ""
                )
                or None,
            }
        )
    return links


def resolve_project_from_bridge_or_transcript(
    conversation_id: str,
    transcript_path: Optional[Union[Path, str]] = None,
    bridge_db: Optional[Union[Path, str]] = None,
) -> str:
    """Résout le projet depuis `bridge.db` RO (`from_workspace` ou `canonical_path` dans le transcript)."""
    cid = str(conversation_id or "").strip()
    db_path = resolve_bridge_db_path(bridge_db)
    conn = _connect_ro(db_path)
    if conn is None:
        return ""

    workspaces: List[Tuple[str, str]] = []
    try:
        with closing(conn):
            tcols = _table_columns(conn, "threads")
            if cid and "from_workspace" in tcols and "caller_conversation_id" in tcols:
                row = conn.execute(
                    "SELECT from_workspace FROM threads "
                    "WHERE caller_conversation_id = ? AND from_workspace IS NOT NULL AND TRIM(from_workspace) != '' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                if row is not None and row[0]:
                    return str(row[0]).strip()

            wcols = _table_columns(conn, "workspaces")
            if "canonical_path" in wcols:
                name_col = "slug" if "slug" in wcols else ("display_name" if "display_name" in wcols else "")
                sel = f"canonical_path{', ' + name_col if name_col else ''}"
                for row in conn.execute(
                    f"SELECT {sel} FROM workspaces WHERE canonical_path IS NOT NULL AND TRIM(canonical_path) != ''"
                ):
                    cpath = str(row[0] or "").strip()
                    if not cpath:
                        continue
                    wname = str(row[1] or "").strip() if name_col and len(row) > 1 else ""
                    if not wname:
                        wname = Path(cpath).name
                    norm_cp = os.path.normcase(os.path.abspath(cpath)).replace("\\", "/").rstrip("/")
                    if norm_cp and wname:
                        workspaces.append((norm_cp, wname))
    except sqlite3.Error:
        return ""

    if not workspaces or not transcript_path:
        return ""
    tpath = Path(transcript_path)
    if not tpath.is_file():
        return ""

    workspaces.sort(key=lambda item: -len(item[0]))
    try:
        with tpath.open("r", encoding="utf-8", errors="replace") as fh:
            for idx, raw_line in enumerate(fh):
                if idx >= 120:
                    break
                line_norm = raw_line.replace("\\\\", "/").replace("\\", "/")
                line_cf = line_norm.lower()
                for norm_cp, wname in workspaces:
                    if norm_cp.lower() in line_cf:
                        return wname
    except OSError:
        return ""
    return ""
