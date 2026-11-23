#!/usr/bin/env python3
"""Générateurs de graphiques SVG et barres HTML/CSS lisibles à toutes largeurs (100 % stdlib)."""

CHARTS_JS = r"""
  const PALETTE = [
    "var(--gm-c1)",
    "var(--gm-c2)",
    "var(--gm-c3)",
    "var(--gm-c4)",
    "var(--gm-c6)",
    "var(--gm-c7)"
  ];

  function sortRecordsChrono(records) {
    return (records || []).slice().sort((a, b) => {
      const ta = String(a.started_at || a.added_at || "");
      const tb = String(b.started_at || b.added_at || "");
      if (ta < tb) return -1;
      if (ta > tb) return 1;
      return String(a.label || "").localeCompare(String(b.label || ""));
    });
  }

  function renderCostEvolutionSvg(records, activeTarget) {
    if (!records.length) return '<div class="empty">Aucune session dans cette sélection.</div>';
    const ptsRecs = sortRecordsChrono(records).slice(-24);
    const width = 620, height = 195;
    const padL = 54, padR = 22, padT = 18, padB = 36;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const costs = ptsRecs.map(recCost);
    const maxY = Math.max(Math.max.apply(null, costs), 0.01) * 1.15;

    const xAt = (i) => ptsRecs.length === 1 ? padL + plotW / 2 : padL + (i / (ptsRecs.length - 1)) * plotW;
    const yAt = (v) => padT + plotH - (v / maxY) * plotH;

    let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Évolution du coût">';
    for (let s = 0; s <= 3; s++) {
      const yv = maxY * (1 - s / 3);
      const yp = yAt(yv);
      svg += '<line x1="' + padL + '" y1="' + yp.toFixed(1) + '" x2="' + (width - padR) + '" y2="' + yp.toFixed(1) + '" stroke="var(--gm-border-soft)" stroke-width="1"/>';
      svg += '<text x="' + (padL - 7) + '" y="' + (yp + 4).toFixed(1) + '" text-anchor="end" fill="var(--gm-fg-muted)" font-size="10" font-family="var(--gm-mono)">$' + yv.toFixed(2) + '</text>';
    }
    if (ptsRecs.length > 1) {
      const poly = ptsRecs.map((_, i) => xAt(i).toFixed(1) + "," + yAt(costs[i]).toFixed(1)).join(" ");
      svg += '<polyline fill="none" stroke="var(--gm-accent)" stroke-width="2" points="' + poly + '"/>';
    }
    const labelStep = ptsRecs.length > 10 ? Math.ceil(ptsRecs.length / 7) : 1;
    ptsRecs.forEach((r, i) => {
      const cx = xAt(i).toFixed(1);
      const cy = yAt(costs[i]).toFixed(1);
      const lbl = String(r.label || "");
      const rowKey = lbl || String(r.conversation_id || "");
      const isAct = activeTarget && (rowKey === activeTarget || r.conversation_id === activeTarget);
      const shortLbl = lbl.length <= 11 ? lbl : lbl.slice(0, 9) + "..";
      svg += '<g class="svg-pt" data-action="open-detail" data-target="' + esc(rowKey) + '" style="cursor:pointer">';
      svg += '<title>' + esc(lbl) + ' : $' + costs[i].toFixed(4) + ' (' + esc(fmtDate(r)) + ')</title>';
      svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + (isAct ? "5.8" : "4.2") + '" fill="' + (isAct ? "var(--gm-c4)" : "var(--gm-accent)") + '" stroke="var(--gm-bg-card)" stroke-width="2"/>';
      if (ptsRecs.length <= 12 || i % labelStep === 0 || i === ptsRecs.length - 1) {
        svg += '<text x="' + cx + '" y="' + (height - 10) + '" text-anchor="middle" fill="var(--gm-fg-muted)" font-size="10">' + esc(shortLbl) + '</text>';
      }
      svg += '</g>';
    });
    svg += "</svg>";
    return svg;
  }

  function renderCategoryHtmlBars(records) {
    if (!records.length) return '<div class="empty">Aucune session dans cette sélection.</div>';
    const totals = {};
    let sumAll = 0;
    records.forEach((r) => {
      const c = recCost(r);
      const cs = r.category_share_pct || {};
      CAT_DEFS.forEach((def) => {
        const share = Math.max(0, Number(cs[def.key]) || 0);
        const part = c * (share / 100.0);
        totals[def.key] = (totals[def.key] || 0) + part;
        sumAll += part;
      });
    });
    const rows = CAT_DEFS.map((def) => ({
      label: def.label,
      val: totals[def.key] || 0,
      pct: sumAll > 0 ? ((totals[def.key] || 0) / sumAll) * 100 : 0,
      color: def.color
    })).filter((it) => it.val > 0 || it.pct > 0);
    if (!rows.length) return '<div class="empty">Aucune ventilation par catégorie disponible.</div>';
    let html = '<div class="share-list">';
    rows.forEach((it) => {
      const barW = Math.max(it.pct > 0 ? 2 : 0, Math.min(100, it.pct));
      html += '<div class="share-row">' +
        '<div class="share-top">' +
        '<span>' + esc(it.label) + '</span>' +
        '<span class="subline"><strong>$' + it.val.toFixed(4) + '</strong> · ' + it.pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="share-track"><div class="share-fill" style="width:' + barW.toFixed(1) + '%;background:' + it.color + '"></div></div>' +
        '</div>';
    });
    html += '</div>';
    return html;
  }

  function renderHtmlBarList(items, totalOverride) {
    if (!items.length) return '<div class="empty">Aucune donnée.</div>';
    const total = totalOverride != null ? totalOverride : items.reduce((acc, it) => acc + Math.max(0, Number(it.val) || 0), 0);
    let html = '<div class="share-list">';
    items.forEach((it, idx) => {
      const val = Math.max(0, Number(it.val) || 0);
      const pct = total > 0 ? (val / total) * 100 : 0;
      const barW = Math.max(val > 0 ? 2 : 0, Math.min(100, pct));
      const color = it.color || PALETTE[idx % PALETTE.length];
      html += '<div class="share-row">' +
        '<div class="share-top">' +
        '<span>' + esc(it.label) + '</span>' +
        '<span class="subline"><strong>$' + val.toFixed(4) + '</strong> · ' + pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="share-track"><div class="share-fill" style="width:' + barW.toFixed(1) + '%;background:' + color + '"></div></div>' +
        (it.meta ? '<div class="share-meta">' + it.meta + '</div>' : '') +
        '</div>';
    });
    html += '</div>';
    return html;
  }

  function renderProjectShareBars(projRows, use2027) {
    if (!projRows.length) return '<div class="empty">Aucun projet archivé.</div>';
    let html = '<div class="share-list">';
    projRows.forEach((p, idx) => {
      const cost = use2027 ? Number(p.cost_2027_usd || 0) : Number(p.cost_usd || 0);
      const pct = use2027 ? Number(p.share_2027_pct || 0) : Number(p.share_pct || 0);
      const barW = Math.max(pct > 0 ? 2 : 0, Math.min(100, pct));
      const color = PALETTE[idx % PALETTE.length];
      const isNamed = p.project && p.project !== "(sans projet)";
      const clickAttr = isNamed
        ? ' class="share-row clickable" data-action="filter-project" data-project="' + esc(p.project) + '" title="Cliquer pour filtrer sur ' + esc(p.project) + '"'
        : ' class="share-row"';
      html += '<div' + clickAttr + '>' +
        '<div class="share-top">' +
        '<span><strong>' + esc(p.project || "(sans projet)") + '</strong></span>' +
        '<span class="subline"><strong>$' + cost.toFixed(4) + '</strong> · ' + pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="share-track"><div class="share-fill" style="width:' + barW.toFixed(1) + '%;background:' + color + '"></div></div>' +
        '<div class="share-meta">' +
        '<span>' + fmtInt(p.sessions) + ' sess.</span>' +
        '<span>Cache ' + Number(p.avg_cache_hit_rate_pct || 0).toFixed(1) + '%</span>' +
        '<span>' + fmtInt(p.llm_calls) + ' appels</span>' +
        (p.bridge_sessions ? '<span>Bridge ' + fmtInt(p.bridge_sessions) + '</span>' : '') +
        '</div></div>';
    });
    html += '</div>';
    return html;
  }

  function renderWorkspaceSharePanel(d, use2027) {
    const wsObj = d && d.by_workspace && typeof d.by_workspace === "object" ? d.by_workspace : {};
    let wsEntries = Object.entries(wsObj);
    let isFallback = false;

    if (!wsEntries.length) {
      isFallback = true;
      const s = (d && d.summary) || {};
      wsEntries = [[
        String((d && d.project) || "workspace principal"),
        {
          agents: s.total_agents || 0,
          llm_calls: s.total_llm_calls || 0,
          cache_hit_rate_pct: s.cache_hit_rate_pct || 0,
          cost_usd: s.cost_usd || {},
          cost_usd_projection: s.cost_usd_projection || s.cost_usd || {},
          cost_share_pct: 100.0
        }
      ]];
    }

    wsEntries.sort((a, b) => {
      const ca = use2027 ? Number(((a[1].cost_usd_projection || a[1].cost_usd) || {}).total_usd || 0) : Number(((a[1].cost_usd) || {}).total_usd || 0);
      const cb = use2027 ? Number(((b[1].cost_usd_projection || b[1].cost_usd) || {}).total_usd || 0) : Number(((b[1].cost_usd) || {}).total_usd || 0);
      return cb - ca;
    });

    const totalShown = wsEntries.reduce((acc, [, w]) => {
      const co = use2027 ? (w.cost_usd_projection || w.cost_usd) : w.cost_usd;
      return acc + Number((co || {}).total_usd || 0);
    }, 0);

    let barsHtml = '<div class="share-list">';
    wsEntries.forEach(([wsName, wData], idx) => {
      const costObj = use2027 ? (wData.cost_usd_projection || wData.cost_usd) : wData.cost_usd;
      const costVal = Number((costObj || {}).total_usd || 0);
      const pct = totalShown > 0
        ? (costVal / totalShown) * 100
        : Number(wData.cost_share_pct || 0);
      const color = PALETTE[idx % PALETTE.length];
      const barW = Math.max(pct > 0 ? 2 : 0, Math.min(100, pct));

      barsHtml += '<div class="share-row">' +
        '<div class="share-top">' +
        '<span class="mono"><strong>' + esc(wsName) + '</strong></span>' +
        '<span class="subline"><strong>$' + costVal.toFixed(4) + '</strong> · ' + pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="share-track"><div class="share-fill" style="width:' + barW.toFixed(1) + '%;background:' + color + '"></div></div>' +
        '<div class="share-meta">' +
        '<span>' + fmtInt(wData.agents || 0) + ' ag.</span>' +
        '<span>' + fmtInt(wData.llm_calls || 0) + ' appels</span>' +
        '<span>Cache ' + Number(wData.cache_hit_rate_pct || 0).toFixed(1) + '%</span>' +
        '</div></div>';
    });
    barsHtml += '</div>';

    const subtitle = isFallback
      ? '<span class="subline">archive 2.0.0 — repli projet</span>'
      : '<span class="subline">' + wsEntries.length + ' dossier(s) / workspace(s)</span>';

    return '<div class="panel"><div class="panel-head"><span class="panel-title">Répartition par Dossier / Workspace</span>' + subtitle + '</div>' +
      barsHtml + '</div>';
  }
"""
