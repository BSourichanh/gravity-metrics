# Gravity Metrics — Instructions Agents

## Architecture & Périmètre
- **Plugin compagnon de Gravity :** `gravity-metrics-plugin/` fournit le hook `PreInvocation` (`/gravity metrics`) et une règle légère `rules/AGENTS.md`. Ne crée jamais de commande `/metrics` séparée (pas de dossier `skills/`).
- **Stockage 100 % centralisé :** toutes les données (`data/history.jsonl`, `METRICS.md`, `dashboard.html`, `reports/`) vivent dans `~/.gemini/gravity-metrics/` (ou `$GRAVITY_METRICS_HOME`). N'écris jamais de fichier de métriques dans les workspaces cibles.
- **Périmètre strict Métriques :** ce dépôt gère exclusivement la collecte, l'historisation et la visualisation des métriques de sessions Antigravity (aucun terme hors périmètre ni jargon interdit).

## Invariants du dépôt
- **100 % bibliothèque standard Python 3** dans `gravity-metrics-plugin/`, `install.py` et `tests/`.
- **Portabilité `hooks.json` :** conserve `"python3 hooks/pre_invocation_metrics.py"` dans `gravity-metrics-plugin/hooks.json` ; l'adaptation Windows (`python` / `py -3`) s'applique uniquement sur la copie installée via `install.py`.
- **CLI non-interactif :** ne lance jamais `python gravity-metrics-plugin/scripts/cli.py add` sans `<UUID>` ni `--latest` dans un terminal non-interactif.
- **Vérification obligatoire après modification :**
  - `python gravity-metrics-plugin/scripts/validate.py --strict`
  - `python -m unittest discover -s tests -p "test_*.py" -v`
