# Plugin Gravity Metrics

Plugin compagnon **100 % bibliothèque standard Python 3** pour collecter, historiser et visualiser les coûts, tokens, taux de cache et signaux de fiabilité des sessions Antigravity / Gravity.

## Commandes `/gravity metrics` (zéro token)

Interceptées localement avant tout appel modèle via le hook `PreInvocation` :

- `/gravity metrics [label] [-n "note"] [--full] [--summary-only]` : collecte la fenêtre courante de la session (du **premier** `/gravity` après la précédente collecte jusqu'à **strictement avant** `/gravity metrics`).
- `/gravity metrics list [--regen]` : affiche l'historique consolidé.
- `/gravity metrics inspect <label_ou_uuid>` : affiche le détail complet d'une session.
- `/gravity metrics remove <label_ou_uuid> [--purge-reports]` : retire une session de la liste.
- `/gravity metrics add <UUID> [label] [-n "note"] [--full]` : collecte une session externe par UUID.
- `/gravity metrics help` : affiche l'aide rapide.

## Stockage centralisé

Toutes les données sont écrites uniquement dans `~/.gemini/gravity-metrics/` (surchargeable via `GRAVITY_METRICS_HOME` ou `--store`) — **zéro fichier créé dans vos projets** :
- `data/history.jsonl` : liste chronologique compacte des sessions.
- `METRICS.md` & `dashboard.html` : vue globale multi-sessions.
- `reports/<slug>.{json,md,html}` : audits détaillés par session.
