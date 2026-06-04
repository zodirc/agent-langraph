/**
 * Context Governance observability — collapsible panel beside chat title (ADR §1.1 #6).
 */
(function initContextGovernancePanel(global) {
  const COLLAPSED_KEY = "ctx_gov_panel_collapsed";
  const DEFAULT_TOKEN_BUDGET = 64800;

  /** 各「预算桶」对人可读的含义（ADR ContextBucket） */
  const BUCKET_HELP = {
    system_policy: { title: "系统策略", desc: "任务规则、安全边界、全局指令" },
    current_turn: { title: "当前轮", desc: "本轮用户目标与即时输入" },
    recent_transcript: { title: "近期对话", desc: "最近几轮聊天原文" },
    semantic_summary: { title: "历史摘要", desc: "较早对话压缩成的摘要" },
    working_memory: { title: "工作记忆", desc: "计划、约束、章节状态等结构化笔记" },
    retrieved_memory: { title: "情节记忆", desc: "从长期记忆召回的片段" },
    retrieved_knowledge: { title: "检索知识", desc: "RAG / 知识库检索结果" },
    tool_observations: { title: "工具输出", desc: "grep、读文件、API 等工具返回" },
    file_context: { title: "文件上下文", desc: "工作区文件切片、diff 等" },
    diagnostics: { title: "诊断信息", desc: "报错、测试失败、终端输出等" },
  };

  const PURPOSE_HELP = {
    reasoning: "推理节点",
    planning: "规划节点",
    writing: "写作节点",
    reviewing: "审阅节点",
    reflection: "反思节点",
    routing: "路由节点",
    summarization: "摘要节点",
    code_agent: "代码 Agent",
  };

  const panelEl = document.getElementById("context-governance-panel");
  if (!panelEl) return;

  const toggleBtnEl = document.getElementById("ctx-gov-toggle");
  const dropdownEl = document.getElementById("ctx-gov-dropdown");
  const badgeEl = document.getElementById("ctx-gov-badge");
  const hintEl = document.getElementById("ctx-gov-hint");
  const summaryEl = document.getElementById("ctx-gov-summary");
  const bucketsEl = document.getElementById("ctx-gov-buckets");
  const keptEl = document.getElementById("ctx-gov-kept");
  const compressedEl = document.getElementById("ctx-gov-compressed");
  const droppedEl = document.getElementById("ctx-gov-dropped");
  const compressedCountEl = document.getElementById("ctx-gov-compressed-count");
  const droppedCountEl = document.getElementById("ctx-gov-dropped-count");
  const purposeEl = document.getElementById("ctx-gov-purpose");
  const scopeEl = document.getElementById("ctx-gov-scope");
  const tokenBudgetEl = document.getElementById("ctx-gov-token-budget");
  const compressBtnEl = document.getElementById("ctx-gov-compress-btn");
  const compressStatusEl = document.getElementById("ctx-gov-compress-status");

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

  function appendSystemLine(text) {
    const fn = runtime().appendSystemLine;
    if (typeof fn === "function") fn(text);
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
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  }

  function isCollapsed() {
    return localStorage.getItem(COLLAPSED_KEY) !== "0";
  }

  function countKeptItems(itemsByBucket) {
    if (!itemsByBucket || typeof itemsByBucket !== "object") return 0;
    return Object.values(itemsByBucket).reduce((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0);
  }

  function summarizeRoles(roles) {
    if (!Array.isArray(roles) || !roles.length) return "—";
    const counts = {};
    for (const r of roles) {
      const key = String(r || "unknown");
      counts[key] = (counts[key] || 0) + 1;
    }
    const roleZh = { system: "系统", user: "用户", assistant: "助手", tool: "工具" };
    return Object.entries(counts)
      .map(([r, n]) => `${roleZh[r] || r}×${n}`)
      .join("，");
  }

  function bucketLabel(bucketKey) {
    const info = BUCKET_HELP[bucketKey];
    if (!info) return { title: bucketKey, desc: "" };
    return info;
  }

  function renderBadge(comp) {
    if (!badgeEl) return;
    if (!comp) {
      badgeEl.textContent = "—";
      badgeEl.className = "ctx-gov-badge-inline ctx-gov-badge-idle";
      badgeEl.title = "尚无组包数据";
      return;
    }
    const kept = countKeptItems(comp.items_by_bucket);
    const compressed = (comp.compressed || []).length;
    const dropped = (comp.dropped || []).length;
    if (compressed === 0 && dropped === 0) {
      badgeEl.textContent = `保留${kept}`;
    } else {
      badgeEl.textContent = `保留${kept}·压${compressed}·丢${dropped}`;
    }
    badgeEl.className = "ctx-gov-badge-inline";
    if (dropped > 0) badgeEl.classList.add("ctx-gov-badge-warn");
    badgeEl.title =
      `进入模型的上下文片段：保留 ${kept} 条` +
      (compressed ? `，压缩 ${compressed} 条` : "") +
      (dropped ? `，丢弃 ${dropped} 条` : "");
  }

  function renderSummary(comp, taskId, purpose) {
    if (!summaryEl) return;
    if (!comp) {
      summaryEl.hidden = true;
      return;
    }
    summaryEl.hidden = false;
    const trace = comp.trace || {};
    const budget = trace.token_budget_total ?? trace.budget_total ?? DEFAULT_TOKEN_BUDGET;
    const model = trace.model_name || "";
    const msgCount = comp.rendered_message_count ?? (comp.rendered_roles || []).length;
    const roleSummary = summarizeRoles(comp.rendered_roles);
    const purposeZh = PURPOSE_HELP[purpose] || purpose;
    summaryEl.innerHTML =
      `<dl class="ctx-gov-dl">` +
      `<dt>当前会话</dt><dd><code>${escapeHtml(taskId.slice(0, 12))}…</code>（任务 ID）</dd>` +
      `<dt>预览场景</dt><dd><strong>${escapeHtml(purposeZh)}</strong>（${escapeHtml(purpose)}）` +
      ` — 模拟该节点下次调模型时的组包结果，并非正在执行的节点</dd>` +
      `<dt>总 token 预算</dt><dd>${escapeHtml(String(budget))}（约 64.8K 上限，各桶之和受此约束）</dd>` +
      `<dt>绑定模型</dt><dd>${escapeHtml(model || "未记录（预览接口未绑定具体模型名）")}</dd>` +
      `<dt>最终消息条数</dt><dd>${escapeHtml(String(msgCount))} 条</dd>` +
      `<dt>消息角色构成</dt><dd>${escapeHtml(roleSummary)}（按顺序统计，非重复粘贴）</dd>` +
      `</dl>`;
  }

  function renderBuckets(comp) {
    if (!bucketsEl) return;
    bucketsEl.replaceChildren();
    const buckets = comp?.buckets;
    if (!Array.isArray(buckets) || !buckets.length) {
      bucketsEl.appendChild(Object.assign(document.createElement("p"), {
        className: "ctx-gov-empty",
        textContent: "暂无桶分配数据。",
      }));
      return;
    }
    const cap = document.createElement("p");
    cap.className = "ctx-gov-section-cap";
    cap.textContent = "各类上下文占用的 token（已用 / 该桶上限）";
    bucketsEl.appendChild(cap);
    for (const b of buckets) {
      const budget = Number(b.budget_tokens) || 1;
      const final = Number(b.final_tokens) || 0;
      const initial = Number(b.initial_tokens) || 0;
      const pct = Math.min(100, Math.round((final / budget) * 100));
      const info = bucketLabel(b.bucket);
      const row = document.createElement("div");
      row.className = "ctx-gov-bucket-row";
      row.title = info.desc;
      row.innerHTML =
        `<div class="ctx-gov-bucket-head">` +
        `<span class="ctx-gov-bucket-name">${escapeHtml(info.title)}` +
        `<span class="ctx-gov-bucket-key">${escapeHtml(b.bucket)}</span></span>` +
        `<span class="ctx-gov-bucket-tokens">${final} / ${budget} tok` +
        (initial !== final ? ` <span class="ctx-gov-muted">(组包前 ${initial})</span>` : "") +
        `</span></div>` +
        `<p class="ctx-gov-bucket-desc">${escapeHtml(info.desc)}</p>` +
        `<div class="ctx-gov-bar" role="presentation"><span class="ctx-gov-bar-fill" style="width:${pct}%"></span></div>`;
      bucketsEl.appendChild(row);
    }
  }

  function renderKept(comp) {
    if (!keptEl) return;
    keptEl.replaceChildren();
    const byBucket = comp?.items_by_bucket;
    if (!byBucket || typeof byBucket !== "object" || !Object.keys(byBucket).length) {
      keptEl.appendChild(Object.assign(document.createElement("p"), {
        className: "ctx-gov-empty",
        textContent: "无保留项。",
      }));
      return;
    }
    for (const [bucket, items] of Object.entries(byBucket)) {
      const sec = document.createElement("section");
      sec.className = "ctx-gov-bucket-items";
      const h = document.createElement("h4");
      h.textContent = `${bucket} (${items.length})`;
      sec.appendChild(h);
      const ul = document.createElement("ul");
      ul.className = "ctx-gov-ul";
      for (const it of items.slice(0, 12)) {
        const li = document.createElement("li");
        li.innerHTML =
          `<span class="ctx-gov-item-id">${escapeHtml(it.id)}</span> ` +
          `<span class="ctx-gov-item-kind">${escapeHtml(it.kind)}</span> ` +
          `<span class="ctx-gov-muted">p=${escapeHtml(it.priority)} · ${it.tokens ?? 0} tok</span>` +
          (it.preview ? `<pre class="ctx-gov-preview">${escapeHtml(it.preview)}</pre>` : "");
        ul.appendChild(li);
      }
      if (items.length > 12) {
        ul.appendChild(Object.assign(document.createElement("li"), {
          className: "ctx-gov-muted",
          textContent: `… 另有 ${items.length - 12} 项`,
        }));
      }
      sec.appendChild(ul);
      keptEl.appendChild(sec);
    }
  }

  function renderItemList(ulEl, items, countEl) {
    if (!ulEl) return;
    ulEl.replaceChildren();
    const list = Array.isArray(items) ? items : [];
    if (countEl) countEl.textContent = list.length ? `(${list.length})` : "";
    if (!list.length) {
      ulEl.appendChild(Object.assign(document.createElement("li"), {
        className: "ctx-gov-muted",
        textContent: "无",
      }));
      return;
    }
    for (const it of list.slice(0, 20)) {
      const li = document.createElement("li");
      const preview = (it.content || it.preview || "").slice(0, 160);
      li.innerHTML =
        `<span class="ctx-gov-item-id">${escapeHtml(it.id)}</span> ` +
        `<span class="ctx-gov-item-kind">${escapeHtml(it.kind || "")}</span> ` +
        `<span class="ctx-gov-muted">${escapeHtml(it.bucket || "")} · ${it.estimated_tokens ?? it.tokens ?? 0} tok</span>` +
        (preview ? `<pre class="ctx-gov-preview">${escapeHtml(preview)}</pre>` : "");
      ulEl.appendChild(li);
    }
    if (list.length > 20) {
      ulEl.appendChild(Object.assign(document.createElement("li"), {
        className: "ctx-gov-muted",
        textContent: `… 另有 ${list.length - 20} 项`,
      }));
    }
  }

  function applyComposition(comp, meta) {
    const taskId = meta?.taskId || getTaskId();
    const purpose = meta?.purpose || purposeEl?.value || "reasoning";
    if (hintEl) hintEl.hidden = Boolean(comp);
    renderBadge(comp);
    renderSummary(comp, taskId, purpose);
    renderBuckets(comp);
    renderKept(comp);
    renderItemList(compressedEl, comp?.compressed, compressedCountEl);
    renderItemList(droppedEl, comp?.dropped, droppedCountEl);
  }

  function compositionFromStatePayload(statePayload) {
    if (!statePayload || typeof statePayload !== "object") return null;
    const direct = statePayload.context_composition;
    if (direct && typeof direct === "object") return direct;
    const trace = statePayload.trace_context || statePayload.traceContext;
    if (trace && typeof trace === "object") {
      return trace.last_context_composition || null;
    }
    return null;
  }

  async function fetchComposition(taskId, purpose) {
    const params = new URLSearchParams({ purpose });
    const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/context-composition?${params}`, {
      headers: { Accept: "application/json" },
    });
    if (res.status === 404) return { error: "not_found" };
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return { error: `http_${res.status}`, detail: text.slice(0, 200) };
    }
    const body = await res.json();
    return { composition: body.composition, purpose: body.purpose, taskId: body.task_id };
  }

  async function fetchStateComposition(taskId) {
    const res = await apiFetch(
      `/tasks/${encodeURIComponent(taskId)}/state?truncate=true`,
      { headers: { Accept: "application/json" } }
    );
    if (!res.ok) return null;
    const body = await res.json();
    const view = body.sources?.merged || body;
    const state = view.state || view;
    return compositionFromStatePayload(state);
  }

  async function refreshPanel(options = {}) {
    if (refreshInFlight && !options.force) return;
    const taskId = (options.taskId || getTaskId() || "").trim();
    if (!taskId) {
      applyComposition(null, {});
      return;
    }
    const purpose = purposeEl?.value || "reasoning";
    refreshInFlight = true;
    try {
      const result = await fetchComposition(taskId, purpose);
      if (result.error === "not_found") {
        applyComposition(null, { taskId, purpose });
        if (compressStatusEl) compressStatusEl.textContent = "任务尚无状态";
        return;
      }
      if (result.error) {
        const fallback = await fetchStateComposition(taskId);
        if (fallback) {
          applyComposition(fallback, { taskId, purpose });
          return;
        }
        if (compressStatusEl) compressStatusEl.textContent = result.detail || "加载失败";
        return;
      }
      applyComposition(result.composition, {
        taskId: result.taskId || taskId,
        purpose: result.purpose || purpose,
      });
      if (compressStatusEl) compressStatusEl.textContent = "";
    } finally {
      refreshInFlight = false;
    }
  }

  async function runCompress() {
    const taskId = getTaskId();
    if (!taskId) {
      if (compressStatusEl) compressStatusEl.textContent = "无活动任务";
      return;
    }
    const scope = scopeEl?.value || "transcript";
    const body = { scope };
    const rawBudget = tokenBudgetEl?.value?.trim();
    if (rawBudget) {
      const n = Number(rawBudget);
      if (Number.isFinite(n) && n >= 1024 && n <= DEFAULT_TOKEN_BUDGET) {
        body.token_budget = Math.floor(n);
      }
    }
    if (compressBtnEl) compressBtnEl.disabled = true;
    if (compressStatusEl) compressStatusEl.textContent = "压缩中…";
    try {
      const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/context/compress`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        const detail =
          typeof errBody.detail === "string"
            ? errBody.detail
            : `HTTP ${res.status}`;
        if (compressStatusEl) compressStatusEl.textContent = detail;
        return;
      }
      const data = await res.json();
      if (data.composition) {
        applyComposition(data.composition, { taskId, purpose: purposeEl?.value || "reasoning" });
      } else {
        await refreshPanel({ force: true });
      }
      const msg =
        `上下文已压缩 (scope=${scope})：保留 ${data.kept ?? "?"}, 丢弃 ${data.dropped ?? "?"}, 压缩 ${data.compressed ?? "?"}`;
      if (compressStatusEl) compressStatusEl.textContent = msg;
      appendSystemLine(`[context] ${msg}`);
    } finally {
      if (compressBtnEl) compressBtnEl.disabled = false;
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
  if (purposeEl) {
    purposeEl.addEventListener("change", () => {
      if (isExpanded()) refreshPanel({ force: true });
    });
  }
  if (compressBtnEl) {
    compressBtnEl.addEventListener("click", () => runCompress());
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
  if (tokenBudgetEl && !tokenBudgetEl.value) {
    tokenBudgetEl.placeholder = String(DEFAULT_TOKEN_BUDGET);
  }

  global.AgentContextGovernance = {
    bump,
    refresh: (opts) => refreshPanel(opts || { force: true }),
    applyComposition,
  };
})(window);
