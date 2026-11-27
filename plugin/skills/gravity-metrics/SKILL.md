---
name: gravity-metrics
description: >-
  Open the unified Gravity dashboard on metrics (/gravity-metrics), manage capture windows (start/stop/record/pending), and hand off to open_gravity_dashboard.
---

# Gravity Metrics

`/gravity-metrics` commands are pre-processed by the active `PreInvocation` hook before the model turn:
- `/gravity-metrics`: opens the unified Gravity Core dashboard in read-only mode (`view="metrics"`, `metrics_sub="overview"`, zero history mutation).
- `/gravity-metrics start [label]`: starts a capture window (0 LLM tokens, terminates before model turn).
- `/gravity-metrics stop [label]` (or `finish` / explicit `<label>`): seals and records the window, then opens `view="metrics"`, `metrics_sub="sessions"` on `target`.
- `/gravity-metrics record <uuid_or_prefix> [label] [--full] [--update] [--force]` (alias `archive`, `--update-sealed`): records or refreshes a specific session.
- `/gravity-metrics pending [--project <name>]`: lists unrecorded Gravity root sessions with work activity (`view="metrics"`, `metrics_sub="pending"`).
- `SPI-2` `capture_policy` auto-start (`$GRAVITY_HOME/core/modules.json`): idempotently opens a zero-token capture window for matching module commands without overwriting an active capture.
- `SessionStart` (`hooks/session_start_metrics.py`) is disabled by default (`"enabled": false` in `plugin/hooks.json`); the script remains usable directly to record `(conversation_id -> cwd, project)` into `session_meta.json`, ignoring subagent worktrees.

For end-of-cycle CLI updates (`scripts/cli.py`, all 10 subcommands supporting `--json`):
- `cli.py start <uuid_or_prefix> [-l LABEL] [--step N] [--json]`: starts or resets a capture window from the CLI.
- `cli.py record <uuid_or_prefix> [--update] [--force]` / `cli.py add <uuid_or_prefix> [--update] [--force]` (aliases `archive`, `--update-sealed`, optional `--self-recording` / `--self-archiving`): refreshes or overwrites an already-recorded window without duplicates.
- `cli.py list [--regen] [--json]`, `cli.py inspect <target> [--json]`, `cli.py remove <target> [--purge-reports] [--purge-state] [--if-exists] [--json]`.
- `cli.py update-meta <target> [-l LABEL] [-p PROJECT] [-n NOTE] [--json]`, `cli.py ignore <uuid> [--reason R] [--json]`, `cli.py unignore <uuid> [--json]`.
- `cli.py pending [--all] [--json]`: lists unrecorded sessions (`--all` includes consultative sessions without work tools).

When the hook injects `<SYSTEM_MESSAGE>`:
1. Read the exact JSON arguments (`view="metrics"`, and optionally `metrics_sub`, `project`, `target`).
2. Immediately call the MCP tool `open_gravity_dashboard` on server `gravity_dashboard` with those exact arguments, without reading `METRICS.md`, `history.jsonl`, or hook scripts beforehand.
3. Reply in one short sentence confirming the dashboard opened, without repeating tables already displayed.
