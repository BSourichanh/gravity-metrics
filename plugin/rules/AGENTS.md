# Gravity Metrics

- `/gravity-metrics` (RO), `start`/`stop`/`record`/`pending` : `PreInvocation` actif -> `open_gravity_dashboard` (`gravity_dashboard`, `view="metrics"`, 0 `/metrics`) ; auto-start `SPI-2` (`capture_policy`).
- `SessionStart` (`hooks/session_start_metrics.py`) désactivé par défaut (`"enabled": false` dans `plugin/hooks.json`).
- `scripts/cli.py --json` (`start`, `add`, `record`, `pending`, `list`, `inspect`, `update-meta`, `ignore`, `unignore`, `remove`).
- Stockage `$GRAVITY_HOME/metrics/` (0 fichier workspace, 0 serveur MCP).
