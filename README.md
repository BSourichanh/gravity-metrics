# Gravity Metrics

> **100% Python 3.10+ standard library** telemetry and cost-analytics module (`plugin/config/module.json`, `id: "metrics"`) for the Gravity ecosystem — `/gravity-metrics` session capture, centralized history, and interactive **MCP App** widget (`aux_pane` & `global`) for costs, tokens, context cache, and reliability signals across Google Antigravity sessions.

## Ecosystem Role (Lot 4b Alignment)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** (`/gravity`): workspace environment initializer (`~/.gemini/gravity/`), module contract registry, and future unified UI host.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`): multi-agent orchestration harness, fail-closed safety hooks, evals, and `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`): dedicated session telemetry & cost analytics module.
  - **Signals for `gravity-workflow` `auto` mode:** produces `session-metrics` and `cost-telemetry` signals designed to feed future adaptive optimization loops in `gravity-workflow`'s `auto` mode.
  - **UI transition toward `gravity` Core:** currently ships a standalone interactive MCP App widget (`ui.mode: "transition-mcp"`, `ui.target: "gravity"`), scheduled to converge into `gravity` Core's unified UI.

```text
gravity-metrics/
├── install.py                              # Cross-platform installer / uninstaller (atomic copy + agy)
├── plugin/                                 # Standalone plugin ready to install
│   ├── plugin.json                         # Official 9-field manifest ("gravity-metrics", v0.2.0)
│   ├── hooks.json                          # PreInvocation hook (pre-capture on /gravity-metrics)
│   ├── mcp_config.json                     # MCP App stdio server configuration
│   ├── README.md                           # Plugin documentation (French)
│   ├── assets/logo.svg                     # 1:1 square SVG icon
│   ├── rules/AGENTS.md                     # Lightweight rule (< 2 KB) for natural-language queries
│   ├── skills/gravity-metrics/SKILL.md     # /gravity-metrics slash skill & widget handoff
│   ├── config/
│   │   ├── module.json                     # Module contract v1 (id: "metrics")
│   │   └── pricing_catalog.json            # Pricing catalog (2026_nov_standard, 2027_standard)
│   ├── hooks/                              # hook_runtime.py + pre_invocation_metrics.py
│   ├── mcp/                                # server.py (stdio JSON-RPC) + widget_html.py (MCP App UI)
│   ├── metrics/                            # Modular engine (pricing, collector, history, store, formatters, markdown)
│   └── scripts/                            # cli.py + validate.py (--strict, --fix-python-cmd)
├── tests/                                  # Unit & integrity test suite (engine, hook, MCP server, installer)
├── AGENTS.md
├── CHANGELOG.md                            # Release notes (v0.2.0)
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

Useful commands:

```bash
python install.py --check          # Inspect prerequisites and installation status
python install.py --dry-run        # Preview planned operations without writing
python install.py --force          # Upgrade or reinstall (supports in-place fallback if locked)
python install.py --scope workspace --workspace <project-path>
python install.py --no-fix-python  # Keep 'python3' unchanged in installed hooks.json / mcp_config.json
python install.py --skip-validate  # Skip strict validation during install
python install.py --uninstall      # Safely remove the installed plugin
```

### Option 2 — Antigravity CLI (`agy`)

```bash
python install.py --method agy
# or directly:
agy plugin install ./plugin
```

*(On Windows without a real `python3` executable on `PATH`, adapt the installed copy via `python plugin/scripts/validate.py --plugin-root ~/.gemini/config/plugins/gravity-metrics --fix-python-cmd`.)*

---

## 2. Usage (`/gravity-metrics` & Interactive Widget)

### One-command capture + automatic widget opening

Run **`/gravity-metrics [label] [-l|--label <label>] [-n|--note "note"] [--profile <name>] [--full]`** at the end of a session:
1. The `PreInvocation` hook intercepts `/gravity-metrics` **before** the model call, captures the exact window (`[first /gravity-workflow or /gravity after previous capture → strictly before /gravity-metrics]`, or the entire conversation with `--full`), updates the centralized store, and displays a compact Markdown summary inline.
2. The agent then calls `open_metrics_dashboard` to automatically open the interactive **Gravity Metrics** widget in the side panel (`aux_pane`) directly in the **`detail`** view for the captured session (scoped to the active project), or in the **`history`** view filtered on the active project (or global outside a project) when no new LLM activity was captured.

### Zero-token interactive widget (`MCP App` — `transition-mcp` → `gravity`)

Open anytime with **zero LLM tokens** from **Plugins > Gravity Metrics** in the Antigravity sidebar (`global`, all projects) or the side panel launcher (`aux_pane`):
- **Scope toggle:** switch seamlessly between the **active project** and **global (all projects)**.
- **Tariff toggle:** compare `2026` pricing vs `2027` projection across KPI cards, SVG charts, and tables.
- **Session drill-down:** click any session or chart point to inspect categories, execution phases, models, agents, waste signals, tools, and errors.
- **Built-in actions:** auto-refresh (every 4s while visible), **Ouvrir `.md`**, **Joindre au prompt**, **Analyser**, and **Supprimer** (with optional `reports/` purge).

### MCP App Tools & Resource (`plugin/mcp/server.py`)

| Surface | Visibility | Purpose |
|---|---|---|
| `open_metrics_dashboard` | `model`, `app` | Opens the widget (`aux_pane` / `global`) with optional `project`, `target`, `view` (`history` \| `detail`) |
| `metrics_get_state` | `app` | Polls `history.jsonl` changes (`since_signature`) and returns canonical projects |
| `metrics_get_session` | `app` | Loads detailed JSON session metrics (`reports/<slug>.json` or live recompute fallback) |
| `metrics_remove_session` | `app` | Removes a session from history (with optional `purge_reports`) and regenerates `METRICS.md` |
| `ui://gravity-metrics/dashboard.html` | UI Resource | Self-contained HTML/SVG/JS widget (`text/html;profile=mcp-app`) |

---

## 3. 100% Centralized Storage & Passive Read

**Strictly zero files are created inside your target projects**, and **zero static `.html` files are generated on disk**.

Storage location follows the unified Gravity precedence: **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`** (overridable via `--store` in the CLI):
- `data/history.jsonl`: compact chronological history (`1` JSON line per session), with case-insensitive project name harmonization.
- `METRICS.md`: global multi-session Markdown report.
- `reports/<slug>.{json,md}`: detailed per-session audits.

During capture, the plugin performs a read-only lookup of the current project name and, if present, `.agents/gravity/profile.json` to annotate the active `gravity-workflow` profile (e.g., `evo→medium · slow · safe`).

---

## 4. CLI & Verification

```powershell
# Direct CLI usage (add supports --latest, <UUID>, -l/--label, -n/--note, --gravity-window, --full, --workspace, --profile, --summary-only)
python plugin/scripts/cli.py list
python plugin/scripts/cli.py list --regen
python plugin/scripts/cli.py add --latest -l "my-run" -n "Short note"
python plugin/scripts/cli.py inspect <label_or_uuid>
python plugin/scripts/cli.py remove <label_or_uuid> --purge-reports

# Strict plugin validation and full unit & integrity test suite
python plugin/scripts/validate.py --strict
agy plugin validate ./plugin --strict
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 5. Documentation & Releases

- [`CHANGELOG.md`](CHANGELOG.md) — Release history (`v0.1.0`, `v0.1.1`, `v0.1.2`, `v0.2.0`)
- [`plugin/README.md`](plugin/README.md) — Plugin documentation (French)
- [`AGENTS.md`](AGENTS.md) — Repository invariants and verification commands

---

## License

Licensed under the MIT License — see [`LICENSE`](LICENSE) for details.
