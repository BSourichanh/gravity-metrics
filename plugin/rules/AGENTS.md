# Gravity Metrics

- `/gravity-metrics` (lecture seule, 0 écriture), `start`/`stop`/`archive`/`pending` : `PreInvocation` + `open_metrics_dashboard` (sans `/metrics`).
- `SessionStart` (`session_meta.json`, hors worktrees) ; archivage explicite (`stop` / `cli.py archive`), `--update-sealed` (bloqué sauf `--force` si Bridge actif).
- `$GRAVITY_METRICS_HOME/METRICS.md` (`$GRAVITY_HOME/metrics`) / `scripts/cli.py` (`add <UUID>|--latest`, `archive`, `pending [--all]`, `list`, `inspect`, `remove`).
- Stockage `$GRAVITY_HOME/metrics/` (0 fichier workspace).
