---
name: gravity-metrics
description: >-
  Open the Gravity Metrics dashboard in read-only mode (/gravity-metrics), manage capture windows (start/stop/archive/pending), and launch the interactive MCP App widget.
---

# Gravity Metrics

`/gravity-metrics` commands are pre-processed by the `PreInvocation` hook before the model turn:
- `/gravity-metrics`: opens the dashboard in read-only mode (`view="overview"`, zero history mutation).
- `/gravity-metrics start [label]`: starts a capture window (0 LLM tokens, terminates before model turn).
- `/gravity-metrics stop [label]` (or `finish` / explicit `<label>`): seals and archives the window, then opens `detail`.
- `/gravity-metrics archive <uuid_or_prefix> [label] [--full] [--force]`: archives a specific session.
- `/gravity-metrics pending [--project <name>]`: lists unarchived Gravity root sessions with real work activity (`view="pending"`).

For end-of-cycle CLI updates (`scripts/cli.py`):
- `cli.py archive <uuid_or_prefix> --update-sealed` / `cli.py add <uuid_or_prefix> --update-sealed`: refreshes an already-sealed window (e.g. after a final `git push`) while preserving the `PendingBridgeJobsError` guard unless `--force` is passed.
- `cli.py pending --all`: includes consultative sessions without work tools (`write_file`, `edit_file`, `run_workflow`, `invoke_subagent`, `swarm`, etc.).

When the hook injects `<SYSTEM_MESSAGE>`:
1. Read the exact JSON arguments (`view`, and optionally `project`, `target`).
2. Immediately call the MCP tool `open_metrics_dashboard` on server `gravity-metrics_dashboard` with those exact arguments (or `{}` if none were provided), without reading `METRICS.md`, `history.jsonl`, or hook scripts beforehand.
3. Reply in one short sentence confirming the widget opened, without repeating tables already displayed.
