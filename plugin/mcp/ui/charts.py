#!/usr/bin/env python3
"""Générateurs de graphiques SVG et barres proportionnelles % (100 % stdlib)."""

CHARTS_JS = r"""
  const PALETTE = [
    "var(--gm-c1)",
    "var(--gm-c2)",
    "var(--gm-c3)",
    "var(--gm-c4)",
    "var(--gm-c6)",
    "var(--gm-c7)"
  ];

  function renderCostEvolutionSvg(records, activeTarget) {
    if (!records.length) return '<div class="empty">Aucune session dans cette sélection.</div>';
    const ptsRecs = records.slice(-24);
    const width = 620, height = 200;
    const padL = 54, padR = 22, padT = 18, padB = 38;
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
      svg += '<polyline fill="none" stroke="var(--gm-c1)" stroke-width="2.2" points="' + poly + '"/>';
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
      svg += '<title>' + esc(lbl) + ' : $' + costs[i].toFixed(4) + '</title>';
      svg += '<circle cx="' + cx + '" cy="' + cy + '" r="' + (isAct ? "6.2" : "4.8") + '" fill="' + (isAct ? "var(--gm-c4)" : "var(--gm-c1)") + '" stroke="var(--gm-bg-card)" stroke-width="2"/>';
      if (ptsRecs.length <= 12 || i % labelStep === 0 || i === ptsRecs.length - 1) {
        svg += '<text x="' + cx + '" y="' + (height - 10) + '" text-anchor="middle" fill="var(--gm-fg-muted)" font-size="10">' + esc(shortLbl) + '</text>';
      }
      svg += '</g>';
    });
    svg += "</svg>";
    return svg;
  }

  function renderCategoryStackedSvg(records) {
    if (!records.length) return '<div class="empty">Aucune session dans cette sélection.</div>';
    const recs = records.slice(-12);
    const width = 620, labelW = 150, barW = width - labelW - 14, rowH = 24;
    const height = recs.length * rowH + 8;
    const cats = CAT_DEFS.slice(0, 5);
    let legend = '<div class="legend">';
    cats.forEach((c) => {
      legend += '<span class="legend-item"><span class="legend-swatch" style="background:' + c.color + '"></span>' + esc(c.label) + '</span>';
    });
    legend += '</div>';

    let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Répartition par catégorie">';
    recs.forEach((r, idx) => {
      const y = 4 + idx * rowH;
      const lbl = String(r.label || "");
      const rowKey = lbl || String(r.conversation_id || "");
      const shortLbl = lbl.length <= 20 ? lbl : lbl.slice(0, 17) + "...";
      const cs = r.category_share_pct || {};
      svg += '<g data-action="open-detail" data-target="' + esc(rowKey) + '" style="cursor:pointer">';
      svg += '<text x="2" y="' + (y + 14) + '" fill="var(--gm-fg-muted)" font-size="11">' + esc(shortLbl) + '</text>';
      svg += '<rect x="' + labelW + '" y="' + (y + 3) + '" width="' + barW + '" height="14" rx="3" fill="var(--gm-bg-muted)"/>';
      let xCur = labelW;
      cats.forEach((c) => {
        const pct = Math.max(0, Number(cs[c.key]) || 0);
        const sw = (pct / 100) * barW;
        if (sw > 0.5) {
          svg += '<rect x="' + xCur.toFixed(1) + '" y="' + (y + 3) + '" width="' + sw.toFixed(1) + '" height="14" fill="' + c.color + '"><title>' + esc(c.label) + ' : ' + pct.toFixed(1) + '%</title></rect>';
          xCur += sw;
        }
      });
      svg += '</g>';
    });
    svg += "</svg>";
    return legend + svg;
  }

  function renderHorizontalBarsSvg(items) {
    if (!items.length) return '<div class="empty">Aucune donnée.</div>';
    const width = 640, labelW = 180, valW = 84, barMaxW = width - labelW - valW - 14, rowH = 26;
    const height = items.length * rowH + 8;
    const maxV = Math.max.apply(null, items.map((it) => it.val)) || 1;
    let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img">';
    items.forEach((it, idx) => {
      const y = 4 + idx * rowH;
      const bw = it.val > 0 ? Math.max(2, (it.val / maxV) * barMaxW) : 0;
      const lbl = String(it.label || "");
      const shortLbl = lbl.length <= 24 ? lbl : lbl.slice(0, 21) + "...";
      svg += '<text x="2" y="' + (y + 16) + '" fill="var(--gm-fg-muted)" font-size="11"><title>' + esc(lbl) + '</title>' + esc(shortLbl) + '</text>';
      svg += '<rect x="' + labelW + '" y="' + (y + 4) + '" width="' + barMaxW + '" height="15" rx="3" fill="var(--gm-bg-muted)"/>';
      if (bw > 0) {
        svg += '<rect x="' + labelW + '" y="' + (y + 4) + '" width="' + bw.toFixed(1) + '" height="15" rx="3" fill="' + it.color + '"/>';
      }
      svg += '<text x="' + (labelW + barMaxW + 8) + '" y="' + (y + 16) + '" fill="var(--gm-fg)" font-size="11" font-weight="600" font-family="var(--gm-mono)">$' + it.val.toFixed(4) + '</text>';
    });
    svg += "</svg>";
    return svg;
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
        '<span>' + fmtInt(p.sessions) + ' session(s)</span>' +
        '<span>Cache moy : ' + Number(p.avg_cache_hit_rate_pct || 0).toFixed(1) + '%</span>' +
        '<span>Appels : ' + fmtInt(p.llm_calls) + '</span>' +
        (p.bridge_sessions ? '<span>Bridge : ' + fmtInt(p.bridge_sessions) + '</span>' : '') +
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
      const c = use2027 ? (s.cost_usd_projection || s.cost_usd || {}) : (s.cost_usd || {});
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
    let rowsHtml = "";
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
        '<span><code>' + esc(wsName) + '</code></span>' +
        '<span class="subline"><strong>$' + costVal.toFixed(4) + '</strong> · ' + pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="share-track"><div class="share-fill" style="width:' + barW.toFixed(1) + '%;background:' + color + '"></div></div>' +
        '<div class="share-meta">' +
        '<span>Ag. : ' + fmtInt(wData.agents || 0) + '</span>' +
        '<span>Appels : ' + fmtInt(wData.llm_calls || 0) + '</span>' +
        '<span>Cache : ' + Number(wData.cache_hit_rate_pct || 0).toFixed(1) + '%</span>' +
        '</div></div>';

      rowsHtml += '<tr>' +
        '<td><code>' + esc(wsName) + '</code></td>' +
        '<td class="num">' + fmtInt(wData.agents || 0) + '</td>' +
        '<td class="num">' + fmtInt(wData.llm_calls || 0) + '</td>' +
        '<td class="num">' + Number(wData.cache_hit_rate_pct || 0).toFixed(1) + '%</td>' +
        '<td class="num"><strong>$' + costVal.toFixed(4) + '</strong></td>' +
        '<td class="num">' + pct.toFixed(1) + '%</td>' +
        '</tr>';
    });
    barsHtml += '</div>';

    const subtitle = isFallback
      ? '<span class="subline">archive 2.0.0 — repli projet</span>'
      : '<span class="subline">' + wsEntries.length + ' dossier(s) / workspace(s)</span>';

    return '<div class="panel"><div class="panel-head"><span class="panel-title">Répartition par Dossier / Workspace (%)</span>' + subtitle + '</div>' +
      '<div class="panels two">' +
      barsHtml +
      '<div class="table-wrap"><table><thead><tr><th>Dossier</th><th class="num">Ag.</th><th class="num">Appels</th><th class="num">Cache %</th><th class="num">Coût</th><th class="num">Part</th></tr></thead><tbody>' + rowsHtml + '</tbody></table></div>' +
      '</div></div>';
  }
"""
