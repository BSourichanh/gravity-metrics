# Plugin Gravity Metrics

Module `metrics` (`config/module.json`, `id: "metrics"`) **100 % bibliothèque standard Python 3** pour collecter, historiser et visualiser en widget **MCP App** les coûts, tokens, taux de cache et signaux de fiabilité des sessions Google Antigravity.

## Positionnement dans l'écosystème Gravity (Lot 4b)

- **[`gravity` Core](https://github.com/BSourichanh/gravity)** : socle d'initialisation (`$GRAVITY_HOME` / `~/.gemini/gravity/`), registre des modules (`config/module.json`) et futur hôte UI unifié.
- **[`gravity-workflow`](https://github.com/BSourichanh/gravity-workflow)** (`/gravity-workflow`) : harnais d'orchestration multi-agents, garde-fous fail-closed, evals et `/caveman`.
- **`gravity-metrics`** (`/gravity-metrics`) : produit les signaux `session-metrics` et `cost-telemetry` destinés aux futures optimisations du mode `auto` de `gravity-workflow`. Son widget MCP App (`ui.mode: "transition-mcp"`, `ui.target: "gravity"`) est appelé à converger vers l'UI unifiée de `gravity` Core.

## Commande `/gravity-metrics`

- `/gravity-metrics [label] [-l|--label <label>] [-n|--note "note"] [--profile <profil>] [--full]` : le hook `PreInvocation` capture la fenêtre courante de la session (du **premier** `/gravity-workflow` ou `/gravity` après la précédente collecte jusqu'à **strictement avant** `/gravity-metrics`, ou toute la conversation avec `--full`), affiche une synthèse inline et déclenche l'ouverture automatique du widget interactif dans le panneau latéral (`detail` sur la session capturée, ou `history` filtré sur le projet actif si aucune nouvelle activité LLM n'est détectée).
- Ouverture manuelle à **0 token** à tout moment depuis **Plugins > Gravity Metrics** (vue globale tous projets ou panneau latéral).

## Widget MCP App (`open_metrics_dashboard`)

- **Statut UI :** transition MCP App (`transition-mcp` -> `gravity`).
- **Points d'entrée :** panneau latéral (`aux_pane`) et pleine page (`global`).
- **Navigation & comparaison :** bascule projet actif / vue globale et comparateur tarifaire `2026` / `Proj. 2027`, avec rafraîchissement automatique toutes les `4s` lorsque le widget est visible.
- **Exploration & actions :** graphiques SVG, détail par catégorie, phase, modèle, agent, gaspillage, outil et erreur, ainsi que les actions intégrées **Ouvrir .md**, **Joindre au prompt**, **Analyser** et **Supprimer** (avec purge optionnelle de `reports/`).

## Stockage centralisé

Toutes les données sont écrites uniquement selon la priorité **`$GRAVITY_METRICS_HOME` > `$GRAVITY_HOME/metrics` > `~/.gemini/gravity/metrics/`** (ou `--store` en CLI) — **zéro fichier créé dans vos projets** et **aucun fichier `.html` statique généré sur disque** :
- `data/history.jsonl` : liste chronologique compacte des sessions (noms de projets harmonisés sans sensibilité à la casse).
- `METRICS.md` : vue Markdown globale multi-sessions.
- `reports/<slug>.{json,md}` : audits détaillés par session.

## CLI non-interactif (`scripts/cli.py`)

```bash
python scripts/cli.py list [--regen]
python scripts/cli.py add --latest -l "mon-run" -n "Note"
python scripts/cli.py add <UUID> --gravity-window --workspace <chemin-projet>
python scripts/cli.py inspect <label_ou_uuid>
python scripts/cli.py remove <label_ou_uuid> --purge-reports
```
