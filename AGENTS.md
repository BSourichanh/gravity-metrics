# Gravity Metrics — Instructions Agents

## Périmètre & Architecture
- **Module `metrics` (`plugin/`) :** `config/module.json`, hooks (`PreInvocation` actif via `SPI-2` `capture_policy`, `SessionStart` séparé `"enabled": false`), `skills/gravity-metrics/SKILL.md`, `rules/AGENTS.md`, `scripts/dashboard_provider.py` (`SPI-1` pour `gravity_dashboard`), `scripts/cli.py` (10 sous-commandes `--json` : `start`, `add`, `record`, `pending`, `list`, `inspect`, `update-meta`, `ignore`, `unignore`, `remove`), `scripts/validate.py`, `metrics/`, `_shared/` (SSOT `gravity/shared/gravity_shared/`).
- **Contrats (`SPI-1..4`) :** distinct de `gravity` et `gravity-workflow`. Émet `session-metrics` (`SPI-3` `_shared.signal_bus`), `ui.mode: "none"` (`target: "gravity"`), 0 serveur MCP autonome.
- **Stockage centralisé :** `$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/` (`data/history.jsonl`, `METRICS.md`, `reports/*.{json,md}`) ; 0 fichier workspace, 0 `.html` sur disque.

## Invariants
- **Git & versioning :** travail sur `refonte/socle` (jamais `main` ni `archive/pre-refonte-*` sans accord humain) ; *Conventional Commits*, SemVer (`plugin/plugin.json` + `CHANGELOG.md`) ; ne jamais éditer `plugin/_shared/` manuellement (`sync_shared.py`).
- **Qualité & stdlib :** KISS, YAGNI, DRY, SOLID, CUPID ; 100 % stdlib Python 3.10+ (`plugin/`, `install.py`, `tests/`), fichiers `< 800` L, UTF-8 sans BOM, LF, compatibilité Linux & Windows.
- **Portabilité & docs :** garder `"python3"` dans `plugin/hooks.json` (`install.py` adapte sous Windows) ; synchroniser `README.md`, `plugin/README.md`, `CHANGELOG.md`.
- **CLI & fenêtrage :** `cli.py add` exige UUID ou `--latest` ; préserver `--gravity-window` (`/gravity`, `/gravity-workflow`), 0 terme interdit.

## Vérifications obligatoires
- `python3 -I -B ../gravity/scripts/sync_shared.py --check --repos gravity-metrics --json`
- `python3 -I -B plugin/scripts/validate.py --strict`
- `agy plugin validate ./plugin --strict`
- `python3 -I -B -m unittest discover -s tests -p "test_*.py" -v`
