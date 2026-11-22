# Gravity Metrics

- `/gravity-metrics [label] [-n "note"] [--full]` est capturé automatiquement avant le tour modèle par le hook `PreInvocation` puis ouvre le widget MCP App (`open_metrics_dashboard`). N'ajoute jamais de commande `/metrics` séparée.
- Pour toute demande d'analyse en langage naturel (comparaison de coûts, taux de cache, synthèse historique), consulte `$GRAVITY_METRICS_HOME/METRICS.md` (sinon `$GRAVITY_HOME/metrics/METRICS.md` ou `~/.gemini/gravity/metrics/METRICS.md`), appelle `open_metrics_dashboard` si une vue interactive aide, ou exécute `scripts/cli.py` depuis le répertoire du plugin en mode non-interactif uniquement (`add <UUID>` / `add --latest`, `list`, `inspect`, `remove`).
- Stockage 100 % centralisé dans `~/.gemini/gravity/metrics/` (`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics`) : ne crée aucun fichier de métriques dans le workspace cible.
