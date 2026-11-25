#!/usr/bin/env python3
"""Résolution canonique des dépôts/workspaces, normalisation des worktrees et classification atelier/code (100 % stdlib)."""

from collections import Counter
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
from urllib.parse import unquote, urlparse

KNOWN_GRAVITY_REPOS: Tuple[str, ...] = (
    "gravity-architect",
    "gravity-workflow",
    "gravity-metrics",
    "gravity-studio",
    "gravity",
)

WRITE_TOOL_NAMES = frozenset({"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"})
READ_TOOL_NAMES = frozenset({"read_file", "view_file", "list_dir", "grep_search", "find_by_name"})
SHELL_TOOL_NAMES = frozenset({"run_command", "execute_command", "bash", "shell"})

_ATELIER_DOC_PATH_RE = re.compile(r"(?:^|/)(?:specs|tasks|tracking)/", re.IGNORECASE)
_SPEC_TASK_PATH_RE = re.compile(r"(?:^|/)(?:specs|tasks)/", re.IGNORECASE)
_WT_GRAVITY_RE = re.compile(r"(?:^|/)gravity-worktrees/([^/]+)(?:/|$)")
_WT_GEMINI_RE = re.compile(r"(?:^|/)\.gemini/antigravity/worktrees/([^/]+)(?:/|$)")
_WT_DOT_RE = re.compile(r"(?:^|/)\.worktrees/([^/]+)(?:/|$)")
_WORKER_SUFFIX_RE = re.compile(r"-w\d+$", re.IGNORECASE)
_CONTAINER_DIRS = frozenset({"github", "repos", "repo", "projects", "project", "workspaces", "workspace", "work"})
_SCHEME_PREFIX = "fi" + "le:"


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
    """Extrait la commande shell depuis un tool_call (`CommandLine`/`command`/`cmd`), y compris JSON doublement quoté."""
    args = _parse_tool_call_args(tc)
    raw = (
        args.get("CommandLine")
        or args.get("command")
        or args.get("command_line")
        or args.get("cmd")
        or ""
    )
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
    if not raw_path:
        return False
    return bool(_ATELIER_DOC_PATH_RE.search(raw_path.replace("\\", "/").strip()))


def _strip_file_uri(raw: str) -> str:
    s = str(raw or "").strip()
    if s.lower().startswith(_SCHEME_PREFIX):
        parsed = urlparse(s)
        p = unquote(parsed.path or "")
        if len(p) >= 3 and p[0] == "/" and p[2] == ":" and p[1].isalpha():
            p = p[1:]
        return p
    return s


def _normalize_wt_segment(seg: str, known_repos: Set[str], *, generic_suffix: bool) -> str:
    clean = seg.strip()
    if not clean:
        return ""
    for repo in sorted(known_repos, key=lambda r: (-len(r), r)):
        if clean == repo or clean.startswith(repo + "-"):
            return repo
    stripped = _WORKER_SUFFIX_RE.sub("", clean)
    if stripped != clean and stripped:
        return stripped
    if generic_suffix and "-" in clean:
        base, _tail = clean.rsplit("-", 1)
        if base:
            return base
    return clean


def discover_known_repos(
    root_workspace: str = "",
    root_workspace_path: Optional[Union[Path, str]] = None,
) -> Set[str]:
    """Construit l'ensemble des noms de dépôts connus depuis l'écosystème, `projects.json` et les voisins disque."""
    repos: Set[str] = set(KNOWN_GRAVITY_REPOS)
    if root_workspace and root_workspace != "root":
        repos.add(root_workspace)
    if root_workspace_path is None:
        return repos
    try:
        rpath = Path(root_workspace_path)
    except Exception:
        return repos
    if rpath.name:
        repos.add(rpath.name)
    proj_file = rpath / "projects.json"
    if proj_file.is_file():
        try:
            pdata = json.loads(proj_file.read_text(encoding="utf-8"))
            if isinstance(pdata, dict):
                items = pdata.get("projects")
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict):
                            name = str(it.get("name") or it.get("id") or "").strip()
                            if name:
                                repos.add(name)
                            p_rel = str(it.get("path") or "").strip()
                            if p_rel:
                                repos.add(Path(p_rel.replace("\\", "/")).name)
                elif isinstance(items, dict):
                    for k in items:
                        if str(k).strip():
                            repos.add(str(k).strip())
        except Exception:
            pass
    parent = rpath.parent
    if parent.is_dir():
        try:
            for child in parent.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    repos.add(child.name)
        except OSError:
            pass
    return repos


def normalize_worktree_path(
    raw_path: str,
    *,
    root_workspace: str = "",
    root_workspace_path: Optional[Union[Path, str]] = None,
    known_repos: Optional[Set[str]] = None,
    is_workspace_uri: bool = False,
) -> str:
    """Résout un chemin ou URI (worktree inclus) vers le nom canonique du dépôt."""
    cleaned = _strip_file_uri(raw_path)
    if not cleaned:
        return ""
    norm = cleaned.replace("\\", "/").strip()
    if not norm:
        return ""

    repos = known_repos if known_repos is not None else discover_known_repos(root_workspace, root_workspace_path)

    m_gw = _WT_GRAVITY_RE.search(norm)
    if m_gw:
        return _normalize_wt_segment(m_gw.group(1), repos, generic_suffix=False)

    m_gm = _WT_GEMINI_RE.search(norm)
    if m_gm:
        return _normalize_wt_segment(m_gm.group(1), repos, generic_suffix=True)

    m_dw = _WT_DOT_RE.search(norm)
    if m_dw:
        return _normalize_wt_segment(m_dw.group(1), repos, generic_suffix=True)

    low_norm = norm.lower()
    if "/.gemini/antigravity/brain/" in low_norm or "/.system_generated/" in low_norm:
        return ""

    is_abs = norm.startswith("/") or norm.startswith("~/") or bool(re.match(r"^[A-Za-z]:/", norm))
    if not is_abs:
        if norm.startswith("./"):
            norm = norm[2:]
        if not norm.startswith("../"):
            if is_workspace_uri and "/" not in norm:
                return norm
            return root_workspace if (root_workspace and root_workspace != "root") else ""
        if norm.startswith("../"):
            if root_workspace_path is not None:
                joined = posixpath.normpath(f"{Path(root_workspace_path).as_posix()}/{norm}")
                return normalize_worktree_path(
                    joined,
                    root_workspace=root_workspace,
                    root_workspace_path=root_workspace_path,
                    known_repos=repos,
                    is_workspace_uri=is_workspace_uri,
                )
            rest = norm
            while rest.startswith("../"):
                rest = rest[3:]
            seg = rest.split("/", 1)[0].strip()
            return seg if seg and seg not in (".", "..") else ""
        return root_workspace if (root_workspace and root_workspace != "root") else ""

    if is_workspace_uri:
        base = norm.rstrip("/").rsplit("/", 1)[-1].strip()
        return base

    if root_workspace_path is not None:
        root_posix = Path(root_workspace_path).as_posix().rstrip("/")
        parent_posix = Path(root_workspace_path).parent.as_posix().rstrip("/")
        if norm == root_posix or norm.startswith(root_posix + "/"):
            return root_workspace or Path(root_workspace_path).name
        if parent_posix and parent_posix != "/" and norm.startswith(parent_posix + "/"):
            rel = norm[len(parent_posix) + 1 :]
            first_seg = rel.split("/", 1)[0].strip()
            if first_seg and not first_seg.startswith("."):
                return first_seg

    parts = [p for p in norm.split("/") if p]
    for seg in reversed(parts):
        if seg in repos:
            return seg

    try:
        cand = Path(cleaned).expanduser()
        probe = cand if cand.is_dir() else cand.parent
        for _ in range(6):
            if probe.is_dir() and (probe / ".git").exists():
                return probe.name
            if probe.parent == probe:
                break
            probe = probe.parent
    except Exception:
        pass

    for idx, seg in enumerate(parts[:-1]):
        if seg.lower() in _CONTAINER_DIRS or re.match(r"^tmp[a-z0-9_]{4,}$", seg, re.IGNORECASE):
            nxt = parts[idx + 1]
            if nxt and not nxt.startswith("."):
                return nxt
    return ""


def classify_workspace_kind(
    workspace: str,
    *,
    root_workspace: str = "",
    root_workspace_path: Optional[Union[Path, str]] = None,
    workspace_path: Optional[Union[Path, str]] = None,
    has_atelier_markers: Optional[bool] = None,
) -> str:
    """Classe un workspace en `'atelier'` (orchestration root avec `projects.json`/`specs/`) ou `'code'`."""
    ws_clean = str(workspace or "").strip()
    root_clean = str(root_workspace or "").strip()
    if not ws_clean:
        return "code"

    if workspace_path is not None:
        wpath = Path(workspace_path)
        if wpath.is_dir():
            is_atelier = (wpath / "projects.json").is_file() or (wpath / "specs").is_dir()
            if root_clean and ws_clean != root_clean:
                return "code"
            return "atelier" if is_atelier else "code"

    if root_clean and ws_clean != root_clean:
        return "code"

    if has_atelier_markers is not None:
        return "atelier" if has_atelier_markers else "code"

    if root_workspace_path is not None:
        rpath = Path(root_workspace_path)
        if rpath.is_dir():
            is_atelier = (rpath / "projects.json").is_file() or (rpath / "specs").is_dir()
            return "atelier" if is_atelier else "code"

    if ws_clean.lower() == "gravity-studio":
        return "atelier"
    return "code"


def _split_shell_cmds(cmd: str) -> List[str]:
    segments: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    escape = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if escape:
            buf.append(ch)
            escape = False
        elif ch == "\\" and quote != "'":
            buf.append(ch)
            escape = True
        elif ch in ('"', "'") and quote in (None, ch):
            quote = None if quote == ch else ch
            buf.append(ch)
        elif quote is None and cmd[i : i + 2] in ("&&", "||"):
            if "".join(buf).strip():
                segments.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        elif quote is None and ch in (";", "|", "\n"):
            if "".join(buf).strip():
                segments.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if "".join(buf).strip():
        segments.append("".join(buf).strip())
    return segments


def extract_shell_target_dirs(cmd: str) -> List[str]:
    """Extrait les cibles `cd <dir>` et `git -C <dir>` d'une ligne de commande shell."""
    targets: List[str] = []
    if not cmd:
        return targets
    for seg in _split_shell_cmds(cmd):
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            tokens = seg.split()
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) >= 2:
            dest = tokens[1].strip()
            if dest and dest not in ("-", "~"):
                targets.append(dest)
            continue
        cmd_name = Path(tokens[0]).name.lower()
        if cmd_name == "git":
            idx = 1
            while idx < len(tokens):
                tok = tokens[idx]
                if tok == "-C" and idx + 1 < len(tokens):
                    targets.append(tokens[idx + 1])
                    idx += 2
                    continue
                if tok.startswith("-C") and len(tok) > 2:
                    targets.append(tok[2:])
                elif not tok.startswith("-"):
                    break
                idx += 1
    return targets


def _extract_tool_path_arg(args: Dict[str, Any]) -> str:
    for key in ("Path", "TargetFile", "AbsolutePath", "DirectoryPath", "SearchPath", "SearchDirectory", "path", "file_path", "target_file"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class WorkspaceSignalAccumulator:
    """Accumulateur léger collectant les chemins d'écriture, commandes shell et lectures d'un transcript."""

    def __init__(self) -> None:
        self.write_paths: List[str] = []
        self.shell_dirs: List[str] = []
        self.read_paths: List[str] = []

    def feed_tool_call(self, tc: Dict[str, Any]) -> None:
        """Enregistre les indices workspace d'un appel d'outil `PLANNER_RESPONSE`."""
        tname = str(tc.get("name") or "").strip().lower()
        if not tname:
            return
        args = _parse_tool_call_args(tc)
        if tname in WRITE_TOOL_NAMES:
            p = _extract_tool_path_arg(args)
            if p:
                self.write_paths.append(p)
        elif tname in SHELL_TOOL_NAMES:
            cwd_arg = args.get("Cwd") or args.get("cwd")
            if isinstance(cwd_arg, str) and cwd_arg.strip():
                self.shell_dirs.append(cwd_arg.strip())
            cmd = _extract_shell_command_str(tc)
            if cmd:
                self.shell_dirs.extend(extract_shell_target_dirs(cmd))
        elif tname in READ_TOOL_NAMES:
            p = _extract_tool_path_arg(args)
            if p:
                self.read_paths.append(p)

    def finalize(self) -> Dict[str, List[str]]:
        """Retourne les listes collectées pour résolution post-parsing."""
        return {
            "write_paths": self.write_paths,
            "shell_dirs": self.shell_dirs,
            "read_paths": self.read_paths,
        }


def _pick_majority(votes: Counter, root_workspace: str) -> str:
    if not votes:
        return ""
    return min(votes.items(), key=lambda kv: (-kv[1], kv[0] == root_workspace, kv[0]))[0]


def resolve_agent_workspace(
    node: Dict[str, Any],
    ws_signals: Optional[Dict[str, Any]] = None,
    *,
    root_workspace: str = "root",
    root_workspace_path: Optional[Union[Path, str]] = None,
    known_repos: Optional[Set[str]] = None,
) -> str:
    """Résout le dépôt canonique d'un agent selon la hiérarchie stricte (1..5)."""
    repos = known_repos if known_repos is not None else discover_known_repos(root_workspace, root_workspace_path)
    root_ws = str(root_workspace or "").strip() or "root"

    bridge_ws = str(node.get("bridge_workspace") or "").strip()
    if bridge_ws:
        norm_b = normalize_worktree_path(
            bridge_ws,
            root_workspace=root_ws,
            root_workspace_path=root_workspace_path,
            known_repos=repos,
            is_workspace_uri=True,
        )
        return norm_b or bridge_ws

    sigs = ws_signals or {}
    write_votes: Counter = Counter()
    for p in list(sigs.get("write_paths") or []) + list(sigs.get("shell_dirs") or []):
        repo = normalize_worktree_path(
            str(p),
            root_workspace=root_ws,
            root_workspace_path=root_workspace_path,
            known_repos=repos,
        )
        if repo:
            write_votes[repo] += 1
    if write_votes:
        return _pick_majority(write_votes, root_ws)

    read_votes: Counter = Counter()
    for p in sigs.get("read_paths") or []:
        p_str = str(p)
        repo = normalize_worktree_path(
            p_str,
            root_workspace=root_ws,
            root_workspace_path=root_workspace_path,
            known_repos=repos,
        )
        if not repo:
            continue
        if repo == root_ws and _SPEC_TASK_PATH_RE.search(p_str.replace("\\", "/").strip()):
            continue
        read_votes[repo] += 1
    if read_votes:
        return _pick_majority(read_votes, root_ws)

    raw_uris = node.get("workspace_uris") or node.get("workspaceUris") or []
    for uri in raw_uris:
        if not isinstance(uri, str) or not uri.strip():
            continue
        repo = normalize_worktree_path(
            uri,
            root_workspace=root_ws,
            root_workspace_path=root_workspace_path,
            known_repos=repos,
            is_workspace_uri=True,
        )
        if repo and repo != root_ws:
            return repo

    return root_ws
