#!/usr/bin/env python3
"""Générateur du document HTML5 autonome SEP-1865 pour le widget MCP App Gravity Metrics (100 % stdlib)."""

_WIDGET_HTML = r"""<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Gravity Metrics</title>
<style>
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
    max-width: 1380px;
    margin: 0 auto;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 12px;
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
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
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  select, input[type="search"] {
    background: var(--gm-bg-muted);
    border-color: var(--gm-border);
    cursor: text;
    min-width: 0;
  }
  select { cursor: pointer; }
  .kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
    gap: 10px;
  }
  .card {
    background: var(--gm-bg-card);
    border: 1px solid var(--gm-border);
    border-radius: 10px;
    padding: 11px 13px;
    min-width: 0;
  }
  .card .k {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--gm-fg-muted);
  }
  .card .v {
    font-size: 20px;
    font-weight: 700;
    font-family: var(--gm-mono);
    margin: 3px 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card .sub {
    font-size: 12px;
    color: var(--gm-fg-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .panels {
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
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
  .filters input[type="search"] { flex: 1 1 170px; }
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
  .pill.flag { background: var(--gm-danger-bg); border-color: var(--gm-danger); color: var(--gm-danger); margin-right: 3px; }
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
  .empty {
    text-align: center;
    padding: 28px 14px;
    color: var(--gm-fg-muted);
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
  .svg-box { width: 100%; overflow-x: auto; }
  .svg-box svg { display: block; width: 100%; height: auto; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 6px; font-size: 11px; color: var(--gm-fg-muted); }
  .legend-item { display: inline-flex; align-items: center; gap: 4px; }
  .legend-swatch { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
  .row-del {
    padding: 2px 7px;
    font-size: 11px;
  }
  .diag-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
  }
  ul.clean { margin: 6px 0 0; padding-left: 16px; }
  ul.clean li { margin-bottom: 3px; }
</style>
</head>
<body>
<div id="root" class="app">
  <div class="empty">Connexion à Gravity Metrics...</div>
</div>
<script>
(function () {
  "use strict";

  const PROTOCOL_VERSION = "2026-01-26";
  const POLL_INTERVAL_MS = 4000;
  const CAT_DEFS = [
    { key: "orchestration", label: "Orchestration", color: "var(--gm-c1)" },
    { key: "discovery", label: "Découverte", color: "var(--gm-c2)" },
    { key: "build", label: "Build", color: "var(--gm-c3)" },
    { key: "testing", label: "Tests", color: "var(--gm-c4)" },
    { key: "verification", label: "Vérification", color: "var(--gm-c5)" },
    { key: "other", label: "Autre", color: "var(--gm-fg-dim)" }
  ];

  const state = {
    hostCaps: {},
    hostCtx: {},
    records: [],
    projects: [],
    signature: "",
    metricsMdUri: "",
    scope: "global",
    activeProject: "",
    selectedProject: "",
    tariff: "2026",
    view: "history",
    search: "",
    modelFilter: "",
    alertsOnly: false,
    sortBy: "date",
    sortDir: "desc",
    agentSortBy: "cost",
    agentSortDir: "desc",
    detailTarget: "",
    detail: null,
    detailSlug: "",
    detailMdUri: "",
    detailLoading: false,
    confirmDeleteTarget: "",
    confirmPurge: true,
    toast: null,
    loading: true,
    pollInFlight: false,
    hasToolResult: false
  };

  let rpcId = 1;
  const pendingRpc = new Map();
  let fallbackTimer = null;
  let pollTimer = null;

  function esc(val) {
    return String(val == null ? "" : val)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtInt(n) {
    return Math.round(Number(n) || 0).toLocaleString("fr-FR");
  }

  function fmtShortTok(n) {
    const v = Number(n) || 0;
    if (v >= 1000000) return (v / 1000000).toFixed(1) + "M";
    if (v >= 1000) return Math.round(v / 1000) + "k";
    return String(Math.round(v));
  }

  function fmtDur(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return h + "h" + String(m).padStart(2, "0") + "m";
    if (m > 0) return m + "m" + String(r).padStart(2, "0") + "s";
    return r + "s";
  }

  function fmtDate(rec) {
    const raw = String(rec.started_at || rec.added_at || "");
    return raw ? raw.slice(0, 16).replace("T", " ") : "—";
  }

  function recCost(rec) {
    if (state.tariff === "2027") {
      const p27 = rec.cost_2027_usd;
      if (p27 != null) return Number(p27) || 0;
    }
    return Number(rec.cost_usd) || 0;
  }

  function normProj(p) {
    return String(p || "").trim().toLowerCase();
  }

  function hasConversation() {
    return Boolean(state.hostCaps.message || state.hostCaps.updateModelContext);
  }

  function showToast(msg, isError) {
    state.toast = { msg: String(msg || ""), isError: Boolean(isError) };
    render();
  }

  function postToHost(payload) {
    window.parent.postMessage(payload, "*");
  }

  function sendRequest(method, params) {
    const id = rpcId++;
    return new Promise((resolve, reject) => {
      pendingRpc.set(id, { resolve, reject });
      postToHost({ jsonrpc: "2.0", id, method, params: params || {} });
    });
  }

  function sendNotification(method, params) {
    postToHost({ jsonrpc: "2.0", method, params: params || {} });
  }

  function callServerTool(name, args) {
    return sendRequest("tools/call", { name, arguments: args || {} });
  }

  function applyHostContext(ctx) {
    if (!ctx || typeof ctx !== "object") return;
    state.hostCtx = Object.assign({}, state.hostCtx, ctx);
    if (ctx.theme === "light" || ctx.theme === "dark") {
      document.documentElement.dataset.theme = ctx.theme;
      document.documentElement.style.colorScheme = ctx.theme;
    }
    const vars = ctx.styles && ctx.styles.variables;
    if (vars && typeof vars === "object") {
      const st = document.documentElement.style;
      for (const [k, v] of Object.entries(vars)) {
        if (typeof k === "string" && k.startsWith("--") && typeof v === "string") {
          st.setProperty(k, v);
        }
      }
    }
    const fontsCss = ctx.styles && ctx.styles.css && ctx.styles.css.fonts;
    if (typeof fontsCss === "string" && fontsCss.trim()) {
      let el = document.getElementById("gm-host-fonts");
      if (!el) {
        el = document.createElement("style");
        el.id = "gm-host-fonts";
        document.head.appendChild(el);
      }
      el.textContent = fontsCss;
    }
  }

  function extractStructured(res) {
    if (!res || typeof res !== "object") return null;
    if (res.structuredContent && typeof res.structuredContent === "object") {
      return res.structuredContent;
    }
    if (Array.isArray(res.content)) {
      for (const blk of res.content) {
        if (blk && blk.type === "text" && typeof blk.text === "string" && blk.text.trim().startsWith("{")) {
          try {
            return JSON.parse(blk.text);
          } catch (_) {}
        }
      }
    }
    return null;
  }

  function hydrateFromStatePayload(sc, isInitialToolResult) {
    if (!sc || typeof sc !== "object") return;
    if (Array.isArray(sc.records)) state.records = sc.records;
    if (Array.isArray(sc.projects)) state.projects = sc.projects;
    if (typeof sc.signature === "string") state.signature = sc.signature;
    if (typeof sc.metrics_md_uri === "string") state.metricsMdUri = sc.metrics_md_uri;
    state.loading = false;

    if (isInitialToolResult) {
      const initProjRaw = typeof sc.initial_project === "string" ? sc.initial_project.trim() : "";
      const initProj = initProjRaw
        ? state.projects.find((p) => normProj(p) === normProj(initProjRaw)) || initProjRaw
        : "";
      if (initProj) {
        state.activeProject = initProj;
        state.selectedProject = initProj;
        state.scope = sc.initial_scope === "global" ? "global" : "project";
      } else {
        state.scope = "global";
        state.selectedProject = "";
      }

      const initTarget = typeof sc.initial_target === "string" ? sc.initial_target.trim() : "";
      state.confirmDeleteTarget = "";
      if (sc.initial_view === "detail" && initTarget) {
        state.view = "detail";
        state.detailTarget = initTarget;
        if (sc.initial_detail && typeof sc.initial_detail === "object") {
          state.detail = sc.initial_detail;
          state.detailSlug = String(sc.initial_detail_slug || "");
          state.detailMdUri = String(sc.initial_detail_md_uri || "");
          state.detailLoading = false;
        } else {
          openSessionDetail(initTarget);
          return;
        }
      } else {
        state.view = "history";
        state.detail = null;
        state.detailTarget = "";
      }
    } else if (state.view === "detail" && state.detailTarget) {
      const stillExists = state.records.some(
        (r) => r.label === state.detailTarget || r.conversation_id === state.detailTarget
      );
      if (!stillExists) {
        state.view = "history";
        state.detail = null;
        state.detailTarget = "";
      }
    }
    render();
  }

  async function refreshState(silent) {
    if (!state.hostCaps.serverTools || state.pollInFlight) return;
    state.pollInFlight = true;
    try {
      const res = await callServerTool("metrics_get_state", {
        since_signature: silent ? state.signature : ""
      });
      const sc = extractStructured(res);
      if (sc && sc.changed !== false) {
        hydrateFromStatePayload(sc, false);
        if (state.view === "detail" && state.detailTarget) {
          try {
            const detRes = await callServerTool("metrics_get_session", { target: state.detailTarget });
            const detSc = extractStructured(detRes);
            if (detSc && detSc.detail) {
              state.detail = detSc.detail;
              state.detailSlug = String(detSc.slug || "");
              state.detailMdUri = String(detSc.report_md_uri || "");
              render();
            }
          } catch (_) {}
        }
      } else if (!silent) {
        state.loading = false;
        render();
      }
    } catch (err) {
      if (!silent) showToast("Erreur de rafraîchissement : " + err.message, true);
    } finally {
      state.pollInFlight = false;
    }
  }

  async function openSessionDetail(target) {
    if (!target) return;
    state.view = "detail";
    state.detailTarget = target;
    state.detailLoading = true;
    state.confirmDeleteTarget = "";
    render();

    if (!state.hostCaps.serverTools) {
      state.detailLoading = false;
      showToast("serverTools indisponible pour charger le détail.", true);
      return;
    }
    try {
      const res = await callServerTool("metrics_get_session", { target });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Session introuvable");
      }
      const sc = extractStructured(res);
      if (sc && sc.detail) {
        state.detail = sc.detail;
        state.detailSlug = String(sc.slug || "");
        state.detailMdUri = String(sc.report_md_uri || "");
      } else {
        throw new Error("Réponse détail invalide.");
      }
    } catch (err) {
      showToast(err.message, true);
      state.view = "history";
      state.detail = null;
    } finally {
      state.detailLoading = false;
      render();
    }
  }

  async function confirmDeleteSession() {
    const target = state.confirmDeleteTarget;
    if (!target || !state.hostCaps.serverTools) return;
    try {
      const res = await callServerTool("metrics_remove_session", {
        target,
        purge_reports: Boolean(state.confirmPurge)
      });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Suppression échouée");
      }
      const sc = extractStructured(res);
      state.confirmDeleteTarget = "";
      if (sc) {
        if (state.view === "detail" && state.detailTarget === target) {
          state.view = "history";
          state.detail = null;
          state.detailTarget = "";
        }
        hydrateFromStatePayload(sc, false);
        showToast("Session « " + target + " » supprimée.", false);
      }
    } catch (err) {
      showToast("Erreur suppression : " + err.message, true);
    }
  }

  async function openMarkdownFile(uri) {
    if (!uri) {
      showToast("Fichier Markdown introuvable pour cette vue.", true);
      return;
    }
    try {
      const res = await sendRequest("com.google.antigravity/toggle-aux-pane", {
        tabType: "fileView",
        uri: uri
      });
      if (res && res.isError) {
        showToast("Impossible d'ouvrir l'onglet fichier (" + uri + ").", true);
      }
    } catch (err) {
      showToast("Ouverture non supportée sur cette vue (" + err.message + ").", true);
    }
  }

  function buildSessionContextText(d) {
    const s = d.summary || {};
    const c = s.cost_usd || {};
    const eff = d.efficiency_and_waste || {};
    const dup = eff.duplicate_role_spawns || {};
    const rw = eff.rewind_waste || {};
    const intr = eff.interrupted_agents || {};
    const cb = eff.context_bloat || {};
    return [
      "Session Gravity Metrics : " + (d.label || ""),
      "Conversation racine : " + (d.root_conversation_id || ""),
      "Projet : " + (d.project || "—") + " | Profil : " + (d.gravity_profile || "—"),
      "Mode : " + (s.execution_mode || "single") + " | Durée : " + fmtDur(s.wall_clock_s) + " (" + Number(s.parallelism_factor || 1).toFixed(2) + "x)",
      "Coût 2026 : $" + Number(c.total_usd || 0).toFixed(4) + " (Proj 2027 : $" + Number((s.cost_usd_projection || {}).total_usd || 0).toFixed(4) + ") | Cache Hit : " + Number(s.cache_hit_rate_pct || 0).toFixed(2) + "%",
      "Agents : " + (s.total_agents || 0) + " | Appels LLM : " + (s.total_llm_calls || 0) + " | Erreurs : " + (s.total_error_steps || 0) + " (" + Number(s.error_rate_pct || 0).toFixed(2) + "%)",
      "Gaspillages : Dup=" + (dup.duplicate_agent_count || 0) + ", Rewinds=" + (rw.rewind_calls || 0) + ", Interrompus=" + (intr.count || 0) + ", MaxCtx=" + fmtInt(cb.max_context_tokens_overall || 0)
    ].join("\n");
  }

  async function attachSessionToPrompt() {
    if (!state.detail || !state.hostCaps.updateModelContext) return;
    try {
      const summary = buildSessionContextText(state.detail);
      await sendRequest("ui/update-model-context", {
        content: [{ type: "text", text: summary }],
        structuredContent: {
          label: state.detail.label,
          root_conversation_id: state.detail.root_conversation_id,
          project: state.detail.project,
          summary: state.detail.summary
        }
      });
      showToast("Session « " + state.detail.label + " » jointe au prochain prompt.", false);
    } catch (err) {
      showToast("Erreur attachement : " + err.message, true);
    }
  }

  async function analyzeSessionWithAgent() {
    if (!state.detail || !state.hostCaps.message) return;
    try {
      const prompt = "Analyse cette session Gravity Metrics et propose 2-3 optimisations concrètes (coût, cache, gaspillages, modèles) :\n\n" + buildSessionContextText(state.detail);
      const res = await sendRequest("ui/message", {
        role: "user",
        content: [{ type: "text", text: prompt }]
      });
      if (res && res.isError) {
        showToast("Envoi refusé par l'hôte.", true);
      } else {
        showToast("Analyse envoyée dans le chat.", false);
      }
    } catch (err) {
      showToast("Erreur envoi chat : " + err.message, true);
    }
  }

  function scopedRecords() {
    let list = state.records.slice();
    if (state.scope === "project" && state.selectedProject) {
      const selCf = normProj(state.selectedProject);
      list = list.filter((r) => normProj(r.project) === selCf);
    }
    return list;
  }

  function filteredSortedRecords() {
    let list = scopedRecords();
    const q = state.search.trim().toLowerCase();
    if (q) {
      list = list.filter((r) => {
        const hay = [
          r.label,
          r.project,
          r.primary_model,
          r.note,
          r.conversation_id,
          r.execution_mode
        ].join(" ").toLowerCase();
        return hay.includes(q);
      });
    }
    if (state.modelFilter) {
      list = list.filter((r) => {
        const ms = r.models || {};
        return Boolean(ms[state.modelFilter]) || r.primary_model === state.modelFilter;
      });
    }
    if (state.alertsOnly) {
      list = list.filter((r) =>
        (Number(r.duplicate_spawns) || 0) +
        (Number(r.rewind_calls) || 0) +
        (Number(r.interrupted_agents) || 0) > 0
      );
    }

    const dir = state.sortDir === "asc" ? 1 : -1;
    list.sort((a, b) => {
      let va, vb;
      switch (state.sortBy) {
        case "label": va = String(a.label || "").toLowerCase(); vb = String(b.label || "").toLowerCase(); break;
        case "project": va = String(a.project || "").toLowerCase(); vb = String(b.project || "").toLowerCase(); break;
        case "cost": va = recCost(a); vb = recCost(b); break;
        case "cache": va = Number(a.cache_hit_rate_pct) || 0; vb = Number(b.cache_hit_rate_pct) || 0; break;
        case "agents": va = Number(a.agents) || 0; vb = Number(b.agents) || 0; break;
        case "calls": va = Number(a.llm_calls) || 0; vb = Number(b.llm_calls) || 0; break;
        case "dur": va = Number(a.wall_clock_s) || 0; vb = Number(b.wall_clock_s) || 0; break;
        default: va = String(a.started_at || a.added_at || ""); vb = String(b.started_at || b.added_at || ""); break;
      }
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    return list;
  }

  function renderCostEvolutionSvg(records) {
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
      const shortLbl = lbl.length <= 11 ? lbl : lbl.slice(0, 9) + "..";
      svg += '<g class="svg-pt" data-action="open-detail" data-target="' + esc(lbl) + '" style="cursor:pointer">';
      svg += '<title>' + esc(lbl) + ' : $' + costs[i].toFixed(4) + '</title>';
      svg += '<circle cx="' + cx + '" cy="' + cy + '" r="4.8" fill="var(--gm-c1)" stroke="var(--gm-bg-card)" stroke-width="2"/>';
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
      const shortLbl = lbl.length <= 20 ? lbl : lbl.slice(0, 17) + "...";
      const cs = r.category_share_pct || {};
      svg += '<g data-action="open-detail" data-target="' + esc(lbl) + '" style="cursor:pointer">';
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

  function renderTopbar() {
    const projectMap = new Map();
    state.projects.forEach((p) => {
      const clean = String(p || "").trim();
      const cf = normProj(clean);
      if (cf && !projectMap.has(cf)) projectMap.set(cf, clean);
    });
    if (state.activeProject) {
      const cleanAct = String(state.activeProject).trim();
      const cfAct = normProj(cleanAct);
      if (cfAct && !projectMap.has(cfAct)) projectMap.set(cfAct, cleanAct);
    }
    const projectOptions = Array.from(projectMap.values()).sort((a, b) =>
      a.localeCompare(b, "fr", { sensitivity: "base" })
    );

    let scopeHtml = '<div class="seg" role="group" aria-label="Portée">';
    const projBtnLabel = state.activeProject
      ? "Projet : " + state.activeProject
      : (state.selectedProject ? "Projet : " + state.selectedProject : "Par projet");
    scopeHtml += '<button type="button" data-action="set-scope" data-scope="project" class="' + (state.scope === "project" ? "active" : "") + '"' + (projectOptions.length ? "" : " disabled") + '>' + esc(projBtnLabel) + '</button>';
    scopeHtml += '<button type="button" data-action="set-scope" data-scope="global" class="' + (state.scope === "global" ? "active" : "") + '">Global (tous)</button>';
    scopeHtml += '</div>';

    let projSelect = "";
    if (projectOptions.length > 0 && state.scope === "project") {
      projSelect = '<select id="gm-proj-select" aria-label="Projet">';
      projectOptions.forEach((p) => {
        projSelect += '<option value="' + esc(p) + '"' + (normProj(p) === normProj(state.selectedProject) ? " selected" : "") + '>' + esc(p) + '</option>';
      });
      projSelect += '</select>';
    }

    const tariffHtml = '<div class="seg" role="group" aria-label="Tarif">' +
      '<button type="button" data-action="set-tariff" data-tariff="2026" class="' + (state.tariff === "2026" ? "active" : "") + '">2026</button>' +
      '<button type="button" data-action="set-tariff" data-tariff="2027" class="' + (state.tariff === "2027" ? "active" : "") + '">Proj. 2027</button>' +
      '</div>';

    const openGlobalMdBtn = (hasConversation() && state.metricsMdUri)
      ? '<button type="button" class="btn" data-action="open-file" data-uri="' + esc(state.metricsMdUri) + '">METRICS.md</button>'
      : "";

    const refreshBtn = state.hostCaps.serverTools
      ? '<button type="button" class="btn" data-action="refresh">Rafraîchir</button>'
      : "";

    return '<div class="topbar">' +
      '<div class="brand">' +
      '<span class="brand-dot" title="Synchronisation 0 token active"></span>' +
      '<div><div class="brand-title">Gravity Metrics</div>' +
      '<div class="brand-sub">' + esc(state.records.length) + ' session(s) au total' +
      (state.scope === "project" && state.selectedProject ? ' · filtre : ' + esc(state.selectedProject) : ' · vue globale') +
      '</div></div></div>' +
      '<div class="controls">' + scopeHtml + projSelect + tariffHtml + openGlobalMdBtn + refreshBtn + '</div>' +
      '</div>';
  }

  function renderDeleteConfirm() {
    if (!state.confirmDeleteTarget) return "";
    return '<div class="confirm-bar">' +
      '<div><strong>Supprimer la session « ' + esc(state.confirmDeleteTarget) + ' » ?</strong> ' +
      '<label style="margin-left:8px;font-size:12px;cursor:pointer;">' +
      '<input id="gm-purge-cb" type="checkbox"' + (state.confirmPurge ? " checked" : "") + ' /> Purger aussi <code>reports/</code>' +
      '</label></div>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="btn danger" data-action="confirm-delete">Confirmer</button>' +
      '<button type="button" class="btn" data-action="cancel-delete">Annuler</button>' +
      '</div></div>';
  }

  function renderHistoryView() {
    const scoped = scopedRecords();
    const list = filteredSortedRecords();
    const totalCost = scoped.reduce((acc, r) => acc + recCost(r), 0);
    const avgCost = scoped.length ? totalCost / scoped.length : 0;
    const avgCache = scoped.length
      ? scoped.reduce((acc, r) => acc + (Number(r.cache_hit_rate_pct) || 0), 0) / scoped.length
      : 0;
    const totalDur = scoped.reduce((acc, r) => acc + (Number(r.wall_clock_s) || 0), 0);
    const latest = scoped.length ? scoped[scoped.length - 1] : null;
    const best = scoped.length
      ? scoped.reduce((m, r) => (recCost(r) < recCost(m) ? r : m), scoped[0])
      : null;

    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Dernière session</div><div class="v">$' + (latest ? recCost(latest).toFixed(4) : "0.0000") + '</div><div class="sub">' + esc(latest ? latest.label : "—") + '</div></div>' +
      '<div class="card"><div class="k">Coût cumulé</div><div class="v">$' + totalCost.toFixed(4) + '</div><div class="sub">' + scoped.length + ' session(s) · ' + esc(fmtDur(totalDur)) + '</div></div>' +
      '<div class="card"><div class="k">Coût moyen</div><div class="v">$' + avgCost.toFixed(4) + '</div><div class="sub">Cache moyen : ' + avgCache.toFixed(1) + '%</div></div>' +
      '<div class="card"><div class="k">Meilleur coût</div><div class="v">$' + (best ? recCost(best).toFixed(4) : "0.0000") + '</div><div class="sub">' + esc(best ? best.label : "—") + '</div></div>' +
      '</div>';

    const charts = '<div class="panels two">' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Évolution du Coût (' + esc(state.tariff === "2027" ? "Proj. 2027" : "2026") + ')</span></div>' +
      '<div class="svg-box">' + renderCostEvolutionSvg(scoped) + '</div></div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Répartition par Catégorie (% Coût)</span></div>' +
      '<div class="svg-box">' + renderCategoryStackedSvg(scoped) + '</div></div>' +
      '</div>';

    const modelSet = new Set();
    scoped.forEach((r) => {
      if (r.primary_model) modelSet.add(r.primary_model);
      Object.keys(r.models || {}).forEach((m) => modelSet.add(m));
    });
    const modelsList = Array.from(modelSet).sort();

    let modelSel = '<select id="gm-model-filter" aria-label="Filtrer par modèle"><option value="">Tous les modèles</option>';
    modelsList.forEach((m) => {
      modelSel += '<option value="' + esc(m) + '"' + (m === state.modelFilter ? " selected" : "") + '>' + esc(m) + '</option>';
    });
    modelSel += '</select>';

    const sortArrow = (col) => state.sortBy === col ? (state.sortDir === "asc" ? " ▲" : " ▼") : "";

    let rowsHtml = "";
    list.forEach((r) => {
      const lbl = String(r.label || "");
      const rowKey = lbl || String(r.conversation_id || "");
      const dups = Number(r.duplicate_spawns) || 0;
      const rws = Number(r.rewind_calls) || 0;
      const ints = Number(r.interrupted_agents) || 0;
      let alerts = "";
      if (dups) alerts += '<span class="pill flag">Dup:' + dups + '</span>';
      if (rws) alerts += '<span class="pill flag">Rw:' + rws + '</span>';
      if (ints) alerts += '<span class="pill flag">Int:' + ints + '</span>';
      if (!alerts) alerts = '<span class="pill ok">Clean</span>';

      const tokTxt = fmtShortTok(r.input_tokens) + " / " + fmtShortTok(r.cache_read_tokens) + " / " + fmtShortTok(r.output_tokens);
      const mEntries = Object.entries(r.models || {}).sort((a, b) => (Number(b[1].calls) || 0) - (Number(a[1].calls) || 0));
      const mCell = mEntries.length
        ? '<code>' + esc(mEntries[0][0]) + '</code> (' + (Number(mEntries[0][1].calls) || 0) + ')' +
          (mEntries.length > 1 ? '<div class="subline">+' + (mEntries.length - 1) + ' autre(s)</div>' : '')
        : '<code>' + esc(r.primary_model || "—") + '</code>';

      const mode = String(r.execution_mode || "single");
      const gp = String(r.gravity_profile || "");
      const modeCell = '<code>' + esc(mode) + '</code>' + (gp ? '<div class="subline">' + esc(gp) + '</div>' : '');

      const delBtn = state.hostCaps.serverTools
        ? '<button type="button" class="btn row-del" data-action="ask-delete" data-target="' + esc(rowKey) + '" title="Supprimer">✕</button>'
        : "";

      rowsHtml += '<tr class="clickable" data-action="open-detail" data-target="' + esc(rowKey) + '">' +
        '<td><code>' + esc(fmtDate(r)) + '</code></td>' +
        '<td><strong>' + esc(lbl) + '</strong><div class="subline">' + esc(String(r.conversation_id || "").slice(0, 8)) + (r.note ? ' · ' + esc(r.note) : '') + '</div></td>' +
        '<td><code>' + esc(r.project || "—") + '</code></td>' +
        '<td>' + modeCell + '</td>' +
        '<td>' + mCell + '</td>' +
        '<td class="num"><strong>$' + recCost(r).toFixed(4) + '</strong></td>' +
        '<td class="num">' + esc(tokTxt) + '</td>' +
        '<td class="num">' + (Number(r.cache_hit_rate_pct) || 0).toFixed(1) + '%</td>' +
        '<td class="num">' + (Number(r.agents) || 0) + '</td>' +
        '<td class="num">' + fmtInt(r.llm_calls) + '</td>' +
        '<td class="num">' + esc(fmtDur(r.wall_clock_s)) + '</td>' +
        '<td>' + alerts + '</td>' +
        '<td>' + delBtn + '</td>' +
        '</tr>';
    });

    const tableContent = list.length
      ? '<div class="table-wrap"><table><thead><tr>' +
        '<th class="sortable" data-action="sort" data-col="date">Date (UTC)' + sortArrow("date") + '</th>' +
        '<th class="sortable" data-action="sort" data-col="label">Label' + sortArrow("label") + '</th>' +
        '<th class="sortable" data-action="sort" data-col="project">Projet' + sortArrow("project") + '</th>' +
        '<th>Mode</th><th>Modèles</th>' +
        '<th class="num sortable" data-action="sort" data-col="cost">Coût' + sortArrow("cost") + '</th>' +
        '<th class="num">In / Cache / Out</th>' +
        '<th class="num sortable" data-action="sort" data-col="cache">Cache %' + sortArrow("cache") + '</th>' +
        '<th class="num sortable" data-action="sort" data-col="agents">Ag.' + sortArrow("agents") + '</th>' +
        '<th class="num sortable" data-action="sort" data-col="calls">Appels' + sortArrow("calls") + '</th>' +
        '<th class="num sortable" data-action="sort" data-col="dur">Durée' + sortArrow("dur") + '</th>' +
        '<th>Alertes</th><th></th>' +
        '</tr></thead><tbody>' + rowsHtml + '</tbody></table></div>'
      : '<div class="empty">Aucune session trouvée' +
        (state.scope === "project" && state.selectedProject ? ' pour le projet <code>' + esc(state.selectedProject) + '</code>' : '') +
        '. Lancez <code>/gravity-metrics</code> à la fin d’une session.</div>';

    const listPanel = '<div class="panel">' +
      '<div class="panel-head">' +
      '<span class="panel-title">Sessions (' + list.length + '/' + scoped.length + ')</span>' +
      '</div>' +
      '<div class="filters">' +
      '<input id="gm-search" type="search" placeholder="Rechercher label, projet, modèle, note..." value="' + esc(state.search) + '" />' +
      modelSel +
      '<label style="font-size:12px;display:inline-flex;align-items:center;gap:4px;cursor:pointer;">' +
      '<input id="gm-alerts-only" type="checkbox"' + (state.alertsOnly ? " checked" : "") + ' /> Alertes uniquement' +
      '</label>' +
      '</div>' +
      tableContent +
      '</div>';

    return kpis + charts + listPanel;
  }

  function renderDetailView() {
    if (state.detailLoading && !state.detail) {
      return '<div class="panel"><div class="empty">Chargement du détail de « ' + esc(state.detailTarget) + ' »...</div></div>';
    }
    const d = state.detail;
    if (!d) {
      return '<div class="panel"><div class="empty">Détail indisponible. <button class="btn" data-action="back-history">← Retour</button></div></div>';
    }
    const s = d.summary || {};
    const c = s.cost_usd || {};
    const p = s.cost_usd_projection || {};
    const eff = d.efficiency_and_waste || {};
    const dup = eff.duplicate_role_spawns || {};
    const rw = eff.rewind_waste || {};
    const intr = eff.interrupted_agents || {};
    const cb = eff.context_bloat || {};
    const tu = d.tool_usage || {};
    const dq = d.data_quality || {};
    const win = d.window || {};
    const use2027 = state.tariff === "2027";
    const shownTotal = use2027 ? Number(p.total_usd || 0) : Number(c.total_usd || 0);
    const otherTotal = use2027 ? Number(c.total_usd || 0) : Number(p.total_usd || 0);

    let actionBtns = "";
    if (hasConversation() && state.detailMdUri) {
      actionBtns += '<button type="button" class="btn" data-action="open-file" data-uri="' + esc(state.detailMdUri) + '">Ouvrir .md</button>';
    }
    if (state.hostCaps.updateModelContext) {
      actionBtns += '<button type="button" class="btn" data-action="attach-context">Joindre au prompt</button>';
    }
    if (state.hostCaps.message) {
      actionBtns += '<button type="button" class="btn primary" data-action="analyze-session">Analyser</button>';
    }
    if (state.hostCaps.serverTools) {
      actionBtns += '<button type="button" class="btn danger" data-action="ask-delete" data-target="' + esc(d.label) + '">Supprimer</button>';
    }

    const headPanel = '<div class="panel detail-head">' +
      '<div class="detail-row">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
      '<button type="button" class="btn" data-action="back-history">← Historique</button>' +
      '<strong style="font-size:15px;">' + esc(d.label) + '</strong>' +
      '</div>' +
      '<div class="controls">' + actionBtns + '</div>' +
      '</div>' +
      '<div class="detail-meta">' +
      '<span>Racine : <code>' + esc(String(d.root_conversation_id || "").slice(0, 12)) + '</code></span>' +
      (d.project ? '<span>· Projet : <code>' + esc(d.project) + '</code></span>' : '') +
      (d.gravity_profile ? '<span>· Profil : <code>' + esc(d.gravity_profile) + '</code></span>' : '') +
      (win.mode ? '<span>· Fenêtre : <code>' + esc(win.mode) + '</code></span>' : '') +
      '<span>· Durée : <code>' + esc(fmtDur(s.wall_clock_s)) + '</code></span>' +
      '</div></div>';

    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Coût Total (' + (use2027 ? "2027" : "2026") + ')</div><div class="v">$' + shownTotal.toFixed(4) + '</div><div class="sub">' + (use2027 ? "2026" : "Proj. 2027") + ' : $' + otherTotal.toFixed(4) + '</div></div>' +
      '<div class="card"><div class="k">Économie Cache</div><div class="v">$' + Number(c.cache_savings_usd || 0).toFixed(4) + '</div><div class="sub">Cache Hit : ' + Number(s.cache_hit_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Agents / Appels LLM</div><div class="v">' + (s.total_agents || 0) + ' / ' + fmtInt(s.total_llm_calls) + '</div><div class="sub">Parall. : ' + Number(s.parallelism_factor || 1).toFixed(2) + 'x · Err : ' + Number(s.error_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Gaspillages &amp; Ctx</div><div class="v">' + (dup.duplicate_agent_count || 0) + ' dup · ' + (rw.rewind_calls || 0) + ' rw</div><div class="sub">Int : ' + (intr.count || 0) + ' ($' + Number(intr.total_cost_usd || 0).toFixed(3) + ') · Max : ' + fmtInt(cb.max_context_tokens_overall) + '</div></div>' +
      '</div>';

    const catMap = Object.fromEntries(CAT_DEFS.map((x) => [x.key, x]));
    const catItems = Object.entries(d.by_category || {})
      .filter(([, v]) => (v.agents || 0) > 0)
      .map(([k, v]) => {
        const def = catMap[k] || { label: k, color: "var(--gm-fg-dim)" };
        const costObj = use2027 ? (v.cost_usd_projection || v.cost_usd) : v.cost_usd;
        return { label: def.label, val: Number((costObj || {}).total_usd || 0), color: def.color };
      });

    const splitItems = [
      { label: "Cache Read (contexte)", val: Number(c.cache_read_usd || 0), color: "var(--gm-c6)" },
      { label: "Input (tokens frais)", val: Number(c.input_usd || 0), color: "var(--gm-c2)" },
      { label: "Output (réponse + thinking)", val: Number(c.output_usd || 0), color: "var(--gm-c7)" }
    ];

    const phaseItems = Object.entries(d.by_phase || {}).map(([pname, pdata]) => {
      const costObj = use2027 ? (pdata.cost_usd_projection || pdata.cost_usd) : pdata.cost_usd;
      return {
        label: pname,
        val: Number((costObj || {}).total_usd || 0),
        color: pname.indexOf("Overhead") >= 0 ? "var(--gm-c1)" : "var(--gm-c3)"
      };
    });

    const charts = '<div class="panels two">' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Coût par Catégorie</span></div><div class="svg-box">' + renderHorizontalBarsSvg(catItems) + '</div></div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Ventilation (Cache / Input / Output)</span></div><div class="svg-box">' + renderHorizontalBarsSvg(splitItems) + '</div></div>' +
      '</div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Coût par Phase d’Exécution</span></div><div class="svg-box">' + renderHorizontalBarsSvg(phaseItems) + '</div></div>';

    let modelRows = "";
    Object.entries(d.by_model || {}).forEach(([mName, mData]) => {
      const c26 = Number((mData.cost_usd || {}).total_usd || 0);
      const c27 = Number((mData.cost_usd_projection || {}).total_usd || 0);
      modelRows += '<tr>' +
        '<td><code>' + esc(mName) + '</code>' + (mData.is_fallback ? ' <span class="subline">(fallback)</span>' : '') + '</td>' +
        '<td class="num">' + (mData.agents_using || 0) + '</td>' +
        '<td class="num">' + fmtInt(mData.calls) + '</td>' +
        '<td class="num">' + fmtShortTok(mData.input_tokens) + ' / ' + fmtShortTok(mData.cache_read_tokens) + ' / ' + fmtShortTok(mData.output_tokens) + '</td>' +
        '<td class="num">$' + c26.toFixed(4) + '</td>' +
        '<td class="num">$' + c27.toFixed(4) + '</td>' +
        '<td class="num">' + Number(mData.cost_share_pct || 0).toFixed(1) + '%</td>' +
        '</tr>';
    });
    const modelsPanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Modèles</span></div>' +
      '<div class="table-wrap"><table><thead><tr><th>Modèle</th><th class="num">Ag.</th><th class="num">Appels</th><th class="num">In / Cache / Out</th><th class="num">Coût 2026</th><th class="num">Proj 2027</th><th class="num">Part</th></tr></thead>' +
      '<tbody>' + modelRows + '</tbody></table></div></div>';

    const agents = (d.agents || []).slice();
    const adir = state.agentSortDir === "asc" ? 1 : -1;
    agents.sort((a, b) => {
      const ca = use2027 ? Number(((a.cost_usd_projection || a.cost_usd) || {}).total_usd || 0) : Number((a.cost_usd || {}).total_usd || 0);
      const cbv = use2027 ? Number(((b.cost_usd_projection || b.cost_usd) || {}).total_usd || 0) : Number((b.cost_usd || {}).total_usd || 0);
      let va, vb;
      if (state.agentSortBy === "calls") { va = a.llm_calls || 0; vb = b.llm_calls || 0; }
      else if (state.agentSortBy === "ctx") { va = a.max_context_tokens || 0; vb = b.max_context_tokens || 0; }
      else { va = ca; vb = cbv; }
      return va < vb ? -adir : (va > vb ? adir : 0);
    });

    const aArrow = (col) => state.agentSortBy === col ? (state.agentSortDir === "asc" ? " ▲" : " ▼") : "";
    let agRows = "";
    agents.forEach((ag) => {
      const def = catMap[ag.category] || { color: "var(--gm-fg-dim)" };
      const costObj = use2027 ? (ag.cost_usd_projection || ag.cost_usd) : ag.cost_usd;
      const flags = [];
      if (!ag.clean_termination) flags.push("Interrompu");
      if ((ag.rewind_calls || 0) > 0) flags.push("Rw:" + ag.rewind_calls);
      if ((ag.max_context_tokens || 0) > 128000) flags.push(">128k");
      const statusHtml = flags.length
        ? flags.map((f) => '<span class="pill flag">' + esc(f) + '</span>').join("")
        : '<span class="pill ok">OK</span>';
      agRows += '<tr>' +
        '<td><code>' + esc(String(ag.conversation_id || "").slice(0, 8)) + '</code></td>' +
        '<td>' + esc(ag.role) + '</td>' +
        '<td><span class="pill" style="border-color:' + def.color + ';color:' + def.color + '">' + esc(ag.category) + '</span></td>' +
        '<td class="num">' + (ag.depth || 0) + '</td>' +
        '<td class="num">' + fmtInt(ag.llm_calls) + '</td>' +
        '<td class="num">' + fmtShortTok(ag.input_tokens) + ' / ' + fmtShortTok(ag.cache_read_tokens) + ' / ' + fmtShortTok(ag.output_tokens) + '</td>' +
        '<td class="num">' + fmtInt(ag.max_context_tokens) + '</td>' +
        '<td class="num">' + Number(ag.cache_hit_rate_pct || 0).toFixed(1) + '%</td>' +
        '<td class="num"><strong>$' + Number((costObj || {}).total_usd || 0).toFixed(4) + '</strong></td>' +
        '<td>' + statusHtml + '</td>' +
        '</tr>';
    });

    const agentsPanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Agents (' + agents.length + ')</span></div>' +
      '<div class="table-wrap"><table><thead><tr>' +
      '<th>ID</th><th>Rôle</th><th>Catégorie</th><th class="num">Prof.</th>' +
      '<th class="num sortable" data-action="asort" data-col="calls">Appels' + aArrow("calls") + '</th>' +
      '<th class="num">In / Cache / Out</th>' +
      '<th class="num sortable" data-action="asort" data-col="ctx">Max Ctx' + aArrow("ctx") + '</th>' +
      '<th class="num">Cache %</th>' +
      '<th class="num sortable" data-action="asort" data-col="cost">Coût' + aArrow("cost") + '</th>' +
      '<th>Statut</th></tr></thead><tbody>' + agRows + '</tbody></table></div></div>';

    const toolsList = Object.entries(tu.by_tool || {}).slice(0, 10)
      .map(([k, v]) => '<li><code>' + esc(k) + '</code> : <strong>' + fmtInt(v) + '</strong></li>').join("");
    const errList = (tu.top_errors || []).slice(0, 6)
      .map((e) => '<li>(' + esc(e.count) + '×) <code>' + esc(e.reason) + '</code></li>').join("");

    const diagPanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Diagnostic, Outillage &amp; Qualité</span></div>' +
      '<div class="diag-grid">' +
      '<div><strong>Gaspillages</strong><ul class="clean">' +
      '<li>Ratio Vérif / Build+Test : <strong>' + (eff.verification_to_build_cost_ratio == null ? 'N/A' : Number(eff.verification_to_build_cost_ratio).toFixed(2) + 'x') + '</strong></li>' +
      '<li>Doublons : <strong>' + (dup.duplicate_agent_count || 0) + '</strong> ($' + Number(dup.duplicate_cost_usd || 0).toFixed(4) + ')</li>' +
      '<li>Rewinds : <strong>' + (rw.rewind_calls || 0) + '</strong> ($' + Number(rw.rewind_cost_usd || 0).toFixed(4) + ')</li>' +
      '<li>Ctx &gt;100k / &gt;128k / &gt;200k : ' + (cb.calls_over_100k_ctx || 0) + ' / ' + (cb.calls_over_128k_ctx || 0) + ' / ' + (cb.calls_over_200k_ctx || 0) + '</li>' +
      '</ul></div>' +
      '<div><strong>Top Outils (' + fmtInt(tu.total_calls || 0) + ')</strong><ul class="clean">' + (toolsList || '<li>Aucun appel outil</li>') + '</ul></div>' +
      '<div><strong>Erreurs &amp; Qualité données</strong><ul class="clean">' +
      (errList || '<li>Aucune erreur enregistrée</li>') +
      '<li>Fallback : <code>' + esc((dq.unmatched_models || []).join(", ") || "aucun") + '</code></li>' +
      '<li>JSON malformés : <code>' + (dq.malformed_lines || 0) + '</code></li>' +
      '</ul></div></div></div>';

    return headPanel + kpis + charts + modelsPanel + agentsPanel + diagPanel;
  }

  function render() {
    const root = document.getElementById("root");
    if (!root) return;

    const activeEl = document.activeElement;
    const focusId = activeEl && activeEl.id === "gm-search" ? "gm-search" : "";
    const selStart = focusId ? activeEl.selectionStart : null;
    const selEnd = focusId ? activeEl.selectionEnd : null;

    let htmlStr = renderTopbar();
    if (state.toast) {
      htmlStr += '<div class="toast' + (state.toast.isError ? ' err' : '') + '">' +
        '<span>' + esc(state.toast.msg) + '</span>' +
        '<button type="button" class="btn" data-action="close-toast" style="padding:2px 7px;">✕</button>' +
        '</div>';
    }
    htmlStr += renderDeleteConfirm();

    if (state.loading && !state.records.length) {
      htmlStr += '<div class="panel"><div class="empty">Chargement des métriques...</div></div>';
    } else if (state.view === "detail") {
      htmlStr += renderDetailView();
    } else {
      htmlStr += renderHistoryView();
    }

    root.innerHTML = htmlStr;

    if (focusId) {
      const inp = document.getElementById(focusId);
      if (inp) {
        inp.focus();
        if (selStart !== null && selEnd !== null) {
          inp.setSelectionRange(selStart, selEnd);
        }
      }
    }
  }

  document.addEventListener("click", (ev) => {
    const baseEl = ev.target instanceof Element ? ev.target : (ev.target && ev.target.parentElement);
    const target = baseEl ? baseEl.closest("[data-action]") : null;
    if (!target) return;
    const action = target.getAttribute("data-action");
    if (action === "ask-delete") {
      ev.stopPropagation();
      state.confirmDeleteTarget = target.getAttribute("data-target") || "";
      render();
      return;
    }
    if (action === "cancel-delete") {
      state.confirmDeleteTarget = "";
      render();
      return;
    }
    if (action === "confirm-delete") {
      confirmDeleteSession();
      return;
    }
    if (action === "close-toast") {
      state.toast = null;
      render();
      return;
    }
    if (action === "set-scope") {
      state.scope = target.getAttribute("data-scope") || "global";
      if (state.scope === "project" && !state.selectedProject) {
        state.selectedProject = state.activeProject || state.projects[0] || "";
      }
      render();
      return;
    }
    if (action === "set-tariff") {
      state.tariff = target.getAttribute("data-tariff") || "2026";
      render();
      return;
    }
    if (action === "refresh") {
      refreshState(false);
      return;
    }
    if (action === "open-file") {
      openMarkdownFile(target.getAttribute("data-uri") || "");
      return;
    }
    if (action === "open-detail") {
      const t = target.getAttribute("data-target") || "";
      if (t) openSessionDetail(t);
      return;
    }
    if (action === "back-history") {
      state.view = "history";
      state.detail = null;
      state.detailTarget = "";
      state.confirmDeleteTarget = "";
      render();
      return;
    }
    if (action === "attach-context") {
      attachSessionToPrompt();
      return;
    }
    if (action === "analyze-session") {
      analyzeSessionWithAgent();
      return;
    }
    if (action === "sort") {
      const col = target.getAttribute("data-col") || "date";
      if (state.sortBy === col) {
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      } else {
        state.sortBy = col;
        state.sortDir = (col === "label" || col === "project") ? "asc" : "desc";
      }
      render();
      return;
    }
    if (action === "asort") {
      const col = target.getAttribute("data-col") || "cost";
      if (state.agentSortBy === col) {
        state.agentSortDir = state.agentSortDir === "asc" ? "desc" : "asc";
      } else {
        state.agentSortBy = col;
        state.agentSortDir = "desc";
      }
      render();
    }
  });

  document.addEventListener("input", (ev) => {
    const el = ev.target;
    if (!el || !el.id) return;
    if (el.id === "gm-search") {
      state.search = el.value || "";
      render();
    }
  });

  document.addEventListener("change", (ev) => {
    const el = ev.target;
    if (!el || !el.id) return;
    if (el.id === "gm-proj-select") {
      state.selectedProject = el.value || "";
      render();
    } else if (el.id === "gm-model-filter") {
      state.modelFilter = el.value || "";
      render();
    } else if (el.id === "gm-alerts-only") {
      state.alertsOnly = Boolean(el.checked);
      render();
    } else if (el.id === "gm-purge-cb") {
      state.confirmPurge = Boolean(el.checked);
    }
  });

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (!msg || typeof msg !== "object") return;

    if (msg.id !== undefined && msg.id !== null && pendingRpc.has(msg.id) && !msg.method) {
      const entry = pendingRpc.get(msg.id);
      pendingRpc.delete(msg.id);
      if (msg.error) {
        entry.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
      } else {
        entry.resolve(msg.result);
      }
      return;
    }

    if (msg.id !== undefined && msg.id !== null && msg.method) {
      if (msg.method === "ping" || msg.method === "ui/resource-teardown") {
        postToHost({ jsonrpc: "2.0", id: msg.id, result: {} });
      } else {
        postToHost({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: "Method not found" } });
      }
      return;
    }

    if (msg.method === "ui/notifications/host-context-changed") {
      applyHostContext(msg.params || {});
      return;
    }

    if (msg.method === "ui/notifications/tool-input") {
      const args = (msg.params && msg.params.arguments) || {};
      if (typeof args.project === "string" && args.project.trim()) {
        state.activeProject = args.project.trim();
        state.selectedProject = args.project.trim();
        state.scope = "project";
      }
      return;
    }

    if (msg.method === "ui/notifications/tool-result") {
      state.hasToolResult = true;
      if (fallbackTimer) {
        clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
      const sc = extractStructured(msg.params);
      if (sc) {
        hydrateFromStatePayload(sc, true);
      } else {
        refreshState(false);
      }
      return;
    }

    if (msg.method === "ui/notifications/tool-cancelled") {
      state.loading = false;
      refreshState(false);
    }
  });

  async function initBridge() {
    try {
      const initRes = await sendRequest("ui/initialize", {
        appInfo: { name: "gravity-metrics", version: "0.1.1" },
        appCapabilities: {},
        protocolVersion: PROTOCOL_VERSION
      });
      state.hostCaps = (initRes && initRes.hostCapabilities) || {};
      applyHostContext((initRes && initRes.hostContext) || {});
      sendNotification("ui/notifications/initialized", {});

      if (!state.hasToolResult) {
        fallbackTimer = setTimeout(() => {
          if (!state.hasToolResult && state.hostCaps.serverTools) {
            refreshState(false);
          } else if (!state.hasToolResult) {
            state.loading = false;
            render();
          }
        }, 550);
      }

      pollTimer = setInterval(() => {
        if (document.visibilityState === "visible" && state.hostCaps.serverTools && !state.confirmDeleteTarget) {
          refreshState(true);
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      state.loading = false;
      showToast("Initialisation pont MCP App échouée : " + err.message, true);
    }
  }

  initBridge();
})();
</script>
</body>
</html>
"""


def render_mcp_widget_html() -> str:
    """Retourne le document HTML5 autonome SEP-1865 du widget Gravity Metrics."""
    return _WIDGET_HTML
