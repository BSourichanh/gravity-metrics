#!/usr/bin/env python3
"""Tarification des modèles, chemins centralisés et calcul des coûts (100 % stdlib)."""

import fnmatch
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple, Union

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
    "teamwork_test_writer": "testing",
    "teamwork_reviewer": "verification",
    "teamwork_challenger": "verification",
    "teamwork_auditor": "verification",
    "reviewer": "verification",
    "bridge_handoff": "build",
    "bridge_question": "discovery",
    "bridge_info": "orchestration",
    "bridge": "orchestration",
}

GENERIC_AGENT_TYPES = {"research", "self"}


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
    tn = (type_name or "").strip().lower()
    if tn in TYPE_TO_CATEGORY and tn not in GENERIC_AGENT_TYPES:
        return TYPE_TO_CATEGORY[tn]

    rl = (role or "").strip().lower()
    if re.search(r"orchestrator|coordinator|interviewer|planner", rl):
        return "orchestration"
    if re.search(r"explorer|spec.?miner|research", rl):
        return "discovery"
    if re.search(r"test.?writer|e2e.*writer", rl):
        return "testing"
    if re.search(r"reviewer|challenger|auditor", rl):
        return "verification"
    if re.search(r"worker|builder", rl):
        return "build"
    if tn in TYPE_TO_CATEGORY:
        return TYPE_TO_CATEGORY[tn]
    return "other"
