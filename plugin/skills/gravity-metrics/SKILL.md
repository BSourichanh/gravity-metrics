---
name: gravity-metrics
description: >-
  Open the Gravity Metrics dashboard in read-only mode (/gravity-metrics), manage capture windows (start/stop/record/archive/pending), and launch the interactive MCP App widget.
---

# Gravity Metrics

`/gravity-metrics` commands are pre-processed by the `PreInvocation` hook before the model turn:
- `/gravity-metrics`: opens the dashboard in read-only mode (`view="overview"`, zero history mutation).
- `/gravity-metrics start [label]`: starts a capture window (0 LLM tokens, terminates before model turn).
- `/gravity-metrics stop [label]` (or `finish` / explicit `<label>`): seals and records the window, then opens `detail`.
- `/gravity-metrics record <uuid_or_prefix> [label] [--full] [--update] [--force]` (alias `archive`, `--update-sealed`): records or refreshes a specific session.
- `/gravity-metrics pending [--project <name>]`: lists unrecorded Gravity root sessions with real work activity (`view="pending"`).
- `/gravity-architect "<instruction>"` (excluding `init|add|sync|status|archive|audit`): idempotently auto-starts a zero-token `architect-<slug>` capture window anchored on the current turn without overwriting an active capture.
- `SessionStart` (`hooks/session_start_metrics.py`): records `(conversation_id -> cwd, project)` into `session_meta.json`, ignoring subagent worktrees (`/.gemini/antigravity/worktrees/`).

For end-of-cycle CLI updates (`scripts/cli.py`):
- `cli.py start <uuid_or_prefix> [-l LABEL] [--step N]`: explicitly starts or overwrites a capture window from the CLI.
- `cli.py record <uuid_or_prefix> --update` / `cli.py add <uuid_or_prefix> --update` (aliases `archive`, `--update-sealed`, optional `--self-archiving`): refreshes an already-recorded window (e.g. after a final `git push`) without duplicates.
- `cli.py pending --all`: includes consultative sessions without work tools (`write_file`, `edit_file`, `run_workflow`, `invoke_subagent`, `swarm`, etc.).
- In the MCP widget (`Sessions -> Enregistrées`), click **« Mettre à jour »** on any recorded session detail (highlighted when `+N appels` / `MAJ` indicates post-record activity).

When the hook injects `<SYSTEM_MESSAGE>`:
1. Read the exact JSON arguments (`view`, and optionally `project`, `target`).
2. Immediately call the MCP tool `open_metrics_dashboard` on server `gravity-metrics_dashboard` with those exact arguments (or `{}` if none were provided), without reading `METRICS.md`, `history.jsonl`, or hook scripts beforehand.
3. Reply in one short sentence confirming the widget opened, without repeating tables already displayed.
