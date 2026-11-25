---
name: gravity-metrics
description: >-
  Open the unified Gravity Core dashboard on the metrics view (/gravity-metrics), manage capture windows (start/stop/record/archive/pending), and hand off to open_gravity_dashboard.
---

# Gravity Metrics

`/gravity-metrics` commands are pre-processed by the `PreInvocation` hook before the model turn:
- `/gravity-metrics`: opens the Gravity Core dashboard in read-only mode (`view="metrics"`, `metrics_sub="overview"`, zero history mutation).
- `/gravity-metrics start [label]`: starts a capture window (0 LLM tokens, terminates before model turn).
- `/gravity-metrics stop [label]` (or `finish` / explicit `<label>`): seals and records the window, then opens `view="metrics"`, `metrics_sub="sessions"` on `target`.
- `/gravity-metrics record <uuid_or_prefix> [label] [--full] [--update] [--force]` (alias `archive`, `--update-sealed`): records or refreshes a specific session.
- `/gravity-metrics pending [--project <name>]`: lists unrecorded Gravity root sessions with real work activity (`view="metrics"`, `metrics_sub="pending"`).
- `/gravity-architect "<instruction>"` (excluding `init|add|sync|status|archive|audit`): idempotently auto-starts a zero-token `architect-<slug>` capture window anchored on the current turn without overwriting an active capture.
- `SessionStart` (`hooks/session_start_metrics.py`): records `(conversation_id -> cwd, project)` into `session_meta.json`, ignoring subagent worktrees (`/.gemini/antigravity/worktrees/`).

For end-of-cycle CLI updates (`scripts/cli.py`, all supporting `--json`):
- `cli.py start <uuid_or_prefix> [-l LABEL] [--step N]`: explicitly starts or overwrites a capture window from the CLI.
- `cli.py record <uuid_or_prefix> --update` / `cli.py add <uuid_or_prefix> --update` (aliases `archive`, `--update-sealed`, optional `--self-archiving`): refreshes an already-recorded window (e.g. after a final `git push`) without duplicates.
- `cli.py update-meta <target> [-l LABEL] [-p PROJECT] [-n NOTE]`, `cli.py ignore <uuid> [--reason R]`, `cli.py unignore <uuid>`: manages session metadata and pending queue.
- `cli.py pending --all`: includes consultative sessions without work tools (`write_file`, `edit_file`, `run_workflow`, `invoke_subagent`, `swarm`, etc.).
- In the unified Gravity Core dashboard (`Métriques -> Sessions`), click **« Mettre à jour »** on any recorded session detail (highlighted when `+N appels` / `MAJ` indicates post-record activity).

When the hook injects `<SYSTEM_MESSAGE>`:
1. Read the exact JSON arguments (`view="metrics"`, and optionally `metrics_sub`, `project`, `target`).
2. Immediately call the MCP tool `open_gravity_dashboard` on server `gravity_dashboard` with those exact arguments, without reading `METRICS.md`, `history.jsonl`, or hook scripts beforehand.
3. Reply in one short sentence confirming the dashboard opened, without repeating tables already displayed.
