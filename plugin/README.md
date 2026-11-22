# Plugin Gravity Metrics

Module `metrics` (`config/module.json`, `id: "metrics"`) **100 % bibliothèque standard Python 3** pour collecter, archiver et visualiser en widget **MCP App** les coûts, tokens, taux de cache et signaux de fiabilité des sessions Google Antigravity unifiées (arbre natif + enfants Gravity Bridge).

## Positionnement dans l'écosystème Gravity (Lot 4b)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** : socle d'initialisation (`$GRAVITY_HOME` / `~/.gemini/gravity/`), registre des modules (`config/module.json`) et futur hôte UI unifié.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`) : harnais d'orchestration multi-agents, garde-fous fail-closed, evals et `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`) : produit les signaux `session-metrics` et `cost-telemetry` destinés aux futures optimisations du mode `auto` de `gravity-workflow`. Son widget MCP App (`ui.mode: "transition-mcp"`, `ui.target: "gravity"`) est appelé à converger vers l'UI unifiée de `gravity` Core.

## Commande `/gravity-metrics` & Cycle de vie

- `/gravity-metrics [label] [-l|--label <label>] [-n|--note "note"] [--profile <profil>] [--full] [--force]` : le hook `PreInvocation` capture la session Gravity unifiée (du **premier** `/gravity`, `/gravity-workflow`, `/gravity-architect` ou `/gravity-bridge` après la précédente collecte jusqu'à **strictement avant** `/gravity-metrics`, en incluant les sous-agents natifs et les enfants **Gravity Bridge** en `depth 2+` via `bridge.db` en lecture seule + repli transcript), l'archive de façon immuable (`schema_version 2.1.0`, bloqué sauf `--force` si un job Bridge est actif) et ouvre le widget sur `detail`.
- `/gravity-metrics archive <uuid_ou_prefixe> [label] [-n "note"] [--full] [--force]` : analyse et archive *a posteriori* une session existante.
- `/gravity-metrics pending` : affiche la file des sessions Gravity racines non encore archivées (zéro écriture, enfants Bridge exclus) et ouvre le widget.
- Les commandes combinées (`/gravity-metrics /autre-cmd ...`) et textes libres longs (`> 60` car. ou `> 6` mots sans `-l`/`--label`) passent directement en *passthrough* sans capture.
- Ouverture manuelle à **0 token** à tout moment depuis **Plugins > Gravity Metrics** (vue globale tous projets ou panneau latéral).

## Widget MCP App (`open_metrics_dashboard`)

- **Statut UI :** transition MCP App (`transition-mcp` -> `gravity`).
- **Points d'entrée :** panneau latéral (`aux_pane`) et pleine page (`global`).
- **Navigation & archivage :** bascule projet actif / vue globale, comparateur tarifaire `2026` / `Proj. 2027`, file des sessions `pending` avec bouton **Archiver** (`metrics_archive_session`), rafraîchissement automatique toutes les `4s` lorsque le widget est visible.
- **Exploration & actions :** graphiques SVG, ventilation par catégorie, phase, **workspace** (`by_workspace`), **arbre Bridge inter-workspaces**, modèle, agent, gaspillage, outil et erreur, ainsi que **Ouvrir .md**, **Joindre au prompt**, **Analyser** et **Supprimer** (avec purge optionnelle de `reports/`).

## Stockage centralisé

Toutes les données sont écrites uniquement selon la priorité **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`** (ou `--store` en CLI) — **zéro fichier créé dans vos projets** et **aucun fichier `.html` statique généré sur disque** :
- `data/history.jsonl` : liste chronologique immuable des sessions archivées (`status: "archived"`, `workspace_share_pct`, `bridge_children_count`, `bridge_workspaces`).
- `METRICS.md` : vue Markdown globale multi-sessions (incluant la ventilation par workspace Bridge).
- `reports/<slug>.{json,md}` : audits détaillés par session (`by_workspace`, arbre Bridge).

## CLI non-interactif (`scripts/cli.py`)

```bash
python scripts/cli.py list [--regen]
python scripts/cli.py pending [--bridge-db <chemin>] [--json]
python scripts/cli.py add --latest -l "mon-run" -n "Note" [--force]
python scripts/cli.py archive <uuid_ou_prefixe> -l "mon-run" [--bridge-db <chemin>] [--force]
python scripts/cli.py inspect <label_ou_uuid> [--bridge-db <chemin>]
python scripts/cli.py remove <label_ou_uuid> --purge-reports
```
