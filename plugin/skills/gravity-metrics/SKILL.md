---
name: gravity-metrics
description: >-
  Capture Gravity session metrics with /gravity-metrics (including archive and pending subcommands) and open the interactive Gravity Metrics MCP App dashboard widget.
---

# Gravity Metrics

`/gravity-metrics` (including `/gravity-metrics [label] [--full] [--force]`, `/gravity-metrics archive <uuid_ou_prefixe>`, and `/gravity-metrics pending`) is pre-processed by the `PreInvocation` hook before the model turn.

1. Read the injected `<SYSTEM_MESSAGE>` for the exact JSON arguments (`project`, and optionally `target`, `view`).
2. Immediately call the MCP tool `open_metrics_dashboard` on server `gravity-metrics_dashboard` with those exact arguments (or `{}` if none were provided), without reading `METRICS.md`, `history.jsonl`, or hook scripts beforehand.
3. Reply in one short sentence confirming the widget opened, without repeating tables already displayed.
