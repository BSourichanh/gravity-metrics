# Gravity Metrics

> **100% Python 3.10+ standard library** telemetry and technical-signals module (`plugin/config/module.json`, `id: "metrics"`) for the Gravity ecosystem — hybrid capture lifecycle, centralized history, canonical `/swarm` workspace attribution, `_shared` primitives, and CLI/provider integration feeding the unified **`gravity` Core** dashboard (`open_gravity_dashboard`).

> **Quick Start (2 min) & Ecosystem Fit**
>
> - **Tier 2 — Steering** *(prerequisite: [`gravity` Core](https://github.com/BSourichanh/gravity))*: tracks Antigravity session costs, token/cache savings, milestones, technical signals, and agent audits.
> - **First command:** `/gravity-metrics` *(or the unified `gravity` MCP App dashboard)*.
> - **Hub `gravity/docs/`:** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md)
> - **Reference:** [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

## Ecosystem Role (SPI-1..4 Alignment)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** (`/gravity`): workspace environment initializer (`~/.gemini/gravity/`), module contract registry, `_shared/` SSOT provider, and unified UI host (`gravity_dashboard`).
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`): multi-agent orchestration harness, fail-closed safety hooks, evals, and `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`): dedicated session telemetry & technical/cost analytics module.
  - **Signals (`SPI-3`) for `gravity-workflow`:** emits `session-metrics` signals via `_shared.signal_bus` (`$GRAVITY_HOME/signals/session-metrics/<slug>.json`).
  - **Unified UI hosted in `gravity` Core (`SPI-1`, `ui.mode: "none"`, `ui.target: "gravity"`):** `/gravity-metrics` hands off to `open_gravity_dashboard` (`gravity_dashboard`, `view="metrics"`), backed by `plugin/scripts/dashboard_provider.py`.

```text
gravity-metrics/
├── install.py                              # Thin 11-flag installer / uninstaller onto _shared.installer_core
├── plugin/                                 # Standalone plugin ready to install
│   ├── plugin.json                         # Official 9-field manifest ("gravity-metrics", v0.5.1)
│   ├── hooks.json                          # PreInvocation (active) & SessionStart ("enabled": false)
│   ├── README.md                           # Plugin documentation (French)
│   ├── _shared/                            # Read-only SSOT primitives synced from gravity/shared/gravity_shared/
│   ├── assets/logo.svg                     # 1:1 square SVG icon
│   ├── rules/AGENTS.md                     # Lightweight rule (<= 1 KB, rec. <= 600 B)
│   ├── skills/gravity-metrics/SKILL.md     # /gravity-metrics slash skill & Gravity Core handoff
│   ├── config/
│   │   ├── module.json                     # Module contract v1 (id: "metrics", ui.mode: "none", SPI-1 dashboard)
│   │   └── pricing_catalog.json            # Pricing catalog (2026_nov_standard, 2027_standard)
│   ├── hooks/                              # pre_invocation_metrics.py (SPI-2) & session_start_metrics.py
│   ├── metrics/                            # Modular engine (<750 L/file: collector split, history, lifecycle, store, technical_signals, workspace_resolver, audits_reader, ...)
│   └── scripts/                            # cli.py (--json, 10 subcommands), dashboard_provider.py (SPI-1), validate.py (_shared.validator_core)
├── tests/                                  # Unit & integrity test suite
├── AGENTS.md
├── CHANGELOG.md                            # Release notes
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

[`install.py`](install.py) delegates to `_shared.installer_core` to copy [`plugin/`](plugin/) to `~/.gemini/config/plugins/gravity-metrics/`, adapt `python3` in `hooks.json` on the installed Windows copy (leaving the repository untouched), set POSIX executable permissions (`0755`), recover empty target directories, fall back to a transactional in-place upgrade if a running process locks the target directory, and run `validate.py --strict` before committing.

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

## 2. Hybrid Capture Lifecycle & Unified Dashboard

### Commands (`/gravity-metrics` & `SPI-2` `capture_policy`)

- **`/gravity-metrics`** *(bare)*: opens the Gravity Core dashboard in **read-only** mode (`open_gravity_dashboard`, `view="metrics"`, `metrics_sub="overview"`, zero history mutation).
- **`/gravity-metrics start [label]`**: starts an explicit capture window in `active_captures.json` (**0 LLM tokens**, terminates before model turn).
- **`/gravity-metrics stop [label]`** *(alias `finish`, or `/gravity-metrics <label> [-n "note"] [--full] [--update] [--force]`)*: seals `[start_step .. current]`, records immutably (`--full` deduplicated; pass `--update` / `--force` to refresh), and opens `view="metrics"`, `metrics_sub="sessions"` on `target`.
- **`/gravity-metrics record <uuid_or_prefix> [label] [--full] [--update] [--force]`** *(aliases `archive`, `--update-sealed`)*: records or updates a specific session.
- **`/gravity-metrics pending [--project <name>]`**: lists unrecorded root Gravity sessions with work activity (`view="metrics"`, `metrics_sub="pending"`, hiding consultative noise by default; use `cli.py pending --all` to include all sessions).
- **`SPI-2` `capture_policy` auto-start**: `pre_invocation_metrics.py` reads `$GRAVITY_HOME/core/modules.json` dynamically to idempotently auto-start capture windows for configured module commands without hardcoded command regexes.
- **`SessionStart` hook (`session_start_metrics.py`)**: disabled by default (`"enabled": false`); when invoked directly, records `(conversation_id -> cwd, project)` into `session_meta.json`, ignoring subagent worktrees (`/.gemini/antigravity/worktrees/`).
- **End-of-cycle recording & `--update` (`--update-sealed`)**: `cli.py record <uuid_or_prefix> --update` / `cli.py add <uuid_or_prefix> --update` (aliases `archive`, `--update-sealed`, optional `--self-recording` / `--self-archiving`) refreshes an already-sealed window (e.g. after a final `git push`) without duplicates.
- **4 automatic milestones (0 tokens)**: `Conception` -> `Production` (activated by `/swarm`, production subagents, `run_workflow`, and direct non-atelier edits) -> `Verification & Push` -> `Clôture / Enregistrée` (2-milestone fallback `Production -> Clôture` in single-repo sessions), with per-phase cost/token breakdown.
- **Technical Signals & `/swarm` Canonical Workspaces (`workspace_resolver.py`, `technical_signals.py`)**: 5-tier canonical repo resolution normalizing worktree paths (`atelier` vs `code`), `thinking_*`, `compaction_count`, `latency_{avg,p50,p95}_ms`, catalog aliases for `gemini-pro-orchestrator-neon*` and `gemini-flash-worker-agy*`, separated anomaly counters (`total_anomalies`), and `efficiency_and_waste.cold_cache_waste`.

### Unified Dashboard Delegation (`gravity` Core)

- **`SPI-1` Provider (`plugin/scripts/dashboard_provider.py`)**: `plugin/config/module.json` declares `"ui": {"mode": "none", "target": "gravity"}` and `"dashboard": {"provider_script": "scripts/dashboard_provider.py", ...}`; `/gravity-metrics` hands off directly to `open_gravity_dashboard` on server `gravity_dashboard` (`view="metrics"`, `metrics_sub`, `project`, `target`).
- **Read-only Audits (`audits_reader.py`)**: strictly reads `audits/agents/*.md`, rejecting `audits/code/`, symlinks, and NTFS junctions via `_shared.path_safety`.

---

## 3. 100% Centralized Storage & Passive Read

**Strictly zero files are created inside your target projects**, and **zero static `.html` files are generated on disk**.

Storage follows **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`**:
- `data/history.jsonl`, `METRICS.md`, `reports/<slug>.{json,md}`
- `session_meta.json`, `active_captures.json`, `ignored.json`

Workflow profile & `runs.jsonl` lookup reads `$GRAVITY_WORKFLOW_HOME` / `$GRAVITY_HOME/workflow/workspaces/<slug>-<sha256[:8]>/{profile.json,runs.jsonl}` > `.agents/gravity-workflow/` > `.agents/gravity/`.

---

## 4. CLI & Verification

```powershell
python plugin/scripts/cli.py start <uuid_or_prefix> [-l "my-run"] [--step N] [--json]
python plugin/scripts/cli.py list [--regen] [--json]
python plugin/scripts/cli.py pending [--all] [--json]
python plugin/scripts/cli.py add --latest -l "my-run" -n "Short note" [--update] [--force] [--json]
python plugin/scripts/cli.py record <uuid_or_prefix> -l "my-run" [--update] [--force] [--json]  # alias: archive, --update-sealed
python plugin/scripts/cli.py inspect <label_or_uuid> [--json]
python plugin/scripts/cli.py update-meta <label_or_uuid> [-l LABEL] [-p PROJ] [-n NOTE] [--json]
python plugin/scripts/cli.py ignore <uuid> [--reason "..."] [--json]
python plugin/scripts/cli.py unignore <uuid> [--json]
python plugin/scripts/cli.py remove <label_or_uuid> [--purge-reports] [--purge-state] [--if-exists] [--json]

python plugin/scripts/validate.py --strict
agy plugin validate ./plugin --strict
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 5. Documentation & Releases

- [`CHANGELOG.md`](CHANGELOG.md) — Release history (`v0.1.0` – `v0.5.1` + `[Unreleased]`)
- [`plugin/README.md`](plugin/README.md) — Plugin documentation (French)
- [`AGENTS.md`](AGENTS.md) — Repository invariants and verification commands

---

## License

Licensed under the MIT License — see [`LICENSE`](LICENSE) for details.
