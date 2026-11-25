# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **MCP Server & Widget Retirement into Unified Gravity Core (`plugin/config/module.json`, `plugin/hooks/pre_invocation_metrics.py`, `plugin/scripts/validate.py`, `install.py`, docs & tests):**
  - Switched `plugin/config/module.json` `ui.mode` from `"transition-mcp"` to `"none"` (`ui.target: "gravity"`).
  - Updated `plugin/hooks/pre_invocation_metrics.py` (`_build_handoff_system_message`, `_run_metrics_command`) to hand off `/gravity-metrics` commands to `open_gravity_dashboard` on server `gravity_dashboard` (`view="metrics"` with `metrics_sub` and `target` / `project` when applicable).
  - Cleaned up `plugin/scripts/validate.py` (`_validate_mcp_config`, `_run_mcp_dry_run`, `"mcp"`/`"widget_html"` checks removed; hook dry-run updated for `open_gravity_dashboard`) and `install.py` (`mcp/server.py` removed from `EXEC_SCRIPTS`).
  - Synchronized `plugin/rules/AGENTS.md` (`<= 600` B), `plugin/skills/gravity-metrics/SKILL.md` (`<= 6000` B), `AGENTS.md` (`<= 2500` B), `plugin/README.md`, `README.md`, `plugin/plugin.json`, and unit tests.

### Removed
- **Standalone MCP Server & UI (`plugin/mcp_config.json`, `plugin/mcp/`, `tests/test_mcp_server.py`):**
  - Deleted `plugin/mcp_config.json`, the entire `plugin/mcp/` package (`server.py`, `widget_html.py`, `ui/*`), and `tests/test_mcp_server.py` now that Gravity Core hosts the unified dashboard.

### Added
- **Canonical Workspace Resolution, Technical Signals & Full `--json` CLI (`plugin/metrics/workspace_resolver.py`, `plugin/metrics/technical_signals.py`, `plugin/metrics/collector.py`, `plugin/metrics/history.py`, `plugin/scripts/cli.py`, `tests/test_technical_and_workspace.py`):**
  - Added `workspace_resolver.py` (`resolve_agent_workspace`, `normalize_worktree_path`, `classify_workspace_kind`) with 5-tier priority resolution (`bridge_workspace` > write/shell `cd`/`git -C` > read outside `root_workspace` `specs/`/`tasks/` > non-inherit `workspaceUris` > `root_workspace`), worktree normalization (`gravity-worktrees/<repo>-wN`, `.gemini/antigravity/worktrees/<repo>-*`, `.worktrees/<repo>-*`), and `"atelier"` vs `"code"` classification.
  - Added `technical_signals.py` (`extract_technical_signals`, `aggregate_technical_signals`, `TechnicalSignalAccumulator`) computing `thinking_{calls,chars,est_tokens}`, `compaction_count` (`ctx < 0.55 * prev_ctx` with `prev_ctx > 40k` or `/compact`), and bounded step latencies (`latency_{avg,p50,p95}_ms`), exposed in `reports/<slug>.json` and `history.jsonl` (`thinking_est_tokens`, `compaction_count`, `latency_p95_ms`, `workspace_kinds`) while preserving all legacy fields and shrinking `collector.py` line count.
  - Added `--json` support across all `cli.py` subcommands (`start`, `add`, `record`/`archive`, `pending`, `list`, `inspect`, `remove`) plus new `update-meta`, `ignore`, and `unignore` subcommands.

### Fixed
- **`/swarm` Clean Termination, Reviewer Classification & POSIX Worktree Filtering (`ACT-05-02` in `plugin/metrics/collector.py`, `plugin/metrics/pricing.py`, `plugin/hooks/session_start_metrics.py`):**
  - Added `_has_clean_terminal_tool_call()` (`finish`, `post_message`, `swarm_wait`) and `trailing_after_planner` tracking in `parse_agent_transcript()` (plus `subagent_state` propagation from `collect_run_metrics()`) so `/swarm` workers finishing via `post_message` + `swarm_wait` with consecutive clean `GENERIC(DONE)` steps are no longer marked `INTERRUPTED` even when `subagent_state == "SUBAGENT_STATE_KILLED"`.
  - Prioritized `reviewer|challenger|auditor` ahead of `worker|builder` in `classify_agent()` so `Swarm Worker (*-reviewer)` classifies as `verification` instead of `build`.
  - Extended `_is_subagent_worktree()` to ignore POSIX `/gravity-worktrees/` and `/worktrees/` paths in `SessionStart`.

### Added
- **Session Freshness & In-Place MCP/UI Updates (`R5` in `plugin/mcp/server.py`, `plugin/mcp/ui/app_js.py`):**
  - Extended `_compute_pending_source_sig`, `_compute_hist_source_sig`, and `_fast_unchanged_signature` to include `subagents/*.json`, `tasks/task-*.log`, and recorded transcript `stat()` tokens so polling invalidates immediately on child or post-record activity.
  - Added pure in-memory `_compute_recorded_freshness(rec, brain_dir)` (stat fast-path vs `archived_at`/`ended_at`, then RAM-cached `_get_transcript_index`, counting post-`ended_at` / `root_last_step_index` `PLANNER_RESPONSE` calls outside `/gravity-metrics` plus post-`archived_at` `subagents/*.json` / `tasks/task-*.log` files) and enriched `records` copies in `_load_state_snapshot` and `metrics_get_session` (`freshness`) without mutating `history.jsonl`.
  - Added `update_sealed` / `update` boolean parameters to `metrics_archive_session`, registered canonical alias `metrics_record_session` in `_TOOL_DISPATCH` and `_tool_definitions`, and added the **« Mettre à jour »** action button (`updateRecordedSession`) plus discrete `+N appels` / `MAJ` indicators in the widget detail and `Enregistrées` list.
- **Native 4-Phase Detection & `record` Lifecycle (`R1–R4` in `plugin/metrics/`, `plugin/hooks/`, `plugin/scripts/cli.py`):**
  - Added `current_turn_start_step_in_transcript()` (`lifecycle.py`) for `/gravity-architect` auto-capture anchoring, `±2` step deduplication (`find_archived_session`, `upsert_session`, `resolve_sealed_update_target`), `/swarm` + `subagents/*.json` + direct non-studio edit detection for `Production`, and double-quoted `CommandLine` unescaping + `audit_ecosystem.py` / `ecosystem_install.py` / `git ... push` detection for `Vérification & Push`.
  - Added `cli.py record` (with `archive` alias), `--update` (with `--update-sealed` alias), `record_conversation` export (`metrics/__init__.py`), and `Clôture / Enregistrée` milestone wording.

### Changed
- **UI, Manifest & Docs Terminology (`Enregistrer`):**
  - Switched widget labels (`app_js.py`, `charts.py`), `plugin/plugin.json` `suggestedPrompts[1]`, `plugin/rules/AGENTS.md` (`<= 600` B), `plugin/skills/gravity-metrics/SKILL.md`, `plugin/README.md`, and `README.md` from `Archiver` / `Archivées` / `À archiver` to `Enregistrer` / `Enregistrées` / `À enregistrer` while preserving `status: "archived"` on disk for ecosystem compatibility.

### Fixed
- **False Positives `AUD-04-09` & Native Clean Termination (`plugin/metrics/collector.py`, `plugin/metrics/store.py`, `plugin/scripts/cli.py`, `plugin/hooks/pre_invocation_metrics.py`):**
  - `AUD-04-09a`: added `--self-archiving` / `self_archiving` propagation across `cli.py`, `pre_invocation_metrics.py`, `store.py`, and `parse_agent_transcript()` so self-archival from the active root session (`cli.py archive` / `add`) no longer flags `clean_termination=False` (`Interrompu`).
  - `AUD-04-09b`: excluded legitimate remediation `Reviewer` second passes (`source == "workflow"`, `type_name == "reviewer"`) preceded by a `Reviewer` `FAIL` verdict/blocker/major from `duplicate_role_spawns` in `collect_run_metrics()`.

### Changed
- **Live Gravity Bridge Retirement & `#01–#04` Read-Only Compatibility (`plugin/metrics/`, `plugin/mcp/`, `plugin/hooks/`, `plugin/scripts/`, `README.md`, `plugin/README.md`, `plugin/skills/gravity-metrics/SKILL.md`):**
  - Retired all live SQLite `bridge.db` reads across `collector.py`, `audits_reader.py` (`_load_bridge_workspace_dirs` removed), `store.py`, `cli.py`, `pre_invocation_metrics.py`, `mcp/server.py`, and `mcp/ui/app_js.py`.
  - Removed `PendingBridgeJobsError` and unused `bridge_db_path` parameters from `store.py`, removed `plugin/metrics/bridge_links.py` and its live exports from `plugin/metrics/__init__.py`, and removed the `confirmForceArchiveCid` modal and live `has_pending_bridge_jobs` / `bridge_pending_jobs_count` alerts from `app_js.py`.
  - Preserved read-only rendering (`bridge:N` badges, `bridge_sessions`, workspace share, and Bridge tree panels) for historical archived reports (`#01–#04`) across `history.py`, `markdown_report.py`, `mcp/server.py`, and `app_js.py`, and aligned MCP, closure, workflow, lifecycle, and `#01–#04` compatibility tests (`tests/test_mcp_server.py`, `tests/test_bridge_v030.py`, `tests/test_closure_and_pending_hygiene.py`, `tests/test_workflow_and_pending_v042.py`, `tests/test_lifecycle_and_ux_v050.py`).

### Added
- **Validator Integrity Hardening (`INT-01`, `INT-03`, `INT-04`, `INT-06` in `plugin/scripts/validate.py`):**
  - `INT-01`: enforced `ALLOWED_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp", "mcp-app"})` on `config/module.json` `ui.mode`.
  - `INT-03`: promoted `suggestedPrompts` checks in `plugin/plugin.json` to blocking errors (exactly 3 non-empty strings, `<= 100` chars each, `/cmd — résumé` format).
  - `INT-04`: enforced UTF-8 without BOM and LF line endings across all `.py`, `.md`, `.json` files under the plugin root (and in `_read_json_no_bom`) without duplicating D4 Markdown errors.
  - `INT-06`: added OS personal home-directory path (Windows, macOS, and Linux user-profile prefixes, excluding `~`) and `file:` URI leak detection across `.py`, `.md`, `.json`, `.html`, `.svg` (excluding `tests/`).

### Changed
- **Docs & Manifests Alignment (`DOC-02`, `DOC-09`):**
  - Updated `plugin/plugin.json` `suggestedPrompts[0]` (strict read-only phrasing) and `suggestedPrompts[2]` (`/gravity-metrics start — ...` format, `<= 100` chars).
  - Removed forward-looking phrasing from `plugin/config/module.json`, `README.md`, and `plugin/README.md`, aligned `v0.5.0` references to `v0.5.1` in `README.md`, and documented `SessionStart` (`session_start_metrics.py`, `session_meta.json`, excluding worktrees), auto-start `/gravity-architect "<instruction>"`, and `cli.py start` in `AGENTS.md`, `plugin/skills/gravity-metrics/SKILL.md`, and `plugin/rules/AGENTS.md` (within byte budgets).

### Fixed
- **Bridge Multi-Wave Duplicate Exclusion & Milestone Accuracy (`AUD-03-01`, `AUD-03-05` in `plugin/metrics/collector.py`):**
  - `AUD-03-01`: excluded `source == "bridge"` children from `dup_groups_map` so multi-repo/multi-wave Bridge dispatches do not trigger `duplicate_role_spawns` false positives while preserving native subagent duplicate detection.
  - `AUD-03-05`: computed `first_prod_step = min(link_steps + child_steps)` and `last_prod_step = max(link_steps + child_steps)` in `_compute_session_milestones()` and `_compute_pending_milestones()`, gating `verif_step` / `has_verif` on `not bridge_pending_jobs` (resp. `not pending_jobs`) and `st_i > last_prod_step` while preserving 4-phase and 2-phase fallback JSON compatibility.

### Added (Earlier Unreleased)
- **Auto-Capture `/gravity-architect` & CLI `start` (`plugin/hooks/pre_invocation_metrics.py`, `plugin/metrics/lifecycle.py`, `plugin/scripts/cli.py`):**
  - Added idempotent `overwrite: bool = False` (default) on `start_active_capture()` and extracted `next_step_index_in_transcript()` in `lifecycle.py` (re-exported from `metrics/__init__.py`).
  - Auto-started an idempotent zero-token `architect-<slug>` capture (`invocationNum == 0`, `overwrite=False`) in `pre_invocation_metrics.py` on `/gravity-architect <instruction>` (excluding `init|add|sync|status|archive|audit` and preserving passthrough `{}`), while explicit `/gravity-metrics start` passes `overwrite=True`.
  - Added `cli.py start <cid> [-l LABEL] [--step N] [--workspace PATH] [--brain-dir PATH] [--store PATH] [--history PATH]` (`overwrite=True`).
- **End-of-Cycle `--update-sealed` Flag (`plugin/metrics/store.py`, `plugin/scripts/cli.py`):**
  - Added `update_sealed: bool = False` to `record_session_metrics()` and `archive_conversation()` (default `False` preserves strict immutability) and exposed `--update-sealed` on `cli.py archive` and `cli.py add` to refresh an already-sealed window (e.g., after a final `git push`) while still enforcing `PendingBridgeJobsError` unless `--force` is passed.
- **Anti-Noise Pending Filter & `--all` CLI Flag (`plugin/metrics/collector.py`, `plugin/scripts/cli.py`):**
  - Tracked `has_work_activity` on `PLANNER_RESPONSE` tool calls (`write_file`, `write_to_file`, `edit_file`, `replace_file_content`, `multi_replace_file_content`, `run_workflow`, `invoke_subagent`, `swarm`) in `_get_transcript_index()` and added `include_noise: bool = False` to `list_pending_sessions()` (default hides consultative sessions lacking links, children, active captures, and work tools; exposed via `cli.py pending --all`).
- **Neon/Flash Pricing Aliases (`plugin/config/pricing_catalog.json`):**
  - Added `gemini-pro-orchestrator-neon*` (`$3.00 / $0.15 / $15.00`) and `gemini-flash-worker-agy*` (`$0.75 / $0.075 / $3.75` in `2026_nov_standard`, `$1.50 / $0.15 / $7.50` in `2027_standard`) while preserving `_fallback` (`is_fallback: true`) for unknown models.
- **Detailed Anomaly Counters & Cold-Cache Telemetry (`plugin/metrics/collector.py`, `tests/test_neon_errors_cold_cache.py`):**
  - Preserved `error_steps` / `total_error_steps` (`status == "ERROR"`) and `error_rate_pct`, and added `invalid_steps` (`status == "INVALID"`), `shell_nonzero_steps` (`GENERIC` steps with line-anchored `Command exited with code <N>` where `N != 0`, ignoring `Command output:`/`Showing lines ` bodies), and `total_anomalies` per agent, bucket, and `summary`.
  - Added `efficiency_and_waste["cold_cache_waste"]` (`calls`, `input_tokens`, `cost_usd`, `share_of_total_cost_pct`) classifying cold/near-cold `PLANNER_RESPONSE` calls (`(cache == 0 and in > 10k)` or `(in > 50k and cache / (in + cache) < 0.25)`).
- **MCP Apps Host Capability Check (`plugin/mcp/server.py`, `tests/test_mcp_server.py`):**
  - Inspected `params.capabilities` and `params.clientInfo` during `initialize` (`MIN_ANTIGRAVITY_MCP_APP_VERSION = (2, 34, 0)`) and appended a non-fatal `[WARN UI]` notice to `open_metrics_dashboard` text output only when the client is identified without MCP Apps support (`isError: false`, zero false positives on minimal/uninitialized calls).

### Changed
- **NFKD Slugification & Chained-Command Stripping (`plugin/metrics/collector.py`):**
  - Normalized accents via `unicodedata.normalize("NFKD", ...)` in `slugify_label()`.
  - Extended `_GRAVITY_LEADING_AXES_RE` (namespace guard `(?::[a-z0-9_-]+)?(?=\s|$)`, `/boost`, `/grill-me`, `/plan`, `/caveman`, `quick`/`standard`/`deep`, single subcommand slot preserving `/gravity-bridge send audit bridge`) with fixed-point `_strip_leading_gravity_commands()` across `_extract_label_keywords()` and `_clean_pending_title()`.
  - Expanded `suggest_auto_label(..., max_len=64)` to 5 keywords bounded cleanly via `slugify_label()`.
- **End-of-Cycle Archival & Worktree Exclusion (`plugin/hooks/pre_invocation_metrics.py`, `plugin/hooks/session_start_metrics.py`, `plugin/rules/AGENTS.md`, `plugin/skills/gravity-metrics/SKILL.md`, `README.md`, `plugin/README.md`):**
  - Removed premature auto-archiving on `/gravity-architect archive <slug>` from `PreInvocation` (`_ARCHITECT_ARCHIVE_CMD_RE`, `_handle_architect_archive_passthrough()`), retaining `parse_architect_archive_prompt()` / `is_architect_archive` in `collector.py` solely for the passive `Clôture / Archivé` milestone.
  - Updated `session_start_metrics.py` to skip subagent worktree paths containing `/.gemini/antigravity/worktrees/` or `\worktrees\` without writing to `session_meta.json`.
- **CLI Inspect & Session Detail Reports (`plugin/metrics/markdown_report.py`, `README.md`, `plugin/README.md`):**
  - Updated `render_inspect_text()` to display `invalid=<N> | shell!=0=<M>` when present, `coldCache=<N> ($<usd>, <pct>%)` in `Alertes`, `[fallback]` suffixes in `--- Par modèle ---`, and a warning line when `unmatched_models` is non-empty.
  - Updated `render_session_detail_markdown()` to surface `ERROR / INVALID / shell!=0` counts and `cold_cache_waste` in `## 6. Diagnostic & Gaspillages` with full backward compatibility on legacy payloads.

### Fixed
- **Validator Brain Isolation & Markdown Escaping Test (`plugin/scripts/validate.py`, `tests/test_lifecycle_and_ux_v050.py`):**
  - Isolated `ANTIGRAVITY_BRAIN_DIR` to a temporary directory in `_run_hook_dry_runs()` and `_run_mcp_dry_run()` to prevent scanning the user's brain directory.
  - Aligned `test_markdown_view_no_loop_on_gt_entities` with `esc(val)` from `app_js.py` and asserted `!loopCheck.includes('&amp;gt;')`.
- **Dashboard UI Access Labels (`install.py`, `plugin/metrics/markdown_report.py`, `README.md`):**
  - Replaced obsolete `Plugins` menu UI access labels for Gravity Metrics with the current Antigravity UI entry points (left sidebar `Gravity Metrics` or `+` menu in the right side pane).

## [0.5.1] - 2026-11-24

### Added
- **Installer `--strict` Flag (`install.py`, `tests/test_install.py`):**
  - Added `--strict` (`11/11` CLI contract) overriding `--skip-validate`, running read-only `validate.py --strict` under `--check`, and passing `--strict` to `agy plugin validate`.

### Changed
- **Docs UX Alignment (`plugin/plugin.json`, `README.md`, `plugin/README.md`):**
  - Harmonized `suggestedPrompts` in `plugin/plugin.json` to the common `/cmd — summary` convention (`<= 100` chars).
  - Added a concise **Quick Start (2 min) & Ecosystem Fit** / **Démarrage 2 min & Positionnement Écosystème** block (`Tier 2 — Steering` / `Palier 2 — Pilotage`) in `README.md` and `plugin/README.md` with direct links to `gravity/docs/`.

## [0.5.0] - 2026-11-23

### Added
- **D4 Validator Enforcement & Isolated Tests (`plugin/scripts/validate.py`, `tests/test_validate.py`):**
  - Enforced `plugin/rules/AGENTS.md` limits (`> 1 000 B` error, `601..1 000 B` `--strict` warning), `SKILL.md <= 6 000 B`, and root `AGENTS.md <= 2 500 B` with UTF-8 no-BOM, LF, and YAML frontmatter checks, covered by `tests/test_validate.py`.
- **Hybrid Capture Lifecycle & `SessionStart` Hook (`plugin/hooks/session_start_metrics.py`, `pre_invocation_metrics.py`, `plugin/metrics/lifecycle.py`, `hooks.json`):**
  - Added lightweight `SessionStart` hook recording workspace/project provenance into centralized `$GRAVITY_METRICS_HOME/session_meta.json` (fallback via `bridge.db` RO `from_workspace` and `canonical_path` matching).
  - Added zero-token `/gravity-metrics start [label]` (`active_captures.json`) and `/gravity-metrics stop [label]` / `finish` commands, plus immediate auto-archival on `/gravity-architect archive <slug>` (preserving `PendingBridgeJobsError`).
  - Added 4 automatic zero-token cycle milestones (`Conception` → `Production` → `Vérification & Push` → `Clôture / Archivé`, with 2-phase fallback in single-repo sessions) and per-phase token/cost attribution.
  - Added reversible ignore list (`ignored.json`) and post-archival metadata editing (`metrics_ignore_session`, `metrics_unignore_session`, `metrics_update_session_meta`), plus `project` parameter on `metrics_archive_session`.
- **Sober 3-Space MCP App UI (`plugin/mcp/widget_html.py`, `plugin/mcp/ui/`):**
  - Replaced the 4-tab layout with 3 spaces (`Synthèse`, `Sessions` [`Archivées`, `À archiver`, `Comparer`], `Audits Agents`) separated by a visual divider and a dedicated **`Index & Matrice`** button for `README.md`.
  - Adaptive theme inheriting `hostContext.styles.variables` (light/dark/Nord) with neutral graphite/zinc fallbacks, `tabular-nums`, single contextual `<select>` Project, conditional `2026 / 2027` toggle, inline `Archiver…` drawer, inline session metadata editor, and `74ch` audit reader.

### Fixed
- **P0 Audits, Deduplication & Performance (`plugin/metrics/audits_reader.py`, `history.py`, `collector.py`, `server.py`, `markdown_view.py`):**
  - Excluded `_TEMPLATE.md` and `_*` files from `Audits Agents`; moved `README.md` (`is_index`) out of the audits list, badge counter, and `audit_links_by_session` into `index_audit`.
  - Fixed verdict extraction (`Verdict(?:\s+global)?` with `**` before/after `:`, canonical token extraction before `—`/`-`/`|`, and `PARTIAL`/`WARN`/`OK`/`KO` tone classification).
  - Fixed `--full` (`window_start_step is None`) deduplication across `find_archived_session()` and `list_pending_sessions()`, and fixed `resolve_gravity_window(current_prompt_is_metrics=False)` to surface new post-capture activity in `À archiver`.
  - Added `(mtime_ns, size)` incremental caching in `collector.py` and `bridge_links.py`, plus separate source-signature pre-checks (`hist_sig`, `pending_sig`, `audits_sig`) in `server.py` for `< 5 ms` unchanged polls.
  - Fixed infinite loop on `&gt;` in `markdown_view.py` and added `h1..h6`, GitHub-style callouts (`[!NOTE]`, `[!WARNING]`, `[!TIP]`, `[!CAUTION]`), nested lists, and link distinction.
  - Updated Workflow profile lookup to `$GRAVITY_HOME/workflow/workspaces/<hash>/profile.json` > `.agents/gravity-workflow/profile.json` > `.agents/gravity/profile.json`.
  - Fixed `/gravity-metrics pending` to open `view="pending"` (with `--project` support) and prevented incremental JS polling from wiping `detailCache`, scroll, focus, or `A vs B` state.

### Changed
- **D4 Rules & Charter Compaction (`plugin/rules/AGENTS.md`, `AGENTS.md`, `README.md`, `plugin/README.md`):**
  - Compacted `plugin/rules/AGENTS.md` (`1 115 B` → `518 B`) and root `AGENTS.md` (`2 203 B` → `1 648 B`) without losing any `v0.5.0` invariant, and updated D4 docs.
- **Read-Only Bare `/gravity-metrics` & Version Bump (`plugin/hooks/pre_invocation_metrics.py`, `plugin/plugin.json`, `plugin/mcp/server.py`, `plugin/mcp/ui/app_js.py`, `README.md`, `plugin/README.md`, `CHANGELOG.md`):**
  - Bare `/gravity-metrics` now opens the dashboard in read-only mode without mutating `history.jsonl`.
  - Bumped manifest, MCP server, UI `appInfo`, and documentation to `0.5.0`.

## [0.4.3] - 2026-11-22

### Fixed
- **`run_workflow` Discovery Precision (`plugin/metrics/collector.py`):**
  - Tightened Source 2 (`.system_generated/messages/*.json`, excluding `read.json`) in `discover_workflow_children()` to strictly require `toolCall.name == "run_workflow"` alongside `"[agent:"` in content, rejecting `run_command`, `other_tool`, or missing tool metadata.
  - Replaced blind `GENERIC`/`SYSTEM_MESSAGE` scanning in Source 3 (`transcript.jsonl` fallback) with ordered turn-state tracking (`active_wf_turn`, `active_wf_has_other_tools`, `has_any_wf_call`): resets on `USER_INPUT`, activates on `PLANNER_RESPONSE` containing `run_workflow`, skips obvious shell/read outputs (`Command output:`, `Command exited `, `Showing lines `) on parallel tool turns unless `"Workflow "` or `"\nLogs:\n"` is present, and requires either a prior `run_workflow` call or a parent task/sender marker (`task id "<parent>/task-"` or `sender=<parent>/task-`) for `SYSTEM_MESSAGE`.
- **Clean Termination via `finish` (`plugin/metrics/collector.py`):**
  - Added `_has_finish_tool_call()` and tracked `prev_non_sys` in `parse_agent_transcript()` so agents ending on `PLANNER_RESPONSE(status="DONE", tool_calls=[..., {"name": "finish"}, ...])` or followed by `GENERIC(status="DONE")` report `clean_termination=True`, while keeping `GENERIC` `RUNNING`/`ERROR`, non-`finish` tool outputs, and missing transcripts as `clean_termination=False`.

### Added
- **Regression & Precision Tests (`tests/test_workflow_precision_v043.py`, `tests/test_workflow_and_pending_v042.py`):**
  - Added `tests/test_workflow_precision_v043.py` covering non-workflow log inspection isolation, parallel `run_workflow` + `run_command` filtering, `USER_INPUT` turn reset, async `SYSTEM_MESSAGE` parent-marker filtering, `finish` termination edge cases, and mixed-tree `interrupted_agents.count == 0`.
  - Updated permissive `v0.4.2` fixtures in `tests/test_workflow_and_pending_v042.py` to include `run_workflow` tool calls and added `msg-ignored-cmd.json` (`run_command`) rejection coverage.

### Changed
- **Version Bump (`plugin/plugin.json`, `plugin/mcp/server.py`, `plugin/mcp/ui/app_js.py`, `tests/test_mcp_server.py`, `README.md`, `CHANGELOG.md`):**
  - Bumped manifest, MCP server, UI `appInfo`, test expectations, and documentation to `0.4.3` (without altering `SCHEMA_VERSION` `2.1.0` or UI rendering).

## [0.4.2] - 2026-11-22

### Fixed
- **Bridge v0.2.0 Callback Handling (`plugin/metrics/bridge_links.py`):**
  - Added `_is_callback_job()` (`direction == "callback"` or `action == "send_message"`) and dynamically excluded callback jobs in `load_bridge_target_conversation_ids()` (preserving compatibility with v0.1.0 schemas lacking `direction`), preventing root conversations from being misclassified as Bridge children.
  - Added `load_bridge_caller_conversation_ids()` (`?mode=ro`) to identify root caller conversations from `threads.caller_conversation_id`.
  - Separated outbound jobs from callback jobs in `_query_bridge_db_for_threads()` and `discover_bridge_links()`: only non-callback outbound jobs can add threads via `jobs.caller_conversation_id` or provide `to_workspace`, `target_conversation_id`, and `created_at` fallbacks, while active callback jobs (`queued`, `claimed`, `dispatched`) still flag `has_active_job=True` and expose their `job_id`, `job_status`, and `job_action` when outbound is completed or absent.

### Added
- **`run_workflow` Child Discovery & Public Exports (`plugin/metrics/collector.py`, `markdown_report.py`, `__init__.py`):**
  - Added `discover_workflow_children()` (read-only across `.system_generated/tasks/task-*.log`, `.system_generated/messages/*.json` excluding `read.json`, and synchronous `transcript.jsonl` `GENERIC`/`SYSTEM_MESSAGE` fallback) with UUID deduplication preserving the first valid `spawn_step_index` and strict `parent_cid` exclusion.
  - Enqueued `run_workflow` children (`source="workflow"`, inheriting `bridge_workspace`) in `discover_agent_tree()` BFS, updated `_detect_execution_mode()` to recognize `source == "workflow"`, and added `[workflow]` / `[workflow · d.<depth>]` tags in `render_inspect_text()` and `render_session_detail_markdown()`.
  - Exported `load_bridge_caller_conversation_ids`, `discover_workflow_children`, and `load_known_child_conversation_ids` in `plugin/metrics/__init__.py`.

### Changed
- **Strict Root Session Filtering & Version Bump (`plugin/metrics/collector.py`, `plugin/plugin.json`, `plugin/mcp/server.py`, `plugin/mcp/ui/app_js.py`, `tests/test_mcp_server.py`, `README.md`, `plugin/README.md`):**
  - Added `load_known_child_conversation_ids()` (aggregating Bridge targets, `subagents/*.json` excluding `typeName`/`type_name == "root"` and self-references, and `run_workflow` children) and removed `_has_gravity_workspace_uri()` and loose `"gravity"` substring heuristics from `list_gravity_conversations()` and `list_pending_sessions()`.
  - Root sessions are now strictly identified by explicit `/gravity*` slash commands, `runs.jsonl` entries, or `bridge.db` `caller_conversation_id` while excluding all known child conversations.
  - Bumped plugin manifest, MCP server, UI appInfo, documentation, and test expectations to `0.4.2`.

## [0.4.1] - 2026-11-22

### Changed
- **Strict `audits/agents/` Scoping & `audits/code/` Exclusion (`plugin/metrics/audits_reader.py`):**
  - Updated `discover_audit_roots()` so `workspace_dirs`, `include_cwd=True`, and `bridge.db` (`?mode=ro`) target `<workspace>/audits/agents` instead of `<workspace>/audits`.
  - Automatically redirects explicit `audits_dir` / `$GRAVITY_AUDITS_DIR` pointing to a parent folder containing `agents/` (e.g., `<ws>/audits`) toward `<dir>/agents`, while ignoring any `audits/code` directory and preserving workspace name extraction (`root["workspace"]` / `root_id`) on both absolute and relative paths (`.`, `audits`, `audits/agents`).
  - Added defensive `_is_code_audit_path()` checks in `list_audits()` and `_resolve_audit_target()` to reject or exclude any file under a `code/` or `audits/code/` directory segment without blocking legitimate files named `code.md` inside `agents/`.
- **`Audits Agents` Tab & MCP Descriptions (`plugin/mcp/ui/app_js.py`, `plugin/mcp/server.py`, `plugin/plugin.json`, `README.md`, `plugin/README.md`):**
  - Renamed the displayed UI tab label from `"Audits"` to `"Audits Agents"` and updated search placeholder, mobile button, quick selector, and empty state (`audits/agents/`), while preserving `view="audits"` and tool names `metrics_list_audits` / `metrics_read_audit`.
  - Updated `metrics_list_audits` and `metrics_read_audit` MCP titles/descriptions to explicitly reference `audits/agents/` (`Audits Agents`), and bumped manifest, MCP server, and widget versions to `0.4.1`.

## [0.4.0] - 2026-11-22

### Added
- **Read-Only Audits Reader (`plugin/metrics/audits_reader.py`, `plugin/metrics/__init__.py`):**
  - Generic read-only discovery (`$GRAVITY_AUDITS_DIR` > `<workspace>/audits/` > `<canonical_path>/audits/` from `bridge.db` opened strictly with `?mode=ro`).
  - Strict anti-path-traversal enforcement (`target.resolve().is_relative_to(root.resolve())`), `.md`-only filtering, `262 144`-byte (`256 Ko`) read cap with `truncated` flag, and metadata extraction (`title`, `verdict`, `verdict_tone`, `is_index` for `README.md`, `linked_sessions` matched by UUID/prefix against `history.jsonl`).
- **4-Tab Interactive MCP App UI & Modular Package (`plugin/mcp/ui/`, `plugin/mcp/widget_html.py`, `plugin/mcp/server.py`):**
  - Modularized the monolithic HTML/CSS/JS widget into `plugin/mcp/ui/{__init__.py,styles.py,charts.py,markdown_view.py,app_js.py}` assembled dynamically by `render_mcp_widget_html()` with zero static `.html` files on disk.
  - Added 4-tab App Shell (`Vue d'ensemble`, `Rapports`, `À archiver`, `Audits`) with host theme synchronization (`hostContext` + `ui/notifications/host-context-changed`) and capability-aware action hiding in `global` mode (`hostCaps.message`, `hostCaps.updateModelContext`, `aux_pane`).
  - Added `by_project` `%` cost aggregation in `Vue d'ensemble`, enhanced `by_workspace` `%` distribution (with clean `2.0.0` fallback) in `Rapports → Synthèse & Dossiers (%)`, Master-Detail layout (`global`), sticky `← Préc. | Sélecteur rapide | Suiv. →` + `Alt+←`/`Alt+→` navigation (`aux_pane`), detail sub-tabs, and **`A vs B`** comparator.
  - Added `metrics_list_audits` and `metrics_read_audit` (`visibility: ["app"]`) MCP tools, built-in anti-XSS Markdown renderer (`markdown_view.py`), pinned `README.md` index, and bidirectional Audit ↔ Report cross-links (`audit_links_by_session`).

### Changed
- **MCP Dashboard Entrypoint & Versioning (`plugin/mcp/server.py`, `plugin/plugin.json`, `README.md`, `plugin/README.md`):**
  - Extended `open_metrics_dashboard.view` enum to `["overview", "reports", "pending", "audits", "history", "detail"]` while preserving `"history"` and `"detail"` backward compatibility under `MAX_TRAJECTORY_BYTES`.
  - Bumped manifest and MCP App server/widget versions to `0.4.0`.

## [0.3.0] - 2026-11-22

### Added
- **Unified Gravity Session & Bridge Tree (`plugin/metrics/bridge_links.py`, `collector.py`, `pricing.py`):**
  - Added strict read-only `bridge.db` resolver (`--bridge-db` > `$GRAVITY_BRIDGE_HOME/bridge.db` > `$GRAVITY_HOME/bridge/bridge.db` > `~/.gemini/gravity/bridge/bridge.db`) using SQLite `?mode=ro` URI, tolerant of missing databases and `v0.1.0` schemas without `caller_conversation_id`.
  - Dual discovery combining `bridge.db` (`caller_conversation_id` on `v0.2.0+`) and parent transcript MCP parsing (`gravity-bridge_bridge` `send_message`/`dispatch_thread` on `v0.1.0`), deduplicated by `thread_id` and enqueued into `discover_agent_tree()` BFS (`source="bridge"`, `type_name="bridge_<msg_type>"`, `role="bridge:<to_workspace>"`, `depth 2+` native subagent recursion).
  - Added `by_workspace` aggregation (with inherited `bridge_workspace` across `depth 2+` subtrees), `bridge` execution mode/feature priority (`teamwork > swarm > workflow > bridge > subagents > single`), `TYPE_TO_CATEGORY` mappings (`bridge_handoff`, `bridge_question`, `bridge_info`, `bridge`), and `by_phase` subtree aggregation for `bridge:<workspace>`.
  - Added `is_bridge_child_conversation()` and `list_pending_sessions()` excluding Bridge child conversations (`[GRAVITY-BRIDGE |` or `target_conversation_id` in `bridge.db`) by default unless `--all` is passed.
- **Immutable Archiving & Bridge Job Safety (`plugin/metrics/history.py`, `store.py`, `cli.py`):**
  - Bumped history schema to `2.1.0` (backward-compatible with `2.0.0`, defaulting missing `status` to `"archived"`) with `status`, `archived_at`, `bridge_children_count`, `bridge_workspaces`, and `workspace_share_pct`.
  - Enforced immutability on `(conversation_id, window_start_step)` (`already_archived=True` unless `--force`) and introduced `PendingBridgeJobsError` blocking archival when Bridge child jobs are `queued`, `claimed`, or `dispatched` unless `--force` is passed.
  - Added `cli.py archive` (supporting UUID or unique prefix), `cli.py pending` (zero-write), and `--force` / `--bridge-db` flags.
- **MCP App Pending & Archiving Support (`plugin/mcp/server.py`, `plugin/mcp/widget_html.py`):**
  - Exposed `pending_sessions` in `open_metrics_dashboard` and `metrics_get_state`, added app-only `metrics_archive_session` tool with Bridge job-safety guards, and added UI panels for pending sessions, `by_workspace`, and Bridge inter-workspace tree.

### Changed
- **4 Gravity Session Entrypoints (`plugin/metrics/collector.py`):**
  - Extended `_GRAVITY_CMD_RE`, `_GRAVITY_LEADING_AXES_RE`, and `_extract_slash_commands()` to `/gravity`, `/gravity-workflow`, `/gravity-architect`, and `/gravity-bridge` (excluding `/gravity-metrics`).
- **Strict `PreInvocation` Hook & Slug Bounds (`plugin/hooks/pre_invocation_metrics.py`, `collector.py`):**
  - Bounded `slugify_label()` to `<= 64` characters with deterministic `-{sha256[:8]}` suffix on truncation.
  - Added passthrough for combined slash commands (`/gravity-metrics /other ...`) and long free text (`> 60` chars or `> 6` words without `-l`/`--label`), plus `/gravity-metrics archive <uuid>` and `/gravity-metrics pending` subcommands.
- **Reports, Manifest & Docs (`plugin/metrics/markdown_report.py`, `formatters.py`, `plugin/plugin.json`, `plugin/rules/AGENTS.md`, `plugin/skills/gravity-metrics/SKILL.md`, `README.md`, `plugin/README.md`):**
  - Updated Markdown/terminal/chat views with `archivé` status, `bridge` badges, `by_workspace`, Bridge tree, and `pending` tables, and bumped manifest/MCP App versions to `0.3.0`.

## [0.2.0] - 2026-11-22

### Added
- **Module Contract v1 (`plugin/config/module.json`):**
  - Declared `id: "metrics"`, `plugin_name: "gravity-metrics"`, unified storage metadata (`subdir: "metrics"`, `env_override: "GRAVITY_METRICS_HOME"`), command `/gravity-metrics`, capabilities (`session-telemetry`, `cost-analytics`, `workflow-signals`), UI transition mode (`transition-mcp` -> `gravity`), and signal declarations (`produces: ["session-metrics", "cost-telemetry"]`).
- **Contract & Precedence Validation (`plugin/scripts/validate.py`, `tests/`):**
  - Extended `validate.py` to strictly validate `plugin/config/module.json` v1, all 9 required official `plugin.json` fields, and absence of UTF-8 BOM.
  - Added tests covering `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics` storage precedence, `/gravity-workflow` window detection, and `module.json` contract validation.

### Changed
- **Standardized `plugin/` Layout:**
  - Renamed `gravity-metrics-plugin/` to `plugin/` across the repository, `install.py` (`PLUGIN_SOURCE = REPO_ROOT / "plugin"`), and `tests/`.
  - Bumped manifest (`plugin/plugin.json`) and MCP App server/widget (`plugin/mcp/server.py`, `plugin/mcp/widget_html.py`) to `0.2.0`.
- **Unified Centralized Storage (`plugin/metrics/pricing.py`, `install.py`, `plugin/rules/AGENTS.md`):**
  - Updated `default_store_dir()` and installer/documentation references to resolve `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics`.
- **Session Window Detection (`plugin/metrics/collector.py`):**
  - Preserved `--gravity-window` and `features_used` matching for both `/gravity-workflow` and `/gravity` (including namespaced invocations and `auto` mode axis stripping).
- **Lot 4b Ecosystem Alignment (`README.md`, `plugin/README.md`, `AGENTS.md`):**
  - Clearly distinguished `gravity` Core (`BSourichanh/gravity`) and `gravity-workflow` (`/gravity-workflow`, `BSourichanh/gravity-workflow`), documented `gravity-metrics`'s signal role for `gravity-workflow` `auto` mode and MCP App UI transition toward `gravity` Core, and codified KISS/YAGNI/DRY/SOLID/CUPID invariants in `AGENTS.md`.

## [0.1.2] - 2026-11-22

### Changed
- **Repository Agent Guidelines (`AGENTS.md`):**
  - Aligned root `AGENTS.md` on the Gravity ecosystem 3-section standard (`## Périmètre & Architecture`, `## Invariants`, `## Vérifications obligatoires`) under 2,500 UTF-8 bytes (`2,209` bytes) and added the orchestrator-only Git & versioning invariant.

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
