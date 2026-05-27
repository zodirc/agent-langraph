const TOKEN_KEY = "agent_access_token";

function getAuthHeaders() {
  const headers = {};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function fetchJson(path) {
  const res = await fetch(path, { headers: getAuthHeaders() });
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

function renderTasks(data) {
  const tbody = document.querySelector("#tasks-table tbody");
  tbody.innerHTML = (data.tasks || [])
    .map(
      (t) =>
        `<tr><td>${t.task_id.slice(0, 8)}…</td><td>${t.status}</td><td>${t.current_node}</td><td>${t.goal || "-"}</td><td>${t.updated_at}</td></tr>`
    )
    .join("");
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
  } catch (err) {
    console.error(err);
  }
}

refresh();
setInterval(refresh, 10000);
