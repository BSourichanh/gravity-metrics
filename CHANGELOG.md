# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-11-21

### Added
- **Interactive MCP App Server & Widget (`gravity-metrics-plugin/mcp/`):**
  - 100% Python 3 stdlib stdio JSON-RPC 2.0 server (`mcp/server.py`, `mcp/widget_html.py`, `mcp_config.json`) serving `ui://gravity-metrics/dashboard.html` (`text/html;profile=mcp-app`).
  - Exposes `open_metrics_dashboard` (`aux_pane` and `global` entrypoints, visibility `["model", "app"]`) and app-only helper tools (`metrics_get_state`, `metrics_get_session`, `metrics_remove_session`).
  - Widget capabilities: active project vs global scope switch, `2026` vs `Proj. 2027` pricing toggle, interactive SVG charts and session drill-down (categories, phases, models, agents, waste signals, tools, errors), 4s visibility-aware auto-refresh, and built-in actions (`Ouvrir .md`, `Joindre au prompt`, `Analyser`, `Supprimer` with optional `reports/` purge).
- **Dedicated `/gravity-metrics` Skill (`gravity-metrics-plugin/skills/gravity-metrics/SKILL.md`):**
  - Guides the agent to immediately invoke `open_metrics_dashboard` with the hook-injected JSON arguments after `PreInvocation` capture.
- **Extended Strict Validation & End-to-End MCP Tests:**
  - `scripts/validate.py` now validates `skills/`, `mcp_config.json`, and performs a live stdio JSON-RPC MCP App dry-run (`initialize`, `tools/list`, `resources/list`, `resources/read`).
  - Added `tests/test_mcp_server.py` covering protocol handshake, version alignment, tool/resource metadata, `cp1252` resilience, project case deduplication, and deletion lifecycle (29 tests total).
- **Installer Resilience (`install.py`):**
  - Automatic recovery of empty target directories, in-place upgrade fallback (`_sync_directory_in_place` + stale entry pruning) when a running process locks the target directory on Windows (`PermissionError`), and Windows `python3` adaptation for both `hooks.json` and `mcp_config.json` on installed copies.

### Changed
- **Command Migration (`/gravity metrics` → `/gravity-metrics`):**
  - Replaced `/gravity metrics` with `/gravity-metrics [label] [-l|--label <label>] [-n|--note "note"] [--profile <name>] [--full]`, capturing before the model turn (`PreInvocation`) and automatically opening the widget in `detail` view on the captured session (or `history` view scoped to the active project when no new LLM activity occurred).
- **Collector & Classification Refinements (`metrics/collector.py`, `metrics/pricing.py`, `metrics/store.py`):**
  - Added role mappings for `planner` (`orchestration`), `builder` (`build`), and `reviewer` (`verification`), prioritizing specialized roles before generic `research`/`self` types.
  - Improved auto-label keyword extraction, case-insensitive project name harmonization across `store.py` and `mcp/server.py`, and `verification_to_build_ratio` handling (`N/A` when build/test cost is zero).
- **Encoding & Handoff Hardening (`hooks/hook_runtime.py`, `hooks/pre_invocation_metrics.py`):**
  - Enforced ASCII-safe JSON output on stdout, Windows `cp1252` console resilience across hook, MCP server, CLI, validator, and installer, handoff turn exclusion from subsequent capture windows, and a same-turn re-invocation loop guard.

### Removed
- **Static On-Disk HTML Reports:**
  - Removed `metrics/html_report.py`, generated `dashboard.html`, `reports/<slug>.html`, and `--no-html` flags in favor of the live interactive MCP App widget.

## [0.1.0] - 2026-11-21

### Added
- **Standalone Antigravity Companion Plugin (`gravity-metrics-plugin/` — 100% Python 3 stdlib):**
  - Modular metrics engine (`metrics/pricing.py`, `collector.py`, `history.py`, `store.py`, `formatters.py`, `markdown_report.py`, `html_report.py`) and pricing catalog (`config/pricing_catalog.json` with `2026_nov_standard` and `2027_standard`).
  - Zero-token `PreInvocation` hook (`hooks/pre_invocation_metrics.py`, `hooks/hook_runtime.py`), lightweight `rules/AGENTS.md`, centralized store in `~/.gemini/gravity-metrics/`, non-interactive/interactive CLI (`scripts/cli.py`), strict validator (`scripts/validate.py`), cross-platform installer (`install.py`), and unit test suites (`tests/`).
