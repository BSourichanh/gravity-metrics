# Plugin Gravity Metrics

Module `metrics` (`config/module.json`, `id: "metrics"`) **100 % bibliothèque standard Python 3** pour collecter, enregistrer et alimenter le tableau de bord unifié **Gravity Core** (`open_gravity_dashboard`) avec les métriques techniques, coûts, tokens, taux de cache, jalons de cycle et signaux de fiabilité des sessions Google Antigravity (sous-agents natifs, `/swarm`, `run_workflow` et éditions directes, avec rétrocompatibilité lecture seule des rapports historiques `#01–#04`).

> **Démarrage 2 min & Positionnement Écosystème**
>
> - **Palier 2 — Pilotage** *(prérequis : [`gravity` Core](https://github.com/BSourichanh/gravity))* : mesure les coûts, économies de cache, jalons et audits agents de vos sessions Antigravity.
> - **Première commande :** `/gravity-metrics` *(ou le dashboard MCP App unifié `gravity`)*.
> - **Hub `gravity/docs/` :** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md)
> - **Référence :** [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

## Positionnement dans l'écosystème Gravity (Lot 4b)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** : socle d'initialisation (`$GRAVITY_HOME` / `~/.gemini/gravity/`), registre des modules (`config/module.json`) et hôte UI unifié.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`) : harnais d'orchestration multi-agents, garde-fous fail-closed, evals et `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`) : produit les signaux `session-metrics` et `cost-telemetry` destinés à l'optimisation du mode `auto` de `gravity-workflow`. L'interface visuelle est unifiée dans `gravity` Core (`ui.mode: "none"`, `ui.target: "gravity"`, zéro serveur MCP embarqué).

## Cycle hybride & Commande `/gravity-metrics`

- `/gravity-metrics` *(nu)* : ouvre le tableau de bord Gravity Core en **lecture seule** (`open_gravity_dashboard`, `view="metrics"`, `metrics_sub="overview"`, zéro écriture ni mutation).
- `/gravity-metrics start [label]` *(et auto-capture sur `/gravity-architect <instruction>`)* : démarre une fenêtre explicite dans `active_captures.json` à **0 token LLM** (`/gravity-architect` hors `init|add|sync|status|archive|audit` ouvre automatiquement `architect-<slug>` ancré sur le tour courant, de façon idempotente sans écraser une capture existante).
- `/gravity-metrics stop [label]` *(alias `finish`, ou `/gravity-metrics <label>` / `--full` / `--update` / `--force`)* : scelle la fenêtre `[start_step .. courant]`, enregistre de façon immuable (`schema_version 2.1.0`, `--update` / `--force` pour mettre à jour) et ouvre la vue `metrics` (`metrics_sub="sessions"`, `target`).
- `/gravity-metrics record <uuid_ou_prefixe> [label] [-n "note"] [--full] [--update] [--force]` *(alias `archive`, `--update-sealed`)* : analyse et enregistre (ou met à jour) *a posteriori* une session existante.
- `/gravity-metrics pending [--project <nom>]` : affiche la file des sessions Gravity racines avec activité de travail non encore enregistrées (filtre anti-bruit actif par défaut ; `cli.py pending --all` réaffiche tout) et ouvre `open_gravity_dashboard` (`view="metrics"`, `metrics_sub="pending"`).
- **Hook `SessionStart` (`hooks/session_start_metrics.py`) :** enregistre la provenance `(conversation_id → cwd, project)` dans `session_meta.json` (hors worktrees sous-agents `/.gemini/antigravity/worktrees/`).
- **Clôture fin de cycle & `--update` (`--update-sealed`) :** `cli.py record <uuid> --update` (alias `archive`, `--update-sealed`, ou `cli.py add ... --update`, optionnellement `--self-archiving`) met à jour sans doublon une fenêtre déjà scellée lors d'un `git push` ultérieur. `/gravity-architect archive <slug>` reste un signal passif pour le jalon `Clôture / Enregistrée` sans enregistrement prématuré en `PreInvocation`.
- **4 jalons automatiques (0 token) :** `Conception` → `Production` (activée sur `/swarm`, sous-agents de production, `run_workflow` et éditions directes hors docs atelier) → `Vérification & Push` → `Clôture / Enregistrée` (repli 2 jalons `Production → Clôture` en mono-dépôt), avec ventilation coûts/tokens par phase.
- **Signaux techniques & résolution `/swarm` (`workspace_resolver.py`, `technical_signals.py`) :** résolution hiérarchique du dépôt canonique avec normalisation des worktrees (`atelier` vs `code`), extraction `thinking_*`, `compaction_count`, `latency_{avg,p50,p95}_ms`, alias `gemini-pro-orchestrator-neon*` / `gemini-flash-worker-agy*`, ventilation `ERROR` / `INVALID` / `shell!=0` (`total_anomalies`) et suivi `cold_cache_waste`.

## Intégration tableau de bord unifié Gravity Core (`open_gravity_dashboard`)

- **Handoff `PreInvocation` :** `/gravity-metrics` redirige vers l'outil MCP `open_gravity_dashboard` du serveur `gravity_dashboard` (`view="metrics"`, `metrics_sub`, `project`, `target`).
- **Actions déléguées DIP :** les actions du panneau unifié (`record`, `update`, `update-meta`, `ignore`, `unignore`, `remove`) appellent `scripts/cli.py --json` sans import Python croisé.

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
python scripts/validate.py --strict       # Contrôles stdlib, contrat module et seuils D4 (rules <= 600/1000 o, SKILL <= 6000 o, LF sans BOM)
```
