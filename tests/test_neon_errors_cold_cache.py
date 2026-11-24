#!/usr/bin/env python3
"""Tests unitaires : tarifs Neon/Flash, erreurs ventilées et cache froid (100 % stdlib)."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = PROJECT_ROOT / "plugin"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    collect_run_metrics,
    load_pricing_catalog,
    render_inspect_text,
    render_session_detail_markdown,
    resolve_model_pricing,
)

CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"


def _write_transcript(conv_dir: Path, steps: list) -> Path:
    logs_dir = conv_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    tpath = logs_dir / "transcript.jsonl"
    payload = "\n".join(
        s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in steps
    )
    tpath.write_text(payload + "\n", encoding="utf-8")
    return tpath


class TestNeonErrorsAndColdCache(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_pricing_catalog(CATALOG_PATH)

    def test_neon_and_flash_worker_pricing_and_inspect_fallback(self) -> None:
        p_neon_26, fb_neon_26 = resolve_model_pricing(
            "gemini-pro-orchestrator-neon", self.catalog, "2026_nov_standard"
        )
        self.assertFalse(fb_neon_26)
        self.assertEqual((p_neon_26["input"], p_neon_26["cache_read"], p_neon_26["output"]), (3.0, 0.15, 15.0))

        p_neon_27, fb_neon_27 = resolve_model_pricing(
            "gemini-pro-orchestrator-neon-v2", self.catalog, "2027_standard"
        )
        self.assertFalse(fb_neon_27)
        self.assertEqual((p_neon_27["input"], p_neon_27["cache_read"], p_neon_27["output"]), (3.0, 0.15, 15.0))

        p_agy_26, fb_agy_26 = resolve_model_pricing(
            "gemini-flash-worker-agy", self.catalog, "2026_nov_standard"
        )
        self.assertFalse(fb_agy_26)
        self.assertEqual((p_agy_26["input"], p_agy_26["cache_read"], p_agy_26["output"]), (0.75, 0.075, 3.75))

        p_agy_27, fb_agy_27 = resolve_model_pricing(
            "gemini-flash-worker-agy", self.catalog, "2027_standard"
        )
        self.assertFalse(fb_agy_27)
        self.assertEqual((p_agy_27["input"], p_agy_27["cache_read"], p_agy_27["output"]), (1.5, 0.15, 7.5))

        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "11111111-2222-3333-4444-000000000001"
            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-24T10:00:00Z",
                        "content": "/gravity-workflow",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T10:00:05Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                    },
                    {
                        "step_index": 2,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T10:00:10Z",
                        "model": "gemini-flash-worker-agy",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                    },
                    {
                        "step_index": 3,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T10:00:15Z",
                        "model": "future-unknown-model",
                        "input_tokens": 1000,
                        "cache_read_tokens": 0,
                        "output_tokens": 100,
                    },
                ],
            )
            metrics = collect_run_metrics(
                root_id,
                brain_dir=brain,
                pricing_catalog_path=CATALOG_PATH,
                bridge_db_path=Path(tmp) / "missing_bridge.db",
                store_dir=Path(tmp) / "store",
            )
            self.assertFalse(metrics["by_model"]["gemini-pro-orchestrator-neon"]["is_fallback"])
            self.assertFalse(metrics["by_model"]["gemini-flash-worker-agy"]["is_fallback"])
            self.assertTrue(metrics["by_model"]["future-unknown-model"]["is_fallback"])
            self.assertEqual(metrics["data_quality"]["unmatched_models"], ["future-unknown-model"])

            insp = render_inspect_text(metrics)
            self.assertIn("future-unknown-model", insp)
            self.assertIn("[fallback]", insp)
            self.assertNotIn("gemini-pro-orchestrator-neon     :    1 calls | $ 0.0045 ( 62.5%) [fallback]", insp)
            self.assertIn("Modèles non catalogués (tarif fallback) : future-unknown-model", insp)

    def test_error_invalid_and_shell_nonzero_separation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "22222222-3333-4444-5555-000000000002"
            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-24T11:00:00Z",
                        "content": "/gravity-workflow",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "ERROR",
                        "created_at": "2026-11-24T11:00:02Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 100,
                        "cache_read_tokens": 0,
                        "output_tokens": 10,
                        "error": "Model timeout",
                    },
                    {
                        "step_index": 2,
                        "type": "PLANNER_RESPONSE",
                        "status": "INVALID",
                        "created_at": "2026-11-24T11:00:04Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 100,
                        "cache_read_tokens": 0,
                        "output_tokens": 0,
                        "content": "Invalid tool call schema",
                    },
                    {
                        "step_index": 3,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-24T11:00:06Z",
                        "content": "Created At: 2026-11-24T11:00:05Z\nCommand exited with code 127.\n\nCommand output:\nbash: rg: command not found",
                    },
                    {
                        "step_index": 4,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-24T11:00:08Z",
                        "content": "Created At: 2026-11-24T11:00:07Z\nCommand exited with code 0.\n\nCommand output:\n+Command exited with code 1\nCommand exited with code 2 inside output",
                    },
                    {
                        "step_index": 5,
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-24T11:00:10Z",
                        "content": "Showing lines 1-10 of 20\nCommand exited with code 9",
                    },
                ],
            )
            metrics = collect_run_metrics(
                root_id,
                brain_dir=brain,
                pricing_catalog_path=CATALOG_PATH,
                bridge_db_path=Path(tmp) / "missing_bridge.db",
                store_dir=Path(tmp) / "store",
            )
            s = metrics["summary"]
            ag = metrics["agents"][0]
            self.assertEqual(s["total_error_steps"], 1)
            self.assertEqual(s["total_invalid_steps"], 1)
            self.assertEqual(s["total_shell_nonzero_steps"], 1)
            self.assertEqual(s["total_anomalies"], 3)
            self.assertEqual(ag["error_steps"], 1)
            self.assertEqual(ag["invalid_steps"], 1)
            self.assertEqual(ag["shell_nonzero_steps"], 1)
            self.assertEqual(ag["total_anomalies"], 3)

            insp = render_inspect_text(metrics)
            self.assertIn("1 erreurs", insp)
            self.assertIn("invalid=1 | shell!=0=1", insp)

            md = render_session_detail_markdown(metrics)
            self.assertIn("Invalid : 1 · Shell!=0 : 1", md)
            self.assertIn("**Anomalies (ERROR / INVALID / shell!=0)** : **3** (1 / 1 / 1)", md)

    def test_cold_cache_waste_calculation_and_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            root_id = "33333333-4444-5555-6666-000000000003"
            _write_transcript(
                brain / root_id,
                [
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-24T12:00:00Z",
                        "content": "/gravity-workflow",
                    },
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T12:00:02Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 194_000,
                        "cache_read_tokens": 0,
                        "output_tokens": 1_000,
                    },
                    {
                        "step_index": 2,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T12:00:04Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 198_000,
                        "cache_read_tokens": 17_000,
                        "output_tokens": 1_000,
                    },
                    {
                        "step_index": 3,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-24T12:00:06Z",
                        "model": "gemini-pro-orchestrator-neon",
                        "input_tokens": 2_000,
                        "cache_read_tokens": 200_000,
                        "output_tokens": 1_000,
                    },
                ],
            )
            metrics = collect_run_metrics(
                root_id,
                brain_dir=brain,
                pricing_catalog_path=CATALOG_PATH,
                bridge_db_path=Path(tmp) / "missing_bridge.db",
                store_dir=Path(tmp) / "store",
            )
            cc = metrics["efficiency_and_waste"]["cold_cache_waste"]
            self.assertEqual(cc["calls"], 2)
            self.assertEqual(cc["input_tokens"], 392_000)

            expected_cold_cost = (
                (194_000 / 1e6) * 3.0 + (1_000 / 1e6) * 15.0
                + (198_000 / 1e6) * 3.0 + (17_000 / 1e6) * 0.15 + (1_000 / 1e6) * 15.0
            )
            expected_warm_cost = (2_000 / 1e6) * 3.0 + (200_000 / 1e6) * 0.15 + (1_000 / 1e6) * 15.0
            expected_total = expected_cold_cost + expected_warm_cost
            expected_share = round((expected_cold_cost / expected_total) * 100.0, 2)

            self.assertAlmostEqual(cc["cost_usd"], round(expected_cold_cost, 6), places=6)
            self.assertEqual(cc["share_of_total_cost_pct"], expected_share)

            insp = render_inspect_text(metrics)
            self.assertIn(
                f"coldCache=2 (${round(expected_cold_cost, 6):.4f}, {expected_share:.1f}%)",
                insp,
            )
            md = render_session_detail_markdown(metrics)
            self.assertIn(
                f"**Cache froid / quasi-froid** : **2** appel(s) (392 000 in, **${round(expected_cold_cost, 6):.4f}**, **{expected_share:.1f}%** du coût)",
                md,
            )


if __name__ == "__main__":
    unittest.main()
