const TOKEN_KEY = "agent_access_token";

let llmPanelTaskId = null;
let llmSummaries = [];

function getAuthHeaders() {
  const headers = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function fetchJson(path) {
  const res = window.PlatformAuth?.authFetch
    ? await window.PlatformAuth.authFetch(path, { headers: getAuthHeaders() })
    : await fetch(path, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`${path} ${res.status}`);
  return res.json();
}

function renderMetrics(summary) {
  const counters = summary.counters || {};
  const cards = [
    ["任务创建", counters.tasks_created || 0],
    ["任务完成", counters.tasks_completed || 0],
    ["死信队列", counters.tasks_dead_letter || 0],
    ["人工审核", counters.policy_reviews || 0],
    ["策略拒绝", counters.policy_rejects || 0],
    ["工具失败", counters.tool_failures || 0],
    ["完成率", `${Math.round((summary.task_completion_rate || 0) * 100)}%`],
  ];
  const root = document.getElementById("metrics-cards");
  root.innerHTML = cards
    .map(
      ([label, value]) =>
        `<div class="card"><div>${label}</div><div class="value">${value}</div></div>`
    )
    .join("");
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatRequestBlock(request) {
  const messages = (request && request.messages) || [];
  return messages
    .map((m) => `[${m.role}]\n${m.content || ""}`)
    .join("\n\n---\n\n");
}

function renderLlmTurn(item, displayIndex) {
  const reqText = formatRequestBlock(item.request);
  const respText = item.response || "";
  const idx = displayIndex != null ? displayIndex : item.index;
  return `<article class="llm-turn">
    <div class="llm-turn-head">
      <span><strong>#${idx}</strong> ${escapeHtml(item.purpose || "")}</span>
      <span>${escapeHtml(item.status || "")}</span>
      <span>${escapeHtml(item.source || "")}</span>
      <span>${escapeHtml(item.created_at || "")}</span>
    </div>
    <div class="llm-turn-block">
      <h3>发给 LLM</h3>
      <pre>${escapeHtml(reqText)}</pre>
    </div>
    <div class="llm-turn-block">
      <h3>LLM 返回</h3>
      <pre>${escapeHtml(respText)}</pre>
    </div>
  </article>`;
}

function populateLlmIndexSelect(summaries, count) {
  const sel = document.getElementById("llm-index-select");
  const hint = document.getElementById("llm-filter-hint");
  if (!sel) return;
  if (!summaries.length) {
    sel.innerHTML = '<option value="">无记录</option>';
    sel.disabled = true;
    if (hint) hint.textContent = "";
    return;
  }
  sel.disabled = false;
  sel.innerHTML = summaries
    .map((s) => {
      const kb = Math.round((s.request_chars + s.response_chars) / 1024);
      const trunc = s.truncated ? " · 已截断" : "";
      return `<option value="${s.index}">#${s.index} · ${escapeHtml(s.purpose)} · ${escapeHtml(s.status)} · ${kb}KB${trunc}</option>`;
    })
    .join("");
  if (hint) {
    const bytes = summaries.reduce((n, s) => n + (s.request_chars || 0) + (s.response_chars || 0), 0);
    const mb = (bytes / (1024 * 1024)).toFixed(2);
    hint.textContent =
      `共 ${count} 次（按时间顺序）；本会话日志约 ${mb} MB · 保留上限 120 条或 1GB · 删任务会清除日志`;
  }
}

function renderLlmPanelMeta(taskId, sessionId, count, selectedIndex) {
  const meta = document.getElementById("llm-panel-meta");
  if (!meta) return;
  const sid = sessionId || taskId;
  if (selectedIndex) {
    meta.textContent = `任务 ${taskId} · 会话 ${sid} · 第 ${selectedIndex} / ${count} 次`;
  } else {
    meta.textContent = `任务 ${taskId} · 会话 ${sid} · 共 ${count} 次`;
  }
}

async function loadLlmInteractionDetail(index) {
  const list = document.getElementById("llm-interactions-list");
  if (!list || !llmPanelTaskId || !index) return;
  list.innerHTML = '<p class="dashboard-sub">加载中…</p>';
  const data = await fetchJson(
    `/tasks/${llmPanelTaskId}/llm-interactions?index=${encodeURIComponent(index)}`
  );
  const item = data.interaction;
  if (!item) {
    list.innerHTML = '<p class="dashboard-sub">未找到该次交互。</p>';
    return;
  }
  list.innerHTML = renderLlmTurn(item, item.index);
  renderLlmPanelMeta(data.task_id, data.session_id, data.count, item.index);
}

async function openLlmPanel(taskId) {
  const panel = document.getElementById("llm-panel");
  const filters = document.getElementById("llm-panel-filters");
  const list = document.getElementById("llm-interactions-list");
  if (!panel || !list) return;

  llmPanelTaskId = taskId;
  panel.hidden = false;
  if (filters) filters.hidden = false;
  list.innerHTML = '<p class="dashboard-sub">加载交互列表…</p>';
  panel.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    setDashboardStatus("");
    const summaryData = await fetchJson(
      `/tasks/${taskId}/llm-interactions?summary=true`
    );
    llmSummaries = summaryData.summaries || [];
    populateLlmIndexSelect(llmSummaries, summaryData.count || 0);
    renderLlmPanelMeta(summaryData.task_id, summaryData.session_id, summaryData.count, null);

    const sel = document.getElementById("llm-index-select");
    if (!llmSummaries.length) {
      list.innerHTML =
        '<p class="dashboard-sub">暂无 LLM 交互记录（需 observability.llm_interaction_log_enabled=true）。</p>';
      return;
    }
    const firstIndex = llmSummaries[0].index;
    if (sel) sel.value = String(firstIndex);
    await loadLlmInteractionDetail(firstIndex);
  } catch (err) {
    console.error(err);
    setDashboardStatus(`LLM 交互加载失败：${err.message || err}`);
    list.innerHTML = '<p class="dashboard-sub">加载失败。</p>';
  }
}

function renderTasks(data) {
  const tbody = document.querySelector("#tasks-table tbody");
  tbody.innerHTML = (data.tasks || [])
    .map(
      (t) =>
        `<tr>
          <td title="${escapeHtml(t.task_id)}">${t.task_id.slice(0, 8)}…</td>
          <td>${escapeHtml(t.status)}</td>
          <td>${escapeHtml(t.current_node)}</td>
          <td>${escapeHtml(t.goal || "-")}</td>
          <td>${escapeHtml(t.updated_at)}</td>
          <td><button type="button" class="tasks-table-btn" data-task-id="${escapeHtml(t.task_id)}">LLM</button></td>
        </tr>`
    )
    .join("");
  tbody.querySelectorAll(".tasks-table-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-task-id");
      if (id) openLlmPanel(id);
    });
  });
}

function renderDlq(data) {
  const tbody = document.querySelector("#dlq-table tbody");
  tbody.innerHTML = (data.entries || [])
    .map(
      (e) =>
        `<tr><td>${e.task_id.slice(0, 8)}…</td><td>${e.retry_count}</td><td>${(e.errors || []).join("; ").slice(0, 80)}</td><td>${e.enqueued_at}</td></tr>`
    )
    .join("");
}

function setDashboardStatus(text) {
  const el = document.getElementById("dashboard-status");
  if (!el) return;
  if (text) {
    el.textContent = text;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

async function syncMetricsLink() {
  const link = document.getElementById("dashboard-metrics-link");
  if (!link) return;
  try {
    const res = await fetch("/health");
    if (!res.ok) return;
    const data = await res.json();
    const enabled = data.metrics_enabled !== false;
    link.classList.toggle("is-disabled", !enabled);
    link.setAttribute("aria-disabled", enabled ? "false" : "true");
    if (!enabled) {
      link.title = "observability.metrics_enabled=false，/metrics 不可用";
    }
  } catch {
    /* ignore */
  }
}

async function refresh() {
  try {
    const [metrics, tasks, dlq] = await Promise.all([
      fetchJson("/metrics/summary"),
      fetchJson("/tasks?limit=20"),
      fetchJson("/dead-letter?limit=20"),
    ]);
    renderMetrics(metrics);
    renderTasks(tasks);
    renderDlq(dlq);
    setDashboardStatus("");
  } catch (err) {
    console.error(err);
    setDashboardStatus(`数据加载失败：${err.message || err}`);
  }
}

document.getElementById("llm-panel-close")?.addEventListener("click", () => {
  const panel = document.getElementById("llm-panel");
  const filters = document.getElementById("llm-panel-filters");
  if (panel) panel.hidden = true;
  if (filters) filters.hidden = true;
  llmPanelTaskId = null;
  llmSummaries = [];
});

document.getElementById("llm-index-select")?.addEventListener("change", (ev) => {
  const index = parseInt(ev.target.value, 10);
  if (!index || !llmPanelTaskId) return;
  loadLlmInteractionDetail(index).catch((err) => {
    console.error(err);
    setDashboardStatus(`加载第 ${index} 次失败：${err.message || err}`);
  });
});

syncMetricsLink();
refresh();
setInterval(refresh, 10000);
