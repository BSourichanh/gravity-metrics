# Plugin Gravity Metrics

Module `metrics` (`config/module.json`, `id: "metrics"`) **100 % bibliothèque standard Python 3.10+** pour collecter, enregistrer et exposer au tableau de bord unifié **`gravity` Core** (`open_gravity_dashboard`) les coûts, tokens, taux de cache, jalons de cycle, signaux techniques et audits agents des sessions Google Antigravity.

> **Démarrage 2 min & Positionnement Écosystème**
>
> - **Palier 2 — Pilotage** *(prérequis : [`gravity` Core](https://github.com/BSourichanh/gravity))* : mesure les coûts, économies de cache, jalons et audits agents de vos sessions Antigravity.
> - **Première commande :** `/gravity-metrics` *(ou le dashboard MCP App unifié `gravity`)*.
> - **Hub `gravity/docs/` :** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md)
> - **Référence :** [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

## Positionnement dans l'écosystème Gravity (`SPI-1..4`)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** : socle d'initialisation (`$GRAVITY_HOME` / `~/.gemini/gravity/`), registre des modules (`config/module.json`), SSOT `_shared/` et hôte UI unifié (`gravity_dashboard`).
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`) : harnais d'orchestration multi-agents, garde-fous fail-closed, evals et `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`) : émet les signaux `session-metrics` via `_shared.signal_bus` (`SPI-3`) et délègue l'interface visuelle à `gravity` Core (`ui.mode: "none"`, `ui.target: "gravity"`, `scripts/dashboard_provider.py`).

## Cycle hybride & Commande `/gravity-metrics`

- `/gravity-metrics` *(nu)* : ouvre le tableau de bord Gravity Core en **lecture seule** (`open_gravity_dashboard`, `view="metrics"`, `metrics_sub="overview"`, zéro écriture ni mutation).
- `/gravity-metrics start [label]` : démarre une fenêtre explicite dans `active_captures.json` à **0 token LLM** (terminaison avant tour modèle).
- `/gravity-metrics stop [label]` *(alias `finish`, ou `/gravity-metrics <label>` / `--full` / `--update` / `--force`)* : scelle la fenêtre `[start_step .. courant]`, enregistre de façon immuable (`schema_version 2.1.0`, `--update` / `--force` pour mettre à jour) et ouvre `view="metrics"`, `metrics_sub="sessions"`.
- `/gravity-metrics record <uuid_ou_prefixe> [label] [-n "note"] [--full] [--update] [--force]` *(alias `archive`, `--update-sealed`)* : analyse et enregistre (ou met à jour) *a posteriori* une session existante.
- `/gravity-metrics pending [--project <nom>]` : affiche la file des sessions Gravity racines avec activité de travail non encore enregistrées (filtre anti-bruit actif par défaut ; `cli.py pending --all` réaffiche tout) et ouvre `open_gravity_dashboard` (`view="metrics"`, `metrics_sub="pending"`).
- **Politique d'auto-capture `SPI-2` :** `hooks/pre_invocation_metrics.py` lit dynamiquement `capture_policy` dans `$GRAVITY_HOME/core/modules.json` pour démarrer une fenêtre idempotente à 0 token sans regex codée en dur.
- **Hook `SessionStart` (`hooks/session_start_metrics.py`) :** désactivé par défaut dans `hooks.json` (`"enabled": false`) ; appelé directement, enregistre `(conversation_id → cwd, project)` dans `session_meta.json` (hors worktrees sous-agents `/.gemini/antigravity/worktrees/`).
- **4 jalons automatiques (0 token) :** `Conception` → `Production` → `Vérification & Push` → `Clôture / Enregistrée` (repli 2 jalons `Production → Clôture` en mono-dépôt), avec ventilation coûts/tokens par phase.
- **Signaux techniques & résolution `/swarm` (`workspace_resolver.py`, `technical_signals.py`) :** résolution hiérarchique du dépôt canonique avec normalisation des worktrees (`atelier` vs `code`), `thinking_*`, `compaction_count`, `latency_{avg,p50,p95}_ms` et suivi `cold_cache_waste`.

## Stockage centralisé

Toutes les données vivent sous **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`** — **zéro fichier dans vos projets** et **aucun `.html` statique sur disque** :
- `data/history.jsonl`, `METRICS.md`, `reports/<slug>.{json,md}`
- `session_meta.json`, `active_captures.json`, `ignored.json`

## CLI non-interactif (`scripts/cli.py`)

```bash
python scripts/cli.py start <uuid_ou_prefixe> [-l "mon-run"] [--step N] [--json]
python scripts/cli.py list [--regen] [--json]
python scripts/cli.py pending [--all] [--json]
python scripts/cli.py add --latest -l "mon-run" -n "Note" [--update] [--force] [--json]
python scripts/cli.py record <uuid_ou_prefixe> -l "mon-run" [--update] [--force] [--json]  # alias: archive, --update-sealed
python scripts/cli.py inspect <label_ou_uuid> [--json]
python scripts/cli.py update-meta <label_ou_uuid> [-l LABEL] [-p PROJ] [-n NOTE] [--json]
python scripts/cli.py ignore <uuid> [--reason "..."] [--json]
python scripts/cli.py unignore <uuid> [--json]
python scripts/cli.py remove <label_ou_uuid> --purge-reports [--json]
python scripts/validate.py --strict       # Délègue à _shared.validator_core (stdlib, module.json, D4)
```
