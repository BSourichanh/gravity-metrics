# Gravity Metrics — Instructions Agents

## Périmètre & Architecture
- **Plugin compagnon :** `gravity-metrics-plugin/` fournit le hook `PreInvocation` (`/gravity-metrics`), `skills/gravity-metrics/SKILL.md`, le serveur MCP App `mcp/server.py` (`open_metrics_dashboard` `aux_pane`/`global`) et `rules/AGENTS.md`. Jamais de commande `/metrics` séparée.
- **Stockage 100 % centralisé :** toutes les données (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`) vivent dans `~/.gemini/gravity-metrics/` (ou `$GRAVITY_METRICS_HOME`). Zéro fichier de métriques dans les workspaces cibles et zéro `.html` statique sur disque.
- **Périmètre strict Métriques :** collecte, historisation et visualisation des métriques de sessions Antigravity uniquement (aucun terme hors périmètre ni jargon interdit).

## Invariants
- **Branche de développement (`dev`) obligatoire :** tout développement s'effectue sur `dev` (ou branche dédiée), puis est fusionné vers `main` aux releases.
- **Git & versioning (chef d'orchestre uniquement) :** `dev` / branches dédiées, jamais de commit ni push direct sur `main` ; *Conventional Commits* ; SemVer synchronisé dans `gravity-metrics-plugin/plugin.json` + `CHANGELOG.md` ; tags annotés `vX.Y.Z` aux releases. Sous-agents et workers `swarm` ne committent, ne poussent et ne taguent jamais.
- **Documentation synchronisée :** toute évolution fonctionnelle ou structurelle met à jour `README.md`, `gravity-metrics-plugin/README.md` et `CHANGELOG.md` dans le même lot.
- **100 % bibliothèque standard Python 3** dans `gravity-metrics-plugin/`, `install.py` et `tests/`.
- **Portabilité `"python3"` :** conserver `"python3"` dans `gravity-metrics-plugin/hooks.json` et `mcp_config.json` (adaptation Windows `python` / `py -3` via `install.py` sur la copie installée uniquement).
- **CLI strictement non-interactif :** ne jamais lancer `python gravity-metrics-plugin/scripts/cli.py add` sans `<UUID>` ni `--latest` dans un terminal non-interactif.

## Vérifications obligatoires
- `python gravity-metrics-plugin/scripts/validate.py --strict`
- `agy plugin validate ./gravity-metrics-plugin`
- `python -m unittest discover -s tests -p "test_*.py" -v`
