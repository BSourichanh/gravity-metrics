#!/usr/bin/env python3
"""Formateurs de nombres, durées et résumés pour les rapports Gravity (100 % stdlib)."""

from typing import Any, Dict


def _fmt_int(val: Any) -> str:
    return f"{int(val or 0):,}".replace(",", " ")


def _fmt_short_tok(val: Any) -> str:
    n = int(val or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _fmt_dur(seconds: float) -> str:
    s = int(round(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _session_date_str(rec: Dict[str, Any]) -> str:
    ts = rec.get("started_at") or rec.get("added_at") or ""
    return str(ts)[:16].replace("T", " ")


def _alerts_str(rec: Dict[str, Any]) -> str:
    return (
        f"D:{int(rec.get('duplicate_spawns') or 0)} "
        f"R:{int(rec.get('rewind_calls') or 0)} "
        f"I:{int(rec.get('interrupted_agents') or 0)}"
    )


def _format_models_summary(rec: Dict[str, Any]) -> str:
    models = rec.get("models") or {}
    if not models:
        pm = str(rec.get("primary_model") or "unknown")
        return f"`{pm}`"
    items = sorted(
        models.items(),
        key=lambda kv: (-int(kv[1].get("calls") or 0), -float(kv[1].get("cost_usd") or 0.0), kv[0]),
    )
    return ", ".join(f"`{m_name}` ({int(m_data.get('calls') or 0)})" for m_name, m_data in items)
