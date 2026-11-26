#!/usr/bin/env python3
"""Extraction et agrégation des signaux techniques (thinking, compactions, latences) (100 % stdlib)."""

from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, Iterable, List, Optional

_COMPACT_CMD_RE = re.compile(r"^/compact(?::[a-z0-9_-]+)?(?:\s|$)", re.IGNORECASE)
_USER_REQ_OPEN = "<USER_REQUEST>"
_USER_REQ_CLOSE = "</USER_REQUEST>"
MAX_LATENCY_GAP_S = 600.0
COMPACTION_PREV_MIN_TOKENS = 40_000
COMPACTION_DROP_RATIO = 0.55


def _percentile_sorted(sorted_vals: List[int], pct: float) -> int:
    """Retourne le percentile `pct` (0..1) d'une liste triée d'entiers."""
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(pct * len(sorted_vals)) - 1))
    return int(sorted_vals[idx])


def _parse_iso_dt(ts_str: Any) -> Optional[datetime]:
    if not ts_str or not isinstance(ts_str, str):
        return None
    raw = ts_str.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_user_text_for_compact(raw: str) -> str:
    txt = str(raw or "")
    s_idx = txt.find(_USER_REQ_OPEN)
    if s_idx != -1:
        after = txt[s_idx + len(_USER_REQ_OPEN) :]
        e_idx = after.find(_USER_REQ_CLOSE)
        txt = after[:e_idx] if e_idx != -1 else after
    return txt.strip()


def _extract_thinking_chars(raw_thinking: Any) -> int:
    if isinstance(raw_thinking, str):
        return len(raw_thinking)
    if isinstance(raw_thinking, list):
        total = 0
        for item in raw_thinking:
            if isinstance(item, str):
                total += len(item)
            elif isinstance(item, dict):
                total += len(str(item.get("text") or item.get("thinking") or ""))
        return total
    if isinstance(raw_thinking, dict):
        return len(str(raw_thinking.get("text") or raw_thinking.get("thinking") or ""))
    return 0


class TechnicalSignalAccumulator:
    """Accumulateur en une passe des signaux techniques d'un transcript agent."""

    def __init__(self) -> None:
        self.thinking_calls = 0
        self.thinking_chars = 0
        self.compaction_count = 0
        self._prev_ctx = 0
        self._just_compacted = False
        self._prev_dt: Optional[datetime] = None
        self.latency_samples_ms: List[int] = []

    def feed(self, obj: Dict[str, Any], dt: Optional[datetime] = None) -> None:
        """Consomme une entrée JSON de transcript dans la fenêtre courante."""
        eff_dt = dt if dt is not None else _parse_iso_dt(obj.get("created_at"))
        if eff_dt is not None:
            if self._prev_dt is not None:
                delta_s = (eff_dt - self._prev_dt).total_seconds()
                if 0.0 < delta_s <= MAX_LATENCY_GAP_S:
                    self.latency_samples_ms.append(int(round(delta_s * 1000.0)))
            self._prev_dt = eff_dt

        step_type = obj.get("type")
        if step_type == "USER_INPUT":
            user_txt = _extract_user_text_for_compact(str(obj.get("content") or ""))
            if _COMPACT_CMD_RE.match(user_txt):
                self.compaction_count += 1
                self._just_compacted = True
            return

        if step_type != "PLANNER_RESPONSE":
            return

        t_chars = _extract_thinking_chars(obj.get("thinking"))
        if t_chars > 0:
            self.thinking_calls += 1
            self.thinking_chars += t_chars

        ctx = int(obj.get("input_tokens") or 0) + int(obj.get("cache_read_tokens") or 0)
        if ctx > 0:
            if (
                self._prev_ctx > COMPACTION_PREV_MIN_TOKENS
                and ctx < COMPACTION_DROP_RATIO * self._prev_ctx
                and not self._just_compacted
            ):
                self.compaction_count += 1
            self._just_compacted = False
            self._prev_ctx = ctx

    def finalize(self, *, include_samples: bool = True) -> Dict[str, Any]:
        """Calcule les agrégats thinking, compaction et latences (avg, p50, p95)."""
        sorted_ms = sorted(self.latency_samples_ms)
        avg_ms = round(sum(sorted_ms) / len(sorted_ms), 1) if sorted_ms else 0.0
        out: Dict[str, Any] = {
            "thinking_calls": self.thinking_calls,
            "thinking_chars": self.thinking_chars,
            "thinking_est_tokens": self.thinking_chars // 4,
            "compaction_count": self.compaction_count,
            "latency_avg_ms": avg_ms,
            "latency_p50_ms": _percentile_sorted(sorted_ms, 0.50),
            "latency_p95_ms": _percentile_sorted(sorted_ms, 0.95),
        }
        if include_samples:
            out["_latency_samples_ms"] = list(self.latency_samples_ms)
        return out


def extract_technical_signals(steps: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Extrait les signaux techniques depuis une séquence de steps de transcript."""
    acc = TechnicalSignalAccumulator()
    for step in steps:
        if isinstance(step, dict):
            acc.feed(step)
    return acc.finalize(include_samples=False)


def aggregate_technical_signals(agents: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Agrège les signaux techniques de tous les agents d'une session."""
    t_calls = 0
    t_chars = 0
    t_est = 0
    compactions = 0
    samples_ms: List[int] = []
    fallback_p50: List[int] = []
    fallback_p95: List[int] = []
    fallback_avg: List[float] = []

    for ag in agents:
        if not isinstance(ag, dict):
            continue
        t_calls += int(ag.get("thinking_calls") or 0)
        t_chars += int(ag.get("thinking_chars") or 0)
        t_est += int(ag.get("thinking_est_tokens") or 0)
        compactions += int(ag.get("compaction_count") or 0)
        raw_samples = ag.get("_latency_samples_ms")
        if isinstance(raw_samples, list) and raw_samples:
            samples_ms.extend(int(v) for v in raw_samples if isinstance(v, (int, float)) and v > 0)
        elif int(ag.get("latency_p95_ms") or 0) > 0:
            fallback_avg.append(float(ag.get("latency_avg_ms") or 0.0))
            fallback_p50.append(int(ag.get("latency_p50_ms") or 0))
            fallback_p95.append(int(ag.get("latency_p95_ms") or 0))

    if samples_ms:
        sorted_ms = sorted(samples_ms)
        avg_ms = round(sum(sorted_ms) / len(sorted_ms), 1)
        p50_ms = _percentile_sorted(sorted_ms, 0.50)
        p95_ms = _percentile_sorted(sorted_ms, 0.95)
        samples_count = len(sorted_ms)
    elif fallback_p95:
        avg_ms = round(sum(fallback_avg) / len(fallback_avg), 1)
        p50_ms = _percentile_sorted(sorted(fallback_p50), 0.50)
        p95_ms = max(fallback_p95)
        samples_count = len(fallback_p95)
    else:
        avg_ms = 0.0
        p50_ms = 0
        p95_ms = 0
        samples_count = 0

    return {
        "thinking_calls": t_calls,
        "thinking_chars": t_chars,
        "thinking_est_tokens": t_est,
        "compaction_count": compactions,
        "latency_avg_ms": avg_ms,
        "latency_p50_ms": p50_ms,
        "latency_p95_ms": p95_ms,
        "latency_samples_count": samples_count,
    }
