# Gravity Metrics — Instructions Agents

## Périmètre & Architecture
- **Module `metrics` (`plugin/`) :** `plugin/` fournit `config/module.json`, le hook `PreInvocation` (`/gravity-metrics`), `skills/gravity-metrics/SKILL.md`, le serveur MCP App `mcp/server.py` (`open_metrics_dashboard` `aux_pane`/`global`) et `rules/AGENTS.md`. Jamais de commande `/metrics` séparée.
- **Écosystème Lot 4b :** distinct de `gravity` Core (`BSourichanh/gravity`, initialisation & registre unifié) et de `gravity-workflow` (`/gravity-workflow`, `BSourichanh/gravity-workflow`). Produit `session-metrics` et `cost-telemetry` pour le mode `auto` de `gravity-workflow`, avec UI MCP App en transition vers `gravity` Core (`ui.mode: "transition-mcp"`).
- **Stockage 100 % centralisé :** résolution `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/` (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`). Zéro fichier dans les workspaces cibles et zéro `.html` statique sur disque.

## Invariants
- **Branche `dev` obligatoire :** tout développement s'effectue sur `dev` (jamais directement sur `main`).
- **Git & versioning (chef d'orchestre uniquement) :** *Conventional Commits*, SemVer synchronisé dans `plugin/plugin.json` + `CHANGELOG.md`, tags annotés `vX.Y.Z` sur `main` ; sous-agents et workers `swarm` ne committent, ne poussent et ne taguent jamais.
- **Qualité & stdlib :** appliquer KISS, YAGNI, DRY, SOLID, CUPID ; 100 % bibliothèque standard Python 3.10+ dans `plugin/`, `install.py` et `tests/` (UTF-8 sans BOM, LF).
- **Documentation & portabilité :** synchroniser `README.md`, `plugin/README.md` et `CHANGELOG.md` dans le même lot ; garder `"python3"` dans `plugin/hooks.json` et `plugin/mcp_config.json` (adaptation Windows via `install.py` sur la copie installée).
- **CLI non-interactif & fenêtrage :** jamais de `cli.py add` sans UUID ni `--latest` ; préserver la détection `--gravity-window` pour `/gravity` et `/gravity-workflow`, sans terme ni jargon interdit.

## Vérifications obligatoires
- `python plugin/scripts/validate.py --strict`
- `agy plugin validate ./plugin --strict`
- `python -m unittest discover -s tests -p "test_*.py" -v`
