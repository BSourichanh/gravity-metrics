# Gravity Metrics

- `/gravity-metrics` (RO), `start`/`stop`/`record`/`archive`/`pending` : `PreInvocation` + `open_metrics_dashboard` (0 `/metrics`) ; auto-start `/gravity-architect "<instruction>"`.
- `SessionStart` (`session_meta.json`, hors worktrees) ; `stop`/`cli.py record` (alias `archive`, `--update`/`--update-sealed`, `--force` ; `--self-archiving` sans faux `Interrompu`).
- `$GRAVITY_METRICS_HOME/METRICS.md` ; `cli.py` (`start`, `add <UUID>|--latest`, `record`, `pending [--all]`, `list`, `inspect`, `remove`).
- Stockage `$GRAVITY_HOME/metrics/` (0 fichier workspace).
