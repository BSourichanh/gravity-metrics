# Gravity Metrics

- `/gravity-metrics` (RO), `start`/`stop`/`archive`/`pending` : `PreInvocation` + `open_metrics_dashboard` (0 `/metrics`) ; auto-start `/gravity-architect "<instruction>"`.
- `SessionStart` (`session_meta.json`, hors worktrees) ; `stop`/`cli.py archive` (`--update-sealed`, `--force` ; auto-archivage sans faux `Interrompu`).
- `$GRAVITY_METRICS_HOME/METRICS.md` ; `cli.py` (`start`, `add <UUID>|--latest`, `archive`, `pending [--all]`, `list`, `inspect`, `remove`).
- Stockage `$GRAVITY_HOME/metrics/` (0 fichier workspace).
