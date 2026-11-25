# Gravity Metrics — Instructions Agents

## Périmètre & Architecture
- **Module `metrics` (`plugin/`) :** `config/module.json` (`ui.mode: "none"`, `ui.target: "gravity"`), hooks `SessionStart` (`hooks/session_start_metrics.py` -> `session_meta.json`, hors worktrees) et `PreInvocation` (`/gravity-metrics` -> `open_gravity_dashboard` sur `gravity_dashboard`, `view="metrics"`, pas de `/metrics`), `skills/gravity-metrics/SKILL.md`, moteur `metrics/` (`workspace_resolver.py`, `technical_signals.py`, `collector.py`, `history.py`, `store.py`, `lifecycle.py`), `rules/AGENTS.md` (0 serveur MCP local).
- **Lot 4b :** distinct de `gravity` (`BSourichanh/gravity`) et `gravity-workflow` (`/gravity-workflow`). Émet `session-metrics` et `cost-telemetry` (`auto`), affichage délégué à `gravity`.
- **Stockage centralisé :** `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/` (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`) ; 0 fichier workspace, 0 `.html` sur disque.

## Invariants
- **Git & versioning (chef d'orchestre seul) :** branche `dev` (jamais `main`) ; *Conventional Commits*, SemVer (`plugin/plugin.json` + `CHANGELOG.md`), tags `vX.Y.Z` sur `main` (sous-agents/`swarm` : 0 commit, push ni tag).
- **Qualité & stdlib :** KISS, YAGNI, DRY, SOLID, CUPID ; 100 % stdlib Python 3.10+ (`plugin/`, `install.py`, `tests/`), UTF-8 sans BOM, LF.
- **Portabilité & docs :** garder `"python3"` dans `plugin/hooks.json` (`install.py` adapte sous Windows) ; synchroniser `README.md`, `plugin/README.md`, `CHANGELOG.md`.
- **CLI & fenêtrage :** `cli.py add` exige UUID ou `--latest` ; `--json` sur toutes commandes + `update-meta`/`ignore`/`unignore` ; préserver `--gravity-window`, 0 terme interdit.

## Vérifications obligatoires
- `python plugin/scripts/validate.py --strict`
- `agy plugin validate ./plugin --strict`
- `python -m unittest discover -s tests -p "test_*.py" -v`

