# Gravity Metrics

- `/gravity-metrics [label] [-n "note"] [--full] [--force]`, `/gravity-metrics archive <uuid> [label] [--full] [--force]` et `/gravity-metrics pending` sont gérés avant le tour modèle par `PreInvocation` puis ouvrent le widget MCP App (`open_metrics_dashboard`). Pas de commande `/metrics` séparée.
- Chaque session unifie l'arbre natif et les enfants Gravity Bridge (`bridge.db` en lecture seule + transcript) avec archivage immuable (bloqué par `--force` si un job Bridge est actif).
- Pour toute analyse en langage naturel, consulte `$GRAVITY_METRICS_HOME/METRICS.md` (`$GRAVITY_HOME/metrics/METRICS.md` ou `~/.gemini/gravity/metrics/METRICS.md`), appelle `open_metrics_dashboard`, ou exécute `scripts/cli.py` en mode non-interactif (`add <UUID>|--latest`, `archive <UUID>`, `pending`, `list`, `inspect`, `remove`).
- Stockage 100 % centralisé dans `~/.gemini/gravity/metrics/` (`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics`) : zéro fichier créé dans le workspace cible.
