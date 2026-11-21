#!/usr/bin/env python3
"""Génération des dashboards HTML/SVG autonomes anti-XSS (100 % stdlib)."""

import html
from typing import Any, Dict, List, Tuple

from .collector import slugify_label
from .formatters import _fmt_dur, _fmt_int, _fmt_short_tok, _session_date_str
from .pricing import CATEGORY_LABELS_FR


def _render_cost_evolution_svg(
    records: List[Dict[str, Any]], width: int = 760, height: int = 230
) -> str:
    if not records:
        return "<p>Aucune session enregistrée.</p>"
    pad_l, pad_r, pad_t, pad_b = 58, 28, 22, 46
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    costs = [float(r.get("cost_usd") or 0.0) for r in records]
    max_y = max(max(costs, default=1.0), 0.01) * 1.15

    def _x(idx: int) -> float:
        if len(records) == 1:
            return pad_l + plot_w / 2.0
        return pad_l + (idx / (len(records) - 1)) * plot_w

    def _y(val: float) -> float:
        return pad_t + plot_h - (val / max_y) * plot_h

    parts: List[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Évolution du coût USD">'
    ]
    for step in range(4):
        frac = step / 3.0
        yv = max_y * (1.0 - frac)
        yp = _y(yv)
        parts.append(f'<line x1="{pad_l}" y1="{yp:.1f}" x2="{width - pad_r}" y2="{yp:.1f}" stroke="#1e293b" stroke-width="1"/>')
        parts.append(
            f'<text x="{pad_l - 8}" y="{yp + 4:.1f}" text-anchor="end" fill="#94a3b8" '
            f'font-size="11" font-family="ui-monospace, monospace">${yv:.2f}</text>'
        )

    if len(records) > 1:
        pts = " ".join(f"{_x(i):.1f},{_y(c):.1f}" for i, c in enumerate(costs))
        parts.append(f'<polyline fill="none" stroke="#38bdf8" stroke-width="2.5" points="{pts}"/>')

    for i, rec in enumerate(records):
        cx, cy = _x(i), _y(costs[i])
        lbl = str(rec.get("label") or "")
        short_lbl = lbl if len(lbl) <= 14 else lbl[:12] + ".."
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="#38bdf8" stroke="#0f172a" stroke-width="2"/>')
        parts.append(
            f'<text x="{cx:.1f}" y="{cy - 10:.1f}" text-anchor="middle" fill="#f8fafc" '
            f'font-size="11" font-weight="600" font-family="ui-monospace, monospace">${costs[i]:.2f}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 14}" text-anchor="middle" fill="#cbd5e1" '
            f'font-size="11" font-family="system-ui, sans-serif">{html.escape(short_lbl, quote=True)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _render_category_stacked_svg(records: List[Dict[str, Any]], width: int = 760) -> str:
    if not records:
        return "<p>Aucune donnée.</p>"
    cat_cols = [
        ("orchestration", "Orchestration", "#38bdf8"),
        ("discovery", "Découverte", "#a78bfa"),
        ("build", "Build", "#34d399"),
        ("testing", "Tests", "#fbbf24"),
        ("verification", "Vérification", "#f87171"),
    ]
    row_h = 28
    label_w = 170
    bar_w = width - label_w - 24
    legend_h = 26
    height = legend_h + len(records) * row_h + 12

    parts: List[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Répartition par catégorie">'
    ]
    lx = 6
    for _, name, col in cat_cols:
        parts.append(f'<rect x="{lx}" y="4" width="10" height="10" rx="2" fill="{col}"/>')
        parts.append(
            f'<text x="{lx + 14}" y="13" fill="#cbd5e1" font-size="11" '
            f'font-family="system-ui, sans-serif">{html.escape(name, quote=True)}</text>'
        )
        lx += 110

    for idx, rec in enumerate(records):
        y = legend_h + idx * row_h
        lbl = str(rec.get("label") or "")
        short_lbl = lbl if len(lbl) <= 22 else lbl[:19] + "..."
        parts.append(
            f'<text x="4" y="{y + 16}" fill="#cbd5e1" font-size="12" '
            f'font-family="system-ui, sans-serif">{html.escape(short_lbl, quote=True)}</text>'
        )
        cs = rec.get("category_share_pct") or {}
        x_cur = float(label_w)
        for cat_key, _, col in cat_cols:
            pct = max(0.0, float(cs.get(cat_key) or 0.0))
            seg_w = (pct / 100.0) * bar_w
            if seg_w > 0.5:
                parts.append(f'<rect x="{x_cur:.1f}" y="{y + 4}" width="{seg_w:.1f}" height="16" fill="{col}"/>')
                x_cur += seg_w

    parts.append("</svg>")
    return "\n".join(parts)


def render_history_dashboard_html(records: List[Dict[str, Any]]) -> str:
    """Génère dashboard.html multi-sessions autonome (sans CDN externe, anti-XSS)."""
    latest = records[-1] if records else None
    best = min(records, key=lambda r: float(r.get("cost_usd") or 0.0)) if records else None

    latest_cost = float(latest.get("cost_usd") or 0.0) if latest else 0.0
    total_cost = sum(float(r.get("cost_usd") or 0.0) for r in records)
    avg_cost = (total_cost / len(records)) if records else 0.0
    avg_cache = (
        sum(float(r.get("cache_hit_rate_pct") or 0.0) for r in records) / len(records)
        if records
        else 0.0
    )

    rows_html: List[str] = []
    for rec in records:
        dups = int(rec.get("duplicate_spawns") or 0)
        rws = int(rec.get("rewind_calls") or 0)
        ints = int(rec.get("interrupted_agents") or 0)
        alerts_parts: List[str] = []
        if dups:
            alerts_parts.append(f"Dup:{dups}")
        if rws:
            alerts_parts.append(f"Rw:{rws}")
        if ints:
            alerts_parts.append(f"Int:{ints}")
        alerts_html = (
            " ".join(f'<span class="flag">{html.escape(a, quote=True)}</span>' for a in alerts_parts)
            if alerts_parts
            else '<span class="ok">Clean</span>'
        )
        tok_txt = (
            f"{_fmt_short_tok(rec.get('input_tokens'))} / "
            f"{_fmt_short_tok(rec.get('cache_read_tokens'))} / "
            f"{_fmt_short_tok(rec.get('output_tokens'))}"
        )
        slug = rec.get("detail_slug") or slugify_label(
            str(rec.get("label") or ""),
            f"session-{str(rec.get('conversation_id') or '')[:8]}",
        )
        esc_slug = html.escape(slug, quote=True)
        detail_cell = (
            f'<a class="dlink" href="reports/{esc_slug}.html">Dashboard</a> · '
            f'<a class="dlink" href="reports/{esc_slug}.md">MD</a>'
        )
        proj_txt = html.escape(str(rec.get("project") or "—"), quote=True)
        mode = str(rec.get("execution_mode") or "single")
        feats = [str(f) for f in (rec.get("features_used") or []) if f != mode]
        gp = str(rec.get("gravity_profile") or "").strip()
        sub_parts: List[str] = []
        if feats:
            sub_parts.append(", ".join(feats))
        if gp:
            sub_parts.append(gp)
        mode_cell = f'<code>{html.escape(mode, quote=True)}</code>' + (
            f'<div class="cid">{html.escape(" · ".join(sub_parts), quote=True)}</div>'
            if sub_parts
            else ""
        )
        models_dict = rec.get("models") or {}
        if models_dict:
            m_sorted = sorted(
                models_dict.items(),
                key=lambda kv: (-int(kv[1].get("calls") or 0), kv[0]),
            )
            m_main = f"<code>{html.escape(m_sorted[0][0], quote=True)}</code> ({int(m_sorted[0][1].get('calls') or 0)})"
            m_extra = ", ".join(
                f"{m_k} ({int(m_v.get('calls') or 0)})" for m_k, m_v in m_sorted[1:]
            )
            models_cell = m_main + (f'<div class="cid">{html.escape(m_extra, quote=True)}</div>' if m_extra else "")
        else:
            models_cell = f"<code>{html.escape(str(rec.get('primary_model') or ''), quote=True)}</code>"
        rows_html.append(
            "<tr>"
            f"<td><code>{html.escape(_session_date_str(rec), quote=True)}</code></td>"
            f"<td><strong>{html.escape(str(rec.get('label') or ''), quote=True)}</strong>"
            f"<div class=\"cid\">{html.escape(str(rec.get('conversation_id') or '')[:8], quote=True)}</div></td>"
            f"<td><code>{proj_txt}</code></td>"
            f"<td>{mode_cell}</td>"
            f"<td>{models_cell}</td>"
            f"<td class=\"num\"><strong>${float(rec.get('cost_usd') or 0.0):.4f}</strong></td>"
            f"<td class=\"num\">{html.escape(tok_txt, quote=True)}</td>"
            f"<td class=\"num\">{float(rec.get('cache_hit_rate_pct') or 0.0):.1f}%</td>"
            f"<td class=\"num\">{int(rec.get('agents') or 0)}</td>"
            f"<td class=\"num\">{_fmt_int(rec.get('llm_calls'))}</td>"
            f"<td class=\"num\">{html.escape(_fmt_dur(rec.get('wall_clock_s') or 0), quote=True)}</td>"
            f"<td>{alerts_html}</td>"
            f"<td>{detail_cell}</td>"
            f"<td>{html.escape(str(rec.get('note') or ''), quote=True)}</td>"
            "</tr>"
        )

    latest_lbl = html.escape(str(latest.get("label") if latest else "—"), quote=True)
    best_lbl = html.escape(str(best.get("label") if best else "—"), quote=True)
    best_cost = float(best.get("cost_usd") or 0.0) if best else 0.0

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gravity Metrics — Historique ({len(records)} sessions)</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:24px; background:#0f172a; color:#e2e8f0; font-family:system-ui,-apple-system,sans-serif; line-height:1.5; }}
  .wrap {{ max-width:1380px; margin:0 auto; }}
  h1 {{ margin:0 0 6px; font-size:24px; color:#f8fafc; }}
  h2 {{ margin:0 0 12px; font-size:16px; color:#93c5fd; border-bottom:1px solid #1e293b; padding-bottom:6px; }}
  .meta {{ color:#94a3b8; font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
  .card {{ background:#111c33; border:1px solid #1e293b; border-radius:10px; padding:14px 16px; }}
  .card .k {{ font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:.04em; }}
  .card .v {{ font-size:24px; font-weight:700; color:#f8fafc; margin:4px 0; font-family:ui-monospace,monospace; }}
  .card .sub {{ font-size:12px; color:#cbd5e1; }}
  .panels {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(500px,1fr)); gap:16px; margin-top:16px; }}
  .panel {{ background:#111c33; border:1px solid #1e293b; border-radius:10px; padding:16px; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:8px 10px; border-bottom:1px solid #1e293b; text-align:left; vertical-align:top; }}
  th {{ color:#94a3b8; font-weight:600; background:#0b1324; }}
  td.num, th.num {{ text-align:right; font-family:ui-monospace,monospace; white-space:nowrap; }}
  .cid {{ font-size:11px; color:#64748b; font-family:ui-monospace,monospace; }}
  .flag {{ display:inline-block; padding:1px 6px; border-radius:4px; background:#451a1a; color:#fca5a5; font-size:11px; margin-right:3px; }}
  .ok {{ color:#6ee7b7; font-size:11px; }}
  .dlink {{ color:#7dd3fc; text-decoration:none; font-size:12px; }}
  .dlink:hover {{ text-decoration:underline; }}
  code {{ background:#1e293b; padding:2px 5px; border-radius:4px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Gravity Metrics — Historique des Sessions</h1>
  <div class="meta">Source : <code>data/history.jsonl</code> · {len(records)} session(s) enregistrée(s)</div>

  <div class="grid">
    <div class="card"><div class="k">Dernière Session</div><div class="v">${latest_cost:.4f}</div><div class="sub">{latest_lbl}</div></div>
    <div class="card"><div class="k">Coût Cumulé</div><div class="v">${total_cost:.4f}</div><div class="sub">{len(records)} session(s)</div></div>
    <div class="card"><div class="k">Coût Moyen</div><div class="v">${avg_cost:.4f}</div><div class="sub">Cache moyen : {avg_cache:.1f}%</div></div>
    <div class="card"><div class="k">Meilleur Coût</div><div class="v">${best_cost:.4f}</div><div class="sub">{best_lbl} ({len(records)} sessions)</div></div>
  </div>

  <div class="panels">
    <div class="panel"><h2>Évolution du Coût par Session</h2>{_render_cost_evolution_svg(records)}</div>
    <div class="panel"><h2>Répartition par Catégorie (% Coût)</h2>{_render_category_stacked_svg(records)}</div>
  </div>

  <div class="panel" style="margin-top:16px">
    <h2>Liste Historique des Sessions</h2>
    <table>
      <thead>
        <tr>
          <th>Date (UTC)</th><th>Label</th><th>Projet</th><th>Mode</th><th>Modèles</th>
          <th class="num">Coût</th>
          <th class="num">In / Cache / Out</th><th class="num">Cache %</th>
          <th class="num">Agents</th><th class="num">Appels</th><th class="num">Durée</th>
          <th>Alertes</th><th>Détails</th><th>Note</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows_html)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


def _render_detail_svg_bars(
    items: List[Tuple[str, float, str]], width: int = 720, unit_prefix: str = "$"
) -> str:
    if not items:
        return "<p>Aucune donnée.</p>"
    max_val = max((v for _, v, _ in items), default=1.0) or 1.0
    row_h = 28
    label_w = 230
    val_w = 95
    bar_max_w = max(120, width - label_w - val_w - 20)
    height = len(items) * row_h + 14
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Répartition des coûts">'
    ]
    for idx, (lbl, val, color) in enumerate(items):
        y = 7 + idx * row_h
        bw = max(2.0, (val / max_val) * bar_max_w) if val > 0 else 0.0
        short_lbl = lbl if len(lbl) <= 32 else lbl[:29] + "..."
        parts.append(
            f'<text x="4" y="{y + 17}" fill="#cbd5e1" font-size="12" '
            f'font-family="system-ui, sans-serif">{html.escape(short_lbl, quote=True)}</text>'
        )
        parts.append(f'<rect x="{label_w}" y="{y + 4}" width="{bar_max_w}" height="17" rx="4" fill="#1e293b"/>')
        if bw > 0:
            parts.append(
                f'<rect x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="17" rx="4" fill="{html.escape(color, quote=True)}"/>'
            )
        parts.append(
            f'<text x="{label_w + bar_max_w + 10}" y="{y + 17}" fill="#f8fafc" font-size="12" '
            f'font-weight="600" font-family="ui-monospace, monospace">{html.escape(unit_prefix + f"{val:.4f}", quote=True)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_session_detail_html(metrics: Dict[str, Any]) -> str:
    """Génère le tableau de bord HTML/SVG détaillé d'une session (sans CDN, anti-XSS)."""
    s = metrics["summary"]
    c = s["cost_usd"]
    p = s.get("cost_usd_projection") or {}
    eff = metrics["efficiency_and_waste"]
    win = metrics.get("window") or {}
    cat_colors = {
        "orchestration": "#38bdf8",
        "discovery": "#a78bfa",
        "build": "#34d399",
        "testing": "#fbbf24",
        "verification": "#f87171",
        "other": "#94a3b8",
    }
    cat_items = [
        (CATEGORY_LABELS_FR.get(cat, cat), data["cost_usd"]["total_usd"], cat_colors.get(cat, "#94a3b8"))
        for cat, data in metrics["by_category"].items()
        if data["agents"] > 0
    ]
    split_items = [
        ("Cache Read (contexte)", c["cache_read_usd"], "#22d3ee"),
        ("Input (tokens frais)", c["input_usd"], "#818cf8"),
        ("Output (réponse + thinking)", c["output_usd"], "#f472b6"),
    ]
    phase_items = [
        (pname, pdata["cost_usd"]["total_usd"], "#60a5fa" if "Overhead" not in pname else "#38bdf8")
        for pname, pdata in (metrics.get("by_phase") or {}).items()
    ]

    agent_rows: List[str] = []
    for ag in metrics["agents"]:
        cat = ag["category"]
        bcol = cat_colors.get(cat, "#94a3b8")
        alerts: List[str] = []
        if not ag["clean_termination"]:
            alerts.append("Interrompu")
        if ag["rewind_calls"] > 0:
            alerts.append(f"Rw:{ag['rewind_calls']}")
        if ag["max_context_tokens"] > 128_000:
            alerts.append(">128k")
        al_html = (
            " ".join(f'<span class="flag">{html.escape(a, quote=True)}</span>' for a in alerts)
            or '<span class="ok">OK</span>'
        )
        agent_rows.append(
            "<tr>"
            f"<td><code>{html.escape(ag['conversation_id'][:8], quote=True)}</code></td>"
            f"<td>{html.escape(ag['role'], quote=True)}</td>"
            f'<td><span class="badge" style="border-color:{bcol};color:{bcol}">{html.escape(cat, quote=True)}</span></td>'
            f"<td class=\"num\">{ag['depth']}</td>"
            f"<td class=\"num\">{ag['llm_calls']}</td>"
            f"<td class=\"num\">{_fmt_int(ag['input_tokens'])}</td>"
            f"<td class=\"num\">{_fmt_int(ag['cache_read_tokens'])}</td>"
            f"<td class=\"num\">{_fmt_int(ag['output_tokens'])}</td>"
            f"<td class=\"num\">{_fmt_int(ag['max_context_tokens'])}</td>"
            f"<td class=\"num\">{ag['cache_hit_rate_pct']:.1f}%</td>"
            f"<td class=\"num\"><strong>${ag['cost_usd']['total_usd']:.4f}</strong></td>"
            f"<td>{al_html}</td>"
            "</tr>"
        )

    dup = eff["duplicate_role_spawns"]
    rw = eff["rewind_waste"]
    intr = eff["interrupted_agents"]
    cb = eff["context_bloat"]

    extra_meta: List[str] = []
    if metrics.get("project"):
        extra_meta.append(f"Projet : <code>{html.escape(str(metrics['project']), quote=True)}</code>")
    if metrics.get("gravity_profile"):
        extra_meta.append(f"Profil Gravity : <code>{html.escape(str(metrics['gravity_profile']), quote=True)}</code>")
    if win.get("mode"):
        extra_meta.append(f"Mode fenêtre : <code>{html.escape(str(win['mode']), quote=True)}</code>")
    extra_meta_str = (" · " + " · ".join(extra_meta)) if extra_meta else ""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Détail Session — {html.escape(metrics['label'], quote=True)}</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; padding:24px; background:#0f172a; color:#e2e8f0; font-family:system-ui,-apple-system,sans-serif; line-height:1.5; }}
  .wrap {{ max-width:1320px; margin:0 auto; }}
  h1 {{ margin:6px 0; font-size:24px; color:#f8fafc; }}
  h2 {{ margin:0 0 12px; font-size:16px; color:#93c5fd; border-bottom:1px solid #1e293b; padding-bottom:6px; }}
  .back {{ color:#7dd3fc; text-decoration:none; font-size:13px; }}
  .back:hover {{ text-decoration:underline; }}
  .meta {{ color:#94a3b8; font-size:13px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
  .card {{ background:#111c33; border:1px solid #1e293b; border-radius:10px; padding:14px 16px; }}
  .card .k {{ font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:.04em; }}
  .card .v {{ font-size:24px; font-weight:700; color:#f8fafc; margin:4px 0; font-family:ui-monospace,monospace; }}
  .card .sub {{ font-size:12px; color:#cbd5e1; }}
  .panels {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(480px,1fr)); gap:16px; margin-top:16px; }}
  .panel {{ background:#111c33; border:1px solid #1e293b; border-radius:10px; padding:16px; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:8px 10px; border-bottom:1px solid #1e293b; text-align:left; }}
  th {{ color:#94a3b8; font-weight:600; background:#0b1324; }}
  td.num, th.num {{ text-align:right; font-family:ui-monospace,monospace; white-space:nowrap; }}
  .badge {{ display:inline-block; padding:2px 7px; border:1px solid; border-radius:999px; font-size:11px; }}
  .flag {{ display:inline-block; padding:1px 6px; border-radius:4px; background:#451a1a; color:#fca5a5; font-size:11px; margin-right:3px; }}
  .ok {{ color:#6ee7b7; font-size:11px; }}
  code {{ background:#1e293b; padding:2px 5px; border-radius:4px; font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../dashboard.html">← Historique global</a>
  <h1>Détail Session — {html.escape(metrics['label'], quote=True)}</h1>
  <div class="meta">
    Racine : <code>{html.escape(metrics['root_conversation_id'], quote=True)}</code> ·
    Profil tarifaire : <code>{html.escape(metrics['pricing_profile'], quote=True)}</code> ·
    Fenêtre : {html.escape(str(s['first_ts']), quote=True)} → {html.escape(str(s['last_ts']), quote=True)} ({_fmt_dur(s['wall_clock_s'])}){extra_meta_str}
  </div>

  <div class="grid">
    <div class="card"><div class="k">Coût Total</div><div class="v">${c['total_usd']:.4f}</div><div class="sub">Projection 2027 : ${p.get('total_usd', 0.0):.4f}</div></div>
    <div class="card"><div class="k">Économie Cache</div><div class="v">${c['cache_savings_usd']:.4f}</div><div class="sub">Cache Hit Rate : {s['cache_hit_rate_pct']:.2f}%</div></div>
    <div class="card"><div class="k">Agents / Appels LLM</div><div class="v">{s['total_agents']} / {_fmt_int(s['total_llm_calls'])}</div><div class="sub">Parallélisme : {s['parallelism_factor']:.2f}x · Erreurs : {s['error_rate_pct']:.2f}%</div></div>
    <div class="card"><div class="k">Gaspillages & Contexte</div><div class="v">{dup['duplicate_agent_count']} dup · {rw['rewind_calls']} rw</div><div class="sub">Interrompus : {intr['count']} (${intr['total_cost_usd']:.4f}) · Max Ctx : {_fmt_int(cb['max_context_tokens_overall'])}</div></div>
  </div>

  <div class="panels">
    <div class="panel"><h2>Coût par Catégorie</h2>{_render_detail_svg_bars(cat_items)}</div>
    <div class="panel"><h2>Ventilation (Cache / Input / Output)</h2>{_render_detail_svg_bars(split_items)}</div>
  </div>

  <div class="panel" style="margin-top:16px">
    <h2>Coût par Phase d'Exécution</h2>
    {_render_detail_svg_bars(phase_items, width=900)}
  </div>

  <div class="panel" style="margin-top:16px">
    <h2>Détail Complet des Agents ({len(metrics['agents'])})</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th><th>Rôle</th><th>Catégorie</th><th class="num">Prof.</th>
          <th class="num">Appels</th><th class="num">Input</th><th class="num">Cache Read</th>
          <th class="num">Output</th><th class="num">Max Ctx</th><th class="num">Cache %</th>
          <th class="num">Coût USD</th><th>Statut</th>
        </tr>
      </thead>
      <tbody>{"".join(agent_rows)}</tbody>
    </table>
  </div>
</div>
</body>
</html>
"""
