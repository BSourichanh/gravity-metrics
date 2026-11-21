# Gravity Metrics

> Companion **100% Python 3.10+ standard library** plugin for [Gravity](https://github.com/BSourichanh/gravity) — zero-token capture via `/gravity metrics`, centralized session history, and dashboards for costs, tokens, context cache, and reliability signals across Google Antigravity sessions.

```text
gravity-metrics/
├── install.py                              # Cross-platform installer / uninstaller (atomic copy + agy)
├── gravity-metrics-plugin/                 # Standalone plugin ready to install
│   ├── plugin.json                         # Official manifest ("gravity-metrics")
│   ├── hooks.json                          # PreInvocation hook (zero-token /gravity metrics handler)
│   ├── assets/logo.svg                     # 1:1 square SVG icon
│   ├── rules/AGENTS.md                     # Lightweight rule (< 900 B) for natural-language queries
│   ├── config/pricing_catalog.json         # Pricing catalog (2026_nov_standard, 2027_standard)
│   ├── hooks/                              # hook_runtime.py + pre_invocation_metrics.py
│   ├── metrics/                            # Modular engine (pricing, collector, history, store, formatters, reports)
│   └── scripts/                            # cli.py + validate.py (--strict, --fix-python-cmd)
├── tests/                                  # Unit & integrity test suite (engine, hook, installer)
├── AGENTS.md
├── LICENSE
└── README.md
```

---

## 1. Installation

> Prerequisite: **Python 3.10+** (100% standard library, zero external dependencies).

### Option 1 — Automatic Installer (Recommended, Windows / macOS / Linux)

```bash
python install.py
```

[`install.py`](install.py) copies [`gravity-metrics-plugin/`](gravity-metrics-plugin/) to `~/.gemini/config/plugins/gravity-metrics/`, automatically adapts `python3` to `python` or `py -3` on the installed Windows copy (leaving the repository untouched), sets POSIX executable permissions (`0755`), and runs `validate.py --strict` before committing.

Useful commands:

```bash
python install.py --check          # Inspect prerequisites and installation status
python install.py --dry-run        # Preview planned operations without writing
python install.py --force          # Upgrade or reinstall
python install.py --scope workspace --workspace <project-path>
python install.py --uninstall      # Safely remove the installed plugin
```

### Option 2 — Antigravity CLI (`agy`)

```bash
python install.py --method agy
# or directly:
agy plugin install ./gravity-metrics-plugin
```

*(On Windows without a real `python3` executable on `PATH`, adapt the installed copy via `python gravity-metrics-plugin/scripts/validate.py --plugin-root ~/.gemini/config/plugins/gravity-metrics --fix-python-cmd`.)*

---

## 2. End-of-Session Usage (`/gravity metrics`)

The plugin registers no separate `/metrics` command: everything runs through `/gravity metrics`, intercepted before any model call by the `PreInvocation` hook (**0 tokens**, **\$0.00**, zero transcript pollution).

### Bounded Capture Window

By default, `/gravity metrics` measures only the window:
**`[first /gravity after previous capture → strictly before /gravity metrics]`**
- Chat activity prior to the first `/gravity` is ignored (earlier steps and subagents excluded).
- The `/gravity metrics` turn itself (and anything following it) is excluded.
- Consecutive `/gravity` turns before `/gravity metrics` are grouped into a single session; starting a new `/gravity` sequence after `/gravity metrics` creates a new distinct entry in the history.
- If no prior `/gravity` exists in the window, capture cleanly falls back to the entire conversation (or since the previous capture) and notes this in the summary.

### Available Commands

- `/gravity metrics`: captures the current window with an automatic label (`<project>-<keywords>`).
- `/gravity metrics <label> [-n "note"] [--full] [--summary-only] [--no-html]`: captures with a custom label, note, or `--full` (entire conversation).
- `/gravity metrics list [--regen]`: displays the global session history.
- `/gravity metrics inspect <label_or_uuid>`: inspects full session details (categories, models, agents, waste signals).
- `/gravity metrics remove <label_or_uuid> [--purge-reports]`: removes a session from the global history.
- `/gravity metrics add <UUID> [label] [-n "note"] [--full]`: captures an external session by UUID.
- `/gravity metrics help`: displays quick command help.

---

## 3. 100% Centralized Storage & Passive Read

**Strictly zero files are created inside your target projects.**

All data is persisted in **`~/.gemini/gravity-metrics/`** (overridable via `GRAVITY_METRICS_HOME` or `--store` in the CLI):
- `data/history.jsonl`: compact chronological history (`1` JSON line per session).
- `METRICS.md` & `dashboard.html`: global multi-session comparison views.
- `reports/<slug>.{json,md,html}`: detailed per-session audits.

During capture, the plugin performs a read-only lookup of the current project name and, if present, `.agents/gravity/profile.json` to annotate the active Gravity profile (e.g., `evo→medium · slow · safe`).

---

## 4. CLI & Verification

```powershell
# Direct CLI usage
python gravity-metrics-plugin/scripts/cli.py list
python gravity-metrics-plugin/scripts/cli.py add --latest -l "my-run" -n "Short note"
python gravity-metrics-plugin/scripts/cli.py inspect <label_or_uuid>

# Strict plugin validation and unit test suite
python gravity-metrics-plugin/scripts/validate.py --strict
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## License

Licensed under the MIT License — see [`LICENSE`](LICENSE) for details.
