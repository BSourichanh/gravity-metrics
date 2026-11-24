#!/usr/bin/env python3
"""CLI unique pour mesurer, historiser et comparer les sessions Gravity (100 % stdlib)."""

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from metrics import (  # noqa: E402
    PendingBridgeJobsError,
    apply_remove,
    archive_conversation,
    collect_run_metrics,
    default_brain_dir,
    history_lock,
    list_gravity_conversations,
    list_pending_sessions,
    load_history,
    load_session_for_inspect,
    read_gravity_profile_summary,
    record_session_metrics,
    regenerate_views,
    render_inspect_text,
    render_pending_table,
    render_terminal_table,
    resolve_conversation_id_in_brain,
    resolve_store_paths,
)

DEFAULT_CATALOG_PATH = PLUGIN_ROOT / "config" / "pricing_catalog.json"


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _discover_runs_jsonl(workspace: Optional[Path] = None) -> List[Path]:
    candidates = [
        Path.cwd() / ".agents" / "gravity" / "observability" / "runs.jsonl",
        Path.cwd() / "gravity" / "observability" / "runs.jsonl",
    ]
    if workspace is not None:
        candidates.insert(0, workspace / ".agents" / "gravity" / "observability" / "runs.jsonl")
    return [p for p in candidates if p.is_file()]


def _pick_conversation_interactively(
    brain_dir: Path,
    include_all: bool,
    workspace: Optional[Path] = None,
    bridge_db_path: Optional[Path] = None,
) -> Tuple[str, str]:
    convs = list_gravity_conversations(
        brain_dir,
        include_all=include_all,
        runs_jsonl_paths=_discover_runs_jsonl(workspace),
        bridge_db_path=bridge_db_path,
    )
    if not convs:
        scope = "trouvée" if include_all else "liée à Gravity trouvée (essayez --all ou passez un UUID)"
        raise RuntimeError(f"Aucune conversation {scope} dans {brain_dir}")

    print("Sessions récentes disponibles :")
    for idx, item in enumerate(convs[:15], start=1):
        tag = "[Gravity]" if item["is_gravity"] else "[Autre]  "
        date_s = str(item["updated_at"])[:16].replace("T", " ")
        print(f"  {idx:2d}. {date_s} {tag} ({item['conversation_id'][:8]}) {item['title']}")

    raw_choice = input("Numéro de la session à ajouter [1] : ").strip()
    sel = 1 if not raw_choice else int(raw_choice)
    if sel < 1 or sel > min(len(convs), 15):
        raise ValueError(f"Choix hors plage : {sel}")
    chosen = convs[sel - 1]
    return chosen["conversation_id"], chosen["title"]


def _print_archived_summary(
    saved_rec: dict, cid: str, store_dir: Path, *, summary_only: bool
) -> None:
    slug = saved_rec["detail_slug"]
    reports_dir = store_dir / "reports"
    reports_msg = ""
    if not summary_only and not saved_rec.get("already_archived"):
        reports_msg = f"\n     Rapport session : {reports_dir / (slug + '.md')}"

    if saved_rec.get("already_archived"):
        print(
            f"[INFO] Session déjà archivée (immuable) : {saved_rec['label']} ({cid[:8]}) "
            "— utilisez --update-sealed ou --force pour mettre à jour"
        )
    else:
        print(f"[OK] Session archivée : {saved_rec['label']} ({cid[:8]})")
    print(
        f"     Coût: ${saved_rec['cost_usd']:.4f} | "
        f"Agents: {saved_rec['agents']} | Appels: {saved_rec['llm_calls']} | "
        f"Cache: {saved_rec['cache_hit_rate_pct']:.1f}%"
    )
    print(f"     Historique global: {store_dir / 'METRICS.md'}" + reports_msg)


def _cmd_add(args: argparse.Namespace) -> int:
    cid: Optional[str] = args.conversation_id
    label: str = (args.label or "").strip()
    note: str = (args.note or "").strip()
    store_dir, history_path = resolve_store_paths(args.history, args.store)
    ws_path: Optional[Path] = args.workspace.expanduser().resolve() if args.workspace else None
    brain_dir = args.brain_dir.expanduser()
    catalog_path = args.catalog.expanduser()
    bridge_db_path: Optional[Path] = args.bridge_db.expanduser() if args.bridge_db else None

    if cid is None:
        if args.latest:
            convs = list_gravity_conversations(
                brain_dir,
                include_all=args.all,
                runs_jsonl_paths=_discover_runs_jsonl(ws_path),
                bridge_db_path=bridge_db_path,
            )
            if not convs:
                print(
                    f"[ERREUR] Aucune session Gravity trouvée dans {brain_dir}.",
                    file=sys.stderr,
                )
                return 1
            cid = convs[0]["conversation_id"]
            if not label:
                label = f"session-{cid[:8]}"
        else:
            try:
                cid, _title = _pick_conversation_interactively(
                    brain_dir, args.all, ws_path, bridge_db_path
                )
            except (EOFError, KeyboardInterrupt):
                print("\nAnnulé.", file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"[ERREUR] {exc}", file=sys.stderr)
                return 1
            if not label:
                label = input(f"Label de la session [session-{cid[:8]}] : ").strip()
            if not note:
                note = input("Note courte (optionnel) : ").strip()
    else:
        try:
            cid = resolve_conversation_id_in_brain(cid, brain_dir)
        except (ValueError, FileNotFoundError) as exc:
            print(f"[ERREUR] {exc}", file=sys.stderr)
            return 1

    explicit_label = label
    if not label:
        label = f"session-{cid[:8]}"

    project = ws_path.name if ws_path else ""
    gravity_profile = read_gravity_profile_summary(ws_path)
    use_gravity_win = bool(args.gravity_window and not args.full)

    metrics = collect_run_metrics(
        root_id=cid,
        brain_dir=brain_dir,
        pricing_catalog_path=catalog_path,
        pricing_profile=args.profile,
        label=label,
        gravity_window=use_gravity_win,
        full_conversation=bool(args.full),
        project=project,
        gravity_profile=gravity_profile,
        bridge_db_path=bridge_db_path,
    )
    try:
        saved_rec, _ = record_session_metrics(
            metrics,
            store_dir=store_dir,
            history_path=history_path,
            label=explicit_label if args.update_sealed else label,
            note=note,
            summary_only=args.summary_only,
            force=bool(args.force),
            update_sealed=bool(args.update_sealed),
        )
    except PendingBridgeJobsError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    _print_archived_summary(saved_rec, cid, store_dir, summary_only=args.summary_only)
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    store_dir, history_path = resolve_store_paths(args.history, args.store)
    ws_path: Optional[Path] = args.workspace.expanduser().resolve() if args.workspace else None
    brain_dir = args.brain_dir.expanduser()
    catalog_path = args.catalog.expanduser()
    bridge_db_path: Optional[Path] = args.bridge_db.expanduser() if args.bridge_db else None

    project = ws_path.name if ws_path else ""
    gravity_profile = read_gravity_profile_summary(ws_path)
    use_gravity_win = not bool(args.full)

    try:
        saved_rec, _, metrics = archive_conversation(
            args.target,
            store_dir=store_dir,
            history_path=history_path,
            brain_dir=brain_dir,
            catalog_path=catalog_path,
            label=(args.label or "").strip(),
            note=(args.note or "").strip(),
            pricing_profile=args.profile,
            gravity_window=use_gravity_win,
            full_conversation=bool(args.full),
            project=project,
            gravity_profile=gravity_profile,
            summary_only=args.summary_only,
            force=bool(args.force),
            update_sealed=bool(args.update_sealed),
            bridge_db_path=bridge_db_path,
        )
    except (PendingBridgeJobsError, FileNotFoundError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    _print_archived_summary(
        saved_rec,
        metrics["root_conversation_id"],
        store_dir,
        summary_only=args.summary_only,
    )
    return 0


def _cmd_pending(args: argparse.Namespace) -> int:
    _store_dir, history_path = resolve_store_paths(args.history, args.store)
    brain_dir = args.brain_dir.expanduser()
    bridge_db_path: Optional[Path] = args.bridge_db.expanduser() if args.bridge_db else None
    pending = list_pending_sessions(
        brain_dir,
        history_path,
        bridge_db_path=bridge_db_path,
        include_noise=bool(args.all),
    )
    if args.json:
        print(json.dumps(pending, indent=2, ensure_ascii=False))
    else:
        print(render_pending_table(pending))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store_dir, history_path = resolve_store_paths(args.history, args.store)
    if args.regen:
        with history_lock(history_path):
            records = load_history(history_path)
            regenerate_views(store_dir, records)
    else:
        records = load_history(history_path)
    print(render_terminal_table(records))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    store_dir, history_path = resolve_store_paths(args.history, args.store)
    brain_dir = args.brain_dir.expanduser()
    catalog_path = args.catalog.expanduser()
    bridge_db_path: Optional[Path] = args.bridge_db.expanduser() if args.bridge_db else None
    try:
        metrics, _ = load_session_for_inspect(
            args.target,
            store_dir=store_dir,
            history_path=history_path,
            brain_dir=brain_dir,
            catalog_path=catalog_path,
            pricing_profile=args.profile,
            bridge_db_path=bridge_db_path,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1
    print(render_inspect_text(metrics))
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    store_dir, history_path = resolve_store_paths(args.history, args.store)
    try:
        _, remaining = apply_remove(
            args.target,
            store_dir=store_dir,
            history_path=history_path,
            purge_reports=args.purge_reports,
        )
    except (KeyError, ValueError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Session supprimée ({remaining} restante(s)).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Outil CLI de collecte, d'historisation et d'analyse des métriques de sessions Gravity.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Ajoute et archive une session Gravity dans l'historique.")
    p_add.add_argument("conversation_id", nargs="?", default=None, help="UUID (ou préfixe) de la conversation (optionnel).")
    p_add.add_argument("-l", "--label", default="", help="Label lisible de la session.")
    p_add.add_argument("-n", "--note", default="", help="Note libre sur le changement testé.")
    p_add.add_argument("--latest", action="store_true", help="Sélectionne automatiquement la session Gravity la plus récente.")
    p_add.add_argument("--all", action="store_true", help="Inclut aussi les conversations hors Gravity et enfants Bridge dans le menu.")
    p_add.add_argument("--gravity-window", action="store_true", help="Mesure uniquement la fenêtre [début /gravity* → avant /gravity-metrics].")
    p_add.add_argument("--full", action="store_true", help="Mesure l'ensemble de la conversation.")
    p_add.add_argument("--workspace", type=Path, default=None, help="Workspace projet pour lire (en lecture seule) nom et profil Gravity.")
    p_add.add_argument("--profile", default="2026_nov_standard", help="Profil tarifaire (défaut: 2026_nov_standard).")
    p_add.add_argument("--brain-dir", type=Path, default=default_brain_dir(), help="Répertoire des conversations.")
    p_add.add_argument("--bridge-db", type=Path, default=None, help="Chemin explicite vers bridge.db (lecture seule).")
    p_add.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH, help="Catalogue tarifaire JSON.")
    p_add.add_argument("--store", type=Path, default=None, help="Dossier centralisé (défaut: $GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics).")
    p_add.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_add.add_argument("--summary-only", action="store_true", help="N'écrit que dans l'historique global (sans fichiers reports/).")
    p_add.add_argument("--update-sealed", action="store_true", help="Met à jour une fenêtre déjà archivée sans contourner les jobs Bridge actifs.")
    p_add.add_argument("--force", action="store_true", help="Autorise l'écrasement d'une archive existante ou le contournement d'un job Bridge actif.")
    p_add.set_defaults(func=_cmd_add)

    p_arch = sub.add_parser("archive", help="Analyse et archive a posteriori une session Gravity par UUID ou préfixe.")
    p_arch.add_argument("target", help="UUID ou préfixe unique de la conversation.")
    p_arch.add_argument("-l", "--label", default="", help="Label lisible de la session (auto-généré si omis).")
    p_arch.add_argument("-n", "--note", default="", help="Note libre sur le changement testé.")
    p_arch.add_argument("--gravity-window", action="store_true", default=True, help="Mesure la fenêtre Gravity (défaut).")
    p_arch.add_argument("--full", action="store_true", help="Mesure l'ensemble de la conversation.")
    p_arch.add_argument("--workspace", type=Path, default=None, help="Workspace projet pour lire nom et profil Gravity.")
    p_arch.add_argument("--profile", default="2026_nov_standard", help="Profil tarifaire.")
    p_arch.add_argument("--brain-dir", type=Path, default=default_brain_dir(), help="Répertoire des conversations.")
    p_arch.add_argument("--bridge-db", type=Path, default=None, help="Chemin explicite vers bridge.db (lecture seule).")
    p_arch.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH, help="Catalogue tarifaire JSON.")
    p_arch.add_argument("--store", type=Path, default=None, help="Dossier centralisé.")
    p_arch.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_arch.add_argument("--summary-only", action="store_true", help="N'écrit que dans l'historique global.")
    p_arch.add_argument("--update-sealed", action="store_true", help="Met à jour une fenêtre déjà archivée sans contourner les jobs Bridge actifs.")
    p_arch.add_argument("--force", action="store_true", help="Autorise le ré-archivage ou l'archivage avec job Bridge actif.")
    p_arch.set_defaults(func=_cmd_archive)

    p_pend = sub.add_parser("pending", help="Liste les sessions Gravity racines non encore archivées (zéro écriture).")
    p_pend.add_argument("--brain-dir", type=Path, default=default_brain_dir(), help="Répertoire des conversations.")
    p_pend.add_argument("--bridge-db", type=Path, default=None, help="Chemin explicite vers bridge.db (lecture seule).")
    p_pend.add_argument("--store", type=Path, default=None, help="Dossier centralisé.")
    p_pend.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_pend.add_argument("--all", action="store_true", help="Inclut aussi les sessions consultatives sans outil de travail.")
    p_pend.add_argument("--json", action="store_true", help="Affiche la liste au format JSON.")
    p_pend.set_defaults(func=_cmd_pending)

    p_list = sub.add_parser("list", help="Affiche la liste historique des sessions archivées.")
    p_list.add_argument("--store", type=Path, default=None, help="Dossier centralisé (défaut: $GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics).")
    p_list.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_list.add_argument("--regen", action="store_true", help="Régénère METRICS.md.")
    p_list.set_defaults(func=_cmd_list)

    p_insp = sub.add_parser("inspect", help="Affiche le détail complet d'une session.")
    p_insp.add_argument("target", help="Label ou UUID (ou préfixe d'UUID) de la session.")
    p_insp.add_argument("--profile", default="2026_nov_standard", help="Profil tarifaire.")
    p_insp.add_argument("--brain-dir", type=Path, default=default_brain_dir(), help="Répertoire des conversations.")
    p_insp.add_argument("--bridge-db", type=Path, default=None, help="Chemin explicite vers bridge.db (lecture seule).")
    p_insp.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH, help="Catalogue tarifaire JSON.")
    p_insp.add_argument("--store", type=Path, default=None, help="Dossier centralisé (défaut: $GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics).")
    p_insp.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_insp.set_defaults(func=_cmd_inspect)

    p_rm = sub.add_parser("remove", help="Retire une session de l'historique.")
    p_rm.add_argument("target", help="Label ou UUID (ou préfixe) de la session.")
    p_rm.add_argument("--store", type=Path, default=None, help="Dossier centralisé (défaut: $GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics).")
    p_rm.add_argument("--history", type=Path, default=None, help="Chemin explicite vers history.jsonl.")
    p_rm.add_argument(
        "--purge-reports",
        "--purge-details",
        dest="purge_reports",
        action="store_true",
        help="Supprime aussi les rapports détaillés dans reports/.",
    )
    p_rm.set_defaults(func=_cmd_remove)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
