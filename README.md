# Gravity Metrics

> **100% Python 3.10+ standard library** telemetry and cost-analytics module (`plugin/config/module.json`, `id: "metrics"`) for the Gravity ecosystem — hybrid capture lifecycle, centralized history, and sober **MCP App** widget (`aux_pane` & `global`) for costs, tokens, context cache, milestones, and agent audits across Google Antigravity sessions.

> **Quick Start (2 min) & Ecosystem Fit**
>
> - **Tier 2 — Steering** *(prerequisite: [`gravity` Core](https://github.com/BSourichanh/gravity))*: tracks Antigravity session costs, token/cache savings, milestones, and agent audits.
> - **First command:** `/gravity-metrics` *(or the unified `gravity` MCP App dashboard)*.
> - **Hub `gravity/docs/`:** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md)
> - **Reference:** [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

## Ecosystem Role (Lot 4b Alignment)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** (`/gravity`): workspace environment initializer (`~/.gemini/gravity/`), module contract registry, and unified UI host.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`): multi-agent orchestration harness, fail-closed safety hooks, evals, and `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`): dedicated session telemetry & cost analytics module.
  - **Signals for `gravity-workflow` `auto` mode:** produces `session-metrics` and `cost-telemetry` signals for adaptive optimization loops.
  - **Reference UI design system (`transition-mcp` → `gravity`):** ships an adaptive, sober 3-space MCP App widget (`ui.mode: "transition-mcp"`, `ui.target: "gravity"`).

```text
gravity-metrics/
├── install.py                              # Cross-platform installer / uninstaller (atomic copy + agy)
├── plugin/                                 # Standalone plugin ready to install
│   ├── plugin.json                         # Official 9-field manifest ("gravity-metrics", v0.5.1)
│   ├── hooks.json                          # SessionStart + PreInvocation hooks
│   ├── mcp_config.json                     # MCP App stdio server configuration
│   ├── README.md                           # Plugin documentation (French)
│   ├── assets/logo.svg                     # 1:1 square SVG icon
│   ├── rules/AGENTS.md                     # Lightweight rule (<= 1 KB, rec. <= 600 B)
│   ├── skills/gravity-metrics/SKILL.md     # /gravity-metrics slash skill & widget handoff
│   ├── config/
│   │   ├── module.json                     # Module contract v1 (id: "metrics")
│   │   └── pricing_catalog.json            # Pricing catalog (2026_nov_standard, 2027_standard)
│   ├── hooks/                              # hook_runtime.py + pre_invocation_metrics.py + session_start_metrics.py
│   ├── mcp/                                # server.py, widget_html.py & ui/ (styles, charts, markdown_view, app_js)
│   ├── metrics/                            # Modular engine (audits_reader, collector, history, lifecycle, store, ...)
│   └── scripts/                            # cli.py + validate.py (--strict, --fix-python-cmd)
├── tests/                                  # Unit & integrity test suite
├── AGENTS.md
├── CHANGELOG.md                            # Release notes (v0.5.1)
├── LICENSE
└── README.md
```

---

## 1. Installation

> Prerequisite: **Python 3.10+** (100% standard library, zero external Python or Node/npm dependencies).

### Option 1 — Automatic Installer (Recommended, Windows / macOS / Linux)

```bash
python install.py
```

[`install.py`](install.py) copies [`plugin/`](plugin/) to `~/.gemini/config/plugins/gravity-metrics/`, automatically adapts `python3` in `hooks.json` and `mcp_config.json` on the installed Windows copy (leaving the repository untouched), sets POSIX executable permissions (`0755`), recovers empty target directories, falls back to an in-place upgrade (with stale file pruning) on Windows if a running MCP process locks the target directory, and runs `validate.py --strict` before committing.

```bash
python install.py --check          # Inspect prerequisites and installation status
python install.py --dry-run        # Preview planned operations without writing
python install.py --force          # Upgrade or reinstall (supports in-place fallback if locked)
python install.py --scope workspace --workspace <project-path>
python install.py --uninstall      # Safely remove the installed plugin
```

### Option 2 — Antigravity CLI (`agy`)

```bash
python install.py --method agy
# or directly:
agy plugin install ./plugin
```

---

## 2. Hybrid Capture Lifecycle & 3-Space Widget

### Commands (`/gravity-metrics` & CLI)

- **`/gravity-metrics`** *(bare)*: opens the interactive dashboard in **read-only** mode (`view="overview"`, zero history mutation).
- **`/gravity-metrics start [label]`** *(and auto-capture on `/gravity-architect <instruction>`)*: starts an explicit capture window in `active_captures.json` (**0 LLM tokens**; `/gravity-architect` excluding `init|add|sync|status|archive|audit` auto-starts `architect-<slug>` anchored on the current turn idempotently without overwriting an existing capture).
- **`/gravity-metrics stop [label]`** *(alias `finish`, or `/gravity-metrics <label> [-n "note"] [--full] [--update] [--force]`)*: seals `[start_step .. current]`, records immutably (`--full` deduplicated; pass `--update` / `--force` to refresh), and opens `detail`.
- **`/gravity-metrics record <uuid_or_prefix> [label] [--full] [--update] [--force]`** *(aliases `archive`, `--update-sealed`)*: records or updates a specific session.
- **`/gravity-metrics pending [--project <name>]`**: lists unrecorded root Gravity sessions with work activity (`view="pending"`, hiding consultative noise by default; use `cli.py pending --all` to include all sessions).
- **`SessionStart` hook (`session_start_metrics.py`)**: records `(conversation_id → cwd, project)` into `session_meta.json` (ignoring subagent worktrees under `/.gemini/antigravity/worktrees/`).
- **End-of-cycle recording & `--update` (`--update-sealed`)**: `cli.py record <uuid_or_prefix> --update` / `cli.py add <uuid_or_prefix> --update` (aliases `archive`, `--update-sealed`, optional `--self-archiving`) refreshes an already-sealed window (e.g. after a final `git push`) without duplicates. (`/gravity-architect archive <slug>` remains a passive milestone signal without premature `PreInvocation` recording.)
- **4 automatic milestones (0 tokens)**: `Conception` → `Production` (activated by `/swarm`, production subagents, `run_workflow`, and direct non-studio edits) → `Vérification & Push` → `Clôture / Enregistrée` (2-milestone fallback `Production → Clôture` in single-repo sessions), with per-phase cost/token breakdown.
- **Pricing, Reliability & Cold-Cache Observability**: catalog aliases for `gemini-pro-orchestrator-neon*` and `gemini-flash-worker-agy*` (`2026_nov_standard` & `2027_standard`), `[fallback]` / `unmatched_models` warnings in `cli.py inspect`, separated anomaly counters (`total_error_steps`, `total_invalid_steps`, `total_shell_nonzero_steps`, `total_anomalies`), and `efficiency_and_waste.cold_cache_waste` tracking in CLI & Markdown reports.

### Sober 3-Space MCP App Widget (`transition-mcp` → `gravity`)

Open anytime with **zero LLM tokens** from the **left sidebar (`Gravity Metrics`)** or the **`+` menu in the right side pane** (`global` or `aux_pane`):
- **Adaptive Theme & Contextual Header:** inherits `hostContext.styles.variables` (light/dark/Nord) with neutral graphite/zinc fallback, `tabular-nums`, single `<select>` Project on `Synthèse`/`Sessions`, and conditional `2026 / 2027` toggle (shown only when `cost_usd != cost_2027_usd`).
- **3 Spaces:**
  1. **`Synthèse` (`overview`):** 4 KPIs (cumulative/avg cost, avg cache/savings, sessions/agents, health/alerts), project distribution (`>= 2` projects), chronological SVG evolution (`started_at`), HTML/CSS category bars, and 5 latest sessions.
  2. **`Sessions` (`sessions` / `pending` / `compare`):**
     - `Enregistrées`: Master-Detail (`>= 920px` 280px sidebar with search/model/alerts/sort; `< 920px` quick bar), `enregistrée` pill, discrete `+N appels` / `MAJ` freshness indicator when post-record activity exists, **`Mettre à jour`** button on every recorded session (`update_sealed: true`), inline metadata editing (`metrics_update_session_meta`), milestones & read-only historical Bridge badges/trees (`#01–#04`), HTML/CSS workspace bars.
     - `À enregistrer`: `En cours` vs `Terminée`, milestone badges, inline **`Enregistrer…`** drawer (Label, Project, Note, Window vs Full), and **`Ignorer`** / *Masquées* filter (`ignored.json`).
     - `Comparer`: dedicated first-level A vs B comparator preserved across polls.
  3. **`Audits Agents` (`audits`):** separated by a visual divider, dedicated **`Index & Matrice`** button (`README.md` excluded from list/counter/session links), `_TEMPLATE.md`/`_*` and `audits/code/` excluded, colored canonical verdict extraction (`PASS`, `PARTIAL`, `FAIL`, `UNTESTED`, `WARN`, `BLOQUANT`, `OK`, `KO`), `74ch` anti-XSS Markdown reader (`h1..h6`, callouts, nested lists).
- **Incremental JS & `< 5 ms` Unchanged Polls:** `(mtime_ns, size)` caching in `collector.py` + separate source signature pre-checks (`hist_sig`, `pending_sig`, `audits_sig`) in `server.py`.

### MCP App Tools & Resource (`plugin/mcp/server.py`)

| Surface | Visibility | Purpose |
|---|---|---|
| `open_metrics_dashboard` | `model`, `app` | Opens the widget (`aux_pane` / `global`) with optional `project`, `target`, `view` |
| `metrics_get_state` | `app` | Fast signature-gated poll returning `records` (with `has_new_activity` / `new_llm_calls_delta`), `by_project`, `pending_sessions`, `ignored_sessions`, `index_audit`, `audit_links_by_session` |
| `metrics_get_session` | `app` | Loads detailed JSON session metrics (`reports/<slug>.json` or live recompute) and `freshness` |
| `metrics_archive_session` / `metrics_record_session` | `app` | Records or updates (`update_sealed`/`update`) a session (supports `label`, `project`, `note`, `full`, `force`, `update_sealed`) |
| `metrics_update_session_meta` | `app` | Updates `label`, `project`, or `note` on a recorded session without mutating financial metrics |
| `metrics_ignore_session` / `metrics_unignore_session` | `app` | Hides or restores a pending session via `ignored.json` |
| `metrics_remove_session` | `app` | Removes a session from history (with optional `purge_reports`) |
| `metrics_list_audits` / `metrics_read_audit` | `app` | Read-only `.md` discovery in `audits/agents/` (`index_audit` separated, 256 KiB cap) |
| `ui://gravity-metrics/dashboard.html` | UI Resource | Modular HTML/CSS/SVG/JS widget assembled from `plugin/mcp/ui/` |

---

## 3. 100% Centralized Storage & Passive Read

**Strictly zero files are created inside your target projects**, and **zero static `.html` files are generated on disk**.

Storage follows **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`**:
- `data/history.jsonl`, `METRICS.md`, `reports/<slug>.{json,md}`
- `session_meta.json`, `active_captures.json`, `ignored.json`

Workflow profile lookup reads `$GRAVITY_HOME/workflow/workspaces/<hash>/profile.json` > `.agents/gravity-workflow/profile.json` > `.agents/gravity/profile.json`.

---

## 4. CLI & Verification

```powershell
python plugin/scripts/cli.py start <uuid_or_prefix> [-l "my-run"] [--step N]
python plugin/scripts/cli.py list
python plugin/scripts/cli.py pending [--all]
python plugin/scripts/cli.py add --latest -l "my-run" -n "Short note" [--update]
python plugin/scripts/cli.py record <uuid_or_prefix> -l "my-run" [--update]  # alias: archive, --update-sealed
python plugin/scripts/cli.py inspect <label_or_uuid>
python plugin/scripts/cli.py remove <label_or_uuid> --purge-reports

python plugin/scripts/validate.py --strict
agy plugin validate ./plugin --strict
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 5. Documentation & Releases

- [`CHANGELOG.md`](CHANGELOG.md) — Release history (`v0.1.0` – `v0.5.1`)
- [`plugin/README.md`](plugin/README.md) — Plugin documentation (French)
- [`AGENTS.md`](AGENTS.md) — Repository invariants and verification commands

---

## License

Licensed under the MIT License — see [`LICENSE`](LICENSE) for details.
