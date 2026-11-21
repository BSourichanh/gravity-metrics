#!/usr/bin/env python3
"""Génération des vues Markdown, chat et terminal des métriques Gravity (100 % stdlib)."""

from pathlib import Path
from typing import Any, Dict, List

from .collector import METRICS_MARKER, slugify_label
from .formatters import (
    _alerts_str,
    _fmt_dur,
    _fmt_int,
    _fmt_short_tok,
    _format_models_summary,
    _session_date_str,
)
from .pricing import CATEGORY_LABELS_FR


def _md_cell(val: Any, fallback: str = "") -> str:
    txt = str(val if val is not None else "")
    cleaned = txt.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    return cleaned or fallback


def _mode_cell_md(rec: Dict[str, Any]) -> str:
    mode = str(rec.get("execution_mode") or "single")
    feats = [str(f) for f in (rec.get("features_used") or []) if f != mode]
    gp = str(rec.get("gravity_profile") or "").strip().replace("|", "/")
    cell = f"`{mode}`" + (f" ({', '.join(feats)})" if feats else "")
    if gp:
        cell += f" · `{gp}`"
    return cell


def _window_desc(win: Dict[str, Any]) -> str:
    wmode = str(win.get("mode") or "full_conversation")
    bef = int(win.get("excluded_before_steps") or 0)
    aft = int(win.get("excluded_after_steps") or 0)
    if wmode == "gravity_session":
        return f"Fenêtre `/gravity` (exclus : {bef} avant, {aft} après)"
    if wmode == "since_last_metrics":
        return f"Depuis dernière collecte (aucun `/gravity` préalable ; exclus : {bef} avant, {aft} après)"
    if wmode == "custom":
        return f"Fenêtre personnalisée (exclus : {bef} avant, {aft} après)"
    return (
        f"Conversation entière (repli — aucun `/gravity` préalable ; exclus : {aft} après)"
        if not win.get("has_gravity_anchor") and aft > 0
        else "Conversation entière"
    )


def render_metrics_markdown(records: List[Dict[str, Any]]) -> str:
    """Génère METRICS.md à partir de la liste historique des sessions."""
    total_cost = sum(float(r.get("cost_usd") or 0.0) for r in records)
    lines: List[str] = [
        "# Gravity Metrics — Historique des Sessions",
        "",
        f"- **Sessions suivies** : **{len(records)}**",
        f"- **Coût cumulé** : **${total_cost:.4f}**",
        "",
    ]

    if not records:
        lines.append("_Aucune session enregistrée. Lancez `/gravity metrics` ou `python scripts/cli.py add`._")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "## 1. Vue d'ensemble, Orchestration & Coûts",
            "",
            "| Date (UTC) | Label | Projet | Mode / Features | Agents (Prof.) | Appels LLM | Coût ($) | Proj. 2027 | Éco. Cache | Cache % | Durée (Parall.) | Détails | Note |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for rec in records:
        slug = rec.get("detail_slug") or slugify_label(
            str(rec.get("label") or ""),
            f"session-{str(rec.get('conversation_id') or '')[:8]}",
        )
        lbl_cell = _md_cell(rec.get("label"), "—")
        proj_cell = _md_cell(rec.get("project"), "—")
        agents_cell = f"{int(rec.get('agents') or 0)} (p.{int(rec.get('max_depth') or 0)})"
        par_f = float(rec.get("parallelism_factor") or 1.0)
        dur_cell = f"{_fmt_dur(rec.get('wall_clock_s') or 0)} ({par_f:.2f}x)"
        note_clean = _md_cell(rec.get("note"))
        details_links = f"[MD](reports/{slug}.md) · [HTML](reports/{slug}.html)"
        lines.append(
            f"| `{_session_date_str(rec)}` | **{lbl_cell}** | {proj_cell} | "
            f"{_mode_cell_md(rec)} | {agents_cell} | {_fmt_int(rec.get('llm_calls'))} | "
            f"${float(rec.get('cost_usd') or 0.0):.4f} | "
            f"${float(rec.get('cost_2027_usd') or 0.0):.4f} | ${float(rec.get('cache_savings_usd') or 0.0):.2f} | "
            f"{float(rec.get('cache_hit_rate_pct') or 0.0):.1f}% | {dur_cell} | {details_links} | {note_clean} |"
        )

    lines.extend(
        [
            "",
            "## 2. Tous les Modèles, Tokens & Fiabilité",
            "",
            "| Label | Modèles utilisés (appels) | Tokens (In / Cache / Out) | Max Ctx | Outils | Erreurs (%) | Inputs Util. | Alertes (Dup/Rw/Int) |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for rec in records:
        lbl_cell = _md_cell(rec.get("label"), "—")
        toks = (
            f"{_fmt_short_tok(rec.get('input_tokens'))} / "
            f"{_fmt_short_tok(rec.get('cache_read_tokens'))} / "
            f"{_fmt_short_tok(rec.get('output_tokens'))}"
        )
        errs_cell = f"{int(rec.get('error_steps') or 0)} ({float(rec.get('error_rate_pct') or 0.0):.2f}%)"
        lines.append(
            f"| **{lbl_cell}** | {_format_models_summary(rec)} | {toks} | "
            f"{_fmt_int(rec.get('max_context_tokens'))} | {_fmt_int(rec.get('tool_calls'))} | "
            f"{errs_cell} | {int(rec.get('user_inputs') or 0)} | `{_alerts_str(rec)}` |"
        )

    lines.extend(
        [
            "",
            "## 3. Répartition du Coût par Catégorie (%)",
            "",
            "| Label | Orchestration % | Découverte % | Build % | Tests E2E % | Vérification % |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for rec in records:
        lbl_cell = _md_cell(rec.get("label"), "—")
        cs = rec.get("category_share_pct") or {}
        lines.append(
            f"| **{lbl_cell}** | {cs.get('orchestration', 0.0):.1f}% | "
            f"{cs.get('discovery', 0.0):.1f}% | {cs.get('build', 0.0):.1f}% | "
            f"{cs.get('testing', 0.0):.1f}% | {cs.get('verification', 0.0):.1f}% |"
        )

    lines.extend(
        [
            "",
            "---",
            "*Commandes rapides : `/gravity metrics` · `/gravity metrics list` · `/gravity metrics inspect <label>`*",
            "",
        ]
    )
    return "\n".join(lines)


def render_terminal_table(records: List[Dict[str, Any]]) -> str:
    """Formate l'historique des sessions en tableau lisible pour le terminal."""
    if not records:
        return "Aucune session enregistrée dans l'historique."
    header = (
        f"{'Date':16} {'Label':24} {'Projet':14} {'Mode':10} {'Modèle':24} "
        f"{'Coût':>9} {'Cache%':>7} {'Ag':>4} {'Calls':>6} {'Durée':>8} {'Alertes':11} Note"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for rec in records:
        lbl = str(rec.get("label") or "")[:24]
        proj = str(rec.get("project") or "-")[:14]
        mode = str(rec.get("execution_mode") or "single")[:10]
        mod = str(rec.get("primary_model") or "")[:24]
        cost_s = f"${float(rec.get('cost_usd') or 0.0):.4f}"
        cache_s = f"{float(rec.get('cache_hit_rate_pct') or 0.0):.1f}%"
        ag_s = str(int(rec.get("agents") or 0))
        calls_s = str(int(rec.get("llm_calls") or 0))
        dur_s = _fmt_dur(rec.get("wall_clock_s") or 0)
        al_s = _alerts_str(rec)
        note_s = str(rec.get("note") or "")[:36]
        lines.append(
            f"{_session_date_str(rec):16} {lbl:24} {proj:14} {mode:10} {mod:24} "
            f"{cost_s:>9} {cache_s:>7} {ag_s:>4} {calls_s:>6} {dur_s:>8} {al_s:11} {note_s}"
        )
    lines.append(sep)
    lines.append(f"Total : {len(records)} session(s)")
    return "\n".join(lines)


def render_inspect_text(metrics: Dict[str, Any]) -> str:
    """Formate l'inspection détaillée d'une session pour le terminal."""
    s = metrics["summary"]
    c = s["cost_usd"]
    p = s.get("cost_usd_projection") or {}
    eff = metrics["efficiency_and_waste"]
    dup = eff["duplicate_role_spawns"]
    rw = eff["rewind_waste"]
    intr = eff["interrupted_agents"]
    cb = eff["context_bloat"]
    win = metrics.get("window") or {}

    lines = [
        f"=== Inspection Gravity [{metrics['label']}] ({metrics['root_conversation_id']}) ===",
    ]
    if metrics.get("project") or metrics.get("gravity_profile"):
        lines.append(
            f"Projet    : {metrics.get('project') or '-'} | Profil Gravity : {metrics.get('gravity_profile') or '-'}"
        )
    lines.extend(
        [
            f"Mode      : {s.get('execution_mode', 'single')} (features: {', '.join(s.get('features_used') or ['single'])})",
            f"Fenêtre   : {s['first_ts']} -> {s['last_ts']} ({_fmt_dur(s['wall_clock_s'])}, parallélisme {s['parallelism_factor']:.2f}x) [{_window_desc(win)}]",
            f"Activité  : {s['total_agents']} agents (prof. {s['max_tree_depth']}) | {s['total_llm_calls']:,} appels LLM | {s['total_tool_calls']:,} outils | {s['total_error_steps']} erreurs ({s['error_rate_pct']:.2f}%)",
            f"Tokens    : in={s['input_tokens']:,} | cache={s['cache_read_tokens']:,} ({s['cache_hit_rate_pct']:.2f}%) | out={s['output_tokens']:,}",
            f"Coût      : ${c['total_usd']:.4f} (in ${c['input_usd']:.4f} + cache ${c['cache_read_usd']:.4f} + out ${c['output_usd']:.4f}) | économie cache ${c['cache_savings_usd']:.4f}",
            f"Proj 2027 : ${p.get('total_usd', 0.0):.4f}",
            f"Alertes   : doublons={dup['duplicate_agent_count']} (${dup['duplicate_cost_usd']:.4f}) | rewinds={rw['rewind_calls']} (${rw['rewind_cost_usd']:.4f}) | interrompus={intr['count']} (${intr['total_cost_usd']:.4f}) | maxCtx={cb['max_context_tokens_overall']:,}",
            "",
            "--- Par catégorie ---",
        ]
    )
    for cat, data in metrics["by_category"].items():
        if data["agents"] == 0:
            continue
        lines.append(
            f"  {cat:14s} : {data['agents']:2d} ag | {data['llm_calls']:4d} calls | "
            f"${data['cost_usd']['total_usd']:7.4f} ({data['cost_share_pct']:5.1f}%)"
        )

    lines.append("")
    lines.append("--- Par modèle ---")
    for m_name, m_data in metrics["by_model"].items():
        lines.append(
            f"  {m_name:32s} : {m_data['calls']:4d} calls | "
            f"${m_data['cost_usd']['total_usd']:7.4f} ({m_data['cost_share_pct']:5.1f}%)"
        )

    lines.append("")
    lines.append("--- Top 10 agents par coût ---")
    for idx, ag in enumerate(metrics["agents"][:10], start=1):
        st = "OK" if ag["clean_termination"] else "INTERRUPTED"
        lines.append(
            f"  {idx:2d}. {ag['role'][:34]:34s} [{ag['category']:13s}] "
            f"{ag['llm_calls']:3d} calls | maxCtx={ag['max_context_tokens']:7,d} | "
            f"${ag['cost_usd']['total_usd']:.4f} ({st})"
        )
    return "\n".join(lines)


def render_session_detail_markdown(metrics: Dict[str, Any]) -> str:
    """Génère le rapport Markdown détaillé d'une session dans reports/."""
    s = metrics["summary"]
    c = s["cost_usd"]
    p = s.get("cost_usd_projection") or {}
    eff = metrics["efficiency_and_waste"]
    dq = metrics["data_quality"]
    tu = metrics.get("tool_usage") or {"total_calls": 0, "by_tool": {}, "top_errors": []}
    win = metrics.get("window") or {}

    lines: List[str] = [
        f"# Détail Session Gravity — `{metrics['label']}`",
        "",
        "[← Retour aux métriques globales](../METRICS.md)",
        "",
        f"- **Conversation racine** : `{metrics['root_conversation_id']}`",
    ]
    if metrics.get("project"):
        lines.append(f"- **Projet** : `{metrics['project']}`")
    if metrics.get("gravity_profile"):
        lines.append(f"- **Profil Gravity** : `{metrics['gravity_profile']}`")
    proj_total = float(p.get("total_usd", 0.0))
    proj_delta = proj_total - float(c["total_usd"])
    delta_sign = "+" if proj_delta >= 0 else "-"
    lines.extend(
        [
            f"- **Fenêtre mesurée** : {_window_desc(win)}",
            f"- **Profil tarifaire** : `{metrics['pricing_profile']}` (projection : `{metrics.get('secondary_pricing_profile') or 'N/A'}`)",
            f"- **Fenêtre UTC** : `{s['first_ts']}` → `{s['last_ts']}` (**{_fmt_dur(s['wall_clock_s'])}** muraux)",
            "",
            "## 1. Synthèse & Coûts",
            "",
            "| Indicateur | Valeur | Détail |",
            "|---|---:|---|",
            f"| **Coût total ({metrics['pricing_profile']})** | **${c['total_usd']:.4f}** | Input ${c['input_usd']:.4f} · Cache ${c['cache_read_usd']:.4f} · Output ${c['output_usd']:.4f} |",
            f"| **Projection ({metrics.get('secondary_pricing_profile') or '2027_standard'})** | **${proj_total:.4f}** | Écart : **{delta_sign}${abs(proj_delta):.4f}** |",
            f"| **Économie grâce au cache** | **${c['cache_savings_usd']:.4f}** | Sans cache : ${c['without_cache_usd']:.4f} (Cache Hit : **{s['cache_hit_rate_pct']:.2f}%**) |",
            f"| **Agents / Profondeur max** | **{s['total_agents']}** / {s['max_tree_depth']} | Parallélisme : **{s['parallelism_factor']:.2f}x** (actif : {_fmt_dur(s['sum_agent_active_s'])}) |",
            f"| **Appels LLM / Appels outils** | **{_fmt_int(s['total_llm_calls'])}** / **{_fmt_int(s['total_tool_calls'])}** | Erreurs : {s['total_error_steps']} ({s['error_rate_pct']:.2f}%) · Inputs utilisateur : {s['total_user_inputs']} |",
            f"| **Tokens (In / Cache / Out)** | **{_fmt_int(s['total_tokens'])}** | {_fmt_int(s['input_tokens'])} / {_fmt_int(s['cache_read_tokens'])} / {_fmt_int(s['output_tokens'])} |",
            "",
            "## 2. Répartition par Catégorie",
            "",
            "| Catégorie | Agents | Appels | Input | Cache Read | Output | Coût USD | Part |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for cat, data in metrics["by_category"].items():
        if data["agents"] == 0:
            continue
        lines.append(
            f"| {CATEGORY_LABELS_FR.get(cat, cat)} | {data['agents']} | {_fmt_int(data['llm_calls'])} | "
            f"{_fmt_int(data['input_tokens'])} | {_fmt_int(data['cache_read_tokens'])} | "
            f"{_fmt_int(data['output_tokens'])} | ${data['cost_usd']['total_usd']:.4f} | **{data['cost_share_pct']:.2f}%** |"
        )

    lines.extend(
        [
            "",
            "## 3. Répartition par Modèle",
            "",
            "| Modèle | Agents | Appels | Input | Cache Read | Output | Coût 2026 | Proj 2027 | Part |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for m_name, m_data in metrics["by_model"].items():
        fb = " *(fallback)*" if m_data["is_fallback"] else ""
        lines.append(
            f"| `{m_name}`{fb} | {m_data['agents_using']} | {_fmt_int(m_data['calls'])} | "
            f"{_fmt_int(m_data['input_tokens'])} | {_fmt_int(m_data['cache_read_tokens'])} | "
            f"{_fmt_int(m_data['output_tokens'])} | ${m_data['cost_usd']['total_usd']:.4f} | "
            f"${m_data['cost_usd_projection']['total_usd']:.4f} | {m_data['cost_share_pct']:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## 4. Chronologie & Coût par Phase",
            "",
            "| Phase | Fenêtre UTC | Durée | Agents | Appels | Coût USD | Part |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for pname, pdata in (metrics.get("by_phase") or {}).items():
        t0_s = (pdata.get("first_ts") or "")[11:19]
        t1_s = (pdata.get("last_ts") or "")[11:19]
        pname_cell = _md_cell(pname, "—")
        lines.append(
            f"| {pname_cell} | `{t0_s}`–`{t1_s}` | {_fmt_dur(pdata.get('wall_span_s') or 0)} | "
            f"{pdata['agents']} | {_fmt_int(pdata['llm_calls'])} | "
            f"${pdata['cost_usd']['total_usd']:.4f} | {pdata['cost_share_pct']:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## 5. Top 15 Agents par Coût",
            "",
            "| Rang | Rôle | Catégorie | Appels | Max Ctx | Input | Cache Read | Output | Coût USD | Statut |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for idx, ag in enumerate(metrics["agents"][:15], start=1):
        term = "Propre" if ag["clean_termination"] else "Interrompu"
        role_cell = _md_cell(ag.get("role"), "—")
        lines.append(
            f"| {idx} | {role_cell} (`{ag['conversation_id'][:8]}`) | {ag['category']} | "
            f"{ag['llm_calls']} | {_fmt_int(ag['max_context_tokens'])} | "
            f"{_fmt_int(ag['input_tokens'])} | {_fmt_int(ag['cache_read_tokens'])} | "
            f"{_fmt_int(ag['output_tokens'])} | **${ag['cost_usd']['total_usd']:.4f}** | {term} |"
        )

    dup = eff["duplicate_role_spawns"]
    rw = eff["rewind_waste"]
    intr = eff["interrupted_agents"]
    cb = eff["context_bloat"]
    lines.extend(
        [
            "",
            "## 6. Diagnostic & Gaspillages",
            "",
            f"- **Ratio Vérification / (Build + Test)** : **{eff.get('verification_to_build_cost_ratio', 0.0):.2f}x**",
            f"- **Doublons de rôles** : **{dup['duplicate_agent_count']}** ({dup['duplicate_calls']} appels, **${dup['duplicate_cost_usd']:.4f}**)",
            f"- **Rewinds / reprises quota** : **{rw['rewind_calls']}** appels (**${rw['rewind_cost_usd']:.4f}**)",
            f"- **Agents interrompus** : **{intr['count']}** ({intr['total_llm_calls']} appels, **${intr['total_cost_usd']:.4f}**)",
            f"- **Contexte max / >100k / >128k / >200k** : **{_fmt_int(cb['max_context_tokens_overall'])}** / {cb.get('calls_over_100k_ctx', 0)} / {cb.get('calls_over_128k_ctx', 0)} / {cb.get('calls_over_200k_ctx', 0)}",
        ]
    )
    if dup.get("groups"):
        lines.extend(["", "### Détail des vagues dupliquées", ""])
        for g in dup["groups"]:
            lines.append(
                f"- `{g['role']}` (`{g['type_name']}`) sous `{g['parent_role']}` : "
                f"{g['spawn_count']} spawns ({g['duplicate_count']} doublon(s), {g['duplicate_calls']} appels, ${g['duplicate_cost_usd']:.4f})"
            )

    lines.extend(["", "## 7. Outillage & Top Erreurs", "", "| Outil | Appels |", "|---|---:|"])
    for tname, cnt in list((tu.get("by_tool") or {}).items())[:12]:
        lines.append(f"| `{tname}` | {_fmt_int(cnt)} |")
    if tu.get("top_errors"):
        lines.extend(["", "### Erreurs principales", ""])
        for er in tu["top_errors"][:8]:
            lines.append(f"- ({er['count']}×) `{er['reason']}`")

    lines.extend(
        [
            "",
            "## 8. Qualité des Données",
            "",
            f"- Modèles fallback : `{dq['unmatched_models'] or 'aucun'}`",
            f"- Transcripts manquants : `{dq['missing_transcripts'] or 'aucun'}`",
            f"- Lignes JSON malformées : `{dq['malformed_lines']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _file_link(label: str, path: Path) -> str:
    return f"[{label}]({path.resolve().as_uri()})"


def render_chat_summary_markdown(
    metrics: Dict[str, Any],
    saved_rec: Dict[str, Any],
    store_dir: Path,
    *,
    reports_written: bool = True,
    html_written: bool = True,
) -> str:
    """Formate la synthèse compacte affichée dans le chat après `/gravity metrics` (0 token)."""
    s = metrics["summary"]
    c = s["cost_usd"]
    win = metrics.get("window") or {}
    slug = saved_rec["detail_slug"]

    meta_parts = []
    if saved_rec.get("project"):
        meta_parts.append(f"Projet : `{saved_rec['project']}`")
    if saved_rec.get("gravity_profile"):
        meta_parts.append(f"Profil Gravity : `{saved_rec['gravity_profile']}`")
    meta_parts.append(_window_desc(win))

    lines = [
        METRICS_MARKER,
        f"### Gravity Metrics — `{saved_rec['label']}` enregistrée *(0 token)*",
        "",
        " · ".join(meta_parts),
        "",
        "| Indicateur | Valeur | Détail |",
        "|---|---|---|",
        f"| **Coût USD (`{metrics['pricing_profile']}`)** | **${c['total_usd']:.4f}** | Input `${c['input_usd']:.4f}` · Cache `${c['cache_read_usd']:.4f}` · Output `${c['output_usd']:.4f}` |",
        f"| **Économie Cache / Proj. 2027** | **${c['cache_savings_usd']:.4f}** / **${saved_rec['cost_2027_usd']:.4f}** | Coût sans cache : `${c['without_cache_usd']:.4f}` |",
        f"| **Cache Hit %** | **{s['cache_hit_rate_pct']:.2f}%** | `{_fmt_int(s['input_tokens'])}` in · `{_fmt_int(s['cache_read_tokens'])}` cache · `{_fmt_int(s['output_tokens'])}` out |",
        f"| **Orchestration** | **{s['total_agents']}** agent(s) · **{_fmt_int(s['total_llm_calls'])}** appels LLM | Mode `{s['execution_mode']}` · prof. `{s['max_tree_depth']}` · `{_fmt_dur(s['wall_clock_s'])}` (`{s['parallelism_factor']:.2f}x`) |",
        f"| **Modèles** | {_format_models_summary(saved_rec)} | Erreurs : `{s['total_error_steps']}` (`{s['error_rate_pct']:.2f}%`) · Max Ctx : `{_fmt_int(saved_rec['max_context_tokens'])}` |",
        f"| **Alertes (`Dup / Rw / Int`)** | **`{_alerts_str(saved_rec)}`** | `D` doublons · `R` rewinds · `I` interrompus |",
        "",
    ]

    links = [_file_link("METRICS.md", store_dir / "METRICS.md")]
    if html_written and (store_dir / "dashboard.html").is_file():
        links.append(_file_link("dashboard.html", store_dir / "dashboard.html"))
    if reports_written:
        links.append(_file_link(f"{slug}.md", store_dir / "reports" / f"{slug}.md"))
        if html_written:
            links.append(_file_link(f"{slug}.html", store_dir / "reports" / f"{slug}.html"))
    lines.append("**Rapports :** " + " · ".join(links))
    return "\n".join(lines)


def render_chat_list_markdown(records: List[Dict[str, Any]], store_dir: Path) -> str:
    """Formate l'historique complet en Markdown pour `/gravity metrics list`."""
    if not records:
        return (
            "### Gravity Metrics — Historique vide\n\n"
            "Aucune session enregistrée. Lancez `/gravity metrics` à la fin d'une session."
        )
    lines = [
        f"### Gravity Metrics — Historique ({len(records)} session(s))",
        "",
        "| Date (UTC) | Label | Projet | Mode | Coût ($) | Cache % | Agents / Appels | Alertes |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for rec in records:
        lbl_cell = _md_cell(rec.get("label"), "—")
        proj = _md_cell(rec.get("project"), "—")
        lines.append(
            f"| `{_session_date_str(rec)}` | **{lbl_cell}** | `{proj}` | "
            f"{_mode_cell_md(rec)} | ${float(rec.get('cost_usd') or 0.0):.4f} | "
            f"{float(rec.get('cache_hit_rate_pct') or 0.0):.1f}% | "
            f"{int(rec.get('agents') or 0)} / {_fmt_int(rec.get('llm_calls'))} | `{_alerts_str(rec)}` |"
        )
    links = [_file_link("METRICS.md", store_dir / "METRICS.md")]
    if (store_dir / "dashboard.html").is_file():
        links.append(_file_link("dashboard.html", store_dir / "dashboard.html"))
    lines.extend(["", "**Vues globales :** " + " · ".join(links)])
    return "\n".join(lines)


def render_chat_inspect_markdown(
    metrics: Dict[str, Any], store_dir: Path, detail_slug: str = ""
) -> str:
    """Formate l'inspection d'une session en Markdown pour `/gravity metrics inspect`."""
    s = metrics["summary"]
    c = s["cost_usd"]
    eff = metrics["efficiency_and_waste"]
    dup = eff["duplicate_role_spawns"]
    rw = eff["rewind_waste"]
    intr = eff["interrupted_agents"]
    cb = eff["context_bloat"]
    win = metrics.get("window") or {}

    lines = [
        f"### Inspection — `{metrics['label']}` (`{metrics['root_conversation_id'][:8]}`)",
        "",
        f"- **Projet / Profil** : `{metrics.get('project') or '—'}` / `{metrics.get('gravity_profile') or '—'}`",
        f"- **Mode / Fenêtre** : `{s.get('execution_mode', 'single')}` · {_window_desc(win)} · `{_fmt_dur(s['wall_clock_s'])}` (`{s['parallelism_factor']:.2f}x`)",
        f"- **Coût** : **${c['total_usd']:.4f}** (in `${c['input_usd']:.4f}` · cache `${c['cache_read_usd']:.4f}` · out `${c['output_usd']:.4f}`) · Cache Hit **{s['cache_hit_rate_pct']:.2f}%**",
        f"- **Activité** : **{s['total_agents']}** agents (prof. `{s['max_tree_depth']}`) · **{_fmt_int(s['total_llm_calls'])}** appels LLM · `{_fmt_int(s['total_tool_calls'])}` outils · `{s['total_error_steps']}` erreurs (`{s['error_rate_pct']:.2f}%`)",
        f"- **Alertes** : `D:{dup['duplicate_agent_count']} R:{rw['rewind_calls']} I:{intr['count']}` · Max Ctx : `{_fmt_int(cb['max_context_tokens_overall'])}`",
        "",
        "| Modèle | Appels | Coût USD | Part |",
        "|---|---:|---:|---:|",
    ]
    for m_name, m_data in metrics["by_model"].items():
        lines.append(
            f"| `{m_name}` | {_fmt_int(m_data['calls'])} | ${m_data['cost_usd']['total_usd']:.4f} | {m_data['cost_share_pct']:.1f}% |"
        )

    slug = detail_slug or slugify_label(
        str(metrics.get("label") or ""),
        f"session-{metrics['root_conversation_id'][:8]}",
    )
    links = [_file_link("METRICS.md", store_dir / "METRICS.md")]
    rep_md = store_dir / "reports" / f"{slug}.md"
    rep_html = store_dir / "reports" / f"{slug}.html"
    if rep_md.is_file():
        links.append(_file_link(f"{slug}.md", rep_md))
    if rep_html.is_file():
        links.append(_file_link(f"{slug}.html", rep_html))
    lines.extend(["", "**Rapports :** " + " · ".join(links)])
    return "\n".join(lines)
