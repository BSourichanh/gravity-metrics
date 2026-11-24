# Plugin Gravity Metrics

Module `metrics` (`config/module.json`, `id: "metrics"`) **100 % bibliothèque standard Python 3** pour collecter, archiver et visualiser en widget **MCP App** les coûts, tokens, taux de cache, jalons de cycle et signaux de fiabilité des sessions Google Antigravity unifiées (arbre natif + enfants Gravity Bridge).

> **Démarrage 2 min & Positionnement Écosystème**
>
> - **Palier 2 — Pilotage** *(prérequis : [`gravity` Core](https://github.com/BSourichanh/gravity))* : mesure les coûts, économies de cache, jalons et audits agents de vos sessions Antigravity.
> - **Première commande :** `/gravity-metrics` *(ou le dashboard MCP App unifié `gravity`)*.
> - **Hub `gravity/docs/` :** [Getting Started](https://github.com/BSourichanh/gravity/blob/main/docs/getting-started.md) · [User Journey](https://github.com/BSourichanh/gravity/blob/main/docs/user-journey.md)
> - **Référence :** [Command Cheatsheet](https://github.com/BSourichanh/gravity/blob/main/docs/command-cheatsheet.md) · [Troubleshooting & Lifecycle](https://github.com/BSourichanh/gravity/blob/main/docs/troubleshooting-and-lifecycle.md)

## Positionnement dans l'écosystème Gravity (Lot 4b)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** : socle d'initialisation (`$GRAVITY_HOME` / `~/.gemini/gravity/`), registre des modules (`config/module.json`) et futur hôte UI unifié.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`) : harnais d'orchestration multi-agents, garde-fous fail-closed, evals et `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`) : produit les signaux `session-metrics` et `cost-telemetry` destinés aux futures optimisations du mode `auto` de `gravity-workflow`. Son design system sobre sert de référence pour `gravity` (`ui.mode: "transition-mcp"`, `ui.target: "gravity"`).

## Cycle hybride & Commande `/gravity-metrics`

- `/gravity-metrics` *(nu)* : ouvre le tableau de bord en **lecture seule** (`view="overview"`, zéro écriture ni mutation).
- `/gravity-metrics start [label]` : démarre une fenêtre explicite dans `active_captures.json` à **0 token LLM** (terminaison avant tour modèle).
- `/gravity-metrics stop [label]` *(alias `finish`, ou `/gravity-metrics <label>` / `--full` / `--force`)* : scelle la fenêtre `[start_step .. courant]`, archive de façon immuable (`schema_version 2.1.0`, bloqué sauf `--force` si un job Bridge est actif) et ouvre la vue détail.
- `/gravity-metrics archive <uuid_ou_prefixe> [label] [-n "note"] [--full] [--force]` : analyse et archive *a posteriori* une session existante.
- `/gravity-metrics pending [--project <nom>]` : affiche la file des sessions Gravity racines avec activité de travail non encore archivées (filtre anti-bruit actif par défaut ; `cli.py pending --all` réaffiche tout) et ouvre `Sessions → À archiver`.
- **Hook `SessionStart` (`hooks/session_start_metrics.py`) :** enregistre la provenance `(conversation_id → cwd, project)` dans `session_meta.json` (hors worktrees sous-agents `/.gemini/antigravity/worktrees/`), complétée par `bridge.db` RO (`from_workspace` + `canonical_path`).
- **Clôture fin de cycle & `--update-sealed` :** `cli.py archive <uuid> --update-sealed` (ou `cli.py add ... --update-sealed`) met à jour une fenêtre déjà scellée lors d'un `git push` ultérieur tout en conservant le garde-fou `PendingBridgeJobsError` (sauf `--force`). `/gravity-architect archive <slug>` reste un signal passif pour le jalon `Clôture / Archivé` sans archivage prématuré en `PreInvocation`.
- **4 jalons automatiques (0 token) :** `Conception` → `Production` → `Vérification & Push` → `Clôture / Archivé` (repli 2 jalons `Production → Clôture` en mono-dépôt), avec ventilation coûts/tokens par phase.
- **Tarifs Neon/Flash, anomalies & cache froid :** alias `gemini-pro-orchestrator-neon*` et `gemini-flash-worker-agy*` (`2026_nov_standard` / `2027_standard`), marquage `[fallback]` / `unmatched_models` dans `cli.py inspect`, ventilation `ERROR` / `INVALID` / `shell!=0` (`total_anomalies`) et suivi `cold_cache_waste` dans les rapports CLI et Markdown.

## Widget MCP App sobre en 3 espaces (`open_metrics_dashboard`)

- **Thème adaptatif :** hérite de `hostContext.styles.variables` (clair, sombre, Nord) avec repli graphite/zinc neutre, `tabular-nums`, header contextuel 1 ligne (1 `<select>` Projet, toggle `2026 / 2027` masqué si identique), et rendu JS incrémental sans perte d'état.
- **3 espaces :**
  - **`Synthèse` (`overview`) :** 4 KPIs utiles, répartition par projet (`>= 2` projets), courbe triée chronologiquement (`started_at`), barres HTML/CSS par catégorie et 5 dernières sessions.
  - **`Sessions` (`sessions` / `pending` / `compare`) :** sous-onglets `Archivées` (Master-Detail, édition rapide `label`/`project`/`note`, jalons, ventilation workspace), `À archiver` (`En cours` vs `Terminée`, tiroir inline `Archiver…`, `Ignorer` / *Masquées* via `ignored.json`) et `Comparer` (A vs B préservé au polling).
  - **`Audits Agents` (`audits`) :** bouton **`Index & Matrice`** (`README.md` hors liste/compteur/liens session), exclusion stricte de `_TEMPLATE.md`/`_*` et `audits/code/`, filtres verdicts canoniques colorés, lecteur Markdown `74ch` (`h1..h6`, callouts, listes imbriquées).

## Stockage centralisé

Toutes les données vivent sous **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`** — **zéro fichier dans vos projets** et **aucun `.html` statique sur disque** :
- `data/history.jsonl`, `METRICS.md`, `reports/<slug>.{json,md}`
- `session_meta.json`, `active_captures.json`, `ignored.json`

## CLI non-interactif (`scripts/cli.py`)

```bash
python scripts/cli.py list [--regen]
python scripts/cli.py pending [--all] [--bridge-db <chemin>] [--json]
python scripts/cli.py add --latest -l "mon-run" -n "Note" [--update-sealed] [--force]
python scripts/cli.py archive <uuid_ou_prefixe> -l "mon-run" [--bridge-db <chemin>] [--update-sealed] [--force]
python scripts/cli.py inspect <label_ou_uuid> [--bridge-db <chemin>]
python scripts/cli.py remove <label_ou_uuid> --purge-reports
python scripts/validate.py --strict       # Contrôles stdlib, contrat module et seuils D4 (rules <= 600/1000 o, SKILL <= 6000 o, LF sans BOM)
```
