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
    ignoredSessions: [],
    activeCaptures: {},
    projects: [],
    byProject: [],
    audits: [],
    auditsRoots: [],
    auditsCount: 0,
    indexAudit: null,
    auditLinksBySession: {},
    auditsLoaded: false,
    auditsLoading: false,
    signature: "",
    histSig: "",
    pendingSig: "",
    auditsSig: "",
    metricsMdUri: "",
    scope: "global",
    activeProject: "",
    selectedProject: "",
    tariff: "2026",
    tab: "overview",
    sessionsSub: "archived",
    detailSubtab: "summary",
    reportsShowListMobile: true,
    auditsShowListMobile: true,
    showIgnoredPending: false,
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
    detailFreshness: null,
    detailLoading: false,
    detailCache: {},
    compareTargetA: "",
    compareTargetB: "",
    compareLoading: false,
    archiveDrawerCid: "",
    archiveForm: { label: "", project: "", note: "", full: false },
    editMetaTarget: "",
    editForm: { label: "", project: "", note: "" },
    auditSearch: "",
    auditWorkspaceFilter: "",
    auditVerdictFilter: "",
    selectedAuditId: "",
    selectedAudit: null,
    auditLoading: false,
    confirmDeleteTarget: "",
    confirmPurge: true,
    archivingCid: "",
    updatingTarget: "",
    toast: null,
    loading: true,
    pollInFlight: false,
    hasToolResult: false
  };

  let rpcId = 1;
  const pendingRpc = new Map();
  let fallbackTimer = null;
  let pollTimer = null;
  let toastTimer = null;

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

  function normProj(p) {
    return String(p || "").trim().toLowerCase();
  }

  function scopedRecords() {
    let list = state.records.slice();
    if (state.scope === "project" && state.selectedProject) {
      const selCf = normProj(state.selectedProject);
      list = list.filter((r) => normProj(r.project) === selCf);
    }
    return list;
  }

  function hasTariffDiff() {
    const list = scopedRecords();
    return list.some((r) => {
      const c26 = Number(r.cost_usd || 0);
      const c27 = r.cost_2027_usd != null ? Number(r.cost_2027_usd) : c26;
      return Math.abs(c26 - c27) > 1e-6;
    });
  }

  function effectiveTariff() {
    return hasTariffDiff() ? state.tariff : "2026";
  }

  function recCost(rec) {
    if (effectiveTariff() === "2027") {
      const p27 = rec.cost_2027_usd;
      if (p27 != null) return Number(p27) || 0;
    }
    return Number(rec.cost_usd) || 0;
  }

  function recCacheSavings(rec) {
    const c26 = Number(rec.cost_usd || 0);
    const sav26 = Number(rec.cache_savings_usd || 0);
    if (effectiveTariff() === "2027" && c26 > 0 && rec.cost_2027_usd != null) {
      const ratio = (Number(rec.cost_2027_usd) || 0) / c26;
      return sav26 * ratio;
    }
    return sav26;
  }

  function recKey(rec) {
    return String((rec && (rec.label || rec.conversation_id)) || "");
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
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    state.toast = { msg: String(msg || ""), isError: Boolean(isError) };
    render();
    if (!isError) {
      toastTimer = setTimeout(() => {
        state.toast = null;
        toastTimer = null;
        const tEl = document.querySelector(".toast");
        if (tEl && tEl.parentNode) tEl.parentNode.removeChild(tEl);
      }, 4000);
    }
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
    const freshness = sc.freshness && typeof sc.freshness === "object" ? sc.freshness : null;
    keys.forEach((k) => {
      state.detailCache[String(k)] = {
        detail: sc.detail,
        slug: String(sc.slug || ""),
        reportMdUri: String(sc.report_md_uri || ""),
        freshness: freshness
      };
    });
  }

  function mapInitialRoute(rawView) {
    const v = String(rawView || "").toLowerCase();
    if (v === "pending") return { tab: "sessions", sub: "pending" };
    if (v === "compare") return { tab: "sessions", sub: "compare" };
    if (v === "detail" || v === "reports" || v === "history" || v === "sessions") {
      return { tab: "sessions", sub: "archived" };
    }
    if (v === "audits") return { tab: "audits", sub: "archived" };
    return { tab: "overview", sub: "archived" };
  }

  function updateBadgesInPlace() {
    const subEl = document.querySelector(".brand-sub");
    if (subEl) {
      const projLabel = state.scope === "project" && state.selectedProject
        ? " · " + state.selectedProject
        : " · tous projets";
      subEl.textContent = state.records.length + " enregistrée(s) · " + state.pendingSessions.length + " en attente" + projLabel;
    }
    const sessBadge = document.querySelector('[data-badge="sessions"]');
    if (sessBadge) {
      const pend = state.pendingSessions.length;
      sessBadge.textContent = pend > 0 ? String(scopedRecords().length) + " · • " + pend : String(scopedRecords().length);
      sessBadge.className = "tab-badge" + (pend > 0 ? " warn" : "");
    }
    const pendBadge = document.querySelector('[data-badge="pending"]');
    if (pendBadge) {
      const pend = state.pendingSessions.length;
      pendBadge.textContent = String(pend);
      pendBadge.className = "tab-badge" + (pend > 0 ? " warn" : "");
    }
    const audBadge = document.querySelector('[data-badge="audits"]');
    if (audBadge) {
      const audCount = state.auditsLoaded ? state.audits.length : state.auditsCount;
      audBadge.textContent = String(audCount);
    }
  }

  function hydrateFromStatePayload(sc, isInitialToolResult) {
    if (!sc || typeof sc !== "object") return;
    const prevHistSig = state.histSig;
    const prevPendingSig = state.pendingSig;
    const prevAuditsSig = state.auditsSig;

    const newHistSig = typeof sc.hist_sig === "string" ? sc.hist_sig : state.histSig;
    const newPendingSig = typeof sc.pending_sig === "string" ? sc.pending_sig : state.pendingSig;
    const newAuditsSig = typeof sc.audits_sig === "string" ? sc.audits_sig : state.auditsSig;

    const histChanged = isInitialToolResult || !prevHistSig || newHistSig !== prevHistSig;
    const pendingChanged = isInitialToolResult || newPendingSig !== prevPendingSig;
    const auditsChanged = isInitialToolResult || newAuditsSig !== prevAuditsSig;

    if (Array.isArray(sc.records)) {
      state.records = sc.records;
      if (histChanged) {
        state.detailCache = {};
      }
    }
    if (Array.isArray(sc.pending_sessions)) state.pendingSessions = sc.pending_sessions;
    if (Array.isArray(sc.ignored_sessions)) state.ignoredSessions = sc.ignored_sessions;
    if (sc.active_captures && typeof sc.active_captures === "object") state.activeCaptures = sc.active_captures;
    if (Array.isArray(sc.projects)) state.projects = sc.projects;
    if (Array.isArray(sc.by_project)) state.byProject = sc.by_project;
    if (typeof sc.audits_count === "number") state.auditsCount = sc.audits_count;
    if ("index_audit" in sc) state.indexAudit = sc.index_audit || null;
    if (sc.audit_links_by_session && typeof sc.audit_links_by_session === "object") {
      state.auditLinksBySession = sc.audit_links_by_session;
    }
    if (Array.isArray(sc.audits)) {
      state.audits = sc.audits;
      state.auditsCount = sc.audits.length;
      state.auditsLoaded = true;
    }
    if (typeof sc.signature === "string") state.signature = sc.signature;
    state.histSig = newHistSig;
    state.pendingSig = newPendingSig;
    state.auditsSig = newAuditsSig;
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
      const route = mapInitialRoute(sc.initial_view);
      state.tab = route.tab;
      state.sessionsSub = route.sub;

      if (state.tab === "sessions" && state.sessionsSub === "archived" && initTarget) {
        state.detailTarget = initTarget;
        state.reportsShowListMobile = false;
        if (sc.initial_detail && typeof sc.initial_detail === "object") {
          state.detail = sc.initial_detail;
          state.detailSlug = String(sc.initial_detail_slug || "");
          state.detailMdUri = String(sc.initial_detail_md_uri || "");
          state.detailFreshness = null;
          state.detailLoading = false;
          cacheDetailPayload(initTarget, {
            detail: state.detail,
            slug: state.detailSlug,
            report_md_uri: state.detailMdUri,
            freshness: null
          });
        } else {
          openSessionDetail(initTarget, true);
          return;
        }
      } else if (state.tab === "sessions" && state.sessionsSub === "archived") {
        const list = filteredSortedRecords();
        if (list.length) {
          openSessionDetail(recKey(list[0]), false);
          return;
        }
      } else if (state.tab === "sessions" && state.sessionsSub === "compare") {
        ensureCompareLoaded();
      } else if (state.tab === "audits") {
        loadAudits(false);
      } else {
        state.detail = null;
        state.detailTarget = "";
      }
      render();
      return;
    }

    if (state.detailTarget) {
      const stillExists = state.records.some(
        (r) => r.label === state.detailTarget || r.conversation_id === state.detailTarget
      );
      if (!stillExists) {
        state.detail = null;
        state.detailTarget = "";
      }
    }

    // Rendu incrémental : éviter d'écraser le DOM lors d'un poll si la vue active n'est pas impactée
    const viewingPending = state.tab === "sessions" && state.sessionsSub === "pending";
    const viewingAudits = state.tab === "audits";
    if (!histChanged && !viewingPending && !viewingAudits && (pendingChanged || auditsChanged)) {
      updateBadgesInPlace();
      return;
    }
    if (!histChanged && viewingAudits && !auditsChanged && pendingChanged) {
      updateBadgesInPlace();
      return;
    }
    if (viewingPending && state.archiveDrawerCid && !histChanged) {
      updateBadgesInPlace();
      return;
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
        const prevHist = state.histSig;
        hydrateFromStatePayload(sc, false);
        if (sc.hist_sig !== prevHist && state.tab === "sessions" && state.sessionsSub === "archived" && state.detailTarget) {
          try {
            const detRes = await callServerTool("metrics_get_session", { target: state.detailTarget });
            const detSc = extractStructured(detRes);
            if (detSc && detSc.detail) {
              state.detail = detSc.detail;
              state.detailSlug = String(detSc.slug || "");
              state.detailMdUri = String(detSc.report_md_uri || "");
              state.detailFreshness = detSc.freshness && typeof detSc.freshness === "object" ? detSc.freshness : null;
              cacheDetailPayload(state.detailTarget, detSc);
              render();
            }
          } catch (_) {}
        }
        if (state.tab === "audits" && sc.audits_sig !== state.auditsSig) {
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
    state.tab = "sessions";
    state.sessionsSub = "archived";
    state.detailTarget = target;
    state.confirmDeleteTarget = "";
    state.editMetaTarget = "";
    if (hideMobileList !== false) state.reportsShowListMobile = false;

    const cached = state.detailCache[target];
    if (cached) {
      state.detail = cached.detail;
      state.detailSlug = cached.slug;
      state.detailMdUri = cached.reportMdUri;
      state.detailFreshness = cached.freshness || null;
      state.detailLoading = false;
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
      state.detailFreshness = loaded.freshness || null;
    } catch (err) {
      showToast(err.message, true);
    } finally {
      state.detailLoading = false;
      render();
    }
  }

  async function ensureCompareLoaded() {
    const pool = scopedRecords();
    const list = pool.length >= 2 ? pool : state.records.slice();
    if (!list.length) return;
    const validKey = (k) => k && state.records.some((r) => recKey(r) === k || r.conversation_id === k);
    if (!validKey(state.compareTargetA)) {
      state.compareTargetA = state.detailTarget && validKey(state.detailTarget)
        ? state.detailTarget
        : recKey(list[list.length - 1]);
    }
    if (!validKey(state.compareTargetB)) {
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
        if ("index_audit" in sc) state.indexAudit = sc.index_audit || null;
        if (sc.audit_links_by_session) state.auditLinksBySession = sc.audit_links_by_session;
        state.auditsLoaded = true;
        const isIdx = state.indexAudit && state.selectedAuditId === state.indexAudit.audit_id;
        if (state.selectedAuditId && !isIdx && !state.audits.some((a) => a.audit_id === state.selectedAuditId)) {
          state.selectedAuditId = "";
          state.selectedAudit = null;
        }
        if (!state.selectedAuditId && state.audits.length) {
          openAudit(state.audits[0].audit_id, false);
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
    const allCand = state.indexAudit ? state.audits.concat([state.indexAudit]) : state.audits;
    const match =
      allCand.find((a) => a.workspace === curWs && String(a.rel_path || "").toLowerCase() === clean) ||
      allCand.find((a) => a.workspace === curWs && String(a.filename || "").toLowerCase() === clean) ||
      allCand.find((a) => String(a.rel_path || "").toLowerCase() === clean) ||
      allCand.find((a) => String(a.filename || "").toLowerCase() === clean);
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

  function openArchiveDrawer(cid) {
    const p = state.pendingSessions.find((x) => x.conversation_id === cid) || {};
    const cap = p.active_capture || {};
    state.archiveDrawerCid = cid;
    state.archiveForm = {
      label: String(cap.label || "").trim(),
      project: String(p.project || state.selectedProject || state.activeProject || "").trim(),
      note: "",
      full: false
    };
    render();
  }

  async function submitArchiveDrawer(cid) {
    if (!cid || !state.hostCaps.serverTools || state.archivingCid) return;
    state.archivingCid = cid;
    const form = state.archiveForm || {};
    render();
    try {
      const res = await callServerTool("metrics_archive_session", {
        conversation_id: cid,
        label: String(form.label || "").trim(),
        project: String(form.project || "").trim(),
        note: String(form.note || "").trim(),
        full: Boolean(form.full),
        force: false
      });
      if (res && res.isError) {
        const errMsg = (res.content && res.content[0] && res.content[0].text) || "Enregistrement refusé";
        throw new Error(errMsg);
      }
      const sc = extractStructured(res);
      state.archiveDrawerCid = "";
      if (sc) {
        hydrateFromStatePayload(sc, false);
        const lbl = (sc.archived && sc.archived.label) || cid.slice(0, 8);
        showToast(
          sc.already_archived
            ? "Session « " + lbl + " » déjà enregistrée (immuable)."
            : "Session « " + lbl + " » enregistrée.",
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

  async function updateRecordedSession(target) {
    if (!target || !state.hostCaps.serverTools || state.updatingTarget) return;
    const rec = state.records.find((r) => r.label === target || r.conversation_id === target) || {};
    const d = state.detail && (state.detail.label === target || state.detail.root_conversation_id === target)
      ? state.detail
      : null;
    const cid = String(rec.conversation_id || (d && d.root_conversation_id) || "").trim();
    if (!cid) {
      showToast("Identifiant de conversation introuvable pour cette session.", true);
      return;
    }
    const lbl = String(rec.label || (d && d.label) || target || "").trim();
    const proj = String(rec.project || (d && d.project) || "").trim();
    const note = String(rec.note || "").trim();
    const winMode = String(rec.window_mode || (d && d.window && d.window.mode) || "");
    state.updatingTarget = lbl || cid;
    render();
    try {
      const res = await callServerTool("metrics_archive_session", {
        conversation_id: cid,
        label: lbl,
        project: proj,
        note: note,
        full: winMode === "full_conversation",
        update_sealed: true
      });
      if (res && res.isError) {
        const errMsg = (res.content && res.content[0] && res.content[0].text) || "Mise à jour refusée";
        throw new Error(errMsg);
      }
      const sc = extractStructured(res);
      state.detailCache = {};
      if (sc) {
        hydrateFromStatePayload(sc, false);
        const savedLbl = (sc.archived && sc.archived.label) || lbl || cid.slice(0, 8);
        state.detailTarget = savedLbl;
        await openSessionDetail(savedLbl, false);
        showToast("Session « " + savedLbl + " » mise à jour.", false);
      }
    } catch (err) {
      showToast("Erreur mise à jour : " + err.message, true);
    } finally {
      state.updatingTarget = "";
      render();
    }
  }

  async function ignorePendingSession(cid) {
    if (!cid || !state.hostCaps.serverTools) return;
    try {
      const res = await callServerTool("metrics_ignore_session", { conversation_id: cid });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Erreur masquage");
      }
      const sc = extractStructured(res);
      if (state.archiveDrawerCid === cid) state.archiveDrawerCid = "";
      if (sc) hydrateFromStatePayload(sc, false);
      showToast("Session masquée (réversible via Masquées).", false);
    } catch (err) {
      showToast(err.message, true);
    }
  }

  async function unignorePendingSession(cid) {
    if (!cid || !state.hostCaps.serverTools) return;
    try {
      const res = await callServerTool("metrics_unignore_session", { conversation_id: cid });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Erreur restauration");
      }
      const sc = extractStructured(res);
      if (sc) hydrateFromStatePayload(sc, false);
      showToast("Session restaurée dans À enregistrer.", false);
    } catch (err) {
      showToast(err.message, true);
    }
  }

  async function saveEditSessionMeta(target) {
    if (!target || !state.hostCaps.serverTools) return;
    const f = state.editForm || {};
    try {
      const res = await callServerTool("metrics_update_session_meta", {
        target,
        label: String(f.label || "").trim(),
        project: String(f.project || "").trim(),
        note: String(f.note || "").trim()
      });
      if (res && res.isError) {
        throw new Error((res.content && res.content[0] && res.content[0].text) || "Mise à jour échouée");
      }
      const sc = extractStructured(res);
      state.editMetaTarget = "";
      if (sc) {
        if (sc.updated && sc.updated.label) {
          state.detailTarget = sc.updated.label;
        }
        hydrateFromStatePayload(sc, false);
        if (state.detailTarget) {
          openSessionDetail(state.detailTarget, false);
        }
        showToast("Métadonnées mises à jour.", false);
      }
    } catch (err) {
      showToast(err.message, true);
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
    const tariff = effectiveTariff();
    if (Array.isArray(state.byProject) && state.byProject.length) {
      return state.byProject.slice().sort((a, b) => {
        const ca = tariff === "2027" ? Number(a.cost_2027_usd || 0) : Number(a.cost_usd || 0);
        const cb = tariff === "2027" ? Number(b.cost_2027_usd || 0) : Number(b.cost_usd || 0);
        return cb - ca;
      });
    }
    return [];
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

  function renderMilestonesStrip(ms, use2027, showCosts) {
    if (!ms || !Array.isArray(ms.phases) || !ms.phases.length) return "";
    let html = '<div class="milestones">';
    ms.phases.forEach((ph, idx) => {
      const st = ph.status === "done" ? "ms-done" : (ph.status === "active" ? "ms-active" : "ms-pending");
      const costVal = use2027 ? Number(ph.cost_2027_usd || 0) : Number(ph.cost_usd || 0);
      const meta = showCosts
        ? '<div class="subline">' + fmtInt(ph.llm_calls || 0) + ' app. · $' + costVal.toFixed(4) + '</div>'
        : '<div class="subline">' + (ph.status === "done" ? "terminé" : (ph.status === "active" ? "en cours" : "à venir")) + '</div>';
      html += '<div class="ms-step ' + st + '">' +
        '<div class="ms-step-head"><span>' + (idx + 1) + '. ' + esc(ph.label) + '</span><span class="ms-dot"></span></div>' +
        meta +
        '</div>';
    });
    html += '</div>';
    return html;
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

    let projSelect = "";
    if (state.tab !== "audits" && projectOptions.length > 0) {
      projSelect = '<select id="gm-proj-select" aria-label="Projet">' +
        '<option value=""' + (state.scope === "global" || !state.selectedProject ? " selected" : "") + '>Tous les projets</option>';
      projectOptions.forEach((p) => {
        const sel = state.scope === "project" && normProj(p) === normProj(state.selectedProject) ? " selected" : "";
        projSelect += '<option value="' + esc(p) + '"' + sel + '>' + esc(p) + '</option>';
      });
      projSelect += '</select>';
    }

    const showTariff = hasTariffDiff();
    const effTariff = effectiveTariff();
    const tariffHtml = showTariff
      ? '<div class="seg" role="group" aria-label="Tarif">' +
        '<button type="button" data-action="set-tariff" data-tariff="2026" class="' + (effTariff === "2026" ? "active" : "") + '">2026</button>' +
        '<button type="button" data-action="set-tariff" data-tariff="2027" class="' + (effTariff === "2027" ? "active" : "") + '">2027</button>' +
        '</div>'
      : "";

    const openGlobalMdBtn = (canUseAuxPane() && state.metricsMdUri)
      ? '<button type="button" class="btn sm" data-action="open-file" data-uri="' + esc(state.metricsMdUri) + '">METRICS.md</button>'
      : "";

    const refreshBtn = state.hostCaps.serverTools
      ? '<button type="button" class="btn sm" data-action="refresh">Rafraîchir</button>'
      : "";

    const scopedCount = scopedRecords().length;
    const pendCount = state.pendingSessions.length;
    const audCount = state.auditsLoaded ? state.audits.length : state.auditsCount;
    const projNote = state.scope === "project" && state.selectedProject ? " · " + state.selectedProject : " · tous projets";

    const sessBadgeTxt = pendCount > 0 ? String(scopedCount) + " · • " + pendCount : String(scopedCount);

    const tabsHtml = '<nav class="nav-tabs" aria-label="Navigation principale">' +
      '<button type="button" data-action="switch-tab" data-tab="overview" class="tab-btn' + (state.tab === "overview" ? " active" : "") + '">Synthèse</button>' +
      '<button type="button" data-action="switch-tab" data-tab="sessions" class="tab-btn' + (state.tab === "sessions" ? " active" : "") + '">Sessions' +
      '<span class="tab-badge' + (pendCount > 0 ? " warn" : "") + '" data-badge="sessions">' + esc(sessBadgeTxt) + '</span></button>' +
      '<span class="nav-divider" aria-hidden="true"></span>' +
      '<button type="button" data-action="switch-tab" data-tab="audits" class="tab-btn' + (state.tab === "audits" ? " active" : "") + '">Audits Agents' +
      '<span class="tab-badge" data-badge="audits">' + esc(String(audCount)) + '</span></button>' +
      '</nav>';

    return '<div class="shell-head">' +
      '<div class="topbar">' +
      '<div class="brand">' +
      '<span class="brand-dot" title="Synchronisation 0 token"></span>' +
      '<div><div class="brand-title">Gravity Metrics</div>' +
      '<div class="brand-sub">' + esc(state.records.length) + ' enregistrée(s) · ' + esc(pendCount) + ' en attente' + esc(projNote) + '</div>' +
      '</div></div>' +
      '<div class="controls">' + projSelect + tariffHtml + openGlobalMdBtn + refreshBtn + '</div>' +
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
        '<input id="gm-purge-cb" type="checkbox"' + (state.confirmPurge ? " checked" : "") + ' /> Purger <code>reports/</code>' +
        '</label></div>' +
        '<div class="confirm-actions">' +
        '<button type="button" class="btn sm danger" data-action="confirm-delete">Confirmer</button>' +
        '<button type="button" class="btn sm" data-action="cancel-delete">Annuler</button>' +
        '</div></div>';
    }
    return html;
  }

  function renderSessionBadges(rec) {
    const cid = String(rec.conversation_id || "");
    const links = state.auditLinksBySession[cid] || [];
    const bCnt = Number(rec.bridge_children_count) || 0;
    let out = "";
    if (bCnt > 0) out += '<span class="pill bridge">bridge:' + bCnt + '</span> ';
    if (links.length) {
      out += '<button type="button" class="pill-btn" data-action="open-audit" data-audit-id="' + esc(links[0].audit_id) + '" title="Ouvrir l’audit associé">Audit' + (links.length > 1 ? ':' + links.length : '') + '</button>';
    }
    return out;
  }

  function renderOverviewTab() {
    const scoped = scopedRecords();
    const use2027 = effectiveTariff() === "2027";
    const totalCost = scoped.reduce((acc, r) => acc + recCost(r), 0);
    const avgCost = scoped.length ? totalCost / scoped.length : 0;
    const totalSavings = scoped.reduce((acc, r) => acc + recCacheSavings(r), 0);
    const avgCache = scoped.length
      ? scoped.reduce((acc, r) => acc + (Number(r.cache_hit_rate_pct) || 0), 0) / scoped.length
      : 0;
    const totalAgents = scoped.reduce((acc, r) => acc + (Number(r.agents) || 0), 0);
    const totalCalls = scoped.reduce((acc, r) => acc + (Number(r.llm_calls) || 0), 0);
    const totalDur = scoped.reduce((acc, r) => acc + (Number(r.wall_clock_s) || 0), 0);
    const sumDup = scoped.reduce((acc, r) => acc + (Number(r.duplicate_spawns) || 0), 0);
    const sumRw = scoped.reduce((acc, r) => acc + (Number(r.rewind_calls) || 0), 0);
    const sumInt = scoped.reduce((acc, r) => acc + (Number(r.interrupted_agents) || 0), 0);
    const totalAlerts = sumDup + sumRw + sumInt;

    let pendBanner = "";
    if (state.pendingSessions.length) {
      pendBanner = '<div class="banner-info">' +
        '<span><strong>' + state.pendingSessions.length + ' session(s)</strong> en attente d’enregistrement.</span>' +
        '<button type="button" class="btn sm primary" data-action="open-pending">Voir À enregistrer →</button>' +
        '</div>';
    }

    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Coût cumulé' + (use2027 ? ' (2027)' : '') + '</div><div class="v">$' + totalCost.toFixed(4) + '</div><div class="sub">moy. $' + avgCost.toFixed(4) + ' · ' + scoped.length + ' session(s)</div></div>' +
      '<div class="card"><div class="k">Cache moyen</div><div class="v">' + avgCache.toFixed(1) + '%</div><div class="sub">$' + totalSavings.toFixed(4) + ' économisés</div></div>' +
      '<div class="card"><div class="k">Sessions &amp; Agents</div><div class="v">' + scoped.length + ' · ' + fmtInt(totalAgents) + ' ag.</div><div class="sub">' + fmtInt(totalCalls) + ' appels · ' + esc(fmtDur(totalDur)) + '</div></div>' +
      '<div class="card"><div class="k">Santé / Alertes</div><div class="v">' + (totalAlerts === 0 ? 'Clean' : fmtInt(totalAlerts) + ' alerte(s)') + '</div><div class="sub">Dup:' + sumDup + ' · Rw:' + sumRw + ' · Int:' + sumInt + '</div></div>' +
      '</div>';

    const byProject = computeByProject();
    const projectPanel = byProject.length >= 2
      ? '<div class="panel"><div class="panel-head">' +
        '<span class="panel-title">Répartition par Projet (' + (use2027 ? '2027' : '2026') + ')</span>' +
        (state.scope === "project" ? '<button type="button" class="btn sm" data-action="clear-project">Tous les projets</button>' : '<span class="subline">' + byProject.length + ' projets</span>') +
        '</div>' + renderProjectShareBars(byProject, use2027) + '</div>'
      : "";

    const charts = '<div class="panels two">' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Évolution du Coût (' + (use2027 ? '2027' : '2026') + ')</span><span class="subline">tri chronologique</span></div>' +
      '<div class="svg-box">' + renderCostEvolutionSvg(scoped, state.detailTarget) + '</div></div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Répartition par Catégorie</span></div>' +
      renderCategoryHtmlBars(scoped) + '</div>' +
      '</div>';

    const recentList = sortRecordsChrono(scoped).reverse().slice(0, 5);
    let recentRows = "";
    recentList.forEach((r) => {
      const rowKey = recKey(r);
      const alertsCount = (Number(r.duplicate_spawns) || 0) + (Number(r.rewind_calls) || 0) + (Number(r.interrupted_agents) || 0);
      const healthPill = alertsCount > 0
        ? '<span class="pill warn">' + alertsCount + ' alerte(s)</span>'
        : '<span class="pill ok">Clean</span>';
      recentRows += '<tr class="clickable" data-action="open-detail" data-target="' + esc(rowKey) + '">' +
        '<td class="mono">' + esc(fmtDate(r)) + '</td>' +
        '<td><strong>' + esc(r.label || rowKey) + '</strong>' + (r.project ? '<div class="subline">' + esc(r.project) + '</div>' : '') + '</td>' +
        '<td>' + esc(r.execution_mode || "single") + ' ' + renderSessionBadges(r) + '</td>' +
        '<td class="num"><strong>$' + recCost(r).toFixed(4) + '</strong></td>' +
        '<td class="num">' + (Number(r.cache_hit_rate_pct) || 0).toFixed(1) + '%</td>' +
        '<td class="num">' + fmtInt(r.llm_calls) + '</td>' +
        '<td>' + healthPill + '</td>' +
        '</tr>';
    });

    const recentPanel = '<div class="panel">' +
      '<div class="panel-head">' +
      '<span class="panel-title">Dernières sessions (' + recentList.length + '/' + scoped.length + ')</span>' +
      (scoped.length > 0 ? '<button type="button" class="btn sm" data-action="switch-tab" data-tab="sessions">Toutes les sessions →</button>' : '') +
      '</div>' +
      (recentList.length
        ? '<div class="table-wrap"><table><thead><tr>' +
          '<th>Date</th><th>Session</th><th>Mode</th><th class="num">Coût</th><th class="num">Cache %</th><th class="num">Appels</th><th>Santé</th>' +
          '</tr></thead><tbody>' + recentRows + '</tbody></table></div>'
        : '<div class="empty">Aucune session enregistrée. Lancez <code>/gravity-metrics stop</code> ou enregistrez depuis <strong>Sessions → À enregistrer</strong>.</div>') +
      '</div>';

    return pendBanner + kpis + projectPanel + charts + recentPanel;
  }

  function renderPendingSubtab() {
    const activeList = state.pendingSessions || [];
    const ignoredList = state.ignoredSessions || [];
    const useList = state.showIgnoredPending ? ignoredList : activeList;

    const topBar = '<div class="panel">' +
      '<div class="panel-head">' +
      '<span class="panel-title">' + (state.showIgnoredPending ? 'Sessions masquées (' + ignoredList.length + ')' : 'Sessions en attente d’enregistrement (' + activeList.length + ')') + '</span>' +
      '<div class="controls">' +
      '<button type="button" class="btn sm' + (state.showIgnoredPending ? ' primary' : '') + '" data-action="toggle-ignored-pending">' +
      (state.showIgnoredPending ? 'Voir actives (' + activeList.length + ')' : 'Masquées (' + ignoredList.length + ')') +
      '</button>' +
      '</div></div>' +
      (!useList.length
        ? '<div class="empty">' + (state.showIgnoredPending ? 'Aucune session masquée.' : 'Aucune session Gravity en attente d’enregistrement.') + '</div>'
        : '') +
      '</div>';

    if (!useList.length) return topBar;

    if (state.showIgnoredPending) {
      let rows = "";
      ignoredList.forEach((p) => {
        const cid = String(p.conversation_id || "");
        rows += '<div class="share-row">' +
          '<div class="share-top">' +
          '<div><strong>' + esc(p.title || "(sans titre)") + '</strong> <span class="mono">(' + esc(cid.slice(0, 8)) + ')</span></div>' +
          '<button type="button" class="btn sm primary" data-action="unignore-pending" data-cid="' + esc(cid) + '">Restaurer</button>' +
          '</div>' +
          '<div class="share-meta">' +
          (p.project ? '<span>Projet : ' + esc(p.project) + '</span>' : '') +
          '<span>' + fmtInt(p.llm_calls) + ' appels</span>' +
          '<span>MAJ : ' + esc(String(p.updated_at || "").slice(0, 16).replace("T", " ")) + '</span>' +
          '</div></div>';
      });
      return topBar + '<div class="panel"><div class="share-list">' + rows + '</div></div>';
    }

    const inProgress = activeList.filter((p) => p.is_active);
    const finished = activeList.filter((p) => !p.is_active);

    const renderGroup = (title, items, badgeTone) => {
      if (!items.length) return "";
      let cards = "";
      items.forEach((p) => {
        const cid = String(p.conversation_id || "");
        const bCnt = Number(p.bridge_children_count) || 0;
        const isBusy = state.archivingCid === cid;
        const isDrawerOpen = state.archiveDrawerCid === cid;
        const phasePill = p.current_phase ? '<span class="pill ' + badgeTone + '">' + esc(p.current_phase) + '</span>' : '';
        const capPill = p.active_capture ? '<span class="pill info">capture active</span>' : '';
        const bridgePill = bCnt > 0 ? '<span class="pill bridge">bridge:' + bCnt + '</span>' : '';

        const actions = state.hostCaps.serverTools
          ? '<div class="controls">' +
            '<button type="button" class="btn sm primary" data-action="toggle-archive-drawer" data-cid="' + esc(cid) + '"' + (isBusy ? ' disabled' : '') + '>' +
            (isDrawerOpen ? 'Fermer' : 'Enregistrer…') + '</button>' +
            '<button type="button" class="btn sm" data-action="ignore-pending" data-cid="' + esc(cid) + '">Ignorer</button>' +
            '</div>'
          : '';

        let drawerHtml = "";
        if (isDrawerOpen) {
          const f = state.archiveForm || {};
          drawerHtml = '<div class="drawer">' +
            '<div class="form-grid">' +
            '<label class="field">Label<input id="gm-arch-label" type="text" placeholder="auto si vide" value="' + esc(f.label || "") + '" /></label>' +
            '<label class="field">Projet<input id="gm-arch-project" type="text" placeholder="nom du projet" value="' + esc(f.project || "") + '" /></label>' +
            '<label class="field">Note<input id="gm-arch-note" type="text" placeholder="note optionnelle" value="' + esc(f.note || "") + '" /></label>' +
            '<label class="field">Portée<select id="gm-arch-scope">' +
            '<option value="window"' + (!f.full ? ' selected' : '') + '>Fenêtre Gravity</option>' +
            '<option value="full"' + (f.full ? ' selected' : '') + '>Conversation complète</option>' +
            '</select></label>' +
            '</div>' +
            '<div class="controls">' +
            '<button type="button" class="btn sm primary" data-action="submit-archive-drawer" data-cid="' + esc(cid) + '"' + (isBusy ? ' disabled' : '') + '>' + (isBusy ? 'Enregistrement...' : 'Enregistrer') + '</button>' +
            '<button type="button" class="btn sm" data-action="cancel-archive-drawer">Annuler</button>' +
            '</div></div>';
        }

        cards += '<div class="share-row">' +
          '<div class="share-top">' +
          '<div><strong>' + esc(p.title || "(sans titre)") + '</strong> <span class="mono">(' + esc(cid.slice(0, 8)) + ')</span> ' +
          capPill + ' ' + phasePill + ' ' + bridgePill + '</div>' +
          actions +
          '</div>' +
          '<div class="share-meta">' +
          (p.project ? '<span>Projet : ' + esc(p.project) + '</span>' : '') +
          '<span>' + fmtInt(p.llm_calls) + ' appels</span>' +
          '<span>' + esc(p.window_mode || "") + '</span>' +
          '<span>MAJ : ' + esc(String(p.updated_at || "").slice(0, 16).replace("T", " ")) + '</span>' +
          '</div>' +
          (p.milestones ? renderMilestonesStrip(p.milestones, false, false) : '') +
          drawerHtml +
          '</div>';
      });
      return '<div class="panel"><div class="panel-head"><span class="panel-title">' + esc(title) + ' (' + items.length + ')</span></div>' +
        '<div class="share-list">' + cards + '</div></div>';
    };

    return topBar +
      renderGroup("En cours", inProgress, "info") +
      renderGroup("Terminée — prête à enregistrer", finished, "ok");
  }

  function renderCompareSubtab() {
    const pool = scopedRecords();
    const allRecs = (pool.length >= 2 ? pool : state.records).slice().reverse();
    if (allRecs.length < 2) {
      return '<div class="panel"><div class="empty">Au moins 2 sessions enregistrées sont nécessaires pour comparer A vs B.</div></div>';
    }
    const use2027 = effectiveTariff() === "2027";
    const keyA = state.compareTargetA || recKey(allRecs[0]);
    const keyB = state.compareTargetB || recKey(allRecs[1]);

    const optionsFor = (selectedKey) => allRecs.map((r) => {
      const k = recKey(r);
      const sel = (k === selectedKey || r.conversation_id === selectedKey) ? " selected" : "";
      return '<option value="' + esc(k) + '"' + sel + '>' + esc((r.label || k) + " (" + (r.project || "—") + " · $" + recCost(r).toFixed(3) + ")") + '</option>';
    }).join("");

    const selectorBar = '<div class="panel"><div class="panel-head"><span class="panel-title">Comparateur A vs B</span>' +
      (pool.length < 2 && state.scope === "project" ? '<span class="subline">portée élargie à tous les projets</span>' : '') +
      '</div>' +
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
      return '<div class="panel"><div class="empty">Sélectionnez une session enregistrée dans la liste pour afficher le détail.</div></div>';
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
    const use2027 = effectiveTariff() === "2027";
    const actCost = use2027 ? p : c;
    const shownTotal = Number(actCost.total_usd || 0);
    const otherTotal = use2027 ? Number(c.total_usd || 0) : Number(p.total_usd || 0);
    const histRec = state.records.find((r) => r.label === d.label || r.conversation_id === d.root_conversation_id) || {};
    const detFresh = state.detailFreshness && typeof state.detailFreshness === "object" ? state.detailFreshness : null;
    const hasNew = Boolean((detFresh && detFresh.has_new_activity) || histRec.has_new_activity);
    const newDelta = Number(
      detFresh && detFresh.new_llm_calls_delta != null
        ? detFresh.new_llm_calls_delta
        : (histRec.new_llm_calls_delta || 0)
    );
    const updateKey = String(d.label || d.root_conversation_id || "");
    const isUpdating = Boolean(state.updatingTarget && (state.updatingTarget === d.label || state.updatingTarget === d.root_conversation_id));

    let actionBtns = "";
    if (state.hostCaps.serverTools) {
      actionBtns += '<button type="button" class="btn sm' + (hasNew ? ' primary' : '') + '" data-action="update-recorded-session" data-target="' + esc(updateKey) + '"' + (state.updatingTarget ? ' disabled' : '') + '>' + (isUpdating ? 'Mise à jour...' : 'Mettre à jour') + '</button>';
      actionBtns += '<button type="button" class="btn sm" data-action="toggle-edit-meta" data-target="' + esc(d.label) + '">Éditer</button>';
    }
    if (canUseAuxPane() && state.detailMdUri) {
      actionBtns += '<button type="button" class="btn sm" data-action="open-file" data-uri="' + esc(state.detailMdUri) + '">Ouvrir .md</button>';
    }
    if (state.hostCaps.updateModelContext && !isGlobalSurface()) {
      actionBtns += '<button type="button" class="btn sm" data-action="attach-context">Joindre</button>';
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
      { id: "summary", label: "Synthèse" },
      { id: "agents", label: (bridgeCount > 0 ? "Agents & Bridge (" : "Agents (") + ((d.agents || []).length) + ")" },
      { id: "diag", label: "Diagnostic" }
    ];
    let subHtml = '<div class="subtabs">';
    subtabs.forEach((st) => {
      subHtml += '<button type="button" class="subtab-btn' + (state.detailSubtab === st.id ? ' active' : '') + '" data-action="set-detail-subtab" data-subtab="' + st.id + '">' + esc(st.label) + '</button>';
    });
    subHtml += '</div>';

    const linkedAudits = state.auditLinksBySession[String(d.root_conversation_id || "")] || [];
    const auditChips = linkedAudits.map((a) =>
      '<button type="button" class="pill-btn" data-action="open-audit" data-audit-id="' + esc(a.audit_id) + '">Audit : ' + esc(a.title) + (a.verdict ? ' (' + esc(a.verdict) + ')' : '') + '</button>'
    ).join(" ");

    let editFormHtml = "";
    if (state.editMetaTarget === d.label) {
      const ef = state.editForm || {};
      editFormHtml = '<div class="inline-form">' +
        '<div class="form-grid">' +
        '<label class="field">Label<input id="gm-edit-label" type="text" value="' + esc(ef.label || "") + '" /></label>' +
        '<label class="field">Projet<input id="gm-edit-project" type="text" value="' + esc(ef.project || "") + '" /></label>' +
        '<label class="field">Note<input id="gm-edit-note" type="text" value="' + esc(ef.note || "") + '" /></label>' +
        '</div>' +
        '<div class="controls">' +
        '<button type="button" class="btn sm primary" data-action="save-edit-meta" data-target="' + esc(d.label) + '">Enregistrer</button>' +
        '<button type="button" class="btn sm" data-action="cancel-edit-meta">Annuler</button>' +
        '</div></div>';
    }

    const freshBadge = hasNew
      ? '<span class="pill warn" title="Nouveaux appels ou activité détectés depuis l’enregistrement">' + (newDelta > 0 ? ('+' + fmtInt(newDelta) + ' appel' + (newDelta > 1 ? 's' : '')) : 'MAJ') + '</span>'
      : '';

    const headPanel = '<div class="panel detail-head">' +
      '<div class="detail-row">' +
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">' +
      '<strong style="font-size:15px;">' + esc(d.label) + '</strong>' +
      '<span class="pill ok">enregistrée</span>' +
      freshBadge +
      (bridgeCount > 0 ? '<span class="pill bridge">Bridge:' + bridgeCount + '</span>' : '') +
      '</div>' +
      '<div class="controls">' + actionBtns + '</div>' +
      '</div>' +
      '<div class="detail-meta">' +
      '<span class="mono">' + esc(String(d.root_conversation_id || "").slice(0, 12)) + '</span>' +
      (d.project ? '<span>· ' + esc(d.project) + '</span>' : '') +
      (d.gravity_profile ? '<span>· ' + esc(d.gravity_profile) + '</span>' : '') +
      (win.mode ? '<span>· ' + esc(win.mode) + '</span>' : '') +
      '<span>· ' + esc(fmtDur(s.wall_clock_s)) + '</span>' +
      (histRec.note ? '<span>· ' + esc(histRec.note) + '</span>' : '') +
      '</div>' +
      (auditChips ? '<div class="detail-meta"><span>Audits liés :</span>' + auditChips + '</div>' : '') +
      editFormHtml +
      subHtml +
      '</div>';

    if (state.detailSubtab === "agents") {
      const catMap = Object.fromEntries(CAT_DEFS.map((x) => [x.key, x]));
      const ms = d.milestones || histRec.milestones || null;
      const msPanel = ms
        ? '<div class="panel"><div class="panel-head"><span class="panel-title">Jalons de cycle (' + esc(ms.mode === "4_phases" ? "4 phases" : "2 phases") + ')</span>' +
          '<span class="subline">' + esc(ms.current_phase || "") + '</span></div>' +
          renderMilestonesStrip(ms, use2027, true) + '</div>'
        : "";

      const bridgeAgents = (d.agents || []).filter((ag) => ag.source === "bridge");
      let bridgePanel = "";
      if (bridgeAgents.length) {
        let bRows = "";
        bridgeAgents.forEach((ag) => {
          const costObj = use2027 ? (ag.cost_usd_projection || ag.cost_usd) : ag.cost_usd;
          const ws = ag.bridge_workspace || ag.workspace || "unknown";
          bRows += '<tr>' +
            '<td class="mono">' + esc(ws) + '</td>' +
            '<td class="mono">' + esc(ag.bridge_thread_id || "—") + ' (' + esc(String(ag.conversation_id || "").slice(0, 8)) + ')</td>' +
            '<td>' + esc(ag.type_name || "bridge") + '</td>' +
            '<td class="num">' + (ag.depth || 1) + '</td>' +
            '<td class="num">' + fmtInt(ag.llm_calls) + '</td>' +
            '<td class="num"><strong>$' + Number((costObj || {}).total_usd || 0).toFixed(4) + '</strong></td>' +
            '<td>' + esc(ag.bridge_job_status || ag.subagent_state || "—") + '</td>' +
            '</tr>';
        });
        bridgePanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Arbre Bridge Inter-Workspaces (' + bridgeAgents.length + ')</span></div>' +
          '<div class="table-wrap"><table><thead><tr><th>Workspace</th><th>Thread</th><th>Type</th><th class="num">Prof.</th><th class="num">Appels</th><th class="num">Coût</th><th>Job</th></tr></thead><tbody>' + bRows + '</tbody></table></div></div>';
      }

      let modelRows = "";
      Object.entries(d.by_model || {}).forEach(([mName, mData]) => {
        const cVal = use2027
          ? Number(((mData.cost_usd_projection || mData.cost_usd) || {}).total_usd || 0)
          : Number((mData.cost_usd || {}).total_usd || 0);
        modelRows += '<tr>' +
          '<td class="mono">' + esc(mName) + (mData.is_fallback ? ' (fallback)' : '') + '</td>' +
          '<td class="num">' + (mData.agents_using || 0) + '</td>' +
          '<td class="num">' + fmtInt(mData.calls) + '</td>' +
          '<td class="num">' + fmtShortTok(mData.input_tokens) + ' / ' + fmtShortTok(mData.cache_read_tokens) + ' / ' + fmtShortTok(mData.output_tokens) + '</td>' +
          '<td class="num"><strong>$' + cVal.toFixed(4) + '</strong></td>' +
          '<td class="num">' + Number(mData.cost_share_pct || 0).toFixed(1) + '%</td>' +
          '</tr>';
      });
      const modelsPanel = '<div class="panel"><div class="panel-head"><span class="panel-title">Modèles</span></div>' +
        '<div class="table-wrap"><table><thead><tr><th>Modèle</th><th class="num">Ag.</th><th class="num">Appels</th><th class="num">In / Cache / Out</th><th class="num">Coût</th><th class="num">Part</th></tr></thead>' +
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
          ? flags.map((f) => '<span class="pill flag">' + esc(f) + '</span>').join(" ")
          : '<span class="pill ok">OK</span>';
        const srcBadge = ag.source === "bridge"
          ? ' <span class="pill bridge">' + esc(ag.bridge_workspace || ag.workspace || "bridge") + '</span>'
          : '';
        agRows += '<tr>' +
          '<td class="mono">' + esc(String(ag.conversation_id || "").slice(0, 8)) + '</td>' +
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

      return headPanel + msPanel + bridgePanel + modelsPanel + agentsPanel;
    }

    if (state.detailSubtab === "diag") {
      const toolsList = Object.entries(tu.by_tool || {}).slice(0, 12)
        .map(([k, v]) => '<li><span class="mono">' + esc(k) + '</span> : <strong>' + fmtInt(v) + '</strong></li>').join("");
      const errList = (tu.top_errors || []).slice(0, 8)
        .map((e) => '<li>(' + esc(e.count) + '×) <span class="mono">' + esc(e.reason) + '</span></li>').join("");

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
        '<li>Fallback : <span class="mono">' + esc((dq.unmatched_models || []).join(", ") || "aucun") + '</span></li>' +
        '<li>JSON malformés : <span class="mono">' + (dq.malformed_lines || 0) + '</span></li>' +
        '</ul></div></div></div>';

      return headPanel + diagPanel;
    }

    // Sous-onglet Synthèse
    const kpis = '<div class="kpis">' +
      '<div class="card"><div class="k">Coût Total (' + (use2027 ? "2027" : "2026") + ')</div><div class="v">$' + shownTotal.toFixed(4) + '</div>' +
      '<div class="sub">' + (hasTariffDiff() ? ((use2027 ? "2026" : "2027") + " : $" + otherTotal.toFixed(4)) : esc(fmtDur(s.wall_clock_s))) + '</div></div>' +
      '<div class="card"><div class="k">Économie Cache</div><div class="v">$' + Number(actCost.cache_savings_usd || 0).toFixed(4) + '</div><div class="sub">Cache Hit : ' + Number(s.cache_hit_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Agents / Appels LLM</div><div class="v">' + (s.total_agents || 0) + ' / ' + fmtInt(s.total_llm_calls) + '</div><div class="sub">Parall. : ' + Number(s.parallelism_factor || 1).toFixed(2) + 'x · Err : ' + Number(s.error_rate_pct || 0).toFixed(2) + '%</div></div>' +
      '<div class="card"><div class="k">Gaspillages &amp; Ctx</div><div class="v">' + (dup.duplicate_agent_count || 0) + ' dup · ' + (rw.rewind_calls || 0) + ' rw</div><div class="sub">Int : ' + (intr.count || 0) + ' · Max : ' + fmtInt(cb.max_context_tokens_overall) + '</div></div>' +
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
      { label: "Cache Read (contexte)", val: Number(actCost.cache_read_usd || 0), color: "var(--gm-c6)" },
      { label: "Input (tokens frais)", val: Number(actCost.input_usd || 0), color: "var(--gm-c2)" },
      { label: "Output (réponse + thinking)", val: Number(actCost.output_usd || 0), color: "var(--gm-c7)" }
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
      '<div class="panel"><div class="panel-head"><span class="panel-title">Coût par Catégorie</span></div>' + renderHtmlBarList(catItems) + '</div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Ventilation (Cache / Input / Output)</span></div>' + renderHtmlBarList(splitItems) + '</div>' +
      '</div>' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Coût par Phase d’Exécution</span></div>' + renderHtmlBarList(phaseItems) + '</div>';

    return headPanel + kpis + wsPanel + charts;
  }

  function renderArchivedSubtab() {
    const scoped = scopedRecords();
    const list = filteredSortedRecords();
    const curIdx = list.findIndex((r) => recKey(r) === state.detailTarget || r.conversation_id === state.detailTarget);
    const hasPrev = curIdx > 0;
    const hasNext = curIdx >= 0 && curIdx < list.length - 1;

    const modelSet = new Set();
    scoped.forEach((r) => {
      if (r.primary_model) modelSet.add(r.primary_model);
      Object.keys(r.models || {}).forEach((m) => modelSet.add(m));
    });
    let modelOpts = '<option value="">Tous modèles</option>';
    Array.from(modelSet).sort().forEach((m) => {
      modelOpts += '<option value="' + esc(m) + '"' + (m === state.modelFilter ? " selected" : "") + '>' + esc(m) + '</option>';
    });

    const sortOpts = [
      { v: "date", l: "Date" },
      { v: "cost", l: "Coût" },
      { v: "cache", l: "Cache %" },
      { v: "calls", l: "Appels" },
      { v: "label", l: "Label" }
    ].map((o) => '<option value="' + o.v + '"' + (state.sortBy === o.v ? " selected" : "") + '>Tri : ' + o.l + '</option>').join("");

    const quickSelectOpts = list.map((r) => {
      const k = recKey(r);
      const sel = (k === state.detailTarget || r.conversation_id === state.detailTarget) ? " selected" : "";
      return '<option value="' + esc(k) + '"' + sel + '>' + esc((r.label || k) + " · $" + recCost(r).toFixed(3)) + '</option>';
    }).join("");

    const stickyNav = '<div class="sticky-nav only-narrow">' +
      '<div class="sticky-nav-group">' +
      '<button type="button" class="btn sm" data-action="toggle-reports-list">' + (state.reportsShowListMobile ? 'Masquer' : 'Liste (' + list.length + ')') + '</button>' +
      '<button type="button" class="btn sm" data-action="nav-prev"' + (hasPrev ? '' : ' disabled') + ' title="Précédent (Alt+←)">←</button>' +
      '<select id="gm-quick-report" aria-label="Sélecteur rapide">' + (quickSelectOpts || '<option value="">Aucun rapport</option>') + '</select>' +
      '<button type="button" class="btn sm" data-action="nav-next"' + (hasNext ? '' : ' disabled') + ' title="Suivant (Alt+→)">→</button>' +
      '</div></div>';

    let masterItems = "";
    list.forEach((r) => {
      const k = recKey(r);
      const isAct = k === state.detailTarget || r.conversation_id === state.detailTarget;
      const bCnt = Number(r.bridge_children_count) || 0;
      const audLinks = state.auditLinksBySession[String(r.conversation_id || "")] || [];
      const tag = bCnt > 0 ? " · bridge:" + bCnt : (audLinks.length ? " · audit" : "");
      const hasNewRec = Boolean(r.has_new_activity);
      const newDeltaRec = Number(r.new_llm_calls_delta || 0);
      const freshTag = hasNewRec
        ? ' <span class="pill warn">' + (newDeltaRec > 0 ? ('+' + fmtInt(newDeltaRec) + ' app.') : 'MAJ') + '</span>'
        : '';
      masterItems += '<button type="button" class="master-item' + (isAct ? ' active' : '') + '" data-action="open-detail" data-target="' + esc(k) + '">' +
        '<div class="master-item-top"><strong>' + esc(r.label || k) + '</strong><span>$' + recCost(r).toFixed(4) + '</span></div>' +
        '<div class="master-item-sub">' +
        '<span>' + esc(fmtDate(r)) + (r.project ? ' · ' + esc(r.project) : '') + '</span>' +
        '<span>' + (Number(r.cache_hit_rate_pct) || 0).toFixed(0) + '% cache' + esc(tag) + freshTag + '</span>' +
        '</div></button>';
    });

    const masterPane = '<aside class="master-pane' + (state.reportsShowListMobile ? '' : ' hide-mobile') + '">' +
      '<input id="gm-search" type="search" placeholder="Filtrer sessions..." value="' + esc(state.search) + '" />' +
      '<div class="filters" style="margin-bottom:0;">' +
      '<select id="gm-model-filter" style="flex:1 1 110px;">' + modelOpts + '</select>' +
      '<select id="gm-sort-by" style="flex:1 1 95px;">' + sortOpts + '</select>' +
      '</div>' +
      '<label style="font-size:11.5px;display:inline-flex;align-items:center;gap:5px;cursor:pointer;color:var(--gm-fg-muted);">' +
      '<input id="gm-alerts-only" type="checkbox"' + (state.alertsOnly ? " checked" : "") + ' /> Alertes uniquement' +
      '</label>' +
      '<div class="master-list">' + (masterItems || '<div class="empty">Aucune session trouvée.</div>') + '</div>' +
      '</aside>';

    const detailPane = '<div class="detail-pane">' + stickyNav + renderReportDetailContent() + '</div>';
    return '<div class="master-detail">' + masterPane + detailPane + '</div>';
  }

  function renderSessionsTab() {
    const archCount = scopedRecords().length;
    const pendCount = state.pendingSessions.length;
    const subNav = '<div class="subtabs" style="border-top:none;padding-top:0;">' +
      '<button type="button" class="subtab-btn' + (state.sessionsSub === "archived" ? " active" : "") + '" data-action="set-sessions-sub" data-sub="archived">Enregistrées <span class="tab-badge">' + archCount + '</span></button>' +
      '<button type="button" class="subtab-btn' + (state.sessionsSub === "pending" ? " active" : "") + '" data-action="set-sessions-sub" data-sub="pending">À enregistrer <span class="tab-badge' + (pendCount > 0 ? " warn" : "") + '" data-badge="pending">' + pendCount + '</span></button>' +
      '<button type="button" class="subtab-btn' + (state.sessionsSub === "compare" ? " active" : "") + '" data-action="set-sessions-sub" data-sub="compare">Comparer</button>' +
      '</div>';

    if (state.sessionsSub === "pending") {
      return subNav + renderPendingSubtab();
    }
    if (state.sessionsSub === "compare") {
      return subNav + renderCompareSubtab();
    }
    return subNav + renderArchivedSubtab();
  }

  function renderAuditsTab() {
    const q = state.auditSearch.trim().toLowerCase();
    const wsFilter = state.auditWorkspaceFilter;
    const vFilter = state.auditVerdictFilter;
    const use2027 = effectiveTariff() === "2027";

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

    let wsOpts = '<option value="">Tous dossiers</option>';
    workspaces.forEach((w) => {
      wsOpts += '<option value="' + esc(w) + '"' + (w === wsFilter ? " selected" : "") + '>' + esc(w) + '</option>';
    });
    let vOpts = '<option value="">Tous verdicts</option>';
    verdicts.forEach((v) => {
      vOpts += '<option value="' + esc(v) + '"' + (v === vFilter ? " selected" : "") + '>' + esc(v) + '</option>';
    });

    let itemsHtml = "";
    filtered.forEach((a) => {
      const isAct = a.audit_id === state.selectedAuditId;
      const tone = a.verdict_tone || "neutral";
      const linkedCnt = (a.linked_sessions || []).length;
      itemsHtml += '<button type="button" class="master-item' + (isAct ? ' active' : '') + '" data-action="open-audit" data-audit-id="' + esc(a.audit_id) + '">' +
        '<div class="master-item-top">' +
        '<strong>' + esc(a.title || a.filename) + '</strong>' +
        (a.verdict ? '<span class="pill ' + esc(tone) + '">' + esc(a.verdict) + '</span>' : '') +
        '</div>' +
        '<div class="master-item-sub">' +
        '<span>' + esc(a.workspace) + ' / ' + esc(a.rel_path) + '</span>' +
        '<span>' + esc(a.date || "") + (linkedCnt ? ' · ' + linkedCnt + ' sess.' : '') + '</span>' +
        '</div></button>';
    });

    const masterPane = '<aside class="master-pane' + (state.auditsShowListMobile ? '' : ' hide-mobile') + '">' +
      '<input id="gm-audit-search" type="search" placeholder="Rechercher audit agent, verdict..." value="' + esc(state.auditSearch) + '" />' +
      '<div class="filters" style="margin-bottom:0;">' +
      '<select id="gm-audit-ws" style="flex:1 1 110px;">' + wsOpts + '</select>' +
      '<select id="gm-audit-verdict" style="flex:1 1 110px;">' + vOpts + '</select>' +
      '</div>' +
      '<div class="master-list">' +
      (state.auditsLoading && !state.audits.length ? '<div class="empty">Découverte des audits...</div>' : (itemsHtml || '<div class="empty">Aucun audit agent Markdown trouvé.</div>')) +
      '</div></aside>';

    const quickOpts = filtered.map((a) =>
      '<option value="' + esc(a.audit_id) + '"' + (a.audit_id === state.selectedAuditId ? " selected" : "") + '>' +
      esc((a.title || a.filename) + " (" + a.workspace + ")") + '</option>'
    ).join("");

    const indexBtn = state.indexAudit
      ? '<button type="button" class="btn sm' + (state.selectedAuditId === state.indexAudit.audit_id ? ' primary' : '') + '" data-action="open-index-audit">Index &amp; Matrice</button>'
      : '';

    const navBar = '<div class="sticky-nav">' +
      '<div class="sticky-nav-group">' +
      '<button type="button" class="btn sm only-narrow" data-action="toggle-audits-list">' + (state.auditsShowListMobile ? 'Masquer' : 'Audits (' + filtered.length + ')') + '</button>' +
      '<select id="gm-quick-audit" class="only-narrow" aria-label="Sélecteur rapide d’audit">' + (quickOpts || '<option value="">Aucun audit</option>') + '</select>' +
      '</div>' +
      '<div class="controls">' + indexBtn + '<button type="button" class="btn sm" data-action="reload-audits">Actualiser</button></div>' +
      '</div>';

    let readerHtml = "";
    if (state.auditLoading && !state.selectedAudit) {
      readerHtml = '<div class="panel"><div class="empty">Lecture de l’audit...</div></div>';
    } else if (!state.selectedAudit) {
      readerHtml = '<div class="panel"><div class="empty">' +
        (filtered.length ? 'Sélectionnez un audit dans la liste.' : 'Aucun dossier <code>audits/agents/</code> détecté.') +
        '</div></div>';
    } else {
      const a = state.selectedAudit;
      const tone = a.verdict_tone || "neutral";
      const openBtn = (canUseAuxPane() && a.uri)
        ? '<button type="button" class="btn sm" data-action="open-file" data-uri="' + esc(a.uri) + '">Ouvrir .md</button>'
        : "";
      const sessChips = (a.linked_sessions || []).map((ls) => {
        const scost = use2027 && ls.cost_2027_usd != null ? Number(ls.cost_2027_usd) : Number(ls.cost_usd || 0);
        return '<button type="button" class="pill-btn" data-action="open-detail" data-target="' + esc(ls.label || ls.conversation_id) + '">' +
          esc(ls.label) + ' ($' + scost.toFixed(3) + ')</button>';
      }).join(" ");

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
        '<span>' + esc(a.workspace) + '</span>' +
        '<span class="mono">· ' + esc(a.rel_path) + '</span>' +
        '<span>· ' + esc(a.date || "—") + '</span>' +
        '<span>· ' + esc(fmtBytes(a.size_bytes)) + '</span>' +
        '</div>' +
        (sessChips ? '<div class="detail-meta"><span>Sessions liées :</span>' + sessChips + '</div>' : '') +
        '</div>' +
        '<div class="panel">' + renderSafeMarkdown(a.content || "") + '</div>';
    }

    return '<div class="master-detail">' + masterPane + '<div class="detail-pane">' + navBar + readerHtml + '</div></div>';
  }

  function render() {
    const root = document.getElementById("root");
    if (!root) return;

    const activeEl = document.activeElement;
    const focusId = activeEl && activeEl.id ? activeEl.id : "";
    const selStart = (focusId && typeof activeEl.selectionStart === "number") ? activeEl.selectionStart : null;
    const selEnd = (focusId && typeof activeEl.selectionEnd === "number") ? activeEl.selectionEnd : null;
    const masterListEl = root.querySelector(".master-list");
    const masterScroll = masterListEl ? masterListEl.scrollTop : 0;
    const winScroll = window.scrollY || 0;

    let htmlStr = renderShellHeader();
    if (state.toast) {
      htmlStr += '<div class="toast' + (state.toast.isError ? ' err' : '') + '">' +
        '<span>' + esc(state.toast.msg) + '</span>' +
        '<button type="button" class="btn sm" data-action="close-toast">✕</button>' +
        '</div>';
    }
    htmlStr += renderConfirmBars();

    if (state.loading && !state.records.length && !state.pendingSessions.length) {
      htmlStr += '<div class="panel"><div class="empty">Chargement des métriques...</div></div>';
    } else if (state.tab === "sessions") {
      htmlStr += renderSessionsTab();
    } else if (state.tab === "audits") {
      htmlStr += renderAuditsTab();
    } else {
      htmlStr += renderOverviewTab();
    }

    root.innerHTML = htmlStr;

    const newMaster = root.querySelector(".master-list");
    if (newMaster && masterScroll > 0) newMaster.scrollTop = masterScroll;
    if (winScroll > 0) window.scrollTo(0, winScroll);

    if (focusId) {
      const inp = document.getElementById(focusId);
      if (inp) {
        inp.focus();
        if (selStart !== null && selEnd !== null && typeof inp.setSelectionRange === "function") {
          try { inp.setSelectionRange(selStart, selEnd); } catch (_) {}
        }
      }
    }
  }

  document.addEventListener("keydown", (ev) => {
    if (state.tab === "sessions" && state.sessionsSub === "archived" && ev.altKey && !ev.ctrlKey && !ev.metaKey) {
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
      if (nextTab === "sessions" && state.sessionsSub === "archived" && !state.detailTarget) {
        const list = filteredSortedRecords();
        if (list.length) {
          openSessionDetail(recKey(list[0]), false);
          return;
        }
      }
      if (nextTab === "sessions" && state.sessionsSub === "compare") {
        ensureCompareLoaded();
      }
      if (nextTab === "audits" && !state.auditsLoaded) {
        loadAudits(false);
        return;
      }
      render();
      return;
    }
    if (action === "set-sessions-sub") {
      state.sessionsSub = target.getAttribute("data-sub") || "archived";
      if (state.sessionsSub === "archived" && !state.detailTarget) {
        const list = filteredSortedRecords();
        if (list.length) {
          openSessionDetail(recKey(list[0]), false);
          return;
        }
      } else if (state.sessionsSub === "compare") {
        ensureCompareLoaded();
      }
      render();
      return;
    }
    if (action === "open-pending") {
      state.tab = "sessions";
      state.sessionsSub = "pending";
      render();
      return;
    }
    if (action === "set-detail-subtab") {
      state.detailSubtab = target.getAttribute("data-subtab") || "summary";
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
    if (action === "nav-prev") { navigateReport(-1); return; }
    if (action === "nav-next") { navigateReport(1); return; }
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
    if (action === "clear-project") {
      state.scope = "global";
      state.selectedProject = "";
      render();
      return;
    }
    if (action === "open-audit") {
      ev.stopPropagation();
      const aid = target.getAttribute("data-audit-id") || "";
      if (!state.auditsLoaded) loadAudits(false);
      if (aid) openAudit(aid, true);
      return;
    }
    if (action === "open-index-audit") {
      if (state.indexAudit && state.indexAudit.audit_id) {
        openAudit(state.indexAudit.audit_id, true);
      }
      return;
    }
    if (action === "open-audit-ref") {
      const ref = target.getAttribute("data-ref") || "";
      resolveAuditRefAndOpen(ref);
      return;
    }
    if (action === "reload-audits") { loadAudits(true); return; }
    if (action === "ask-delete") {
      ev.stopPropagation();
      state.confirmDeleteTarget = target.getAttribute("data-target") || "";
      render();
      return;
    }
    if (action === "cancel-delete") { state.confirmDeleteTarget = ""; render(); return; }
    if (action === "confirm-delete") { confirmDeleteSession(); return; }
    if (action === "toggle-archive-drawer") {
      const cid = target.getAttribute("data-cid") || "";
      if (state.archiveDrawerCid === cid) {
        state.archiveDrawerCid = "";
        render();
      } else {
        openArchiveDrawer(cid);
      }
      return;
    }
    if (action === "cancel-archive-drawer") { state.archiveDrawerCid = ""; render(); return; }
    if (action === "submit-archive-drawer") {
      const cid = target.getAttribute("data-cid") || "";
      if (cid) submitArchiveDrawer(cid);
      return;
    }
    if (action === "ignore-pending") {
      const cid = target.getAttribute("data-cid") || "";
      if (cid) ignorePendingSession(cid);
      return;
    }
    if (action === "unignore-pending") {
      const cid = target.getAttribute("data-cid") || "";
      if (cid) unignorePendingSession(cid);
      return;
    }
    if (action === "toggle-ignored-pending") {
      state.showIgnoredPending = !state.showIgnoredPending;
      render();
      return;
    }
    if (action === "toggle-edit-meta") {
      const tgt = target.getAttribute("data-target") || "";
      if (state.editMetaTarget === tgt) {
        state.editMetaTarget = "";
      } else {
        const rec = state.records.find((r) => r.label === tgt || r.conversation_id === tgt) || {};
        state.editMetaTarget = tgt;
        state.editForm = {
          label: String(rec.label || tgt),
          project: String(rec.project || ""),
          note: String(rec.note || "")
        };
      }
      render();
      return;
    }
    if (action === "update-recorded-session") {
      const tgt = target.getAttribute("data-target") || state.detailTarget || "";
      if (tgt) updateRecordedSession(tgt);
      return;
    }
    if (action === "cancel-edit-meta") { state.editMetaTarget = ""; render(); return; }
    if (action === "save-edit-meta") {
      const tgt = target.getAttribute("data-target") || "";
      if (tgt) saveEditSessionMeta(tgt);
      return;
    }
    if (action === "close-toast") { state.toast = null; render(); return; }
    if (action === "set-tariff") {
      state.tariff = target.getAttribute("data-tariff") || "2026";
      render();
      return;
    }
    if (action === "refresh") { refreshState(false); return; }
    if (action === "open-file") { openMarkdownFile(target.getAttribute("data-uri") || ""); return; }
    if (action === "open-detail") {
      const t = target.getAttribute("data-target") || "";
      if (t) openSessionDetail(t, true);
      return;
    }
    if (action === "attach-context") { attachSessionToPrompt(); return; }
    if (action === "analyze-session") { analyzeSessionWithAgent(); return; }
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
    } else if (el.id === "gm-arch-label") {
      state.archiveForm.label = el.value || "";
    } else if (el.id === "gm-arch-project") {
      state.archiveForm.project = el.value || "";
    } else if (el.id === "gm-arch-note") {
      state.archiveForm.note = el.value || "";
    } else if (el.id === "gm-edit-label") {
      state.editForm.label = el.value || "";
    } else if (el.id === "gm-edit-project") {
      state.editForm.project = el.value || "";
    } else if (el.id === "gm-edit-note") {
      state.editForm.note = el.value || "";
    }
  });

  document.addEventListener("change", (ev) => {
    const el = ev.target;
    if (!el || !el.id) return;
    if (el.id === "gm-proj-select") {
      const val = el.value || "";
      if (!val) {
        state.scope = "global";
        state.selectedProject = "";
      } else {
        state.scope = "project";
        state.selectedProject = val;
      }
      if (state.tab === "sessions" && state.sessionsSub === "archived") {
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
      } else if (state.tab === "sessions" && state.sessionsSub === "compare") {
        ensureCompareLoaded();
      }
      render();
    } else if (el.id === "gm-model-filter") {
      state.modelFilter = el.value || "";
      render();
    } else if (el.id === "gm-sort-by") {
      state.sortBy = el.value || "date";
      state.sortDir = state.sortBy === "label" ? "asc" : "desc";
      render();
    } else if (el.id === "gm-alerts-only") {
      state.alertsOnly = Boolean(el.checked);
      render();
    } else if (el.id === "gm-purge-cb") {
      state.confirmPurge = Boolean(el.checked);
    } else if (el.id === "gm-arch-scope") {
      state.archiveForm.full = el.value === "full";
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
        const route = mapInitialRoute(args.view);
        state.tab = route.tab;
        state.sessionsSub = route.sub;
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
        appInfo: { name: "gravity-metrics", version: "0.5.1" },
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
