# Gravity Metrics — Instructions Agents

## Architecture & Périmètre
- **Plugin compagnon de Gravity :** `gravity-metrics-plugin/` fournit le hook `PreInvocation` (`/gravity-metrics`), le skill `skills/gravity-metrics/SKILL.md`, le serveur MCP App `mcp/server.py` (`open_metrics_dashboard` en panneau latéral `aux_pane` et pleine page `global`) et une règle légère `rules/AGENTS.md`. N'ajoute jamais de commande `/metrics` séparée.
- **Stockage 100 % centralisé :** toutes les données (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`) vivent dans `~/.gemini/gravity-metrics/` (ou `$GRAVITY_METRICS_HOME`). N'écris jamais de fichier de métriques dans les workspaces cibles et ne génère pas de fichiers `.html` statiques sur disque.
- **Périmètre strict Métriques :** ce dépôt gère exclusivement la collecte, l'historisation et la visualisation des métriques de sessions Antigravity (aucun terme hors périmètre ni jargon interdit).

## Invariants du dépôt
- **Branche de développement (`dev`) :** tout développement et toute modification s'effectuent sur la branche `dev`, puis sont fusionnés vers `main` lors des releases.
- **Documentation synchronisée :** toute évolution fonctionnelle ou structurelle met à jour [`README.md`](README.md), [`gravity-metrics-plugin/README.md`](gravity-metrics-plugin/README.md) et [`CHANGELOG.md`](CHANGELOG.md) dans le même lot de travail.
- **100 % bibliothèque standard Python 3** dans `gravity-metrics-plugin/`, `install.py` et `tests/`.
- **Portabilité `hooks.json` & `mcp_config.json` :** conserve `"python3"` dans `gravity-metrics-plugin/hooks.json` et `gravity-metrics-plugin/mcp_config.json` ; l'adaptation Windows (`python` / `py -3`) s'applique uniquement sur la copie installée via `install.py`.
- **CLI non-interactif :** ne lance jamais `python gravity-metrics-plugin/scripts/cli.py add` sans `<UUID>` ni `--latest` dans un terminal non-interactif.
- **Vérification obligatoire après modification :**
  - `python gravity-metrics-plugin/scripts/validate.py --strict`
  - `agy plugin validate ./gravity-metrics-plugin`
  - `python -m unittest discover -s tests -p "test_*.py" -v`
