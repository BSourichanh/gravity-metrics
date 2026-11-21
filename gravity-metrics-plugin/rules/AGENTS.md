# Gravity Metrics

- `/gravity metrics` (collecte, `list`, `inspect`, `remove`, `add`, `help`) est géré automatiquement en 0 token par le hook `PreInvocation` du plugin `gravity-metrics`. N'ajoute jamais de commande `/metrics` séparée.
- Pour toute demande d'analyse en langage naturel (comparaison de coûts, taux de cache, synthèse historique), consulte `~/.gemini/gravity-metrics/METRICS.md` (ou `$GRAVITY_METRICS_HOME`) et exécute `scripts/cli.py` depuis le répertoire du plugin en mode non-interactif uniquement (`add <UUID>` ou `add --latest`, jamais `add` sans argument).
- Stockage 100 % centralisé dans `~/.gemini/gravity-metrics/` : ne crée aucun fichier de métriques dans le workspace cible.
