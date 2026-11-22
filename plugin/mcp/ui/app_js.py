#!/usr/bin/env python3
"""Logique applicative JS SEP-1865 du widget MCP App Gravity Metrics (100 % stdlib)."""

APP_JS = r"""
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
    pendingSessions: [],
    projects: [],
    byProject: [],
    audits: [],
    auditsRoots: [],
    auditsCount: 0,
    auditLinksBySession: {},
    auditsLoaded: false,
    auditsLoading: false,
    signature: "",
    metricsMdUri: "",
    scope: "global",
    activeProject: "",
    selectedProject: "",
    tariff: "2026",
    tab: "overview",
    reportSubtab: "summary",
    reportsShowListMobile: true,
    auditsShowListMobile: true,
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
    detailCache: {},
    compareTargetA: "",
    compareTargetB: "",
    compareLoading: false,
    auditSearch: "",
    auditWorkspaceFilter: "",
    auditVerdictFilter: "",
    selectedAuditId: "",
    selectedAudit: null,
    auditLoading: false,
    confirmDeleteTarget: "",
    confirmPurge: true,
    archivingCid: "",
    confirmForceArchiveCid: "",
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

  function fmtBytes(n) {
    const b = Number(n) || 0;
    if (b >= 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + " Mo";
    if (b >= 1024) return (b / 1024).toFixed(1) + " Ko";
    return b + " o";
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

  function recKey(rec) {
    return String((rec && (rec.label || rec.conversation_id)) || "");
  }

  function normProj(p) {
    return String(p || "").trim().toLowerCase();
  }

  function hasConversation() {
    return Boolean(state.hostCaps.message || state.hostCaps.updateModelContext);
  }

  function isGlobalSurface() {
    const ep = String(state.hostCtx.entrypoint || state.hostCtx.surface || "").toLowerCase();
    return ep === "global";
  }

  function canUseAuxPane() {
    return Boolean(hasConversation() && !isGlobalSurface());
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

  function cacheDetailPayload(target, sc) {
    if (!sc || !sc.detail) return;
    const keys = [target, sc.detail.label, sc.detail.root_conversation_id].filter(Boolean);
    keys.forEach((k) => {
      state.detailCache[String(k)] = {
        detail: sc.detail,
        slug: String(sc.slug || ""),
        reportMdUri: String(sc.report_md_uri || "")
      };
    });
  }

  function mapInitialTab(rawView) {
    const v = String(rawView || "").toLowerCase();
    if (v === "detail" || v === "reports") return "reports";
    if (v === "pending") return "pending";
    if (v === "audits") return "audits";
    return "overview";
  }

  function hydrateFromStatePayload(sc, isInitialToolResult) {
    if (!sc || typeof sc !== "object") return;
    if (Array.isArray(sc.records)) {
      state.records = sc.records;
      state.detailCache = {};
    }
    if (Array.isArray(sc.pending_sessions)) state.pendingSessions = sc.pending_sessions;
    if (Array.isArray(sc.projects)) state.projects = sc.projects;
    if (Array.isArray(sc.by_project)) state.byProject = sc.by_project;
    if (typeof sc.audits_count === "number") state.auditsCount = sc.audits_count;
    if (sc.audit_links_by_session && typeof sc.audit_links_by_session === "object") {
      state.auditLinksBySession = sc.audit_links_by_session;
    }
    if (Array.isArray(sc.audits)) {
      state.audits = sc.audits;
      state.auditsCount = sc.audits.length;
      state.auditsLoaded = true;
    }
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
      state.tab = mapInitialTab(sc.initial_view);

      if (state.tab === "reports" && initTarget) {
        state.detailTarget = initTarget;
        state.reportsShowListMobile = false;
        if (sc.initial_detail && typeof sc.initial_detail === "object") {
          state.detail = sc.initial_detail;
          state.detailSlug = String(sc.initial_detail_slug || "");
          state.detailMdUri = String(sc.initial_detail_md_uri || "");
          state.detailLoading = false;
          cacheDetailPayload(initTarget, {
            detail: state.detail,
            slug: state.detailSlug,
            report_md_uri: state.detailMdUri
          });
        } else {
          openSessionDetail(initTarget, true);
          return;
        }
      } else if (state.tab === "reports") {
        const list = filteredSortedRecords();
        if (list.length) {
          openSessionDetail(recKey(list[0]), false);
          return;
        }
      } else if (state.tab === "audits") {
        loadAudits(false);
      } else {
        state.detail = null;
        state.detailTarget = "";
      }
    } else if (state.detailTarget) {
      const stillExists = state.records.some(
        (r) => r.label === state.detailTarget || r.conversation_id === state.detailTarget
      );
      if (!stillExists) {
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
        if (state.tab === "reports" && state.detailTarget) {
          try {
            const detRes = await callServerTool("metrics_get_session", { target: state.detailTarget });
            const detSc = extractStructured(detRes);
            if (detSc && detSc.detail) {
              state.detail = detSc.detail;
              state.detailSlug = String(detSc.slug || "");
              state.detailMdUri = String(detSc.report_md_uri || "");
              cacheDetailPayload(state.detailTarget, detSc);
              render();
            }
          } catch (_) {}
        }
        if (state.tab === "audits") {
          loadAudits(true);
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

  async function fetchSessionDetail(target) {
    if (!target) return null;
    if (state.detailCache[target]) return state.detailCache[target];
    if (!state.hostCaps.serverTools) return null;
    const res = await callServerTool("metrics_get_session", { target });
    if (res && res.isError) {
      throw new Error((res.content && res.content[0] && res.content[0].text) || "Session introuvable");
    }
    const sc = extractStructured(res);
    if (!sc || !sc.detail) throw new Error("Réponse détail invalide.");
    cacheDetailPayload(target, sc);
    return state.detailCache[target];
  }

  async function openSessionDetail(target, hideMobileList) {
    if (!target) return;
    state.tab = "reports";
    state.detailTarget = target;
    state.confirmDeleteTarget = "";
    if (hideMobileList !== false) state.reportsShowListMobile = false;

    const cached = state.detailCache[target];
    if (cached) {
      state.detail = cached.detail;
      state.detailSlug = cached.slug;
      state.detailMdUri = cached.reportMdUri;
      state.detailLoading = false;
      if (state.reportSubtab === "compare") ensureCompareLoaded();
      render();
      return;
    }

    state.detailLoading = true;
    render();
    if (!state.hostCaps.serverTools) {
      state.detailLoading = false;
      showToast("serverTools indisponible pour charger le détail.", true);
      return;
    }
    try {
      const loaded = await fetchSessionDetail(target);
      state.detail = loaded.detail;
      state.detailSlug = loaded.slug;
      state.detailMdUri = loaded.reportMdUri;
      if (state.reportSubtab === "compare") ensureCompareLoaded();
    } catch (err) {
      showToast(err.message, true);
    } finally {
      state.detailLoading = false;
      render();
    }
  }

  async function ensureCompareLoaded() {
    const list = scopedRecords();
    if (!list.length) return;
    if (!state.compareTargetA) {
      state.compareTargetA = state.detailTarget || recKey(list[list.length - 1]);
    }
    if (!state.compareTargetB) {
      const idxA = list.findIndex((r) => recKey(r) === state.compareTargetA || r.conversation_id === state.compareTargetA);
      const fallbackIdx = idxA > 0 ? idxA - 1 : (list.length > 1 ? list.length - 2 : 0);
      state.compareTargetB = recKey(list[fallbackIdx] || list[0]);
    }
    if (!state.hostCaps.serverTools) return;
    const needA = state.compareTargetA && !state.detailCache[state.compareTargetA];
    const needB = state.compareTargetB && !state.detailCache[state.compareTargetB];
    if (!needA && !needB) return;

    state.compareLoading = true;
    render();
    try {
      const tasks = [];
      if (needA) tasks.push(fetchSessionDetail(state.compareTargetA));
      if (needB) tasks.push(fetchSessionDetail(state.compareTargetB));
      await Promise.all(tasks);
    } catch (err) {
      showToast("Comparaison : " + err.message, true);
    } finally {
      state.compareLoading = false;
      render();
    }
  }

  async function loadAudits(force) {
    if (!state.hostCaps.serverTools || (state.auditsLoading && !force)) return;
    state.auditsLoading = true;
    render();
    try {
      const res = await callServerTool("metrics_list_audits", {});
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Chargement audits échoué");
      }
      const sc = extractStructured(res);
      if (sc) {
        state.audits = Array.isArray(sc.audits) ? sc.audits : [];
        state.auditsRoots = Array.isArray(sc.roots) ? sc.roots : [];
        state.auditsCount = state.audits.length;
        if (sc.audit_links_by_session) state.auditLinksBySession = sc.audit_links_by_session;
        state.auditsLoaded = true;
        if (state.selectedAuditId && !state.audits.some((a) => a.audit_id === state.selectedAuditId)) {
          state.selectedAuditId = "";
          state.selectedAudit = null;
        }
        if (!state.selectedAuditId && state.audits.length) {
          const pinned = state.audits.find((a) => a.is_index) || state.audits[0];
          openAudit(pinned.audit_id, false);
          return;
        }
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      state.auditsLoading = false;
      render();
    }
  }

  async function openAudit(auditId, hideMobileList) {
    if (!auditId) return;
    state.tab = "audits";
    state.selectedAuditId = auditId;
    if (hideMobileList !== false) state.auditsShowListMobile = false;

    if (!state.hostCaps.serverTools) {
      render();
      return;
    }
    state.auditLoading = true;
    render();
    try {
      const res = await callServerTool("metrics_read_audit", { audit_id: auditId });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Lecture audit échouée");
      }
      const sc = extractStructured(res);
      if (sc && sc.audit) {
        state.selectedAudit = sc.audit;
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      state.auditLoading = false;
      render();
    }
  }

  function resolveAuditRefAndOpen(refPath) {
    if (!refPath) return;
    const clean = String(refPath).replace(/^\.\//, "").toLowerCase();
    const curWs = state.selectedAudit ? state.selectedAudit.workspace : "";
    const match =
      state.audits.find((a) => a.workspace === curWs && String(a.rel_path || "").toLowerCase() === clean) ||
      state.audits.find((a) => a.workspace === curWs && String(a.filename || "").toLowerCase() === clean) ||
      state.audits.find((a) => String(a.rel_path || "").toLowerCase() === clean) ||
      state.audits.find((a) => String(a.filename || "").toLowerCase() === clean);
    if (match) {
      openAudit(match.audit_id, true);
    } else {
      showToast("Audit référencé introuvable dans les dossiers découverts : " + refPath, true);
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
        if (state.detailTarget === target) {
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

  async function archivePendingSession(cid, force) {
    if (!cid || !state.hostCaps.serverTools || state.archivingCid) return;
    state.archivingCid = cid;
    if (force) state.confirmForceArchiveCid = "";
    render();
    try {
      const res = await callServerTool("metrics_archive_session", {
        conversation_id: cid,
        force: Boolean(force)
      });
      if (res && res.isError) {
        const errMsg = (res.content && res.content[0] && res.content[0].text) || "Archivage refusé";
        if (!force && errMsg.indexOf("--force") >= 0) {
          state.confirmForceArchiveCid = cid;
        }
        throw new Error(errMsg);
      }
      const sc = extractStructured(res);
      state.confirmForceArchiveCid = "";
      if (sc) {
        hydrateFromStatePayload(sc, false);
        const lbl = (sc.archived && sc.archived.label) || cid.slice(0, 8);
        showToast(
          sc.already_archived
            ? "Session « " + lbl + " » déjà archivée (immuable)."
            : "Session « " + lbl + " » archivée.",
          false
        );
      }
    } catch (err) {
      showToast(err.message, true);
    } finally {
      state.archivingCid = "";
      render();
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

  function computeByProject() {
    if (Array.isArray(state.byProject) && state.byProject.length) {
      return state.byProject.slice().sort((a, b) => {
        const ca = state.tariff === "2027" ? Number(a.cost_2027_usd || 0) : Number(a.cost_usd || 0);
        const cb = state.tariff === "2027" ? Number(b.cost_2027_usd || 0) : Number(b.cost_usd || 0);
        return cb - ca;
      });
    }
    const groups = new Map();
    let total26 = 0;
    let total27 = 0;
    state.records.forEach((r) => {
      const pName = String(r.project || "").trim() || "(sans projet)";
      const key = pName.toLowerCase();
      if (!groups.has(key)) {
        groups.set(key, {
          project: pName,
          sessions: 0,
          cost_usd: 0,
          cost_2027_usd: 0,
          cacheSum: 0,
          llm_calls: 0,
          bridge_sessions: 0
        });
      }
      const g = groups.get(key);
      const c26 = Number(r.cost_usd || 0);
      const c27 = r.cost_2027_usd != null ? Number(r.cost_2027_usd) : c26;
      g.sessions += 1;
      g.cost_usd += c26;
      g.cost_2027_usd += c27;
      g.cacheSum += Number(r.cache_hit_rate_pct || 0);
      g.llm_calls += Number(r.llm_calls || 0);
      if ((Number(r.bridge_children_count) || 0) > 0) g.bridge_sessions += 1;
      total26 += c26;
      total27 += c27;
    });
    return Array.from(groups.values()).map((g) => ({
      project: g.project,
      sessions: g.sessions,
      cost_usd: g.cost_usd,
      cost_2027_usd: g.cost_2027_usd,
      share_pct: total26 > 0 ? (g.cost_usd / total26) * 100 : 0,
      share_2027_pct: total27 > 0 ? (g.cost_2027_usd / total27) * 100 : 0,
      avg_cache_hit_rate_pct: g.sessions > 0 ? g.cacheSum / g.sessions : 0,
      llm_calls: g.llm_calls,
      bridge_sessions: g.bridge_sessions
    })).sort((a, b) => {
      const ca = state.tariff === "2027" ? a.cost_2027_usd : a.cost_usd;
      const cb = state.tariff === "2027" ? b.cost_2027_usd : b.cost_usd;
      return cb - ca;
    });
  }

  function navigateReport(delta) {
    const list = filteredSortedRecords();
    if (!list.length) return;
    let idx = list.findIndex((r) => recKey(r) === state.detailTarget || r.conversation_id === state.detailTarget);
    if (idx < 0) idx = 0;
    const nextIdx = idx + delta;
    if (nextIdx < 0 || nextIdx >= list.length) return;
    openSessionDetail(recKey(list[nextIdx]), true);
  }

  function renderShellHeader() {
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

    const openGlobalMdBtn = (canUseAuxPane() && state.metricsMdUri)
      ? '<button type="button" class="btn" data-action="open-file" data-uri="' + esc(state.metricsMdUri) + '">METRICS.md</button>'
      : "";

    const refreshBtn = state.hostCaps.serverTools
      ? '<button type="button" class="btn" data-action="refresh">Rafraîchir</button>'
      : "";

    const scopedCount = scopedRecords().length;
    const pendCount = state.pendingSessions.length;
    const audCount = state.auditsLoaded ? state.audits.length : state.auditsCount;

    const tabs = [
      { id: "overview", label: "Vue d'ensemble", badge: "" },
      { id: "reports", label: "Rapports", badge: String(scopedCount) },
      { id: "pending", label: "À archiver", badge: String(pendCount), warn: pendCount > 0 },
      { id: "audits", label: "Audits", badge: String(audCount) }
    ];
    let tabsHtml = '<nav class="nav-tabs" aria-label="Navigation principale">';
    tabs.forEach((t) => {
      const isAct = state.tab === t.id;
      const badgeHtml = t.badge !== ""
        ? '<span class="tab-badge' + (t.warn ? ' warn' : '') + '">' + esc(t.badge) + '</span>'
        : '';
      tabsHtml += '<button type="button" data-action="switch-tab" data-tab="' + t.id + '" class="tab-btn' + (isAct ? ' active' : '') + '">' +
        esc(t.label) + badgeHtml + '</button>';
    });
    tabsHtml += '</nav>';

    return '<div class="shell-head">' +
      '<div class="topbar">' +
      '<div class="brand">' +
      '<span class="brand-dot" title="Synchronisation 0 token active"></span>' +
      '<div><div class="brand-title">Gravity Metrics</div>' +
      '<div class="brand-sub">' + esc(state.records.length) + ' session(s) archivée(s) · ' + esc(pendCount) + ' en attente' +
      (state.scope === "project" && state.selectedProject ? ' · filtre : ' + esc(state.selectedProject) : ' · vue globale') +
      '</div></div></div>' +
      '<div class="controls">' + scopeHtml + projSelect + tariffHtml + openGlobalMdBtn + refreshBtn + '</div>' +
      '</div>' +
      tabsHtml +
      '</div>';
  }

  function renderConfirmBars() {
    let html = "";
    if (state.confirmDeleteTarget) {
      html += '<div class="confirm-bar">' +
        '<div><strong>Supprimer la session « ' + esc(state.confirmDeleteTarget) + ' » ?</strong> ' +
        '<label style="margin-left:8px;font-size:12px;cursor:pointer;">' +
        '<input id="gm-purge-cb" type="checkbox"' + (state.confirmPurge ? " checked" : "") + ' /> Purger aussi <code>reports/</code>' +
        '</label></div>' +
        '<div class="confirm-actions">' +
        '<button type="button" class="btn danger" data-action="confirm-delete">Confirmer</button>' +
        '<button type="button" class="btn" data-action="cancel-delete">Annuler</button>' +
        '</div></div>';
    }
    if (state.confirmForceArchiveCid) {
      html += '<div class="confirm-bar">' +
        '<div><strong>Jobs Bridge encore actifs pour <code>' + esc(state.confirmForceArchiveCid.slice(0, 8)) + '</code>.</strong> ' +
        'Forcer l’archivage immuable maintenant ?</div>' +
        '<div class="confirm-actions">' +
        '<button type="button" class="btn danger" data-action="confirm-force-archive" data-cid="' + esc(state.confirmForceArchiveCid) + '">Forcer l’archivage</button>' +
        '<button type="button" class="btn" data-action="cancel-force-archive">Annuler</button>' +
        '</div></div>';
    }
    return html;
  }

  function renderSessionBadges(rec) {
    const cid = String(rec.conversation_id || "");
    const links = state.auditLinksBySession[cid] || [];
    const bCnt = Number(rec.bridge_children_count) || 0;
    let out = "";
    if (bCnt > 0) out += '<span class="pill bridge">bridge:' + bCnt + '</span>';
    if (links.length) {
      out += '<button type="button" class="pill-btn" data-action="open-audit" data-audit-id="' + esc(links[0].audit_id) + '" title="Ouvrir l’audit associé">Audit' + (links.length > 1 ? ':' + links.length : '') + '</button>';
    }
    return out;
  }

  function renderOverviewTab() {
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

    let pendBanner = "";
    if (state.pendingSessions.length) {
      pendBanner = '<div class="banner-info">' +
        '<span><strong>' + state.pendingSessions.length + ' session(s) Gravity</strong> en attente d’archivage immuable.</span>' +
        '<button type="button" class="btn sm primary" data-action="switch-tab" data-tab="pending">Voir À archiver →</button>' +
        '</div>';
    }

    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Dernière session</div><div class="v">$' + (latest ? recCost(latest).toFixed(4) : "0.0000") + '</div><div class="sub">' + esc(latest ? latest.label : "—") + '</div></div>' +
      '<div class="card"><div class="k">Coût cumulé</div><div class="v">$' + totalCost.toFixed(4) + '</div><div class="sub">' + scoped.length + ' session(s) · ' + esc(fmtDur(totalDur)) + '</div></div>' +
      '<div class="card"><div class="k">Coût moyen</div><div class="v">$' + avgCost.toFixed(4) + '</div><div class="sub">Cache moyen : ' + avgCache.toFixed(1) + '%</div></div>' +
      '<div class="card"><div class="k">Meilleur coût</div><div class="v">$' + (best ? recCost(best).toFixed(4) : "0.0000") + '</div><div class="sub">' + esc(best ? best.label : "—") + '</div></div>' +
      '</div>';

    const byProject = computeByProject();
    const projectPanel = '<div class="panel"><div class="panel-head">' +
      '<span class="panel-title">Répartition par Projet (% Coût ' + (state.tariff === "2027" ? "Proj. 2027" : "2026") + ')</span>' +
      (state.scope === "project" ? '<button type="button" class="btn sm" data-action="set-scope" data-scope="global">Tout afficher</button>' : '<span class="subline">' + byProject.length + ' projet(s)</span>') +
      '</div>' + renderProjectShareBars(byProject, state.tariff === "2027") + '</div>';

    const charts = '<div class="panels two">' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Évolution du Coût (' + esc(state.tariff === "2027" ? "Proj. 2027" : "2026") + ')</span></div>' +
      '<div class="svg-box">' + renderCostEvolutionSvg(scoped, state.detailTarget) + '</div></div>' +
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
      const rowKey = recKey(r);
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
      const stBadge = '<span class="pill ok">' + esc(r.status === "archived" || !r.status ? "archivé" : r.status) + '</span>';
      const modeCell = '<code>' + esc(mode) + '</code> ' + renderSessionBadges(r) + ' ' + stBadge + (gp ? '<div class="subline">' + esc(gp) + '</div>' : '');

      const delBtn = state.hostCaps.serverTools
        ? '<button type="button" class="btn row-del" data-action="ask-delete" data-target="' + esc(rowKey) + '" title="Supprimer">✕</button>'
        : "";

      rowsHtml += '<tr class="clickable" data-action="open-detail" data-target="' + esc(rowKey) + '">' +
        '<td><code>' + esc(fmtDate(r)) + '</code></td>' +
        '<td><strong>' + esc(r.label || rowKey) + '</strong><div class="subline">' + esc(String(r.conversation_id || "").slice(0, 8)) + (r.note ? ' · ' + esc(r.note) : '') + '</div></td>' +
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
      '<span class="panel-title">Historique des sessions (' + list.length + '/' + scoped.length + ')</span>' +
      (list.length >= 2 ? '<button type="button" class="btn sm" data-action="open-compare">Comparer A vs B</button>' : '') +
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

    return pendBanner + kpis + projectPanel + charts + listPanel;
  }

  function renderPendingTab() {
    const pend = state.pendingSessions || [];
    if (!pend.length) {
      return '<div class="panel"><div class="empty">Aucune session Gravity en attente d’archivage. Toutes vos sessions racines sont archivées.</div></div>';
    }
    let rows = "";
    pend.forEach((p) => {
      const cid = String(p.conversation_id || "");
      const bCnt = Number(p.bridge_children_count) || 0;
      const pJobs = Number(p.bridge_pending_jobs_count) || 0;
      const wss = (p.bridge_workspaces || []).join(", ");
      let bridgeHtml = "—";
      if (pJobs > 0) {
        bridgeHtml = '<span class="pill warn">Bridge:' + bCnt + ' · actif(' + pJobs + ')</span>';
      } else if (bCnt > 0) {
        bridgeHtml = '<span class="pill bridge">Bridge:' + bCnt + (wss ? ' · ' + esc(wss) : '') + '</span>';
      }
      const isBusy = state.archivingCid === cid;
      const archBtn = state.hostCaps.serverTools
        ? '<button type="button" class="btn primary row-del" data-action="archive-pending" data-cid="' + esc(cid) + '"' + (isBusy ? ' disabled' : '') + '>' + (isBusy ? '...' : 'Archiver') + '</button>'
        : "";
      rows += '<tr>' +
        '<td><code>' + esc(cid.slice(0, 8)) + '</code></td>' +
        '<td><strong>' + esc(p.title || "(sans titre)") + '</strong><div class="subline">' + esc(p.window_mode || "") + '</div></td>' +
        '<td><code>' + esc(String(p.updated_at || "").slice(0, 16).replace("T", " ")) + '</code></td>' +
        '<td class="num">' + fmtInt(p.llm_calls) + '</td>' +
        '<td>' + bridgeHtml + '</td>' +
        '<td>' + archBtn + '</td>' +
        '</tr>';
    });
    return '<div class="panel"><div class="panel-head"><span class="panel-title">Sessions en attente d’archivage (' + pend.length + ')</span><span class="subline">Enfants Bridge exclus automatiquement</span></div>' +
      '<div class="table-wrap"><table><thead><tr>' +
      '<th>ID</th><th>Session</th><th>MAJ (UTC)</th><th class="num">Appels</th><th>Bridge</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
  }

  function renderCompareSubtab() {
    const allRecs = state.records.slice().reverse();
    if (allRecs.length < 2) {
      return '<div class="panel"><div class="empty">Au moins 2 sessions archivées sont nécessaires pour comparer A vs B.</div></div>';
    }
    const use2027 = state.tariff === "2027";
    const keyA = state.compareTargetA || recKey(allRecs[0]);
    const keyB = state.compareTargetB || recKey(allRecs[1]);

    const optionsFor = (selectedKey) => allRecs.map((r) => {
      const k = recKey(r);
      const sel = (k === selectedKey || r.conversation_id === selectedKey) ? " selected" : "";
      return '<option value="' + esc(k) + '"' + sel + '>' + esc((r.label || k) + " (" + (r.project || "—") + " · $" + recCost(r).toFixed(3) + ")") + '</option>';
    }).join("");

    const selectorBar = '<div class="panel"><div class="panel-head"><span class="panel-title">Comparateur A vs B</span></div>' +
      '<div class="compare-bar">' +
      '<div><div class="subline" style="margin-bottom:4px;">Session A (référence)</div><select id="gm-cmp-a" style="width:100%;">' + optionsFor(keyA) + '</select></div>' +
      '<button type="button" class="btn sm" data-action="swap-compare" title="Permuter A et B">⇄</button>' +
      '<div><div class="subline" style="margin-bottom:4px;">Session B (comparée)</div><select id="gm-cmp-b" style="width:100%;">' + optionsFor(keyB) + '</select></div>' +
      '</div></div>';

    if (state.compareLoading) {
      return selectorBar + '<div class="panel"><div class="empty">Chargement des rapports à comparer...</div></div>';
    }
    const cA = state.detailCache[keyA] && state.detailCache[keyA].detail;
    const cB = state.detailCache[keyB] && state.detailCache[keyB].detail;
    if (!cA || !cB) {
      return selectorBar + '<div class="panel"><div class="empty">Sélectionnez deux sessions pour charger le comparatif.</div></div>';
    }

    const getVal = (d) => {
      const s = d.summary || {};
      const c = use2027 ? (s.cost_usd_projection || s.cost_usd || {}) : (s.cost_usd || {});
      const eff = d.efficiency_and_waste || {};
      return {
        cost: Number(c.total_usd || 0),
        inputUsd: Number(c.input_usd || 0),
        cacheUsd: Number(c.cache_read_usd || 0),
        outputUsd: Number(c.output_usd || 0),
        savings: Number(c.cache_savings_usd || 0),
        cachePct: Number(s.cache_hit_rate_pct || 0),
        dur: Number(s.wall_clock_s || 0),
        par: Number(s.parallelism_factor || 1),
        agents: Number(s.total_agents || 0),
        calls: Number(s.total_llm_calls || 0),
        errPct: Number(s.error_rate_pct || 0),
        maxCtx: Number((eff.context_bloat || {}).max_context_tokens_overall || 0),
        dups: Number((eff.duplicate_role_spawns || {}).duplicate_agent_count || 0),
        rw: Number((eff.rewind_waste || {}).rewind_calls || 0),
        intr: Number((eff.interrupted_agents || {}).count || 0)
      };
    };

    const va = getVal(cA);
    const vb = getVal(cB);
    const metricsRows = [
      { label: "Coût Total (" + (use2027 ? "2027" : "2026") + ")", a: va.cost, b: vb.cost, fmt: (x) => "$" + x.toFixed(4), lowerBetter: true },
      { label: "Coût Input frais", a: va.inputUsd, b: vb.inputUsd, fmt: (x) => "$" + x.toFixed(4), lowerBetter: true },
      { label: "Coût Cache Read", a: va.cacheUsd, b: vb.cacheUsd, fmt: (x) => "$" + x.toFixed(4), lowerBetter: true },
      { label: "Coût Output", a: va.outputUsd, b: vb.outputUsd, fmt: (x) => "$" + x.toFixed(4), lowerBetter: true },
      { label: "Économie Cache", a: va.savings, b: vb.savings, fmt: (x) => "$" + x.toFixed(4), lowerBetter: false },
      { label: "Cache Hit %", a: va.cachePct, b: vb.cachePct, fmt: (x) => x.toFixed(2) + "%", lowerBetter: false },
      { label: "Durée murale", a: va.dur, b: vb.dur, fmt: fmtDur, lowerBetter: true },
      { label: "Parallélisme", a: va.par, b: vb.par, fmt: (x) => x.toFixed(2) + "x", lowerBetter: false },
      { label: "Agents", a: va.agents, b: vb.agents, fmt: fmtInt, lowerBetter: true },
      { label: "Appels LLM", a: va.calls, b: vb.calls, fmt: fmtInt, lowerBetter: true },
      { label: "Taux d’erreur %", a: va.errPct, b: vb.errPct, fmt: (x) => x.toFixed(2) + "%", lowerBetter: true },
      { label: "Max Context Tokens", a: va.maxCtx, b: vb.maxCtx, fmt: fmtInt, lowerBetter: true },
      { label: "Doublons / Rewinds / Interrompus", a: va.dups + va.rw + va.intr, b: vb.dups + vb.rw + vb.intr, fmt: fmtInt, lowerBetter: true }
    ];

    let rowsHtml = "";
    metricsRows.forEach((m) => {
      const diff = m.b - m.a;
      const pct = m.a !== 0 ? (diff / Math.abs(m.a)) * 100 : null;
      let cls = "delta-flat";
      if (Math.abs(diff) > 1e-9) {
        const improved = m.lowerBetter ? diff < 0 : diff > 0;
        cls = improved ? "delta-good" : "delta-bad";
      }
      const sign = diff > 0 ? "+" : (diff < 0 ? "−" : "");
      const diffStr = Math.abs(diff) < 1e-9 ? "=" : sign + m.fmt(Math.abs(diff));
      const pctStr = pct === null ? "" : " (" + (diff > 0 ? "+" : "") + pct.toFixed(1) + "%)";
      rowsHtml += '<tr>' +
        '<td><strong>' + esc(m.label) + '</strong></td>' +
        '<td class="num">' + esc(m.fmt(m.a)) + '</td>' +
        '<td class="num">' + esc(m.fmt(m.b)) + '</td>' +
        '<td class="num ' + cls + '">' + esc(diffStr + pctStr) + '</td>' +
        '</tr>';
    });

    const tablePanel = '<div class="panel"><div class="panel-head">' +
      '<span class="panel-title">Métriques comparées : ' + esc(cA.label) + ' vs ' + esc(cB.label) + '</span>' +
      '<span class="subline">Δ = B − A</span></div>' +
      '<div class="table-wrap"><table><thead><tr>' +
      '<th>Indicateur</th><th class="num">A : ' + esc(cA.label) + '</th><th class="num">B : ' + esc(cB.label) + '</th><th class="num">Delta (B − A)</th>' +
      '</tr></thead><tbody>' + rowsHtml + '</tbody></table></div></div>';

    return selectorBar + tablePanel;
  }

  function renderReportDetailContent() {
    if (state.detailLoading && !state.detail) {
      return '<div class="panel"><div class="empty">Chargement du rapport « ' + esc(state.detailTarget) + ' »...</div></div>';
    }
    const d = state.detail;
    if (!d) {
      return '<div class="panel"><div class="empty">Sélectionnez un rapport dans la liste pour afficher le détail.</div></div>';
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
    if (canUseAuxPane() && state.detailMdUri) {
      actionBtns += '<button type="button" class="btn sm" data-action="open-file" data-uri="' + esc(state.detailMdUri) + '">Ouvrir .md</button>';
    }
    if (state.hostCaps.updateModelContext && !isGlobalSurface()) {
      actionBtns += '<button type="button" class="btn sm" data-action="attach-context">Joindre au prompt</button>';
    }
    if (state.hostCaps.message && !isGlobalSurface()) {
      actionBtns += '<button type="button" class="btn sm primary" data-action="analyze-session">Analyser</button>';
    }
    if (state.hostCaps.serverTools) {
      actionBtns += '<button type="button" class="btn sm danger" data-action="ask-delete" data-target="' + esc(d.label) + '">Supprimer</button>';
    }

    const bridgeMeta = d.bridge || {};
    const bridgeCount = Number(bridgeMeta.children_count) || 0;
    const subtabs = [
      { id: "summary", label: "Synthèse & Dossiers (%)" },
      { id: "agents", label: "Agents & Bridge (" + ((d.agents || []).length) + ")" },
      { id: "diag", label: "Diagnostic & Outils" },
      { id: "compare", label: "Comparer A vs B" }
    ];
    let subHtml = '<div class="subtabs">';
    subtabs.forEach((st) => {
      subHtml += '<button type="button" class="subtab-btn' + (state.reportSubtab === st.id ? ' active' : '') + '" data-action="set-report-subtab" data-subtab="' + st.id + '">' + esc(st.label) + '</button>';
    });
    subHtml += '</div>';

    const linkedAudits = state.auditLinksBySession[String(d.root_conversation_id || "")] || [];
    const auditChips = linkedAudits.map((a) =>
      '<button type="button" class="pill-btn" data-action="open-audit" data-audit-id="' + esc(a.audit_id) + '">Audit : ' + esc(a.title) + (a.verdict ? ' (' + esc(a.verdict) + ')' : '') + '</button>'
    ).join(" ");

    const headPanel = '<div class="panel detail-head">' +
      '<div class="detail-row">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
      '<strong style="font-size:15px;">' + esc(d.label) + '</strong>' +
      '<span class="pill ok">archivé</span>' +
      (bridgeCount > 0 ? '<span class="pill bridge">Bridge:' + bridgeCount + '</span>' : '') +
      '</div>' +
      '<div class="controls">' + actionBtns + '</div>' +
      '</div>' +
      '<div class="detail-meta">' +
      '<span>Racine : <code>' + esc(String(d.root_conversation_id || "").slice(0, 12)) + '</code></span>' +
      (d.project ? '<span>· Projet : <code>' + esc(d.project) + '</code></span>' : '') +
      (d.gravity_profile ? '<span>· Profil : <code>' + esc(d.gravity_profile) + '</code></span>' : '') +
      (win.mode ? '<span>· Fenêtre : <code>' + esc(win.mode) + '</code></span>' : '') +
      '<span>· Durée : <code>' + esc(fmtDur(s.wall_clock_s)) + '</code></span>' +
      '</div>' +
      (auditChips ? '<div class="detail-meta"><span>Audits liés :</span>' + auditChips + '</div>' : '') +
      subHtml +
      '</div>';

    if (state.reportSubtab === "compare") {
      return headPanel + renderCompareSubtab();
    }

    if (state.reportSubtab === "agents") {
      const catMap = Object.fromEntries(CAT_DEFS.map((x) => [x.key, x]));
      const bridgeAgents = (d.agents || []).filter((ag) => ag.source === "bridge");
      let bridgePanel = "";
      if (bridgeAgents.length) {
        let bRows = "";
        bridgeAgents.forEach((ag) => {
          const costObj = use2027 ? (ag.cost_usd_projection || ag.cost_usd) : ag.cost_usd;
          const ws = ag.bridge_workspace || ag.workspace || "unknown";
          bRows += '<tr>' +
            '<td><code>' + esc(ws) + '</code></td>' +
            '<td><code>' + esc(ag.bridge_thread_id || "—") + '</code> <span class="subline">' + esc(String(ag.conversation_id || "").slice(0, 8)) + '</span></td>' +
            '<td><code>' + esc(ag.type_name || "bridge") + '</code></td>' +
            '<td class="num">' + (ag.depth || 1) + '</td>' +
            '<td class="num">' + fmtInt(ag.llm_calls) + '</td>' +
            '<td class="num"><strong>$' + Number((costObj || {}).total_usd || 0).toFixed(4) + '</strong></td>' +
            '<td><code>' + esc(ag.bridge_job_status || ag.subagent_state || "—") + '</code></td>' +
            '</tr>';
        });
        bridgePanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Arbre Bridge Inter-Workspaces (' + bridgeAgents.length + ')</span></div>' +
          '<div class="table-wrap"><table><thead><tr><th>Workspace</th><th>Thread</th><th>Type</th><th class="num">Prof.</th><th class="num">Appels</th><th class="num">Coût</th><th>Job</th></tr></thead><tbody>' + bRows + '</tbody></table></div></div>';
      }

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
        const srcBadge = ag.source === "bridge"
          ? ' <span class="pill bridge">' + esc(ag.bridge_workspace || ag.workspace || "bridge") + '</span>'
          : '';
        agRows += '<tr>' +
          '<td><code>' + esc(String(ag.conversation_id || "").slice(0, 8)) + '</code></td>' +
          '<td>' + esc(ag.role) + srcBadge + '</td>' +
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

      return headPanel + bridgePanel + modelsPanel + agentsPanel;
    }

    if (state.reportSubtab === "diag") {
      const toolsList = Object.entries(tu.by_tool || {}).slice(0, 12)
        .map(([k, v]) => '<li><code>' + esc(k) + '</code> : <strong>' + fmtInt(v) + '</strong></li>').join("");
      const errList = (tu.top_errors || []).slice(0, 8)
        .map((e) => '<li>(' + esc(e.count) + '×) <code>' + esc(e.reason) + '</code></li>').join("");

      const diagPanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Diagnostic, Outillage &amp; Qualité</span></div>' +
        '<div class="diag-grid">' +
        '<div><strong>Gaspillages</strong><ul class="clean">' +
        '<li>Ratio Vérif / Build+Test : <strong>' + (eff.verification_to_build_cost_ratio == null ? 'N/A' : Number(eff.verification_to_build_cost_ratio).toFixed(2) + 'x') + '</strong></li>' +
        '<li>Doublons : <strong>' + (dup.duplicate_agent_count || 0) + '</strong> ($' + Number(dup.duplicate_cost_usd || 0).toFixed(4) + ')</li>' +
        '<li>Rewinds : <strong>' + (rw.rewind_calls || 0) + '</strong> ($' + Number(rw.rewind_cost_usd || 0).toFixed(4) + ')</li>' +
        '<li>Interrompus : <strong>' + (intr.count || 0) + '</strong> ($' + Number(intr.total_cost_usd || 0).toFixed(4) + ')</li>' +
        '<li>Ctx &gt;100k / &gt;128k / &gt;200k : ' + (cb.calls_over_100k_ctx || 0) + ' / ' + (cb.calls_over_128k_ctx || 0) + ' / ' + (cb.calls_over_200k_ctx || 0) + '</li>' +
        '</ul></div>' +
        '<div><strong>Top Outils (' + fmtInt(tu.total_calls || 0) + ')</strong><ul class="clean">' + (toolsList || '<li>Aucun appel outil</li>') + '</ul></div>' +
        '<div><strong>Erreurs &amp; Qualité données</strong><ul class="clean">' +
        (errList || '<li>Aucune erreur enregistrée</li>') +
        '<li>Fallback : <code>' + esc((dq.unmatched_models || []).join(", ") || "aucun") + '</code></li>' +
        '<li>JSON malformés : <code>' + (dq.malformed_lines || 0) + '</code></li>' +
        '</ul></div></div></div>';

      return headPanel + diagPanel;
    }

    // Sous-onglet Synthèse & Dossiers (%)
    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Coût Total (' + (use2027 ? "2027" : "2026") + ')</div><div class="v">$' + shownTotal.toFixed(4) + '</div><div class="sub">' + (use2027 ? "2026" : "Proj. 2027") + ' : $' + otherTotal.toFixed(4) + '</div></div>' +
      '<div class="card"><div class="k">Économie Cache</div><div class="v">$' + Number(c.cache_savings_usd || 0).toFixed(4) + '</div><div class="sub">Cache Hit : ' + Number(s.cache_hit_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Agents / Appels LLM</div><div class="v">' + (s.total_agents || 0) + ' / ' + fmtInt(s.total_llm_calls) + '</div><div class="sub">Parall. : ' + Number(s.parallelism_factor || 1).toFixed(2) + 'x · Err : ' + Number(s.error_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Gaspillages &amp; Ctx</div><div class="v">' + (dup.duplicate_agent_count || 0) + ' dup · ' + (rw.rewind_calls || 0) + ' rw</div><div class="sub">Int : ' + (intr.count || 0) + ' ($' + Number(intr.total_cost_usd || 0).toFixed(3) + ') · Max : ' + fmtInt(cb.max_context_tokens_overall) + '</div></div>' +
      '</div>';

    const wsPanel = renderWorkspaceSharePanel(d, use2027);

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

    return headPanel + kpis + wsPanel + charts;
  }

  function renderReportsTab() {
    const list = filteredSortedRecords();
    const curIdx = list.findIndex((r) => recKey(r) === state.detailTarget || r.conversation_id === state.detailTarget);
    const hasPrev = curIdx > 0;
    const hasNext = curIdx >= 0 && curIdx < list.length - 1;

    const quickSelectOpts = list.map((r) => {
      const k = recKey(r);
      const sel = (k === state.detailTarget || r.conversation_id === state.detailTarget) ? " selected" : "";
      return '<option value="' + esc(k) + '"' + sel + '>' + esc((r.label || k) + " · $" + recCost(r).toFixed(3)) + '</option>';
    }).join("");

    const stickyNav = '<div class="sticky-nav">' +
      '<div class="sticky-nav-group">' +
      '<button type="button" class="btn sm only-narrow" data-action="toggle-reports-list">' + (state.reportsShowListMobile ? 'Masquer liste' : 'Liste (' + list.length + ')') + '</button>' +
      '<button type="button" class="btn sm" data-action="nav-prev"' + (hasPrev ? '' : ' disabled') + ' title="Rapport précédent (Alt+←)">← Préc.</button>' +
      '<select id="gm-quick-report" aria-label="Sélecteur rapide de rapport">' + (quickSelectOpts || '<option value="">Aucun rapport</option>') + '</select>' +
      '<button type="button" class="btn sm" data-action="nav-next"' + (hasNext ? '' : ' disabled') + ' title="Rapport suivant (Alt+→)">Suiv. →</button>' +
      '</div>' +
      '<div class="controls">' +
      '<span class="subline">' + (curIdx >= 0 ? (curIdx + 1) + '/' + list.length : '0/' + list.length) + '</span>' +
      (list.length >= 2 ? '<button type="button" class="btn sm" data-action="open-compare">A vs B</button>' : '') +
      '</div></div>';

    let masterItems = "";
    list.forEach((r) => {
      const k = recKey(r);
      const isAct = k === state.detailTarget || r.conversation_id === state.detailTarget;
      const bCnt = Number(r.bridge_children_count) || 0;
      const audLinks = state.auditLinksBySession[String(r.conversation_id || "")] || [];
      masterItems += '<button type="button" class="master-item' + (isAct ? ' active' : '') + '" data-action="open-detail" data-target="' + esc(k) + '">' +
        '<div class="master-item-top"><strong>' + esc(r.label || k) + '</strong><span><strong>$' + recCost(r).toFixed(4) + '</strong></span></div>' +
        '<div class="master-item-sub">' +
        '<span>' + esc(fmtDate(r)) + ' · ' + esc(r.project || "—") + '</span>' +
        '<span>' + (Number(r.cache_hit_rate_pct) || 0).toFixed(0) + '% cache' +
        (bCnt > 0 ? ' · bridge:' + bCnt : '') +
        (audLinks.length ? ' · audit' : '') +
        '</span></div></button>';
    });

    const masterPane = '<aside class="master-pane' + (state.reportsShowListMobile ? '' : ' hide-mobile') + '">' +
      '<input id="gm-search" type="search" placeholder="Filtrer rapports..." value="' + esc(state.search) + '" />' +
      '<div class="master-list">' + (masterItems || '<div class="empty">Aucun rapport.</div>') + '</div>' +
      '</aside>';

    const detailPane = '<div class="detail-pane">' + stickyNav + renderReportDetailContent() + '</div>';
    return '<div class="master-detail">' + masterPane + detailPane + '</div>';
  }

  function renderAuditsTab() {
    const q = state.auditSearch.trim().toLowerCase();
    const wsFilter = state.auditWorkspaceFilter;
    const vFilter = state.auditVerdictFilter;

    const workspaces = Array.from(new Set(state.audits.map((a) => String(a.workspace || "")).filter(Boolean))).sort();
    const verdicts = Array.from(new Set(state.audits.map((a) => String(a.verdict || "")).filter(Boolean))).sort();

    const filtered = state.audits.filter((a) => {
      if (wsFilter && a.workspace !== wsFilter) return false;
      if (vFilter && a.verdict !== vFilter) return false;
      if (q) {
        const hay = [a.title, a.filename, a.rel_path, a.workspace, a.verdict, a.excerpt].join(" ").toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });

    const pinned = filtered.filter((a) => a.is_index);
    const regular = filtered.filter((a) => !a.is_index);
    const ordered = pinned.concat(regular);

    let wsOpts = '<option value="">Tous les dossiers</option>';
    workspaces.forEach((w) => {
      wsOpts += '<option value="' + esc(w) + '"' + (w === wsFilter ? " selected" : "") + '>' + esc(w) + '</option>';
    });
    let vOpts = '<option value="">Tous les verdicts</option>';
    verdicts.forEach((v) => {
      vOpts += '<option value="' + esc(v) + '"' + (v === vFilter ? " selected" : "") + '>' + esc(v) + '</option>';
    });

    let itemsHtml = "";
    ordered.forEach((a) => {
      const isAct = a.audit_id === state.selectedAuditId;
      const tone = a.verdict_tone || "neutral";
      const cls = "master-item" + (a.is_index ? " pinned" : "") + (isAct ? " active" : "");
      const linkedCnt = (a.linked_sessions || []).length;
      itemsHtml += '<button type="button" class="' + cls + '" data-action="open-audit" data-audit-id="' + esc(a.audit_id) + '">' +
        '<div class="master-item-top">' +
        '<strong>' + (a.is_index ? '📌 ' : '') + esc(a.title || a.filename) + '</strong>' +
        (a.verdict ? '<span class="pill ' + esc(tone) + '">' + esc(a.verdict) + '</span>' : '') +
        '</div>' +
        '<div class="master-item-sub">' +
        '<span>' + esc(a.workspace) + ' / ' + esc(a.rel_path) + '</span>' +
        '<span>' + esc(a.date || "") + (linkedCnt ? ' · ' + linkedCnt + ' sess.' : '') + '</span>' +
        '</div></button>';
    });

    const masterPane = '<aside class="master-pane' + (state.auditsShowListMobile ? '' : ' hide-mobile') + '">' +
      '<input id="gm-audit-search" type="search" placeholder="Rechercher audit, verdict..." value="' + esc(state.auditSearch) + '" />' +
      '<div class="filters" style="margin-bottom:0;">' +
      '<select id="gm-audit-ws" style="flex:1 1 110px;">' + wsOpts + '</select>' +
      '<select id="gm-audit-verdict" style="flex:1 1 110px;">' + vOpts + '</select>' +
      '</div>' +
      '<div class="master-list">' +
      (state.auditsLoading && !state.audits.length ? '<div class="empty">Découverte des audits...</div>' : (itemsHtml || '<div class="empty">Aucun audit Markdown trouvé.</div>')) +
      '</div></aside>';

    const quickOpts = ordered.map((a) =>
      '<option value="' + esc(a.audit_id) + '"' + (a.audit_id === state.selectedAuditId ? " selected" : "") + '>' +
      esc((a.is_index ? "[Index] " : "") + (a.title || a.filename) + " (" + a.workspace + ")") + '</option>'
    ).join("");

    const navBar = '<div class="sticky-nav">' +
      '<div class="sticky-nav-group">' +
      '<button type="button" class="btn sm only-narrow" data-action="toggle-audits-list">' + (state.auditsShowListMobile ? 'Masquer liste' : 'Audits (' + ordered.length + ')') + '</button>' +
      '<select id="gm-quick-audit" aria-label="Sélecteur rapide d’audit">' + (quickOpts || '<option value="">Aucun audit</option>') + '</select>' +
      '</div>' +
      '<button type="button" class="btn sm" data-action="reload-audits">Actualiser audits</button>' +
      '</div>';

    let readerHtml = "";
    if (state.auditLoading && !state.selectedAudit) {
      readerHtml = '<div class="panel"><div class="empty">Lecture de l’audit...</div></div>';
    } else if (!state.selectedAudit) {
      readerHtml = '<div class="panel"><div class="empty">' +
        (ordered.length ? 'Sélectionnez un fichier Markdown dans la liste.' : 'Aucun dossier <code>audits/</code> détecté (configurez <code>$GRAVITY_AUDITS_DIR</code> ou un workspace Bridge).') +
        '</div></div>';
    } else {
      const a = state.selectedAudit;
      const tone = a.verdict_tone || "neutral";
      const openBtn = (canUseAuxPane() && a.uri)
        ? '<button type="button" class="btn sm" data-action="open-file" data-uri="' + esc(a.uri) + '">Ouvrir .md</button>'
        : "";
      const sessChips = (a.linked_sessions || []).map((ls) =>
        '<button type="button" class="pill-btn" data-action="open-detail" data-target="' + esc(ls.label || ls.conversation_id) + '">' +
        esc(ls.label) + ' ($' + Number(ls.cost_usd || 0).toFixed(3) + ')</button>'
      ).join(" ");

      readerHtml = '<div class="panel detail-head">' +
        '<div class="detail-row">' +
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
        '<strong style="font-size:15px;">' + esc(a.title || a.filename) + '</strong>' +
        (a.is_index ? '<span class="pill ok">Index</span>' : '') +
        (a.verdict ? '<span class="pill ' + esc(tone) + '">' + esc(a.verdict) + '</span>' : '') +
        (a.truncated ? '<span class="pill warn">Tronqué à 256 Ko</span>' : '') +
        '</div>' +
        '<div class="controls">' + openBtn + '</div>' +
        '</div>' +
        '<div class="detail-meta">' +
        '<span>Workspace : <code>' + esc(a.workspace) + '</code></span>' +
        '<span>· Fichier : <code>' + esc(a.rel_path) + '</code></span>' +
        '<span>· Date : <code>' + esc(a.date || "—") + '</code></span>' +
        '<span>· Taille : <code>' + esc(fmtBytes(a.size_bytes)) + '</code></span>' +
        '</div>' +
        (sessChips ? '<div class="detail-meta"><span>Rapports liés :</span>' + sessChips + '</div>' : '') +
        '</div>' +
        '<div class="panel">' + renderSafeMarkdown(a.content || "") + '</div>';
    }

    return '<div class="master-detail">' + masterPane + '<div class="detail-pane">' + navBar + readerHtml + '</div></div>';
  }

  function render() {
    const root = document.getElementById("root");
    if (!root) return;

    const activeEl = document.activeElement;
    const focusId = activeEl && (activeEl.id === "gm-search" || activeEl.id === "gm-audit-search") ? activeEl.id : "";
    const selStart = focusId ? activeEl.selectionStart : null;
    const selEnd = focusId ? activeEl.selectionEnd : null;

    let htmlStr = renderShellHeader();
    if (state.toast) {
      htmlStr += '<div class="toast' + (state.toast.isError ? ' err' : '') + '">' +
        '<span>' + esc(state.toast.msg) + '</span>' +
        '<button type="button" class="btn" data-action="close-toast" style="padding:2px 7px;">✕</button>' +
        '</div>';
    }
    htmlStr += renderConfirmBars();

    if (state.loading && !state.records.length && !state.pendingSessions.length) {
      htmlStr += '<div class="panel"><div class="empty">Chargement des métriques...</div></div>';
    } else if (state.tab === "reports") {
      htmlStr += renderReportsTab();
    } else if (state.tab === "pending") {
      htmlStr += renderPendingTab();
    } else if (state.tab === "audits") {
      htmlStr += renderAuditsTab();
    } else {
      htmlStr += renderOverviewTab();
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

  document.addEventListener("keydown", (ev) => {
    if (state.tab === "reports" && ev.altKey && !ev.ctrlKey && !ev.metaKey) {
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        navigateReport(-1);
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        navigateReport(1);
      }
    }
  });

  document.addEventListener("click", (ev) => {
    const baseEl = ev.target instanceof Element ? ev.target : (ev.target && ev.target.parentElement);
    const target = baseEl ? baseEl.closest("[data-action]") : null;
    if (!target) return;
    const action = target.getAttribute("data-action");

    if (action === "switch-tab") {
      const nextTab = target.getAttribute("data-tab") || "overview";
      state.tab = nextTab;
      if (nextTab === "reports" && !state.detailTarget) {
        const list = filteredSortedRecords();
        if (list.length) {
          openSessionDetail(recKey(list[0]), false);
          return;
        }
      }
      if (nextTab === "audits" && !state.auditsLoaded) {
        loadAudits(false);
        return;
      }
      render();
      return;
    }
    if (action === "set-report-subtab") {
      state.reportSubtab = target.getAttribute("data-subtab") || "summary";
      if (state.reportSubtab === "compare") ensureCompareLoaded();
      render();
      return;
    }
    if (action === "open-compare") {
      state.tab = "reports";
      state.reportSubtab = "compare";
      state.reportsShowListMobile = false;
      ensureCompareLoaded();
      render();
      return;
    }
    if (action === "swap-compare") {
      const tmp = state.compareTargetA;
      state.compareTargetA = state.compareTargetB;
      state.compareTargetB = tmp;
      ensureCompareLoaded();
      render();
      return;
    }
    if (action === "nav-prev") {
      navigateReport(-1);
      return;
    }
    if (action === "nav-next") {
      navigateReport(1);
      return;
    }
    if (action === "toggle-reports-list") {
      state.reportsShowListMobile = !state.reportsShowListMobile;
      render();
      return;
    }
    if (action === "toggle-audits-list") {
      state.auditsShowListMobile = !state.auditsShowListMobile;
      render();
      return;
    }
    if (action === "filter-project") {
      const proj = target.getAttribute("data-project") || "";
      if (proj) {
        state.scope = "project";
        state.selectedProject = proj;
        render();
      }
      return;
    }
    if (action === "open-audit") {
      ev.stopPropagation();
      const aid = target.getAttribute("data-audit-id") || "";
      if (!state.auditsLoaded) loadAudits(false);
      if (aid) openAudit(aid, true);
      return;
    }
    if (action === "open-audit-ref") {
      const ref = target.getAttribute("data-ref") || "";
      resolveAuditRefAndOpen(ref);
      return;
    }
    if (action === "reload-audits") {
      loadAudits(true);
      return;
    }
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
    if (action === "archive-pending") {
      ev.stopPropagation();
      const cid = target.getAttribute("data-cid") || "";
      if (cid) archivePendingSession(cid, false);
      return;
    }
    if (action === "confirm-force-archive") {
      const cid = target.getAttribute("data-cid") || "";
      if (cid) archivePendingSession(cid, true);
      return;
    }
    if (action === "cancel-force-archive") {
      state.confirmForceArchiveCid = "";
      render();
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
      if (state.tab === "reports") {
        const list = filteredSortedRecords();
        const stillInScope = list.some((r) => recKey(r) === state.detailTarget || r.conversation_id === state.detailTarget);
        if (!stillInScope) {
          if (list.length) {
            openSessionDetail(recKey(list[0]), false);
            return;
          }
          state.detailTarget = "";
          state.detail = null;
        }
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
      if (t) openSessionDetail(t, true);
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
    } else if (el.id === "gm-audit-search") {
      state.auditSearch = el.value || "";
      render();
    }
  });

  document.addEventListener("change", (ev) => {
    const el = ev.target;
    if (!el || !el.id) return;
    if (el.id === "gm-proj-select") {
      state.selectedProject = el.value || "";
      if (state.tab === "reports") {
        const list = filteredSortedRecords();
        const stillInScope = list.some((r) => recKey(r) === state.detailTarget || r.conversation_id === state.detailTarget);
        if (!stillInScope) {
          if (list.length) {
            openSessionDetail(recKey(list[0]), false);
            return;
          }
          state.detailTarget = "";
          state.detail = null;
        }
      }
      render();
    } else if (el.id === "gm-model-filter") {
      state.modelFilter = el.value || "";
      render();
    } else if (el.id === "gm-alerts-only") {
      state.alertsOnly = Boolean(el.checked);
      render();
    } else if (el.id === "gm-purge-cb") {
      state.confirmPurge = Boolean(el.checked);
    } else if (el.id === "gm-quick-report") {
      if (el.value) openSessionDetail(el.value, true);
    } else if (el.id === "gm-cmp-a") {
      state.compareTargetA = el.value || "";
      ensureCompareLoaded();
      render();
    } else if (el.id === "gm-cmp-b") {
      state.compareTargetB = el.value || "";
      ensureCompareLoaded();
      render();
    } else if (el.id === "gm-audit-ws") {
      state.auditWorkspaceFilter = el.value || "";
      render();
    } else if (el.id === "gm-audit-verdict") {
      state.auditVerdictFilter = el.value || "";
      render();
    } else if (el.id === "gm-quick-audit") {
      if (el.value) openAudit(el.value, true);
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
      render();
      return;
    }

    if (msg.method === "ui/notifications/tool-input") {
      const args = (msg.params && msg.params.arguments) || {};
      if (typeof args.project === "string" && args.project.trim()) {
        state.activeProject = args.project.trim();
        state.selectedProject = args.project.trim();
        state.scope = "project";
      }
      if (typeof args.view === "string" && args.view.trim()) {
        state.tab = mapInitialTab(args.view);
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
        appInfo: { name: "gravity-metrics", version: "0.4.0" },
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
"""
