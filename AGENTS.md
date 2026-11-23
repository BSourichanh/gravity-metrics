# Gravity Metrics — Instructions Agents

## Périmètre & Architecture
- **Module `metrics` (`plugin/`) :** `config/module.json`, hook `PreInvocation` (`/gravity-metrics`, pas de `/metrics`), `skills/gravity-metrics/SKILL.md`, MCP App `mcp/server.py` (`open_metrics_dashboard` `aux_pane`/`global`), `rules/AGENTS.md`.
- **Lot 4b :** distinct de `gravity` (`BSourichanh/gravity`) et `gravity-workflow` (`/gravity-workflow`). Émet `session-metrics` et `cost-telemetry` (`auto`), `ui.mode: "transition-mcp"` vers `gravity`.
- **Stockage centralisé :** `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/` (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`) ; 0 fichier workspace, 0 `.html` sur disque.

## Invariants
- **Git & versioning (chef d'orchestre seul) :** branche `dev` (jamais `main`) ; *Conventional Commits*, SemVer (`plugin/plugin.json` + `CHANGELOG.md`), tags `vX.Y.Z` sur `main` (sous-agents/`swarm` : 0 commit, push ni tag).
- **Qualité & stdlib :** KISS, YAGNI, DRY, SOLID, CUPID ; 100 % stdlib Python 3.10+ (`plugin/`, `install.py`, `tests/`), UTF-8 sans BOM, LF.
- **Portabilité & docs :** garder `"python3"` dans `plugin/hooks.json` et `plugin/mcp_config.json` (`install.py` adapte sous Windows) ; synchroniser `README.md`, `plugin/README.md`, `CHANGELOG.md`.
- **CLI & fenêtrage :** `cli.py add` exige UUID ou `--latest` ; préserver `--gravity-window` (`/gravity`, `/gravity-workflow`), 0 terme interdit.

## Vérifications obligatoires
- `python plugin/scripts/validate.py --strict`
- `agy plugin validate ./plugin --strict`
- `python -m unittest discover -s tests -p "test_*.py" -v`
