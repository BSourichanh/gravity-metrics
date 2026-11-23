# Gravity Metrics

- `/gravity-metrics` (nu) ouvre le tableau de bord en **lecture seule** (`0` écriture) ; `/gravity-metrics start [label]` et `/gravity-metrics stop [label]` gèrent la fenêtre explicite (`0` token au `start`) ; `/gravity-metrics archive <uuid>` et `/gravity-metrics pending` sont gérés avant le tour modèle par `PreInvocation` puis ouvrent le widget MCP App (`open_metrics_dashboard`). Pas de commande `/metrics` séparée.
- `SessionStart` enregistre la provenance workspace (`session_meta.json`) et `/gravity-architect archive <slug>` déclenche l'archivage automatique (bloqué par `--force` si un job Bridge est actif).
- Pour toute analyse en langage naturel, consulte `$GRAVITY_METRICS_HOME/METRICS.md` (`$GRAVITY_HOME/metrics/METRICS.md` ou `~/.gemini/gravity/metrics/METRICS.md`), appelle `open_metrics_dashboard`, ou exécute `scripts/cli.py` (`add <UUID>|--latest`, `archive <UUID>`, `pending`, `list`, `inspect`, `remove`).
- Stockage 100 % centralisé dans `~/.gemini/gravity/metrics/` (`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics`) : zéro fichier dans le workspace cible.
