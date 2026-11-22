#!/usr/bin/env python3
"""Feuille de styles CSS autonome du widget MCP App Gravity Metrics (100 % stdlib)."""

WIDGET_CSS = r"""
  :root {
    color-scheme: dark;
    --gm-bg: var(--color-background-primary, #0f172a);
    --gm-bg-card: var(--color-background-secondary, #131f38);
    --gm-bg-muted: var(--color-background-tertiary, #1b2944);
    --gm-fg: var(--color-text-primary, #e2e8f0);
    --gm-fg-muted: var(--color-text-secondary, #94a3b8);
    --gm-fg-dim: var(--color-text-tertiary, #64748b);
    --gm-border: var(--color-border-primary, #25344f);
    --gm-border-soft: var(--color-border-secondary, #1c2941);
    --gm-info: var(--color-text-info, #38bdf8);
    --gm-success: var(--color-text-success, #34d399);
    --gm-warning: var(--color-text-warning, #fbbf24);
    --gm-danger: var(--color-text-danger, #f87171);
    --gm-info-bg: var(--color-background-info, rgba(56, 189, 248, 0.14));
    --gm-danger-bg: var(--color-background-danger, rgba(248, 113, 113, 0.14));
    --gm-success-bg: var(--color-background-success, rgba(52, 211, 153, 0.14));
    --gm-warning-bg: rgba(251, 191, 36, 0.14);
    --gm-font: var(--font-sans, system-ui, -apple-system, Segoe UI, sans-serif);
    --gm-mono: var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace);
    --gm-c1: #38bdf8;
    --gm-c2: #a78bfa;
    --gm-c3: #34d399;
    --gm-c4: #fbbf24;
    --gm-c5: #f87171;
    --gm-c6: #22d3ee;
    --gm-c7: #f472b6;
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --gm-bg: var(--color-background-primary, #f8fafc);
    --gm-bg-card: var(--color-background-secondary, #ffffff);
    --gm-bg-muted: var(--color-background-tertiary, #f1f5f9);
    --gm-fg: var(--color-text-primary, #0f172a);
    --gm-fg-muted: var(--color-text-secondary, #475569);
    --gm-fg-dim: var(--color-text-tertiary, #64748b);
    --gm-border: var(--color-border-primary, #cbd5e1);
    --gm-border-soft: var(--color-border-secondary, #e2e8f0);
    --gm-info: var(--color-text-info, #0284c7);
    --gm-success: var(--color-text-success, #059669);
    --gm-warning: var(--color-text-warning, #d97706);
    --gm-danger: var(--color-text-danger, #dc2626);
    --gm-info-bg: var(--color-background-info, rgba(2, 132, 199, 0.10));
    --gm-danger-bg: var(--color-background-danger, rgba(220, 38, 38, 0.10));
    --gm-success-bg: var(--color-background-success, rgba(5, 150, 105, 0.10));
    --gm-warning-bg: rgba(217, 119, 6, 0.10);
    --gm-c1: #0284c7;
    --gm-c2: #7c3aed;
    --gm-c3: #059669;
    --gm-c4: #d97706;
    --gm-c5: #dc2626;
    --gm-c6: #0891b2;
    --gm-c7: #db2777;
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      color-scheme: light;
    }
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    background: var(--gm-bg);
    color: var(--gm-fg);
    font-family: var(--gm-font);
    font-size: 13px;
    line-height: 1.45;
  }
  .app {
    max-width: 1460px;
    margin: 0 auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .shell-head {
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
    padding: 10px 12px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .brand-dot {
    width: 9px;
    height: 9px;
    border-radius: 999px;
    background: var(--gm-success);
    flex-shrink: 0;
  }
  .brand-title {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.01em;
    white-space: nowrap;
  }
  .brand-sub {
    font-size: 12px;
    color: var(--gm-fg-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .nav-tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    border-top: 1px solid var(--gm-border-soft);
    padding-top: 6px;
  }
  .tab-btn {
    font-family: inherit;
    font-size: 12px;
    font-weight: 500;
    color: var(--gm-fg-muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 10px 8px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: color 0.12s, border-color 0.12s;
  }
  .tab-btn:hover { color: var(--gm-fg); }
  .tab-btn.active {
    color: var(--gm-info);
    border-bottom-color: var(--gm-info);
    font-weight: 600;
  }
  .tab-badge {
    font-family: var(--gm-mono);
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 999px;
    background: var(--gm-bg-muted);
    color: var(--gm-fg-muted);
    border: 1px solid var(--gm-border-soft);
  }
  .tab-badge.warn {
    background: var(--gm-warning-bg);
    color: var(--gm-warning);
    border-color: var(--gm-warning);
  }
  .seg {
    display: inline-flex;
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border);
    border-radius: 7px;
    padding: 2px;
    gap: 2px;
  }
  .seg button, .btn, select, input[type="search"] {
    font-family: inherit;
    font-size: 12px;
    color: var(--gm-fg);
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 9px;
    cursor: pointer;
    transition: background 0.12s, border-color 0.12s;
  }
  .seg button.active {
    background: var(--gm-bg-card);
    border-color: var(--gm-border);
    color: var(--gm-info);
    font-weight: 600;
  }
  .btn {
    background: var(--gm-bg-muted);
    border-color: var(--gm-border);
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .btn:hover:not(:disabled) { border-color: var(--gm-info); }
  .btn.primary { background: var(--gm-info-bg); border-color: var(--gm-info); color: var(--gm-info); font-weight: 600; }
  .btn.danger { background: var(--gm-danger-bg); border-color: var(--gm-danger); color: var(--gm-danger); }
  .btn.sm { padding: 3px 7px; font-size: 11px; }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  select, input[type="search"] {
    background: var(--gm-bg-muted);
    border-color: var(--gm-border);
    cursor: text;
    min-width: 0;
    max-width: 100%;
  }
  select { cursor: pointer; }
  .kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
  }
  .card {
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
    padding: 10px 12px;
    min-width: 0;
  }
  .card .k {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--gm-fg-muted);
  }
  .card .v {
    font-size: 19px;
    font-weight: 700;
    font-family: var(--gm-mono);
    margin: 3px 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card .sub {
    font-size: 11.5px;
    color: var(--gm-fg-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .panels {
    display: grid;
    grid-template-columns: 1fr;
    gap: 10px;
  }
  @media (min-width: 860px) {
    .panels.two { grid-template-columns: 1fr 1fr; }
  }
  .panel {
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
    padding: 12px 14px;
    min-width: 0;
  }
  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    padding-bottom: 8px;
    margin-bottom: 10px;
    border-bottom: 1px solid var(--gm-border-soft);
  }
  .panel-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--gm-info);
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    margin-bottom: 10px;
  }
  .filters input[type="search"] { flex: 1 1 160px; }
  .table-wrap {
    width: 100%;
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th, td {
    padding: 7px 8px;
    border-bottom: 1px solid var(--gm-border-soft);
    text-align: left;
    vertical-align: top;
  }
  th {
    color: var(--gm-fg-muted);
    font-weight: 600;
    background: var(--gm-bg-muted);
    white-space: nowrap;
    user-select: none;
  }
  th.sortable { cursor: pointer; }
  th.sortable:hover { color: var(--gm-info); }
  td.num, th.num {
    text-align: right;
    font-family: var(--gm-mono);
    white-space: nowrap;
  }
  tbody tr.clickable { cursor: pointer; }
  tbody tr.clickable:hover { background: var(--gm-bg-muted); }
  .subline {
    font-size: 11px;
    color: var(--gm-fg-dim);
    font-family: var(--gm-mono);
  }
  code {
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border-soft);
    padding: 1px 5px;
    border-radius: 4px;
    font-family: var(--gm-mono);
    font-size: 11px;
  }
  .pill {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 999px;
    font-size: 11px;
    border: 1px solid var(--gm-border);
    white-space: nowrap;
  }
  .pill.ok { background: var(--gm-success-bg); border-color: var(--gm-success); color: var(--gm-success); }
  .pill.flag, .pill.danger { background: var(--gm-danger-bg); border-color: var(--gm-danger); color: var(--gm-danger); margin-right: 3px; }
  .pill.bridge, .pill.info { background: var(--gm-info-bg); border-color: var(--gm-info); color: var(--gm-info); margin-right: 3px; }
  .pill.warn { background: var(--gm-warning-bg); border-color: var(--gm-warning); color: var(--gm-warning); margin-right: 3px; }
  .pill.neutral { color: var(--gm-fg-muted); }
  .pill-btn {
    cursor: pointer;
    background: var(--gm-info-bg);
    border: 1px solid var(--gm-info);
    color: var(--gm-info);
    font-family: inherit;
    font-size: 11px;
    border-radius: 999px;
    padding: 2px 8px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .pill-btn:hover { opacity: 0.85; }
  .confirm-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 10px 12px;
    background: var(--gm-danger-bg);
    border: 1px solid var(--gm-danger);
    border-radius: 8px;
  }
  .confirm-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .toast {
    padding: 8px 12px;
    border-radius: 8px;
    border: 1px solid var(--gm-info);
    background: var(--gm-info-bg);
    color: var(--gm-fg);
    font-size: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .toast.err { border-color: var(--gm-danger); background: var(--gm-danger-bg); }
  .banner-info {
    padding: 8px 12px;
    border-radius: 8px;
    border: 1px solid var(--gm-warning);
    background: var(--gm-warning-bg);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
  }
  .empty {
    text-align: center;
    padding: 26px 14px;
    color: var(--gm-fg-muted);
  }
  .svg-box { width: 100%; overflow-x: auto; }
  .svg-box svg { display: block; width: 100%; height: auto; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 6px; font-size: 11px; color: var(--gm-fg-muted); }
  .legend-item { display: inline-flex; align-items: center; gap: 4px; }
  .legend-swatch { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
  .row-del { padding: 2px 7px; font-size: 11px; }
  .diag-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
  }
  ul.clean { margin: 6px 0 0; padding-left: 16px; }
  ul.clean li { margin-bottom: 3px; }

  /* Répartition % Barres horizontales */
  .share-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .share-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 7px 9px;
    border: 1px solid var(--gm-border-soft);
    border-radius: 8px;
    background: var(--gm-bg-muted);
  }
  .share-row.clickable { cursor: pointer; }
  .share-row.clickable:hover { border-color: var(--gm-info); }
  .share-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
  }
  .share-track {
    width: 100%;
    height: 7px;
    border-radius: 999px;
    background: var(--gm-bg-card);
    overflow: hidden;
    border: 1px solid var(--gm-border-soft);
  }
  .share-fill {
    height: 100%;
    border-radius: 999px;
  }
  .share-meta {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 11px;
    color: var(--gm-fg-muted);
    font-family: var(--gm-mono);
  }

  /* Master-Detail Rapports & Audits */
  .master-detail {
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
    align-items: start;
  }
  .master-pane {
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }
  .master-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 440px;
    overflow-y: auto;
  }
  @media (min-width: 920px) {
    .master-detail {
      grid-template-columns: 320px minmax(0, 1fr);
    }
    .master-pane {
      position: sticky;
      top: 10px;
      max-height: calc(100vh - 28px);
    }
    .master-list {
      max-height: calc(100vh - 200px);
    }
    .only-narrow { display: none !important; }
  }
  @media (max-width: 919px) {
    .master-pane.hide-mobile { display: none; }
  }
  .master-item {
    text-align: left;
    width: 100%;
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border-soft);
    border-radius: 8px;
    padding: 8px 9px;
    color: var(--gm-fg);
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-family: inherit;
  }
  .master-item:hover { border-color: var(--gm-info); }
  .master-item.active {
    background: var(--gm-info-bg);
    border-color: var(--gm-info);
  }
  .master-item.pinned {
    border-color: var(--gm-success);
    background: var(--gm-success-bg);
  }
  .master-item.pinned.active {
    border-color: var(--gm-info);
    background: var(--gm-info-bg);
  }
  .master-item-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 12px;
  }
  .master-item-sub {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--gm-fg-muted);
    font-family: var(--gm-mono);
    flex-wrap: wrap;
  }
  .detail-pane {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .sticky-nav {
    position: sticky;
    top: 4px;
    z-index: 5;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    padding: 8px 10px;
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
  }
  .sticky-nav-group {
    display: flex;
    align-items: center;
    gap: 5px;
    flex: 1 1 auto;
    min-width: 0;
  }
  .sticky-nav select {
    flex: 1 1 140px;
    max-width: 340px;
  }
  .detail-head {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .detail-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .detail-meta {
    font-size: 12px;
    color: var(--gm-fg-muted);
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }
  .subtabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--gm-border-soft);
  }
  .subtab-btn {
    font-family: inherit;
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid var(--gm-border-soft);
    background: var(--gm-bg-muted);
    color: var(--gm-fg-muted);
    cursor: pointer;
  }
  .subtab-btn.active {
    background: var(--gm-info-bg);
    border-color: var(--gm-info);
    color: var(--gm-info);
    font-weight: 600;
  }

  /* Comparateur A vs B */
  .compare-bar {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 8px;
    align-items: center;
  }
  @media (max-width: 620px) {
    .compare-bar { grid-template-columns: 1fr; }
  }
  .delta-good { color: var(--gm-success); font-weight: 600; }
  .delta-bad { color: var(--gm-danger); font-weight: 600; }
  .delta-flat { color: var(--gm-fg-muted); }

  /* Lecteur Markdown sécurisé (Audits) */
  .md-doc {
    font-size: 13px;
    line-height: 1.6;
    word-break: break-word;
  }
  .md-doc h1 { font-size: 18px; margin: 4px 0 10px; border-bottom: 1px solid var(--gm-border-soft); padding-bottom: 6px; }
  .md-doc h2 { font-size: 15px; margin: 14px 0 8px; color: var(--gm-info); }
  .md-doc h3 { font-size: 13.5px; margin: 12px 0 6px; }
  .md-doc h4 { font-size: 12.5px; margin: 10px 0 4px; color: var(--gm-fg-muted); }
  .md-doc p { margin: 6px 0; }
  .md-doc ul, .md-doc ol { margin: 6px 0; padding-left: 20px; }
  .md-doc li { margin-bottom: 3px; }
  .md-doc blockquote {
    margin: 8px 0;
    padding: 6px 12px;
    border-left: 3px solid var(--gm-info);
    background: var(--gm-bg-muted);
    border-radius: 0 6px 6px 0;
    color: var(--gm-fg-muted);
  }
  .md-doc pre {
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border);
    border-radius: 7px;
    padding: 9px 11px;
    overflow-x: auto;
    font-family: var(--gm-mono);
    font-size: 11.5px;
    margin: 8px 0;
  }
  .md-doc pre code {
    border: none;
    background: transparent;
    padding: 0;
    font-size: inherit;
  }
  .md-doc hr {
    border: none;
    border-top: 1px solid var(--gm-border-soft);
    margin: 12px 0;
  }
  .md-link {
    color: var(--gm-info);
    text-decoration: underline dotted;
  }
  .md-audit-link {
    font-family: inherit;
    font-size: inherit;
    color: var(--gm-info);
    background: var(--gm-info-bg);
    border: 1px solid var(--gm-info);
    border-radius: 4px;
    padding: 0 5px;
    cursor: pointer;
  }
"""
