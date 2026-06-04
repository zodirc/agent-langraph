/**
 * Session usage panel — Copilot-style dual metrics (context length + token usage).
 * Provider usage when available; local tokenizer/heuristic fallback otherwise.
 */
(function initContextGovernancePanel(global) {
  const panelEl = document.getElementById("context-governance-panel");
  if (!panelEl) return;

  const toggleBtnEl = document.getElementById("ctx-gov-toggle");
  const badgeEl = document.getElementById("ctx-gov-badge");
  const summaryEl = document.getElementById("ctx-gov-summary-body");

  let refreshInFlight = false;

  function runtime() {
    return global.AgentChatRuntime || {};
  }

  function getTaskId() {
    const fn = runtime().getTaskId;
    return typeof fn === "function" ? fn() : "";
  }

  function isExpanded() {
    return !panelEl.classList.contains("is-collapsed");
  }

  async function apiFetch(url, options) {
    const fn = runtime().apiFetch;
    if (typeof fn === "function") return fn(url, options);
    return fetch(url, options);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function setCollapsed(collapsed) {
    panelEl.classList.toggle("is-collapsed", collapsed);
    if (toggleBtnEl) {
      toggleBtnEl.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    localStorage.setItem("ctx_gov_panel_collapsed", collapsed ? "1" : "0");
  }

  function isCollapsed() {
    return localStorage.getItem("ctx_gov_panel_collapsed") !== "0";
  }

  /** Compact display like 157.3k / 3.1m */
  function fmtCompact(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}m`;
    if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
    return String(Math.round(v));
  }

  function fmtPair(used, max) {
    if (used === null || used === undefined) return "—";
    if (!max) return fmtCompact(used);
    return `${fmtCompact(used)} / ${fmtCompact(max)}`;
  }

  function billingHint(source) {
    if (source === "provider") return "厂商 API usage";
    if (source === "local") return "本地计数（无厂商 usage 时）";
    if (source === "none") return "";
    return "厂商优先，缺省用本地计数";
  }

  function renderBadge(sessionMeta) {
    if (!badgeEl) return;
    if (!sessionMeta) {
      badgeEl.textContent = "—";
      badgeEl.className = "ctx-gov-badge-inline ctx-gov-badge-idle";
      badgeEl.title = "尚无用量数据";
      return;
    }
    const ctx = sessionMeta.context_length_used_tokens;
    const session = sessionMeta.session_tokens_consumed;
    if (ctx > 0) {
      badgeEl.textContent = fmtCompact(ctx);
      badgeEl.title = `上下文 ${fmtCompact(ctx)} / ${fmtCompact(sessionMeta.context_length_max_tokens)}`;
    } else if (session > 0) {
      badgeEl.textContent = fmtCompact(session);
      badgeEl.title = `会话累计 ${session} tok`;
    } else {
      badgeEl.textContent = "—";
      badgeEl.title = "尚无 LLM 调用";
    }
    badgeEl.className = "ctx-gov-badge-inline";
  }

  function renderMeterBar(pct) {
    if (!Number.isFinite(pct) || pct < 0) return "";
    const warn = pct >= 85 ? " ctx-gov-window-fill-warn" : "";
    return (
      `<div class="ctx-gov-window-meter" role="presentation">` +
      `<div class="ctx-gov-bar"><span class="ctx-gov-bar-fill ctx-gov-window-fill${warn}" style="width:${pct}%"></span></div>` +
      `</div>`
    );
  }

  function renderSummary(sessionMeta, taskId, options = {}) {
    if (!summaryEl) return;
    if (!sessionMeta && !options.loading) {
      summaryEl.innerHTML =
        `<p class="ctx-gov-empty">尚无 LLM 调用。发送消息后将显示上下文长度与会话 Token 用量。</p>`;
      return;
    }
    if (options.loading) {
      summaryEl.innerHTML = `<p class="ctx-gov-empty">正在加载…</p>`;
      return;
    }

    const model = sessionMeta.model_name || "—";
    const ctxUsed = sessionMeta.context_length_used_tokens;
    const ctxMax = sessionMeta.context_length_max_tokens || sessionMeta.model_context_window_tokens;
    const sessionTotal = sessionMeta.session_tokens_consumed;
    const lastReq = sessionMeta.last_request_tokens;
    const source = sessionMeta.billing_source || "none";
    const purpose = sessionMeta.last_call_purpose || "—";
    const ctxPct = sessionMeta.context_length_used_percent;
    const ctxBar = renderMeterBar(ctxPct);
    const hint = billingHint(source);

    const contextLine = fmtPair(ctxUsed, ctxMax);
    const tokenLine = fmtPair(sessionTotal, lastReq);

    let sourceNote = "";
    if (source === "provider") {
      sourceNote = "累计与上次请求优先使用厂商 usage。";
    } else if (source === "local") {
      sourceNote = "网关未返回 usage，数字为本地分词计数（与厂商账单可能略有偏差）。";
    } else {
      sourceNote = hint;
    }

    summaryEl.innerHTML =
      `<dl class="ctx-gov-dl ctx-gov-dl-compact" role="list">` +
      `<dt>当前会话</dt><dd><code>${escapeHtml(taskId.slice(0, 12))}…</code></dd>` +
      `<dt>模型</dt><dd><strong>${escapeHtml(model)}</strong></dd>` +
      `<dt>上下文长度</dt><dd>${ctxBar}<strong>${escapeHtml(contextLine)}</strong>` +
      (ctxPct != null ? `<span class="ctx-gov-muted">（${ctxPct}%）</span>` : "") +
      `</dd>` +
      `<dt>Token 用量</dt><dd><strong>${escapeHtml(tokenLine)}</strong>` +
      `<br><span class="ctx-gov-muted">会话累计 / 上次请求 · ${escapeHtml(purpose)}</span></dd>` +
      `</dl>` +
      `<p class="ctx-gov-muted">${escapeHtml(sourceNote)}</p>`;
  }

  async function fetchSessionUsage(taskId) {
    const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/session-usage`, {
      headers: { Accept: "application/json" },
    });
    if (res.status === 404) return { error: "not_found" };
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return { error: `http_${res.status}`, detail: text.slice(0, 200) };
    }
    const body = await res.json();
    return { session: body.session, taskId: body.task_id };
  }

  function applySession(meta) {
    const taskId = meta?.taskId || getTaskId();
    const sessionMeta = meta?.session || null;
    renderBadge(sessionMeta);
    renderSummary(sessionMeta, taskId);
  }

  async function refreshPanel(options = {}) {
    if (refreshInFlight && !options.force) return;
    const taskId = (options.taskId || getTaskId() || "").trim();
    if (!taskId) {
      applySession({});
      return;
    }
    refreshInFlight = true;
    renderSummary(null, taskId, { loading: true });
    try {
      const result = await fetchSessionUsage(taskId);
      if (result.error === "not_found") {
        applySession({ taskId });
        return;
      }
      if (result.error) {
        renderSummary(null, taskId);
        return;
      }
      applySession({ taskId: result.taskId || taskId, session: result.session });
    } finally {
      refreshInFlight = false;
    }
  }

  function togglePanel() {
    const willCollapse = !panelEl.classList.contains("is-collapsed");
    setCollapsed(willCollapse);
    if (!willCollapse) refreshPanel({ force: true });
  }

  function bump() {
    if (isExpanded()) refreshPanel({ silent: true });
  }

  if (toggleBtnEl) {
    toggleBtnEl.addEventListener("click", (ev) => {
      ev.stopPropagation();
      togglePanel();
    });
  }

  document.addEventListener("click", (ev) => {
    if (!isExpanded()) return;
    if (panelEl.contains(ev.target)) return;
    setCollapsed(true);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && isExpanded()) setCollapsed(true);
  });

  setCollapsed(isCollapsed());

  global.AgentContextGovernance = {
    bump,
    refresh: (opts) => refreshPanel(opts || { force: true }),
    applyComposition: (_comp, meta) => applySession(meta),
  };
})(window);
