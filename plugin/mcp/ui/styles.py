#!/usr/bin/env python3
"""Feuille de styles CSS sobre et adaptative du widget MCP App Gravity Metrics (100 % stdlib)."""

WIDGET_CSS = r"""
  :root {
    color-scheme: dark light;
    --gm-bg: var(--color-background-primary, #111317);
    --gm-bg-card: var(--color-background-secondary, #181b20);
    --gm-bg-muted: var(--color-background-tertiary, #22262e);
    --gm-fg: var(--color-text-primary, #eceef2);
    --gm-fg-muted: var(--color-text-secondary, #9ca3af);
    --gm-fg-dim: var(--color-text-tertiary, #6b7280);
    --gm-border: var(--color-border-primary, #2c313c);
    --gm-border-soft: var(--color-border-secondary, #232730);
    --gm-accent: var(--color-text-info, #60a5fa);
    --gm-accent-bg: var(--color-background-info, rgba(96, 165, 250, 0.12));
    --gm-info: var(--gm-accent);
    --gm-success: var(--color-text-success, #34d399);
    --gm-warning: var(--color-text-warning, #f59e0b);
    --gm-danger: var(--color-text-danger, #ef4444);
    --gm-info-bg: var(--gm-accent-bg);
    --gm-success-bg: var(--color-background-success, rgba(52, 211, 153, 0.12));
    --gm-warning-bg: var(--color-background-warning, rgba(245, 158, 11, 0.12));
    --gm-danger-bg: var(--color-background-danger, rgba(239, 68, 68, 0.12));
    --gm-font: var(--font-sans, system-ui, -apple-system, Segoe UI, sans-serif);
    --gm-mono: var(--font-mono, ui-monospace, SFMono-Regular, Consolas, monospace);
    --gm-c1: var(--gm-accent);
    --gm-c2: #8b5cf6;
    --gm-c3: #10b981;
    --gm-c4: #f59e0b;
    --gm-c5: #ef4444;
    --gm-c6: #6366f1;
    --gm-c7: #ec4899;
  }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) {
      color-scheme: light;
      --gm-bg: var(--color-background-primary, #fafafa);
      --gm-bg-card: var(--color-background-secondary, #ffffff);
      --gm-bg-muted: var(--color-background-tertiary, #f3f4f6);
      --gm-fg: var(--color-text-primary, #111827);
      --gm-fg-muted: var(--color-text-secondary, #4b5563);
      --gm-fg-dim: var(--color-text-tertiary, #9ca3af);
      --gm-border: var(--color-border-primary, #e5e7eb);
      --gm-border-soft: var(--color-border-secondary, #f0f1f3);
      --gm-accent: var(--color-text-info, #2563eb);
      --gm-accent-bg: var(--color-background-info, rgba(37, 99, 235, 0.08));
      --gm-success: var(--color-text-success, #059669);
      --gm-warning: var(--color-text-warning, #d97706);
      --gm-danger: var(--color-text-danger, #dc2626);
      --gm-success-bg: var(--color-background-success, rgba(5, 150, 105, 0.10));
      --gm-warning-bg: var(--color-background-warning, rgba(217, 119, 6, 0.10));
      --gm-danger-bg: var(--color-background-danger, rgba(220, 38, 38, 0.10));
      --gm-c2: #7c3aed;
      --gm-c3: #059669;
      --gm-c4: #d97706;
      --gm-c5: #dc2626;
      --gm-c6: #4f46e5;
      --gm-c7: #db2777;
    }
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --gm-bg: var(--color-background-primary, #fafafa);
    --gm-bg-card: var(--color-background-secondary, #ffffff);
    --gm-bg-muted: var(--color-background-tertiary, #f3f4f6);
    --gm-fg: var(--color-text-primary, #111827);
    --gm-fg-muted: var(--color-text-secondary, #4b5563);
    --gm-fg-dim: var(--color-text-tertiary, #9ca3af);
    --gm-border: var(--color-border-primary, #e5e7eb);
    --gm-border-soft: var(--color-border-secondary, #f0f1f3);
    --gm-accent: var(--color-text-info, #2563eb);
    --gm-accent-bg: var(--color-background-info, rgba(37, 99, 235, 0.08));
    --gm-success: var(--color-text-success, #059669);
    --gm-warning: var(--color-text-warning, #d97706);
    --gm-danger: var(--color-text-danger, #dc2626);
    --gm-success-bg: var(--color-background-success, rgba(5, 150, 105, 0.10));
    --gm-warning-bg: var(--color-background-warning, rgba(217, 119, 6, 0.10));
    --gm-danger-bg: var(--color-background-danger, rgba(220, 38, 38, 0.10));
    --gm-c2: #7c3aed;
    --gm-c3: #059669;
    --gm-c4: #d97706;
    --gm-c5: #dc2626;
    --gm-c6: #4f46e5;
    --gm-c7: #db2777;
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
    -webkit-font-smoothing: antialiased;
  }
  .mono, .num, .card .v, .share-meta, .subline {
    font-variant-numeric: tabular-nums;
  }
  .mono {
    font-family: var(--gm-mono);
    font-size: 11.5px;
    color: var(--gm-fg-muted);
  }
  .app {
    max-width: 1440px;
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
    padding: 8px 12px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .brand-dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--gm-success);
    flex-shrink: 0;
  }
  .brand-title {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--gm-fg);
    white-space: nowrap;
  }
  .brand-sub {
    font-size: 11.5px;
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
    align-items: center;
    gap: 2px;
    border-top: 1px solid var(--gm-border-soft);
    padding-top: 4px;
  }
  .nav-divider {
    width: 1px;
    height: 16px;
    background: var(--gm-border);
    margin: 0 6px;
  }
  .tab-btn {
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 500;
    color: var(--gm-fg-muted);
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 10px 7px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: color 0.12s, border-color 0.12s;
  }
  .tab-btn:hover { color: var(--gm-fg); }
  .tab-btn.active {
    color: var(--gm-fg);
    border-bottom-color: var(--gm-accent);
    font-weight: 600;
  }
  .tab-badge {
    font-family: var(--gm-mono);
    font-variant-numeric: tabular-nums;
    font-size: 10.5px;
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
  .seg button, .btn, select, input[type="search"], input[type="text"] {
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
    color: var(--gm-fg);
    font-weight: 600;
  }
  .btn {
    background: var(--gm-bg-muted);
    border-color: var(--gm-border);
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .btn:hover:not(:disabled) { border-color: var(--gm-accent); }
  .btn:focus-visible, select:focus-visible, input:focus-visible, .tab-btn:focus-visible {
    outline: 2px solid var(--gm-accent);
    outline-offset: 1px;
  }
  .btn.primary { background: var(--gm-accent-bg); border-color: var(--gm-accent); color: var(--gm-accent); font-weight: 600; }
  .btn.danger { background: var(--gm-danger-bg); border-color: var(--gm-danger); color: var(--gm-danger); }
  .btn.sm { padding: 3px 7px; font-size: 11px; }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  select, input[type="search"], input[type="text"] {
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
    font-size: 18px;
    font-weight: 600;
    font-family: var(--gm-mono);
    margin: 3px 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--gm-fg);
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
    color: var(--gm-fg);
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
  th.sortable:hover { color: var(--gm-fg); }
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
    padding: 1px 4px;
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
    color: var(--gm-fg-muted);
    background: var(--gm-bg-muted);
  }
  .pill.ok { background: var(--gm-success-bg); border-color: var(--gm-success); color: var(--gm-success); }
  .pill.flag, .pill.danger { background: var(--gm-danger-bg); border-color: var(--gm-danger); color: var(--gm-danger); }
  .pill.bridge, .pill.info { background: var(--gm-accent-bg); border-color: var(--gm-accent); color: var(--gm-accent); }
  .pill.warn { background: var(--gm-warning-bg); border-color: var(--gm-warning); color: var(--gm-warning); }
  .pill.neutral { color: var(--gm-fg-muted); }
  .pill-btn {
    cursor: pointer;
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border);
    color: var(--gm-fg);
    font-family: inherit;
    font-size: 11px;
    border-radius: 999px;
    padding: 2px 8px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .pill-btn:hover { border-color: var(--gm-accent); color: var(--gm-accent); }
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
    border: 1px solid var(--gm-accent);
    background: var(--gm-accent-bg);
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
    border: 1px solid var(--gm-border);
    background: var(--gm-bg-card);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
  }
  .empty {
    text-align: center;
    padding: 24px 14px;
    color: var(--gm-fg-muted);
  }
  .svg-box { width: 100%; overflow-x: auto; }
  .svg-box svg { display: block; width: 100%; height: auto; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 6px; font-size: 11px; color: var(--gm-fg-muted); }
  .legend-item { display: inline-flex; align-items: center; gap: 4px; }
  .legend-swatch { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
  .row-del { padding: 2px 7px; font-size: 11px; }
  .diag-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
  }
  ul.clean { margin: 6px 0 0; padding-left: 16px; }
  ul.clean li { margin-bottom: 3px; }

  /* Barres horizontales HTML/CSS lisibles à toutes largeurs */
  .share-list {
    display: flex;
    flex-direction: column;
    gap: 7px;
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
  .share-row.clickable:hover { border-color: var(--gm-accent); }
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
    height: 6px;
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

  /* Jalons 4 phases */
  .milestones {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 6px;
  }
  .ms-step {
    border: 1px solid var(--gm-border-soft);
    border-radius: 7px;
    padding: 6px 8px;
    background: var(--gm-bg-muted);
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 11.5px;
  }
  .ms-step.ms-done { border-color: var(--gm-success); }
  .ms-step.ms-active { border-color: var(--gm-accent); background: var(--gm-accent-bg); }
  .ms-step-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    font-weight: 600;
    color: var(--gm-fg);
  }
  .ms-dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--gm-fg-dim);
    flex-shrink: 0;
  }
  .ms-done .ms-dot { background: var(--gm-success); }
  .ms-active .ms-dot { background: var(--gm-accent); }

  /* Tiroirs & formulaires inline */
  .drawer, .inline-form {
    background: var(--gm-bg-muted);
    border: 1px solid var(--gm-border);
    border-radius: 8px;
    padding: 10px 12px;
    margin-top: 6px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 8px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-size: 11.5px;
    color: var(--gm-fg-muted);
  }
  .field input, .field select {
    width: 100%;
    background: var(--gm-bg-card);
  }

  /* Master-Detail Sessions & Audits */
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
    gap: 5px;
    max-height: 440px;
    overflow-y: auto;
  }
  @media (min-width: 920px) {
    .master-detail {
      grid-template-columns: 280px minmax(0, 1fr);
    }
    .master-pane {
      position: sticky;
      top: 10px;
      max-height: calc(100vh - 28px);
    }
    .master-list {
      max-height: calc(100vh - 220px);
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
    padding: 7px 9px;
    color: var(--gm-fg);
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-family: inherit;
  }
  .master-item:hover { border-color: var(--gm-accent); }
  .master-item.active {
    background: var(--gm-accent-bg);
    border-color: var(--gm-accent);
  }
  .master-item-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .master-item-sub {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--gm-fg-muted);
    font-family: var(--gm-mono);
    font-variant-numeric: tabular-nums;
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
    padding: 7px 10px;
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
    gap: 8px;
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
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .subtab-btn:hover { color: var(--gm-fg); }
  .subtab-btn.active {
    background: var(--gm-accent-bg);
    border-color: var(--gm-accent);
    color: var(--gm-fg);
    font-weight: 600;
  }

  /* Comparateur A vs B */
  .compare-bar {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 8px;
    align-items: end;
  }
  @media (max-width: 620px) {
    .compare-bar { grid-template-columns: 1fr; }
  }
  .delta-good { color: var(--gm-success); font-weight: 600; }
  .delta-bad { color: var(--gm-danger); font-weight: 600; }
  .delta-flat { color: var(--gm-fg-muted); }

  /* Lecteur Markdown sécurisé (Audits — colonne 74ch) */
  .md-doc {
    max-width: 74ch;
    font-size: 13px;
    line-height: 1.62;
    word-break: break-word;
  }
  .md-doc h1 { font-size: 17px; font-weight: 600; color: var(--gm-fg); margin: 4px 0 10px; border-bottom: 1px solid var(--gm-border-soft); padding-bottom: 6px; }
  .md-doc h2 { font-size: 14.5px; font-weight: 600; color: var(--gm-fg); margin: 14px 0 7px; }
  .md-doc h3 { font-size: 13.5px; font-weight: 600; color: var(--gm-fg); margin: 11px 0 5px; }
  .md-doc h4, .md-doc h5, .md-doc h6 { font-size: 12.5px; font-weight: 600; color: var(--gm-fg-muted); margin: 9px 0 4px; }
  .md-doc p { margin: 6px 0; }
  .md-doc ul, .md-doc ol { margin: 5px 0; padding-left: 20px; }
  .md-doc li { margin-bottom: 3px; }
  .md-doc li > ul, .md-doc li > ol { margin: 3px 0; }
  .md-doc blockquote {
    margin: 8px 0;
    padding: 6px 12px;
    border-left: 3px solid var(--gm-border);
    background: var(--gm-bg-muted);
    border-radius: 0 6px 6px 0;
    color: var(--gm-fg-muted);
  }
  .md-callout {
    margin: 9px 0;
    padding: 8px 12px;
    border-left: 3px solid var(--gm-accent);
    background: var(--gm-accent-bg);
    border-radius: 0 6px 6px 0;
  }
  .md-callout-warning { border-left-color: var(--gm-warning); background: var(--gm-warning-bg); }
  .md-callout-caution { border-left-color: var(--gm-danger); background: var(--gm-danger-bg); }
  .md-callout-tip { border-left-color: var(--gm-success); background: var(--gm-success-bg); }
  .md-callout-title { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 3px; color: var(--gm-fg); }
  .md-callout-body { color: var(--gm-fg); }
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
    color: var(--gm-fg-muted);
    text-decoration: underline dotted;
  }
  .md-ext-link {
    color: var(--gm-fg-muted);
    border-bottom: 1px dashed var(--gm-border);
  }
  .md-audit-link {
    font-family: inherit;
    font-size: inherit;
    color: var(--gm-accent);
    background: var(--gm-accent-bg);
    border: 1px solid var(--gm-accent);
    border-radius: 4px;
    padding: 0 5px;
    cursor: pointer;
  }
"""
