# Gravity Metrics

- `/gravity-metrics` (lecture seule, 0 écriture), `start`/`stop`/`archive`/`pending` : `PreInvocation` + `open_metrics_dashboard` (sans `/metrics`).
- `SessionStart` (`session_meta.json`) ; `/gravity-architect archive <slug>` auto-archive (bloqué sauf `--force` si Bridge actif).
- `$GRAVITY_METRICS_HOME/METRICS.md` (`$GRAVITY_HOME/metrics`) / `scripts/cli.py` (`add <UUID>|--latest`, `archive`, `pending`, `list`, `inspect`, `remove`).
- Stockage `$GRAVITY_HOME/metrics/` (0 fichier workspace).
