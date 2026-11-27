#!/usr/bin/env python3
"""Tarification des modèles, chemins centralisés et calcul des coûts (100 % stdlib)."""

import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

SCHEMA_VERSION = "2.1.0"

CATEGORY_ORDER = (
    "orchestration",
    "discovery",
    "build",
    "testing",
    "verification",
    "other",
)

CATEGORY_LABELS_FR = {
    "orchestration": "Orchestration & Pilotage",
    "discovery": "Découverte & Cadrage",
    "build": "Construction & Remédiation",
    "testing": "Tests E2E",
    "verification": "Vérification (Review/Challenge/Audit)",
    "other": "Autres",
}

TYPE_TO_CATEGORY = {
    "root": "orchestration",
    "teamwork": "orchestration",
    "teamwork_orchestrator": "orchestration",
    "self": "orchestration",
    "planner": "orchestration",
    "teamwork_interviewer": "orchestration",
    "teamwork_explorer": "discovery",
    "teamwork_spec_miner": "discovery",
    "research": "discovery",
    "teamwork_worker": "build",
    "builder": "build",
    "general": "build",
    "general_purpose": "build",
    "teamwork_test_writer": "testing",
    "teamwork_reviewer": "verification",
    "teamwork_challenger": "verification",
    "teamwork_auditor": "verification",
    "reviewer": "verification",
    "code_review": "verification",
    "code_reviewer": "verification",
    "verifier": "verification",
    "bridge_handoff": "build",
    "bridge_question": "discovery",
    "bridge_info": "orchestration",
    "bridge": "orchestration",
}

GENERIC_AGENT_TYPES = {"research", "self", "general", "general_purpose"}


def default_brain_dir() -> Path:
    """Retourne le répertoire racine des conversations Antigravity."""
    env_dir = os.environ.get("ANTIGRAVITY_BRAIN_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".gemini" / "antigravity" / "brain"


def default_store_dir() -> Path:
    """Retourne le répertoire centralisé des données Gravity Metrics."""
    env_home = os.environ.get("GRAVITY_METRICS_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    gravity_home = os.environ.get("GRAVITY_HOME", "").strip()
    if gravity_home:
        return Path(gravity_home).expanduser() / "metrics"
    return Path.home() / ".gemini" / "gravity" / "metrics"


def default_history_path(store_dir: Optional[Union[Path, str]] = None) -> Path:
    """Retourne le chemin par défaut vers data/history.jsonl dans le store centralisé."""
    base = Path(store_dir).expanduser() if store_dir else default_store_dir()
    return base / "data" / "history.jsonl"


def load_pricing_catalog(path: Union[Path, str]) -> Dict[str, Any]:
    """Charge et valide le catalogue tarifaire JSON."""
    catalog_path = Path(path)
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        raise ValueError(f"Catalogue tarifaire invalide : {catalog_path}")
    return data


def resolve_model_pricing(
    model: str, catalog: Dict[str, Any], profile: str
) -> Tuple[Dict[str, Any], bool]:
    """Résout le tarif d'un modèle : exact, puis wildcard fnmatch, sinon _fallback."""
    profiles = catalog.get("profiles", {})
    if profile not in profiles:
        raise ValueError(f"Profil tarifaire inconnu : {profile!r}")
    models_map = profiles[profile].get("models", {})
    model_name = (model or "").strip()

    if model_name and model_name != "_fallback" and model_name in models_map:
        entry = dict(models_map[model_name])
        return entry, bool(entry.get("is_fallback", False))

    for pattern, entry in models_map.items():
        if pattern == "_fallback":
            continue
        if ("*" in pattern or "?" in pattern) and model_name and fnmatch.fnmatchcase(model_name, pattern):
            resolved = dict(entry)
            return resolved, bool(resolved.get("is_fallback", False))

    fallback = dict(
        models_map.get(
            "_fallback",
            {"input": 0.75, "cache_read": 0.075, "output": 3.75, "is_fallback": True},
        )
    )
    return fallback, True


def compute_call_cost(
    input_tok: int,
    cache_tok: int,
    output_tok: int,
    pricing: Dict[str, Any],
) -> Dict[str, float]:
    """Calcule les coûts USD détaillés d'un appel LLM en pleine précision flottante."""
    effective_ctx = input_tok + cache_tok
    threshold = pricing.get("long_context_threshold")

    in_rate = float(pricing.get("input", 0.0))
    cache_rate = float(pricing.get("cache_read", 0.0))
    out_rate = float(pricing.get("output", 0.0))

    if threshold is not None and effective_ctx > int(threshold):
        in_rate = float(pricing.get("input_over_threshold", in_rate))
        cache_rate = float(pricing.get("cache_read_over_threshold", cache_rate))
        out_rate = float(pricing.get("output_over_threshold", out_rate))

    input_usd = (input_tok / 1_000_000.0) * in_rate
    cache_read_usd = (cache_tok / 1_000_000.0) * cache_rate
    output_usd = (output_tok / 1_000_000.0) * out_rate
    total_usd = input_usd + cache_read_usd + output_usd
    without_cache_usd = ((input_tok + cache_tok) / 1_000_000.0) * in_rate + output_usd
    cache_savings_usd = without_cache_usd - total_usd

    return {
        "input_usd": input_usd,
        "cache_read_usd": cache_read_usd,
        "output_usd": output_usd,
        "total_usd": total_usd,
        "without_cache_usd": without_cache_usd,
        "cache_savings_usd": cache_savings_usd,
    }


def _empty_cost_acc() -> Dict[str, float]:
    return {
        "input_usd": 0.0,
        "cache_read_usd": 0.0,
        "output_usd": 0.0,
        "total_usd": 0.0,
        "without_cache_usd": 0.0,
        "cache_savings_usd": 0.0,
    }


def _add_cost(acc: Dict[str, float], delta: Dict[str, float]) -> None:
    for k in acc:
        acc[k] += float(delta.get(k, 0.0))


def _round_cost(acc: Dict[str, float], digits: int = 6) -> Dict[str, float]:
    return {k: round(v, digits) for k, v in acc.items()}


def classify_agent(type_name: str, role: str) -> str:
    """Classe un agent selon son typeName spécialisé, puis le rôle, puis repli générique."""
    tn = re.sub(r"[\s-]+", "_", (type_name or "").strip().lower())
    if tn.startswith("subagent_type_"):
        tn = tn[len("subagent_type_") :]
    if tn in TYPE_TO_CATEGORY and tn not in GENERIC_AGENT_TYPES:
        return TYPE_TO_CATEGORY[tn]

    rl = (role or "").strip().lower()
    if re.search(r"orchestrator|coordinator|interviewer|planner", rl):
        return "orchestration"
    if re.search(r"explorer|spec.?miner|research|investigat", rl):
        return "discovery"
    if re.search(r"test.?writer|e2e.*writer", rl):
        return "testing"
    if re.search(r"worker|builder|implement", rl):
        return "build"
    if re.search(r"reviewer|challenger|auditor|code.?review|verif", rl):
        return "verification"
    if tn in TYPE_TO_CATEGORY:
        return TYPE_TO_CATEGORY[tn]
    return "other"


def safe_int(val: Any) -> int:
    """Retourne un entier non booléen ou 0 par défaut."""
    return val if isinstance(val, int) and not isinstance(val, bool) else 0


def extract_record_tokens(rec: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Retourne `(input_tokens, cache_read_tokens, total_tokens, useful_tokens)` d'un record."""
    in_tok = safe_int(rec.get("input_tokens"))
    cache_tok = safe_int(rec.get("cache_read_tokens"))
    out_tok = safe_int(rec.get("output_tokens"))
    raw_tot = rec.get("total_tokens")
    tot = raw_tot if isinstance(raw_tot, int) and not isinstance(raw_tot, bool) else (in_tok + cache_tok + out_tok)
    raw_useful = rec.get("useful_tokens")
    useful = raw_useful if isinstance(raw_useful, int) and not isinstance(raw_useful, bool) else (in_tok + out_tok)
    return in_tok, cache_tok, tot, useful


def iter_record_category_tokens(rec: Dict[str, Any]) -> List[Tuple[str, int, int, int]]:
    """Extrait `(category, agents, total_tokens, useful_tokens)` avec repli legacy."""
    _, _, rec_tot, rec_useful = extract_record_tokens(rec)
    cat_map = rec.get("category_tokens")
    items: List[Tuple[str, int, int, int]] = []
    if isinstance(cat_map, dict) and cat_map:
        for cat, cdata in cat_map.items():
            if not isinstance(cdata, dict):
                continue
            ag_cnt = safe_int(cdata.get("agents"))
            c_tot = safe_int(cdata.get("tokens"))
            c_useful = safe_int(cdata.get("useful_tokens"))
            if ag_cnt > 0 or c_tot > 0:
                items.append((str(cat), ag_cnt if ag_cnt > 0 else 1, c_tot, c_useful))
        return items
    shares = rec.get("category_share_pct")
    if isinstance(shares, dict) and rec_tot > 0:
        rec_agents = safe_int(rec.get("agents"))
        for cat, pct in shares.items():
            share = max(0.0, float(pct or 0.0))
            if share <= 0.0:
                continue
            est_ag = max(1, round(rec_agents * (share / 100.0))) if rec_agents > 0 else 1
            items.append((str(cat), est_ag, round(rec_tot * (share / 100.0)), round(rec_useful * (share / 100.0))))
    return items


def iter_record_model_tokens(rec: Dict[str, Any]) -> List[Tuple[str, int, int, int, int]]:
    """Extrait `(model, calls, agents, total_tokens, useful_tokens)` avec repli legacy."""
    _, _, rec_tot, rec_useful = extract_record_tokens(rec)
    useful_ratio = (rec_useful / rec_tot) if rec_tot > 0 else 0.0
    models_map = rec.get("models")
    items: List[Tuple[str, int, int, int, int]] = []
    if isinstance(models_map, dict) and models_map:
        has_tok = any(isinstance(v, dict) and v.get("tokens") is not None for v in models_map.values())
        tot_calls = sum(max(0, safe_int(v.get("calls"))) for v in models_map.values() if isinstance(v, dict))
        for m_name, m_data in models_map.items():
            if not isinstance(m_data, dict):
                continue
            m_calls = safe_int(m_data.get("calls"))
            m_agents = safe_int(m_data.get("agents"))
            if has_tok:
                m_tot = safe_int(m_data.get("tokens"))
                raw_mu = m_data.get("useful_tokens")
                m_useful = raw_mu if isinstance(raw_mu, int) and not isinstance(raw_mu, bool) else round(m_tot * useful_ratio)
            else:
                ratio = (m_calls / tot_calls) if tot_calls > 0 else (1.0 / len(models_map))
                m_tot = round(rec_tot * ratio)
                m_useful = round(rec_useful * ratio)
            items.append((str(m_name), m_calls, m_agents, m_tot, m_useful))
        return items
    pm = str(rec.get("primary_model") or "").strip()
    if pm:
        items.append((pm, safe_int(rec.get("llm_calls")), safe_int(rec.get("agents")), rec_tot, rec_useful))
    return items


def _new_axis_bucket(label: str, **extras: int) -> Dict[str, Any]:
    return {
        "label": label, "count": 0, "sessions": 0, "total_tokens": 0, "useful_tokens": 0,
        "cache_num": 0.0, "cache_den": 0.0, "cache_pct_sum": 0.0, **extras,
    }


def _add_weighted_cache(bucket: Dict[str, Any], weight: float, cache_pct: float) -> None:
    bucket["cache_num"] += weight * cache_pct
    bucket["cache_den"] += weight
    bucket["cache_pct_sum"] += cache_pct


def _finalize_analytics_axis(
    buckets: Dict[str, Dict[str, Any]],
    *,
    order_keys: Sequence[str] = (),
    extra_keys: Sequence[str] = (),
) -> Dict[str, Dict[str, Any]]:
    axis_tot = sum(b["total_tokens"] for b in buckets.values())
    if order_keys:
        keys = [k for k in order_keys if k in buckets] + sorted(k for k in buckets if k not in order_keys)
    else:
        keys = sorted(buckets, key=lambda k: (-buckets[k]["total_tokens"], -buckets[k]["count"], k))
    out: Dict[str, Dict[str, Any]] = {}
    for k in keys:
        b = buckets[k]
        cnt, tot, useful, sess = b["count"], b["total_tokens"], b["useful_tokens"], b["sessions"]
        cache_pct = (
            round(b["cache_num"] / b["cache_den"], 2)
            if b["cache_den"] > 0
            else (round(b["cache_pct_sum"] / sess, 2) if sess > 0 else 0.0)
        )
        entry: Dict[str, Any] = {
            "label": b["label"],
            "count": cnt,
            "total_tokens": tot,
            "useful_tokens": useful,
            "avg_tokens": round(tot / cnt, 1) if cnt > 0 else 0.0,
            "avg_useful_tokens": round(useful / cnt, 1) if cnt > 0 else 0.0,
            "cache_hit_rate_pct": cache_pct,
            "share_pct": round((tot / axis_tot) * 100.0, 2) if axis_tot > 0 else 0.0,
        }
        for ek in extra_keys:
            if ek in b:
                entry[ek] = b[ek]
        out[k] = entry
    return out


def accumulate_category_and_model_buckets(
    records: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Agrège les compteurs par catégorie et par modèle (partagé `build_analytics` / `audits_reader`)."""
    cat_b: Dict[str, Dict[str, Any]] = {}
    model_b: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        rec_cache_pct = float(rec.get("cache_hit_rate_pct") or 0.0)
        for cat, ag_cnt, c_tot, c_useful in iter_record_category_tokens(rec):
            b = cat_b.setdefault(cat, _new_axis_bucket(CATEGORY_LABELS_FR.get(cat, cat), agents=0))
            b["count"] += ag_cnt
            b["sessions"] += 1
            b["agents"] += ag_cnt
            b["total_tokens"] += c_tot
            b["useful_tokens"] += c_useful
            _add_weighted_cache(b, float(c_tot), rec_cache_pct)
        for m_name, m_calls, m_agents, m_tot, m_useful in iter_record_model_tokens(rec):
            mb = model_b.setdefault(m_name, _new_axis_bucket(m_name, calls=0, agents=0))
            mb["count"] += 1
            mb["sessions"] += 1
            mb["calls"] += m_calls
            mb["agents"] += m_agents
            mb["total_tokens"] += m_tot
            mb["useful_tokens"] += m_useful
            _add_weighted_cache(mb, float(m_tot), rec_cache_pct)
    return cat_b, model_b


def build_analytics(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Agrège les 4 axes tokens (`by_agent_type`, `by_phase`, `by_session_mode`, `by_model`) sans I/O."""
    agent_b, model_b = accumulate_category_and_model_buckets(records)
    phase_b: Dict[str, Dict[str, Any]] = {}
    mode_b: Dict[str, Dict[str, Any]] = {}

    for rec in records:
        rec_in, rec_cache, rec_tot, rec_useful = extract_record_tokens(rec)
        rec_cache_pct = float(rec.get("cache_hit_rate_pct") or 0.0)
        useful_ratio = (rec_useful / rec_tot) if rec_tot > 0 else 0.0

        ms = rec.get("milestones")
        if isinstance(ms, dict) and isinstance(ms.get("phases"), list):
            for ph in ms["phases"]:
                if not isinstance(ph, dict):
                    continue
                pid = str(ph.get("id") or "").strip().lower()
                if not pid:
                    continue
                p_tot = safe_int(ph.get("tokens"))
                p_calls = safe_int(ph.get("llm_calls"))
                p_agents = safe_int(ph.get("agents"))
                if str(ph.get("status") or "") == "pending" and p_tot == 0 and p_calls == 0 and p_agents == 0:
                    continue
                p_useful = safe_int(ph["useful_tokens"]) if ph.get("useful_tokens") is not None else round(p_tot * useful_ratio)
                pb = phase_b.setdefault(pid, _new_axis_bucket(str(ph.get("label") or pid.capitalize()), agents=0, llm_calls=0))
                pb["count"] += 1
                pb["sessions"] += 1
                pb["agents"] += p_agents
                pb["llm_calls"] += p_calls
                pb["total_tokens"] += p_tot
                pb["useful_tokens"] += p_useful
                _add_weighted_cache(pb, float(p_tot), rec_cache_pct)
        elif rec.get("current_phase") or rec_tot > 0:
            raw_cp = str(rec.get("current_phase") or "production").strip()
            pid = "cloture" if "clôture" in raw_cp.lower() or "cloture" in raw_cp.lower() else raw_cp.lower()
            pb = phase_b.setdefault(pid, _new_axis_bucket(raw_cp or "Production", agents=0, llm_calls=0))
            pb["count"] += 1
            pb["sessions"] += 1
            pb["agents"] += safe_int(rec.get("agents"))
            pb["llm_calls"] += safe_int(rec.get("llm_calls"))
            pb["total_tokens"] += rec_tot
            pb["useful_tokens"] += rec_useful
            _add_weighted_cache(pb, float(rec_tot), rec_cache_pct)

        mode_key = str(rec.get("execution_mode") or "single").strip().lower() or "single"
        mb = mode_b.setdefault(mode_key, _new_axis_bucket(mode_key))
        mb["count"] += 1
        mb["sessions"] += 1
        mb["total_tokens"] += rec_tot
        mb["useful_tokens"] += rec_useful
        prompt_tok = rec_in + rec_cache
        if prompt_tok > 0:
            _add_weighted_cache(mb, float(prompt_tok), (rec_cache / prompt_tok) * 100.0)
        else:
            mb["cache_pct_sum"] += rec_cache_pct

    return {
        "by_agent_type": _finalize_analytics_axis(agent_b, order_keys=CATEGORY_ORDER, extra_keys=("sessions",)),
        "by_phase": _finalize_analytics_axis(
            phase_b, order_keys=("conception", "planification", "production", "validation", "cloture"),
            extra_keys=("agents", "llm_calls"),
        ),
        "by_session_mode": _finalize_analytics_axis(mode_b),
        "by_model": _finalize_analytics_axis(model_b, extra_keys=("calls", "agents")),
    }
