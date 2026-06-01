const outputEl = document.getElementById("output");
const formEl = document.getElementById("command-form");
const inputEl = document.getElementById("command-input");
const stopBtnEl = document.getElementById("stop-btn");
const envBadge = document.getElementById("env-badge");
const sessionBadgeEl = document.getElementById("session-badge");
const langgraphicsMetaEl = document.getElementById("langgraphics-meta");
const langgraphicsLinkEl = document.getElementById("langgraphics-link");
const langgraphicsFrameEl = document.getElementById("langgraphics-frame");
const flowMetaEl = document.getElementById("flow-meta");
const flowTaskEl = document.getElementById("flow-task");
const flowGraphEl = document.getElementById("flow-graph");
const flowTimelineEl = document.getElementById("flow-timeline");
const flowRefreshBtnEl = document.getElementById("flow-refresh-btn");
const flowOpenBtnEl = document.getElementById("flow-open-btn");
const stateDebugBtnEl = document.getElementById("state-debug-btn");
const stateDebugModalEl = document.getElementById("state-debug-modal");
const stateDebugMetaEl = document.getElementById("state-debug-meta");
const stateDebugKeysEl = document.getElementById("state-debug-keys");
const stateDebugJsonEl = document.getElementById("state-debug-json");
const stateDebugRefreshBtnEl = document.getElementById("state-debug-refresh-btn");
const stateDebugCloseBtnEl = document.getElementById("state-debug-close-btn");
const stateDebugLiveEl = document.getElementById("state-debug-live");
const stateDebugSourceTabsEl = document.getElementById("state-debug-source-tabs");
const themeSelectEl = document.getElementById("theme-select");

let running = false;
let activeTaskId = null;
let runTimer = null;
let runStartedAt = 0;
let lastPhaseMessage = "";
let progressLineEl = null;
let tracePanelEl = null;
let traceLineCount = 0;
let answerStreamEl = null;
let answerStreamText = "";
let thinkingHeaderEl = null;
let thinkingPanelEl = null;
let thinkingStreamEl = null;
let thinkingStreamText = "";
let writingHeaderEl = null;
let writingPanelEl = null;
let writingStreamEl = null;
let writingStreamText = "";
let writingStreamFilename = "";
/** @type {{ headerEl: HTMLElement, panelEl: HTMLElement, bodyEl: HTMLElement, filename: string } | null} */
let activeWritingBlock = null;
let contentBlockSeq = 0;
/** Dedupe steer confirm panels within one stream or /confirm turn */
const shownConfirmationKeys = new Set();
/** Chars streamed via writing_delta this turn — skip redundant outcome artifact panel */
let writingStreamCharsThisTurn = 0;
const WRITING_STREAM_MAX_CHARS = 200000;
const FILE_PREVIEW_BOX_MIN_CHARS = 64;
const TRACE_MAX_LINES = 400;
/** Only auto-scroll when user is already near the bottom (allows reading history). */
const SCROLL_PIN_THRESHOLD = 64;
/** Sticky auto-follow for output area: true when user is at bottom, false after manual scroll up. */
let outputAutoFollow = true;

let writingPendingText = "";
let writingFlushScheduled = false;
let writingTraceHintShown = false;
let thinkingPendingText = "";
let thinkingFlushScheduled = false;
let flowAutoRefreshTimer = null;
let stateDebugAutoRefreshTimer = null;
/** @type {null | Record<string, unknown>} */
let stateDebugCache = null;
let stateDebugSelectedKey = "__all__";
let stateDebugSelectedSource = "merged";
const flowLiveHistoryByTask = new Map();
let flowSelectedNode = "";
let flowPopupWin = null;
const FLOW_PREFERRED_ORDER = [
  "planning",
  "retrieval",
  "tool_execution",
  "reasoning",
  "policy",
  "output_guard",
  "output",
  "memory_writeback",
];

function isNearScrollBottom(el, threshold = SCROLL_PIN_THRESHOLD) {
  if (!el) return true;
  const maxScroll = el.scrollHeight - el.clientHeight;
  if (maxScroll <= 0) return true;
  return el.scrollTop >= maxScroll - threshold;
}

function scrollToBottomIfPinned(el) {
  if (!el || !isNearScrollBottom(el)) return;
  el.scrollTop = el.scrollHeight;
}

function updateOutputAutoFollow() {
  outputAutoFollow = isNearScrollBottom(outputEl);
}

function scrollOutputIfPinned() {
  if (!outputEl || !outputAutoFollow) return;
  outputEl.scrollTop = outputEl.scrollHeight;
  outputAutoFollow = true;
}

if (outputEl) {
  outputEl.addEventListener("scroll", updateOutputAutoFollow, { passive: true });
}

function isTerminalTaskStatus(status) {
  const st = String(status || "");
  return st === "COMPLETED" || st === "FAILED" || st === "DEAD_LETTER" || st === "WRITING_FAILED" || st === "REJECTED";
}

function formatFlowAt(raw) {
  if (!raw) return "-";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return String(raw);
  return d.toLocaleTimeString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderFlowTimeline(data, taskId) {
  if (!flowMetaEl || !flowTaskEl || !flowTimelineEl) return;
  const status = data?.status || "-";
  const node = data?.current_node || "-";
  flowMetaEl.textContent = `状态: ${status} | 当前节点: ${node}`;
  flowTaskEl.textContent = `task: ${(taskId || "-").toString().slice(0, 12)}${taskId ? "…" : ""}`;
  const persistedHistory = Array.isArray(data?.node_history) ? data.node_history : [];
  const liveHistory = flowLiveHistoryByTask.get(taskId) || [];
  const history = liveHistory.length ? liveHistory : persistedHistory;
  renderFlowGraph(history);
  if (!history.length) {
    flowTimelineEl.innerHTML = '<p class="flow-empty">暂无节点数据。运行任务后将自动显示。</p>';
    return;
  }
  const items = history
    .slice(-80)
    .map((h, idx) => {
      const hNode = String(h?.node || "?");
      const hStatus = String(h?.status || "-");
      const selected = flowSelectedNode && hNode === flowSelectedNode ? " selected" : "";
      const cls = /FAILED|ERROR|DEAD_LETTER|WRITING_FAILED/.test(hStatus)
        ? `flow-item error${selected}`
        : /(DONE|COMPLETED|WRITTEN|TOOL_EXECUTED|PLANNED|APPROVED|RESUMED)/.test(hStatus)
          ? `flow-item done${selected}`
          : `flow-item${selected}`;
      return `<div class="${cls}" data-flow-node="${escapeHtml(hNode)}" data-flow-idx="${idx}">
        <div class="flow-item-row">
          <span class="flow-node">${escapeHtml(hNode)}</span>
          <span class="flow-status">${escapeHtml(hStatus)}</span>
        </div>
        <div class="flow-time">${escapeHtml(formatFlowAt(h?.at))}</div>
      </div>`;
    })
    .join("");
  flowTimelineEl.innerHTML = items;
  flowTimelineEl.scrollTop = flowTimelineEl.scrollHeight;
  for (const el of flowTimelineEl.querySelectorAll("[data-flow-node]")) {
    el.addEventListener("click", () => {
      flowSelectedNode = String(el.getAttribute("data-flow-node") || "");
      renderFlowGraph(history);
      for (const item of flowTimelineEl.querySelectorAll(".flow-item")) item.classList.remove("selected");
      el.classList.add("selected");
      if (flowMetaEl && flowSelectedNode) {
        flowMetaEl.textContent = `状态: ${status} | 当前节点: ${node} | 定位: ${flowSelectedNode}`;
      }
      syncFlowPopup();
    });
  }
  if (taskId && isTerminalTaskStatus(status) && activeTaskId === taskId) {
    activeTaskId = null;
    stopFlowAutoRefresh();
  }
}

function buildTransitionSet(history) {
  const set = new Set();
  for (let i = 1; i < history.length; i += 1) {
    const from = String(history[i - 1]?.node || "");
    const to = String(history[i]?.node || "");
    if (from && to && from !== to) set.add(`${from}->${to}`);
  }
  return set;
}

function getFlowNodes(history) {
  const nodeSet = new Set();
  for (const h of history) {
    const node = String(h?.node || "").trim();
    if (node) nodeSet.add(node);
  }
  const known = FLOW_PREFERRED_ORDER.filter((n) => nodeSet.has(n));
  const unknown = [...nodeSet]
    .filter((n) => !FLOW_PREFERRED_ORDER.includes(n))
    .sort((a, b) => a.localeCompare(b));
  const ordered = [...known, ...unknown];
  return ordered.length ? ordered : FLOW_PREFERRED_ORDER;
}

function classifyNodeStatus(node, history) {
  const rows = history.filter((h) => String(h?.node || "") === node);
  if (!rows.length) return "idle";
  const latest = String(rows[rows.length - 1]?.status || "");
  if (/FAILED|ERROR|DEAD_LETTER|WRITING_FAILED/.test(latest)) return "error";
  if (/DONE|COMPLETED|WRITTEN|TOOL_EXECUTED|PLANNED|POLICY_CHECKED|RETRIEVED|REASONED/.test(latest)) return "done";
  return "active";
}

function renderFlowGraph(history) {
  if (!flowGraphEl) return;
  if (!history.length) {
    flowGraphEl.innerHTML = '<p class="flow-empty">流程图等待任务数据…</p>';
    return;
  }
  const nodes = getFlowNodes(history);
  const nodeW = 150;
  const nodeH = 38;
  const gapY = 34;
  const marginX = 28;
  const marginY = 16;
  const width = 460;
  const height = marginY + nodes.length * nodeH + Math.max(0, nodes.length - 1) * gapY + 20;
  const layout = new Map();
  const nodeIndex = new Map();
  for (let i = 0; i < nodes.length; i += 1) {
    const x = Math.floor((width - nodeW) / 2);
    const y = marginY + i * (nodeH + gapY);
    layout.set(nodes[i], [x, y]);
    nodeIndex.set(nodes[i], i);
  }
  const transitions = buildTransitionSet(history);
  const lastTransition = history.length >= 2
    ? `${String(history[history.length - 2]?.node || "")}->${String(history[history.length - 1]?.node || "")}`
    : "";
  const edges = [...transitions].map((t) => t.split("->")).filter((pair) => pair.length === 2);

  let edgeSvg = "";
  const markerDefs = `<defs>
    <marker id="flow-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#60a5fa"></path>
    </marker>
    <marker id="flow-arrow-current" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#22d3ee"></path>
    </marker>
  </defs>`;
  for (const [from, to] of edges) {
    const p1 = layout.get(from);
    const p2 = layout.get(to);
    if (!p1 || !p2) continue;
    const fromIdx = nodeIndex.get(from) ?? 0;
    const toIdx = nodeIndex.get(to) ?? 0;
    const x1 = p1[0] + nodeW / 2;
    const y1 = p1[1] + nodeH; // from bottom edge
    const x2 = p2[0] + nodeW / 2;
    const y2 = p2[1]; // to top edge
    const transitionKey = `${from}->${to}`;
    const isCurrent = transitionKey === lastTransition;
    const cls = isCurrent ? "flow-edge-arrow current" : "flow-edge-arrow active";
    const marker = isCurrent ? "flow-arrow-current" : "flow-arrow";
    if (toIdx > fromIdx) {
      // Forward edge: clean top-down line outside node boxes.
      edgeSvg += `<path class="${cls}" marker-end="url(#${marker})" d="M ${x1} ${y1} L ${x2} ${y2 - 4}" />`;
    } else {
      // Back edge: route from side channel (Mermaid-like loopback elbow).
      const sideX = width - marginX;
      const midY1 = y1 + 12;
      const midY2 = y2 - 12;
      edgeSvg += `<path class="${cls}" marker-end="url(#${marker})" d="M ${x1} ${y1} L ${sideX} ${midY1} L ${sideX} ${midY2} L ${x2} ${y2 - 4}" />`;
    }
  }

  let nodeSvg = "";
  for (const node of nodes) {
    const p = layout.get(node);
    if (!p) continue;
    const status = classifyNodeStatus(node, history);
    const selected = flowSelectedNode && flowSelectedNode === node ? " selected" : "";
    const cls =
      status === "error"
        ? `flow-node-box error${selected}`
        : status === "done"
          ? `flow-node-box done${selected}`
          : status === "active"
            ? `flow-node-box active${selected}`
            : `flow-node-box${selected}`;
    const [x, y] = p;
    nodeSvg += `<rect class="${cls}" x="${x}" y="${y}" rx="6" ry="6" width="${nodeW}" height="${nodeH}" />`;
    nodeSvg += `<text class="flow-node-label" x="${x + 10}" y="${y + 21}">${escapeHtml(node)}</text>`;
  }

  flowGraphEl.innerHTML = `<svg class="flow-graph-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${markerDefs}${edgeSvg}${nodeSvg}</svg>`;
  syncFlowPopup();
}

function buildFlowPopupHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"/><title>Flow Graph</title>
  <style>
    body{margin:0;background:#0d1117;color:#c9d1d9;font-family:ui-monospace,Menlo,Consolas,monospace}
    body.theme-light{background:#f6f8fb;color:#1f2937}
    .wrap{padding:14px}
    .meta{font-size:13px;color:#93c5fd;margin:0 0 8px}
    .task{font-size:12px;color:#8b949e;margin:0 0 10px}
    .graph,.timeline{border:1px solid #30363d;border-radius:8px;background:#010409;padding:8px}
    .graph{min-height:280px}
    .timeline{margin-top:10px;max-height:45vh;overflow:auto}
    .flow-graph-svg{width:100%;height:72vh;display:block}
    .flow-edge-arrow{fill:none;stroke:#60a5fa;stroke-width:2.2;opacity:.95}
    .flow-edge-arrow.active{stroke:#60a5fa;stroke-width:2.2;opacity:.95}
    .flow-edge-arrow.current{stroke:#22d3ee;stroke-width:2.8;stroke-dasharray:5 10;animation:flowEdgeWave .7s linear infinite;opacity:1;filter:drop-shadow(0 0 5px rgba(34,211,238,.5))}
    .flow-node-box{fill:rgba(139,148,158,.12);stroke:#4b5563;stroke-width:1.2}
    .flow-node-box.active{fill:rgba(88,166,255,.2);stroke:#60a5fa}
    .flow-node-box.done{fill:rgba(63,185,80,.16);stroke:rgba(63,185,80,.85)}
    .flow-node-box.error{fill:rgba(248,81,73,.18);stroke:rgba(248,81,73,.9)}
    .flow-node-box.selected{stroke:#fde047;stroke-width:2.2}
    .flow-node-label{fill:#d1d5db;font-size:10px}
    .flow-item{padding:6px 8px;border-radius:6px;border:1px solid rgba(139,148,158,.35);margin-bottom:6px;background:rgba(139,148,158,.08)}
    .flow-item.done{border-color:rgba(63,185,80,.55);background:rgba(63,185,80,.1)}
    .flow-item.error{border-color:rgba(248,81,73,.55);background:rgba(248,81,73,.1)}
    .flow-item.selected{border-color:rgba(88,166,255,.75);background:rgba(88,166,255,.2)}
    .flow-item-row{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
    .flow-node{color:#a7f3d0;font-size:12px}
    .flow-status{font-size:11px;color:#bfdbfe;white-space:nowrap}
    .flow-time{margin-top:4px;font-size:11px;color:#8b949e}
    @keyframes flowEdgeWave{to{stroke-dashoffset:-30}}
    body.theme-light .meta{color:#1d4ed8}
    body.theme-light .task{color:#475569}
    body.theme-light .graph,body.theme-light .timeline{border-color:#cbd5e1;background:#ffffff}
    body.theme-light .flow-edge-arrow{stroke:#2563eb}
    body.theme-light .flow-edge-arrow.current{stroke:#0891b2;filter:drop-shadow(0 0 4px rgba(8,145,178,.45))}
    body.theme-light .flow-node-box{fill:#f8fafc;stroke:#94a3b8}
    body.theme-light .flow-node-box.active{fill:#dbeafe;stroke:#3b82f6}
    body.theme-light .flow-node-box.done{fill:#dcfce7;stroke:#16a34a}
    body.theme-light .flow-node-box.error{fill:#fee2e2;stroke:#dc2626}
    body.theme-light .flow-node-label{fill:#0f172a}
    body.theme-light .flow-item{border-color:#cbd5e1;background:#f8fafc}
    body.theme-light .flow-item.done{border-color:#86efac;background:#f0fdf4}
    body.theme-light .flow-item.error{border-color:#fca5a5;background:#fef2f2}
    body.theme-light .flow-item.selected{border-color:#3b82f6;background:#dbeafe}
    body.theme-light .flow-node{color:#065f46}
    body.theme-light .flow-status{color:#1e3a8a}
    body.theme-light .flow-time{color:#64748b}
  </style></head><body>
  <div class="wrap">
    <p id="m" class="meta">等待数据</p><p id="t" class="task">task: -</p>
    <div id="g" class="graph"></div><div id="tl" class="timeline"></div>
  </div></body></html>`;
}

function syncFlowPopup() {
  if (!flowPopupWin || flowPopupWin.closed) return;
  try {
    const d = flowPopupWin.document;
    const m = d.getElementById("m");
    const t = d.getElementById("t");
    const g = d.getElementById("g");
    const tl = d.getElementById("tl");
    if (!m || !t || !g || !tl) return;
    const currentTheme = document.body.classList.contains("theme-light") ? "theme-light" : "theme-dark";
    d.body.classList.remove("theme-light", "theme-dark");
    d.body.classList.add(currentTheme);
    m.textContent = flowMetaEl?.textContent || "等待数据";
    t.textContent = flowTaskEl?.textContent || "task: -";
    g.innerHTML = flowGraphEl?.innerHTML || "";
    tl.innerHTML = flowTimelineEl?.innerHTML || "";
  } catch {
    /* ignore popup sync errors */
  }
}

async function refreshFlowPanel(taskId = null) {
  if (!flowTimelineEl) return;
  const useTaskId = taskId || activeTaskId || getSessionId();
  if (!useTaskId) return;
  try {
    const res = await fetch(`/tasks/${useTaskId}/status`, { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    renderFlowTimeline(data, useTaskId);
  } catch {
    /* ignore flow panel refresh failure */
  }
}

function ensureFlowAutoRefresh() {
  if (flowAutoRefreshTimer) return;
  flowAutoRefreshTimer = setInterval(() => {
    if (!running && !activeTaskId) return;
    refreshFlowPanel();
  }, 1800);
}

function stopFlowAutoRefresh() {
  if (!flowAutoRefreshTimer) return;
  clearInterval(flowAutoRefreshTimer);
  flowAutoRefreshTimer = null;
}

function resolveStateDebugTaskId() {
  return activeTaskId || getSessionId();
}

function getStateDebugActiveView() {
  if (!stateDebugCache) return null;
  const sources = stateDebugCache.sources || {};
  const picked = sources[stateDebugSelectedSource];
  if (picked && picked.state) return picked;
  return stateDebugCache;
}

function renderStateDebugSourceTabs() {
  if (!stateDebugSourceTabsEl || !stateDebugCache) return;
  const liveOn = Boolean(stateDebugCache.live_running);
  const liveAvail = Boolean(stateDebugCache.live_available);
  for (const btn of stateDebugSourceTabsEl.querySelectorAll("[data-source]")) {
    const src = String(btn.getAttribute("data-source") || "merged");
    btn.classList.toggle("selected", src === stateDebugSelectedSource);
    btn.classList.toggle("live-on", src === "live" && liveOn);
    if (src === "live") {
      btn.disabled = !liveAvail;
      btn.title = liveOn ? "任务正在本进程执行中" : liveAvail ? "最近一次 live 缓存" : "当前无 live 数据";
    } else if (src === "store") {
      btn.disabled = !stateDebugCache.store_available;
    } else {
      btn.disabled = false;
    }
  }
}

function renderStateDebugJson() {
  if (!stateDebugJsonEl || !stateDebugCache) return;
  const active = getStateDebugActiveView();
  const stateObj = active?.state || stateDebugCache.state || {};
  const payload =
    stateDebugSelectedKey === "__all__" ? stateObj : stateObj?.[stateDebugSelectedKey];
  try {
    stateDebugJsonEl.textContent = JSON.stringify(payload ?? null, null, 2);
  } catch {
    stateDebugJsonEl.textContent = String(payload);
  }
}

function renderStateDebugKeys() {
  if (!stateDebugKeysEl || !stateDebugCache) return;
  const active = getStateDebugActiveView();
  const summary = active?.field_summary || stateDebugCache.field_summary || {};
  const keys = Object.keys(summary).sort((a, b) => a.localeCompare(b));
  const allSelected = stateDebugSelectedKey === "__all__" ? " selected" : "";
  const buttons = [
    `<button type="button" class="state-debug-key-btn${allSelected}" data-state-key="__all__">
      <span class="key-label">__all__</span>
      <span class="key-meta">完整 state 对象</span>
    </button>`,
    ...keys.map((key) => {
      const selected = key === stateDebugSelectedKey ? " selected" : "";
      const meta = escapeHtml(String(summary[key] || ""));
      return `<button type="button" class="state-debug-key-btn${selected}" data-state-key="${escapeHtml(key)}">
        <span class="key-label">${escapeHtml(key)}</span>
        <span class="key-meta">${meta}</span>
      </button>`;
    }),
  ];
  stateDebugKeysEl.innerHTML = buttons.join("");
  for (const btn of stateDebugKeysEl.querySelectorAll("[data-state-key]")) {
    btn.addEventListener("click", () => {
      stateDebugSelectedKey = String(btn.getAttribute("data-state-key") || "__all__");
      for (const el of stateDebugKeysEl.querySelectorAll(".state-debug-key-btn")) {
        el.classList.toggle("selected", el === btn);
      }
      renderStateDebugJson();
    });
  }
}

function updateStateDebugMeta(view) {
  if (!stateDebugMetaEl) return;
  const active = getStateDebugActiveView() || view;
  const taskId = view?.task_id || resolveStateDebugTaskId();
  const sessionId = view?.session_id || taskId;
  const truncated = active?.truncated ? ` | 已裁剪: ${(active.truncated_fields || []).join(", ")}` : "";
  const sourceLabel =
    stateDebugSelectedSource === "live"
      ? "Live"
      : stateDebugSelectedSource === "store"
        ? "快照"
        : "合并";
  const liveTag = view?.live_running
    ? " | 🟢 live 运行中"
    : view?.live_available
      ? " | live 可用"
      : "";
  stateDebugMetaEl.textContent =
    `[${sourceLabel}] task ${taskId} | session ${sessionId} | status ${active?.status || "-"} | node ${active?.current_node || "-"} | mode ${active?.execution_mode || "-"} | 拉取 ${active?.fetched_at || "-"}${liveTag}${truncated}`;
}

async function refreshStateDebugView() {
  const taskId = resolveStateDebugTaskId();
  if (!taskId || !stateDebugJsonEl) return false;
  try {
    const res = await fetch(`/tasks/${taskId}/state?truncate=true`, { headers: getAuthHeaders() });
    if (res.status === 404) {
      stateDebugCache = null;
      if (stateDebugMetaEl) {
        stateDebugMetaEl.textContent = `尚无任务数据 (task/session: ${taskId.slice(0, 12)}…)。请先发送一条消息。`;
      }
      stateDebugJsonEl.textContent = "{}";
      if (stateDebugKeysEl) {
        stateDebugKeysEl.innerHTML = '<p class="flow-empty">无状态</p>';
      }
      return false;
    }
    if (!res.ok) return false;
    const view = await res.json();
    stateDebugCache = view;
    if (stateDebugSelectedSource === "live" && !view.live_available) {
      stateDebugSelectedSource = view.store_available ? "store" : "merged";
    }
    if (stateDebugSelectedSource === "store" && !view.store_available && view.live_available) {
      stateDebugSelectedSource = "live";
    }
    updateStateDebugMeta(view);
    renderStateDebugSourceTabs();
    renderStateDebugKeys();
    renderStateDebugJson();
    return true;
  } catch {
    return false;
  }
}

function ensureStateDebugAutoRefresh() {
  if (stateDebugAutoRefreshTimer) return;
  stateDebugAutoRefreshTimer = setInterval(() => {
    if (!stateDebugModalEl?.open) return;
    if (stateDebugLiveEl && !stateDebugLiveEl.checked) return;
    refreshStateDebugView();
  }, 2000);
}

function stopStateDebugAutoRefresh() {
  if (!stateDebugAutoRefreshTimer) return;
  clearInterval(stateDebugAutoRefreshTimer);
  stateDebugAutoRefreshTimer = null;
}

async function openStateDebugModal() {
  if (!stateDebugModalEl) return;
  stateDebugSelectedKey = "__all__";
  stateDebugSelectedSource = running ? "merged" : "store";
  if (typeof stateDebugModalEl.showModal === "function") {
    stateDebugModalEl.showModal();
  } else {
    stateDebugModalEl.setAttribute("open", "");
  }
  ensureStateDebugAutoRefresh();
  await refreshStateDebugView();
}

function closeStateDebugModal() {
  if (!stateDebugModalEl) return;
  if (typeof stateDebugModalEl.close === "function") {
    stateDebugModalEl.close();
  } else {
    stateDebugModalEl.removeAttribute("open");
  }
  stopStateDebugAutoRefresh();
}

function updateStopButtonState() {
  if (!stopBtnEl) return;
  stopBtnEl.disabled = !(running || sessionHasInFlightMission);
}
/** Mission control-loop nodes — hidden from chat; use progress/trace for long runs. */
const MISSION_LOOP_NODES = new Set([
  "mission_init",
  "mission_decide",
  "mission_act",
  "mission_observe",
  "mission_eval",
]);
let healthBadgeBase = "";
const TOKEN_KEY = "agent_access_token";
const SESSION_KEY = "agent_session_id";
const THEME_KEY = "agent_theme";
/** Next stream submit uses new_session=true once (after /new). */
let pendingNewSession = false;
let sessionHasInFlightMission = false;

/** UUID v4; works on http://<LAN-IP> where crypto.randomUUID is unavailable. */
function newSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function getSessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = newSessionId();
    localStorage.setItem(SESSION_KEY, id);
  }
  updateSessionBadge(id);
  return id;
}

function updateSessionBadge(sessionId) {
  if (!sessionBadgeEl) return;
  const id = sessionId || getSessionId();
  const short = `${id.slice(0, 8)}…`;
  sessionBadgeEl.textContent = `session ${short}`;
  sessionBadgeEl.title = `会话 ID（完整）: ${id}\n/new 可开启新会话`;
  updateLanggraphicsPanel();
}

function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.body.classList.remove("theme-light", "theme-dark");
  document.body.classList.add(`theme-${t}`);
  if (themeSelectEl) themeSelectEl.value = t;
  localStorage.setItem(THEME_KEY, t);
  syncFlowPopup();
}

function buildSessionLanggraphicsUrl(baseUrl, sessionId) {
  if (!baseUrl) return "";
  try {
    const url = new URL(baseUrl, window.location.origin);
    const lgHost = (url.hostname || "").trim().toLowerCase();
    const pageHost = (window.location.hostname || "").trim();
    const loopbackHosts = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1"]);

    // If backend exposes a loopback host, rewrite to the current page host.
    // This keeps ports/path from LangGraphics while adapting host to deployment access path.
    if (loopbackHosts.has(lgHost) && pageHost) {
      url.hostname = pageHost;
    }
    // Current LangGraphics deployment is global on port 8764 and does not
    // support session-specific query routing.
    url.searchParams.delete("session_id");
    return url.toString();
  } catch {
    return baseUrl;
  }
}

function updateLanggraphicsPanel() {
  if (!langgraphicsMetaEl || !langgraphicsLinkEl || !langgraphicsFrameEl) return;
  const baseUrl = window.__langgraphicsUrl || "";
  const sessionId = localStorage.getItem(SESSION_KEY) || "";
  const sessionUrl = buildSessionLanggraphicsUrl(baseUrl, sessionId);
  if (!sessionUrl) {
    langgraphicsMetaEl.textContent = "LangGraphics 未开启或未配置";
    langgraphicsLinkEl.hidden = true;
    langgraphicsFrameEl.hidden = true;
    langgraphicsFrameEl.removeAttribute("src");
    return;
  }
  const short = `${sessionId.slice(0, 8)}…`;
  let hostInfo = "";
  try {
    hostInfo = new URL(sessionUrl).host;
  } catch {
    hostInfo = "";
  }
  langgraphicsMetaEl.textContent = hostInfo
    ? `当前会话: ${short} | 图地址: ${hostInfo} (全局图)`
    : `当前会话: ${short} (全局图)`;
  langgraphicsLinkEl.hidden = false;
  langgraphicsLinkEl.href = sessionUrl;
  langgraphicsLinkEl.textContent = "在新窗口打开会话图";
  if (langgraphicsFrameEl.getAttribute("src") !== sessionUrl) {
    langgraphicsFrameEl.src = sessionUrl;
  }
  langgraphicsFrameEl.hidden = false;
}

function startNewSession() {
  const id = newSessionId();
  localStorage.setItem(SESSION_KEY, id);
  pendingNewSession = true;
  updateSessionBadge(id);
  clearScreen();
  appendLine(`new session: ${id.slice(0, 8)}… (server task isolated)`, "system");
  return id;
}

function attachSessionFlags(body) {
  const out = body || {};
  if (pendingNewSession) {
    out.new_session = true;
    pendingNewSession = false;
  } else if (out.new_session === undefined) {
    out.new_session = false;
  }
  out.session_id = getSessionId();
  return out;
}

function clearScreen() {
  outputEl.replaceChildren();
  outputAutoFollow = true;
  progressLineEl = null;
  resetTraceBlock();
  resetAnswerStream();
  resetThinkingStream();
  resetWritingStream();
  contentBlockSeq = 0;
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
}

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json" };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function loginCommand(parts) {
  const username = parts[1] || "admin";
  const password = parts[2] || "admin";
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return;
  }
  const data = await res.json();
  localStorage.setItem(TOKEN_KEY, data.access_token);
  appendLine(`logged in as ${data.user_id} (${data.role})`, "system");
}

function appendLine(text, className = "system") {
  const line = document.createElement("p");
  line.className = `line ${className}`;
  line.textContent = text;
  outputEl.appendChild(line);
  scrollOutputIfPinned();
}

function isThinkingSpam(message) {
  const s = (message || "").trim();
  return (
    s.startsWith("{'thinking'") ||
    s.startsWith('{"thinking"') ||
    (s.startsWith("{") && s.includes("thinking"))
  );
}

function formatElapsedSec(payload) {
  if (typeof payload.elapsed_sec === "number") return payload.elapsed_sec;
  if (runStartedAt) return Math.floor((Date.now() - runStartedAt) / 1000);
  return 0;
}

function updateProgressLine(payload) {
  if (!payload.message || isThinkingSpam(payload.message)) return;
  lastPhaseMessage = payload.message.length > 48
    ? `${payload.message.slice(0, 48)}…`
    : payload.message;
  const sec = formatElapsedSec(payload);
  const text = `  … [${sec}s] ${payload.message}`;
  if (!progressLineEl) {
    progressLineEl = document.createElement("p");
    progressLineEl.className = "line system progress-line";
    outputEl.appendChild(progressLineEl);
  }
  progressLineEl.textContent = text;
  scrollOutputIfPinned();
}

function startRunTimer() {
  runStartedAt = Date.now();
  lastPhaseMessage = "处理中";
  if (runTimer) clearInterval(runTimer);
  envBadge.classList.add("badge-busy");
  runTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - runStartedAt) / 1000);
    const phase = lastPhaseMessage || "处理中";
    envBadge.textContent = `运行中 ${sec}s | ${phase}`;
  }, 1000);
}

function stopRunTimer() {
  if (runTimer) {
    clearInterval(runTimer);
    runTimer = null;
  }
  runStartedAt = 0;
  lastPhaseMessage = "";
  progressLineEl = null;
  envBadge.classList.remove("badge-busy");
  envBadge.textContent = healthBadgeBase || envBadge.textContent;
}

function resetTraceBlock() {
  tracePanelEl = null;
  traceLineCount = 0;
}

function resetAnswerStream() {
  answerStreamEl = null;
  answerStreamText = "";
}

function resetThinkingStream() {
  thinkingHeaderEl = null;
  thinkingPanelEl = null;
  thinkingStreamEl = null;
  thinkingStreamText = "";
  thinkingPendingText = "";
  thinkingFlushScheduled = false;
}

function resetWritingStream() {
  writingHeaderEl = null;
  writingPanelEl = null;
  writingStreamEl = null;
  writingStreamText = "";
  writingStreamFilename = "";
  writingPendingText = "";
  writingFlushScheduled = false;
  writingTraceHintShown = false;
  activeWritingBlock = null;
  writingStreamCharsThisTurn = 0;
}

/**
 * Scroll-stacked content block (new header + panel each call; no auto scroll-to-bottom).
 */
function createContentBlock({ title, panelClass, bodyClass, placeholder = "" }) {
  contentBlockSeq += 1;
  const headerEl = document.createElement("p");
  headerEl.className = `line trace-header content-header ${panelClass}-header`;
  headerEl.textContent = title;
  const panelEl = document.createElement("div");
  panelEl.className = `trace-panel content-panel ${panelClass}`;
  const bodyEl = document.createElement("pre");
  bodyEl.className = `line content-body ${bodyClass}`;
  bodyEl.textContent = placeholder;
  panelEl.appendChild(bodyEl);
  outputEl.appendChild(headerEl);
  outputEl.appendChild(panelEl);
  return { headerEl, panelEl, bodyEl };
}

function openWritingContentBlock(filename = "") {
  const fname = (filename || "").trim();
  const title = fname
    ? `── 手稿 · ${fname} ──`
    : `── 手稿生成（流式）──`;
  const block = createContentBlock({
    title,
    panelClass: "writing-panel",
    bodyClass: "trace-writing",
    placeholder: "",
  });
  activeWritingBlock = { ...block, filename: fname };
  writingHeaderEl = block.headerEl;
  writingPanelEl = block.panelEl;
  writingStreamEl = block.bodyEl;
  writingStreamFilename = fname;
  writingStreamText = "";
  writingPendingText = "";
  writingStreamEl.replaceChildren();
  return block.panelEl;
}

function appendFileContentBlock(title, text, { panelClass = "file-panel", bodyClass = "file-content" } = {}) {
  const body = (text || "").trim();
  if (!body) return;
  const block = createContentBlock({
    title,
    panelClass,
    bodyClass,
    placeholder: body,
  });
  return block;
}

/** Create thinking UI early so SSE thinking_delta has a visible target (above trace panel). */
function prepareThinkingStreamUi() {
  if (thinkingPanelEl) return thinkingPanelEl;
  thinkingHeaderEl = document.createElement("p");
  thinkingHeaderEl.className = "line trace-header thinking-header";
  thinkingHeaderEl.textContent = "── 模型思考（流式）──";
  thinkingPanelEl = document.createElement("div");
  thinkingPanelEl.className = "trace-panel thinking-panel";
  thinkingStreamEl = document.createElement("p");
  thinkingStreamEl.className = "line trace trace-thinking";
  thinkingStreamEl.textContent = "等待模型思考流…";
  thinkingPanelEl.appendChild(thinkingStreamEl);
  outputEl.appendChild(thinkingHeaderEl);
  outputEl.appendChild(thinkingPanelEl);
  return thinkingPanelEl;
}

function ensureThinkingPanel() {
  return prepareThinkingStreamUi();
}

function flushThinkingPending() {
  if (!thinkingPendingText || !thinkingStreamEl) return;
  const chunk = thinkingPendingText;
  thinkingPendingText = "";
  thinkingStreamEl.appendChild(document.createTextNode(chunk));
  thinkingStreamText += chunk;
  scrollToBottomIfPinned(thinkingPanelEl);
  scrollOutputIfPinned();
}

function scheduleThinkingFlush() {
  if (thinkingFlushScheduled) return;
  thinkingFlushScheduled = true;
  requestAnimationFrame(() => {
    thinkingFlushScheduled = false;
    flushThinkingPending();
  });
}

function appendThinkingDelta(text) {
  if (!text) return;
  ensureThinkingPanel();
  if (thinkingStreamText === "" && thinkingStreamEl.textContent.startsWith("等待")) {
    thinkingStreamText = "";
    thinkingStreamEl.replaceChildren();
  }
  thinkingPendingText += text;
  scheduleThinkingFlush();
}

/** New manuscript stream panel per run / per filename (scroll stack, no reuse). */
function ensureWritingPanel(filename = "") {
  const fname = (filename || "").trim();
  if (
    activeWritingBlock &&
    (!fname || fname === activeWritingBlock.filename)
  ) {
    writingStreamEl = activeWritingBlock.bodyEl;
    writingPanelEl = activeWritingBlock.panelEl;
    writingHeaderEl = activeWritingBlock.headerEl;
    return writingPanelEl;
  }
  return openWritingContentBlock(fname);
}

function flushWritingPending() {
  if (!writingPendingText || !writingStreamEl) return;
  let chunk = writingPendingText;
  writingPendingText = "";
  const room = WRITING_STREAM_MAX_CHARS - writingStreamText.length;
  if (room <= 0) return;
  if (chunk.length > room) chunk = chunk.slice(0, room);
  writingStreamEl.appendChild(document.createTextNode(chunk));
  writingStreamText += chunk.length;
  scrollToBottomIfPinned(writingPanelEl);
  scrollOutputIfPinned();
}

function scheduleWritingFlush() {
  if (writingFlushScheduled) return;
  writingFlushScheduled = true;
  requestAnimationFrame(() => {
    writingFlushScheduled = false;
    flushWritingPending();
  });
}

function appendWritingDelta(text, payload = {}) {
  if (!text) return;
  const fname = (payload.filename || "").trim();
  if (
    payload.reset ||
    !activeWritingBlock ||
    (fname && fname !== activeWritingBlock.filename)
  ) {
    openWritingContentBlock(fname);
  } else {
    ensureWritingPanel(fname);
  }
  if (payload.reset) {
    writingStreamText = "";
    writingPendingText = "";
    writingStreamEl.replaceChildren();
  }
  if (writingStreamText.length >= WRITING_STREAM_MAX_CHARS) {
    return;
  }
  const room = WRITING_STREAM_MAX_CHARS - writingStreamText.length;
  if (room <= 0) return;
  const slice = text.length > room ? text.slice(0, room) : text;
  writingPendingText += slice;
  writingStreamCharsThisTurn += slice.length;
  scheduleWritingFlush();
}

function ensureAnswerStreamLine() {
  if (answerStreamEl) return answerStreamEl;
  const block = createContentBlock({
    title: "── 回答（流式）──",
    panelClass: "file-panel",
    bodyClass: "file-content",
    placeholder: "",
  });
  answerStreamEl = block.bodyEl;
  answerStreamEl.classList.add("answer-stream");
  return answerStreamEl;
}

function setAnswerStreamText(text) {
  const body = (text || "").trim();
  if (!body) return;
  answerStreamText = body;
  const line = ensureAnswerStreamLine();
  line.replaceChildren();
  line.appendChild(document.createTextNode(answerStreamText));
}

function appendAnswerDelta(text) {
  if (!text) return;
  answerStreamText += text;
  ensureAnswerStreamLine().appendChild(document.createTextNode(text));
}

function ensureTracePanel() {
  if (tracePanelEl) return tracePanelEl;
  const header = document.createElement("p");
  header.className = "line trace-header";
  header.textContent = "── 推理过程（详细）──";
  tracePanelEl = document.createElement("div");
  tracePanelEl.className = "trace-panel";
  outputEl.appendChild(header);
  outputEl.appendChild(tracePanelEl);
  return tracePanelEl;
}

function appendTraceLine(text, payload = {}) {
  if (!text) return;
  const panel = ensureTracePanel();
  const sec = runStartedAt ? Math.floor((Date.now() - runStartedAt) / 1000) : 0;
  const level = payload.level || "delta";
  const node = payload.node || "";
  const phase = payload.phase || "";
  const tag = node ? `${node}/${phase}` : "trace";

  const line = document.createElement("p");
  line.className = `line trace trace-${level}`;
  line.textContent = `[${sec}s] ${tag} · ${text}`;
  panel.appendChild(line);
  traceLineCount += 1;
  while (traceLineCount > TRACE_MAX_LINES && panel.firstChild) {
    panel.removeChild(panel.firstChild);
    traceLineCount -= 1;
  }
  scrollToBottomIfPinned(panel);
  scrollOutputIfPinned();
}

function setRunning(value) {
  running = value;
  if (value) {
    resetTraceBlock();
    resetAnswerStream();
    resetThinkingStream();
    resetWritingStream();
    prepareThinkingStreamUi();
    startRunTimer();
    ensureFlowAutoRefresh();
  } else {
    stopRunTimer();
    if (!activeTaskId) stopFlowAutoRefresh();
  }
  updateStopButtonState();
}

function formatLanggraphicsHealth(lg) {
  if (!lg) return "";
  if (lg.enabled && lg.url) {
    return ` | LG:${lg.url}`;
  }
  if (lg.package_installed && !lg.configured) {
    return " | LG:off(LANGGRAPHICS_ENABLED=false)";
  }
  if (!lg.package_installed) {
    return " | LG:未安装";
  }
  return "";
}

async function showLanggraphicsHelp() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const lg = data.langgraphics || {};
    if (lg.enabled && lg.url) {
      appendLine(`LangGraphics 已启用: ${lg.url}`, "system");
      appendLine("  先打开链接，再提交任务；执行时节点会高亮。", "system");
      return;
    }
    if (lg.package_installed) {
      appendLine("LangGraphics 包已安装，但未开启。", "system");
      appendLine("  Docker: 在 .env 设 LANGGRAPHICS_ENABLED=true", "system");
      appendLine("  然后: HOST_PORT=8001 docker compose up -d --build", "system");
      appendLine("  浏览器: http://localhost:8764", "system");
      return;
    }
    appendLine("LangGraphics 未安装（需 Python>=3.10，见 docs/LANGGRAPHICS.md）", "system");
  } catch (e) {
    appendLine(`langgraphics status failed: ${e}`, "error");
  }
}

async function fetchHealth() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const lg = data.langgraphics || {};
    healthBadgeBase = `${data.env} | auth:${data.auth_enabled} | kb:${data.knowledge_docs}${formatLanggraphicsHealth(lg)}`;
    if (!running) envBadge.textContent = healthBadgeBase;
    if (lg.enabled && lg.url) {
      window.__langgraphicsUrl = lg.url;
    }
    updateLanggraphicsPanel();
  } catch {
    envBadge.textContent = "offline";
    updateLanggraphicsPanel();
  }
}

async function listHistory() {
  const res = await fetch("/tasks?limit=10", { headers: getAuthHeaders() });
  if (!res.ok) {
    if (res.status === 429) {
      appendLine("history rate-limited (429). 请稍等 30-60 秒后重试。", "error");
      return;
    }
    appendLine(`history failed: ${res.status}`, "error");
    return;
  }
  const data = await res.json();
  if (!data.tasks.length) {
    appendLine("(no tasks yet)", "system");
    return;
  }
  for (const task of data.tasks) {
    appendLine(
      `${task.task_id.slice(0, 8)}… [${task.status}] ${task.goal || task.task_type}`,
      "system"
    );
  }
}

async function showStatus(taskId) {
  const res = await fetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return;
  }
  const data = await res.json();
  renderFlowTimeline(data, taskId);
  appendLine(
    `status: ${data.status} | node: ${data.current_node} | review: ${data.review_required}`,
    "system"
  );
  if (data.errors && data.errors.length) {
    for (const err of data.errors) {
      appendLine(`  error: ${err}`, "error");
    }
  }
  if (data.node_history && data.node_history.length) {
    for (const h of data.node_history) {
      appendLine(`  [${h.at}] ${h.node} -> ${h.status}`, "system");
    }
  }
}

async function showAudit(taskId) {
  const res = await fetch(`/tasks/${taskId}/audit`, { headers: getAuthHeaders() });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return;
  }
  const data = await res.json();
  const chain = data.audit_chain || [];
  if (!chain.length) {
    appendLine("(no audit records)", "system");
    return;
  }
  for (const entry of chain) {
    const detail = entry.detail ? ` ${JSON.stringify(entry.detail)}` : "";
    appendLine(`${entry.node} / ${entry.action}${detail}`, "system");
  }
}

async function showResult(taskId) {
  const res = await fetch(`/tasks/${taskId}/result`, { headers: getAuthHeaders() });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return;
  }
  const data = await res.json();
  const body = data.final_answer || "(no answer)";
  if (body.length >= FILE_PREVIEW_BOX_MIN_CHARS) {
    appendFileContentBlock(`── 任务结果 · ${taskId.slice(0, 8)}… ──`, body, {
      panelClass: "file-panel",
      bodyClass: "file-content",
    });
  } else {
    appendLine(body, "result");
  }
}

async function submitReview(taskId, action) {
  const res = await fetch("/reviews", {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ task_id: taskId, action, comment: "web-cli" }),
  });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return;
  }
  const data = await res.json();
  appendLine(`review ${action} -> ${data.status}`, "system");
  await showResult(taskId);
}

function appendSystemLines(lines, className = "system") {
  for (const line of lines || []) {
    const text = String(line || "").trim();
    if (text) appendLine(text, className);
  }
}

async function runAutonomousUi(autonomousUi, taskId) {
  if (!autonomousUi?.enabled || !taskId) return;
  appendSystemLines(autonomousUi.system_lines);
  const behavior = autonomousUi.behavior;
  if (
    behavior === "wait_outcome_confirm" ||
    behavior === "wait_intent_confirm" ||
    behavior === "steer_review_only" ||
    behavior === "failure_pause"
  ) {
    return;
  }
  if (behavior === "auto_resume_step" || behavior === "resume_after_steer") {
    await runResumeStream(taskId, {
      confirm: Boolean(autonomousUi.resume_confirm),
    });
  }
}

async function steerActiveMission(message, opts = {}) {
  const taskId = activeTaskId || getSessionId();
  const payload = {
    message,
    preempt: Boolean(opts.preempt),
    priority: Number.isFinite(opts.priority) ? opts.priority : 0,
    replace_goal: Boolean(opts.replaceGoal),
  };
  const res = await fetch(`/tasks/${taskId}/steer`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return false;
  }
  const data = await res.json();
  const display = data.client_display || {};
  appendSystemLines(display.system_lines);
  if (!display.system_lines?.length) {
    appendLine(`steer → ${data.message || data.status}`, "system");
  }
  return true;
}

async function stopActiveMission() {
  const taskId = activeTaskId || getSessionId();
  const res = await fetch(`/tasks/${taskId}/stop`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    appendLine(`stop failed: ${res.status} ${await res.text()}`, "error");
    return false;
  }
  const data = await res.json();
  const display = data.client_display || {};
  appendSystemLines(display.system_lines);
  if (!display.system_lines?.length) {
    appendLine("stop requested, waiting current step to yield…", "system");
  }
  return true;
}

async function stopTaskById(taskId) {
  const res = await fetch(`/tasks/${taskId}/stop`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) return false;
  return true;
}

async function getTaskStatusValue(taskId) {
  try {
    const res = await fetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
    if (!res.ok) return "";
    const data = await res.json();
    return String(data.status || "");
  } catch {
    return "";
  }
}

async function stopAllInFlightMissions({ limit = 100 } = {}) {
  const res = await fetch(`/tasks?limit=${Math.min(limit, 100)}`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    if (res.status === 429) {
      appendLine("stop-all blocked by rate limit (429). 请稍后再试。", "error");
      return { stopped: 0, total: 0, limited: true };
    }
    appendLine(`list tasks failed: ${res.status}`, "error");
    return { stopped: 0, total: 0, limited: false };
  }
  const data = await res.json();
  const tasks = Array.isArray(data.tasks) ? data.tasks : [];
  const inflight = tasks.filter((t) => {
    const st = String(t.status || "");
    return st === "MISSION_RUNNING" || st === "MISSION_PAUSED";
  });
  let stopped = 0;
  let limited = false;
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (const t of inflight) {
    const r = await fetch(`/tasks/${t.task_id}/stop`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
    if (r.status === 429) {
      limited = true;
      break;
    }
    const ok = r.ok;
    if (ok) stopped += 1;
    // Soft throttle to avoid request burst on strict rate limits.
    await delay(180);
  }
  return { stopped, total: inflight.length, limited };
}

function formatStructuredConfirmHint(confirmation, confirmationActions) {
  const actions = confirmation?.user_actions || confirmationActions;
  if (actions?.resume) {
    const r = actions.resume;
    const s = actions.steer;
    const cli = actions.cli_alias ? ` Web CLI: ${actions.cli_alias}.` : "";
    return (
      `Approve: ${r.method} ${r.path} body ${JSON.stringify(r.body)};` +
      ` or ${s.method} ${s.path} body ${JSON.stringify(s.body)}.${cli}` +
      " To change direction, send a new steer with confirm=false."
    );
  }
  return (
    'Approve via POST /tasks/{id}/resume or /steer with body {"confirm":true}. ' +
    "Web CLI: /confirm."
  );
}

function renderConfirmationSection(section) {
  if (!section || typeof section !== "object") return null;
  switch (section.type) {
    case "text":
      return { kind: "pre", text: String(section.content || "") };
    case "artifact": {
      const fname = section.filename || "artifact";
      const mode = section.preview_mode ? ` · ${section.preview_mode}` : "";
      const trunc = section.truncated ? "（节选）" : "";
      return {
        kind: "file",
        title: `── ${fname}${mode}${trunc} ──`,
        text: String(section.content || ""),
        panelClass: "steer-outcome-panel file-panel",
      };
    }
    case "queue": {
      const items = section.items || [];
      if (!items.length) return null;
      const lines = items.map(
        (i) => `- [${i.status || "pending"}] ${i.title || i.kind || i.id || "?"}`
      );
      return { kind: "pre", text: `执行队列：\n${lines.join("\n")}` };
    }
    case "impact": {
      const ops = section.operations || [];
      if (!ops.length) return null;
      const lines = ops.map((o) => {
        let line = `- ${o.op || "?"}`;
        if (o.artifact) line += `: ${o.artifact}`;
        if (o.work_item_id) line += `: ${o.work_item_id}`;
        if (o.kind) line += ` (${o.kind})`;
        return line;
      });
      return { kind: "pre", text: `影响范围：\n${lines.join("\n")}` };
    }
    case "intervention": {
      const parts = [];
      if (section.action) parts.push(`动作: ${section.action}`);
      if (section.force) parts.push("(强制覆盖 step_policy)");
      if (section.reason) parts.push(String(section.reason));
      if (!parts.length) return null;
      return { kind: "pre", text: parts.join(" ") };
    }
    default:
      return null;
  }
}

function confirmationDedupeKey(confirmation) {
  if (!confirmation || typeof confirmation !== "object") return "";
  const phase = String(confirmation.phase || "unknown");
  const wid = confirmation.work_item_id || confirmation.work_item_kind || "";
  return `${phase}:${wid}`;
}

function gatePendingOnPayload(payload) {
  if (!payload || typeof payload !== "object") return false;
  return Boolean(
    payload.steer_intent_pending_confirm || payload.steer_outcome_pending_confirm
  );
}

function renderSteerGateFromPayload(payload) {
  if (!payload || typeof payload !== "object") return;
  if (payload.steer_intent_pending_confirm && payload.steer_intent_confirmation) {
    appendSteerIntentConfirmPanel(
      payload.steer_intent_confirmation,
      payload.confirmation_actions
    );
  }
  if (
    payload.steer_outcome_pending_confirm &&
    payload.steer_outcome_confirmation &&
    !payload.steer_review_only
  ) {
    appendSteerOutcomeConfirmPanel(
      payload.steer_outcome_confirmation,
      payload.confirmation_actions
    );
  }
}

function appendConfirmationBlock(confirmation, confirmationActions, panelClass) {
  if (!confirmation || typeof confirmation !== "object") return;
  const dedupeKey = confirmationDedupeKey(confirmation);
  if (dedupeKey && shownConfirmationKeys.has(dedupeKey)) return;
  if (dedupeKey) shownConfirmationKeys.add(dedupeKey);

  const sections = Array.isArray(confirmation.sections) ? confirmation.sections : [];
  const summary = String(confirmation.summary_text || "").trim();
  const excerpt = String(confirmation.artifact_excerpt || "").trim();
  if (!summary && !excerpt && !sections.length) return;

  const phase = String(confirmation.phase || "");
  const skipArtifactPreview =
    phase === "outcome" && writingStreamCharsThisTurn > 400;

  for (const section of sections) {
    if (skipArtifactPreview && section.type === "artifact") continue;
    const rendered = renderConfirmationSection(section);
    if (!rendered) continue;
    if (rendered.kind === "file" && rendered.text) {
      appendFileContentBlock(rendered.title, rendered.text, {
        panelClass: rendered.panelClass || "file-panel",
        bodyClass: "file-content",
      });
    } else if (rendered.kind === "pre" && rendered.text) {
      const prePanel = document.createElement("div");
      prePanel.className = panelClass;
      const pre = document.createElement("pre");
      pre.textContent = rendered.text;
      prePanel.appendChild(pre);
      outputEl.appendChild(prePanel);
    }
  }

  if (!sections.length && excerpt && !skipArtifactPreview) {
    const fname = String(confirmation.artifact_filename || "outline.txt");
    appendFileContentBlock(`── 执行结果 · ${fname}（待批准节选）──`, excerpt, {
      panelClass: "steer-outcome-panel file-panel",
      bodyClass: "file-content",
    });
  }

  const panel = document.createElement("div");
  panel.className = panelClass;
  panel.setAttribute("data-steer-confirm", "1");

  const title = document.createElement("h3");
  const panelTitle = confirmation.display?.title || "steer_confirmation";
  title.textContent = `── ${panelTitle} ──`;
  panel.appendChild(title);

  if (summary && (!sections.length || sections.every((s) => s.type !== "text"))) {
    const body = document.createElement("pre");
    body.textContent = summary;
    panel.appendChild(body);
  }

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = formatStructuredConfirmHint(confirmation, confirmationActions);
  panel.appendChild(hint);

  outputEl.appendChild(panel);
}

function appendSteerIntentConfirmPanel(confirmation, confirmationActions) {
  appendConfirmationBlock(confirmation, confirmationActions, "steer-confirm-panel");
}

function appendSteerOutcomeConfirmPanel(confirmation, confirmationActions) {
  appendConfirmationBlock(confirmation, confirmationActions, "steer-outcome-panel");
}

async function resumeMissionOnce(taskId, { confirm = false } = {}) {
  const res = await fetch(`/tasks/${taskId}/resume`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ confirm: Boolean(confirm) }),
  });
  if (!res.ok) {
    appendLine(await res.text(), "error");
    return null;
  }
  return res.json();
}

/** Resume mission with SSE (progress, writing_delta, gates) — preferred for Web CLI. */
async function runResumeStream(taskId, { confirm = false } = {}) {
  if (running) {
    appendLine("已有任务在运行，请稍候", "error");
    return null;
  }
  setRunning(true);
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
  appendLine(`> /resume${confirm ? " confirm" : ""}`, "user");
  const taskIdRef = { id: taskId };
  try {
    const res = await fetch(`/tasks/${taskId}/resume/stream`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ confirm: Boolean(confirm) }),
    });
    if (!res.ok || !res.body) {
      appendLine(`resume stream failed: ${res.status} ${await res.text()}`, "error");
      return null;
    }
    activeTaskId = taskId;
    await consumeSseStream(res, taskIdRef);
    return { task_id: taskIdRef.id || taskId };
  } catch (err) {
    appendLine(`resume stream error: ${err}`, "error");
    return null;
  } finally {
    setRunning(false);
    await refreshFlowPanel(taskIdRef?.id || taskId || activeTaskId);
  }
}

function formatOrchestration(summary, detail) {
  if (detail && typeof detail === "object") {
    const done = detail.done ?? 0;
    const total = detail.total ?? 0;
    const completed = (detail.completed || []).length
      ? (detail.completed || []).join(",")
      : "—";
    const cur = detail.current_title || detail.current_kind;
    const curStatus = detail.current_status;
    let currentPart = "—";
    if (cur) {
      currentPart = curStatus && curStatus !== "done" ? `${cur}(${curStatus})` : String(cur);
    }
    return `orchestration ${done}/${total} done=[${completed}] current=[${currentPart}]`;
  }
  return summary ? String(summary) : "";
}

function handleStreamEvent(eventType, payload, taskIdRef) {
  if (payload.task_id) {
    taskIdRef.id = payload.task_id;
    activeTaskId = payload.task_id;
    ensureFlowAutoRefresh();
  }

  if (eventType === "task_created") {
    if (payload.task_id) {
      flowLiveHistoryByTask.set(payload.task_id, []);
    }
    if (payload.session_id) {
      localStorage.setItem(SESSION_KEY, payload.session_id);
      updateSessionBadge(payload.session_id);
    }
    if (payload.continued) {
      appendLine(
        `session ${(payload.session_id || payload.task_id || "").slice(0, 8)}… turn ${payload.session_turn || "?"}`,
        "system"
      );
    } else {
      appendLine(
        `session ${(payload.session_id || payload.task_id || "").slice(0, 8)}… started (task = session)`,
        "system"
      );
    }
  } else if (eventType === "subtasks") {
    appendLine(`subtasks planned: ${payload.count}`, "system");
    for (const st of payload.subtasks || []) {
      appendLine(`  - [${st.domain}] ${st.description}`, "system");
    }
  } else if (eventType === "worker") {
    appendLine(`  worker ${payload.domain}: ${payload.status} — ${payload.summary || ""}`, "node");
  } else if (eventType === "langgraphics") {
    // Avoid repeating LangGraphics setup hints on every session turn / resume.
    if (payload.continued) return;
    const msg = payload.message || "";
    if (msg) appendLine(msg, "system");
    if (payload.url) {
      window.__langgraphicsUrl = payload.url;
      updateLanggraphicsPanel();
      appendLine(`LangGraphics UI: ${payload.url}`, "system");
      appendLine("  （需在提交任务后才会出现节点动画；仅打开页面而无任务时可能为空图）", "system");
    }
  } else if (eventType === "progress") {
    updateProgressLine(payload);
  } else if (eventType === "trace") {
    const body = (payload.text || "").trim();
    if (!body) return;
    if (body.includes("\n")) {
      for (const part of body.split("\n")) {
        if (part.trim()) appendTraceLine(part.trim(), payload);
      }
    } else {
      appendTraceLine(body, payload);
    }
  } else if (eventType === "plan") {
    appendTraceLine("【SSE 计划快照】", { node: "planning", phase: "plan", level: "detail" });
    for (const [i, step] of (payload.plan || []).entries()) {
      appendTraceLine(`  ${i + 1}. ${step}`, { node: "planning", phase: "plan", level: "detail" });
    }
    if ((payload.selected_tools || []).length) {
      appendTraceLine(`工具: ${payload.selected_tools.join(", ")}`, {
        node: "planning",
        phase: "plan",
        level: "detail",
      });
    }
  } else if (eventType === "tool_preview") {
    const tool = payload.tool || "tool";
    const snippet = (payload.snippet || "").trim();
    if (snippet.length >= FILE_PREVIEW_BOX_MIN_CHARS) {
      appendFileContentBlock(`── 工具输出 · ${tool} ──`, snippet);
    } else if (snippet) {
      appendLine(`  tool ${tool}: ${snippet}`, "node");
    } else {
      appendLine(`  tool ${tool}`, "node");
    }
  } else if (eventType === "thinking_delta") {
    appendThinkingDelta(payload.text || "");
  } else if (eventType === "writing_delta") {
    if (payload.reset || payload.phase === "start") {
      ensureTracePanel();
      if (!writingTraceHintShown) {
        writingTraceHintShown = true;
        appendTraceLine(
          "手稿正文流式可能滞后；请关注本栏 writing/status 进度（模型缓冲与正文 content 解析）",
          { node: "writing", phase: "hint", level: "status" }
        );
      }
    }
    appendWritingDelta(payload.text || "", payload);
  } else if (eventType === "answer_delta") {
    appendAnswerDelta(payload.text || "");
  } else if (eventType === "answer_preview") {
    setAnswerStreamText(payload.text || "");
  } else if (eventType === "node") {
    formatNodeEvent(payload);
    flowSelectedNode = String(payload.node || payload.current_node || flowSelectedNode || "");
    const nodeTaskId = taskIdRef.id || payload.task_id || activeTaskId;
    if (nodeTaskId) {
      const rows = flowLiveHistoryByTask.get(nodeTaskId) || [];
      rows.push({
        node: payload.node || payload.current_node || "?",
        status: payload.status || "",
        at: new Date().toISOString(),
      });
      flowLiveHistoryByTask.set(nodeTaskId, rows.slice(-120));
    }
    refreshFlowPanel(taskIdRef.id || payload.task_id || activeTaskId);
  } else if (eventType === "review_required") {
    appendLine(payload.message, "system");
    appendLine(`approve: /approve ${payload.task_id}`, "system");
    appendLine(`reject:  /reject ${payload.task_id}`, "system");
  } else if (eventType === "mission_paused") {
    appendSystemLines(payload.system_lines);
    if (
      payload.autonomous_ui?.enabled &&
      !payload.steer_outcome_pending_confirm &&
      !payload.steer_intent_pending_confirm
    ) {
      runAutonomousUi(payload.autonomous_ui, payload.task_id);
    }
  } else if (eventType === "error") {
    appendLine(payload.detail, "error");
  } else if (eventType === "done") {
    const gatePending = gatePendingOnPayload(payload);
    renderSteerGateFromPayload(payload);
    if (payload.final_answer && !gatePending) {
      const finalText = String(payload.final_answer);
      if (answerStreamEl && finalText.length >= answerStreamText.length) {
        setAnswerStreamText(finalText);
      } else if (!answerStreamEl) {
        if (finalText.length >= FILE_PREVIEW_BOX_MIN_CHARS) {
          appendFileContentBlock("── 回答 ──", finalText);
        } else {
          appendLine(finalText, "result");
        }
      }
    } else if (!gatePending && !answerStreamEl) {
      appendLine(`done: ${payload.status}`, "system");
    } else if (gatePending) {
      appendLine(`done: ${payload.status} — 待批准（见上方确认面板，/confirm 继续）`, "system");
      if (answerStreamEl && answerStreamText.trim()) {
        appendLine(
          "  （上方「回答（流式）」为预览，正式结果以确认面板为准；批准后继续执行）",
          "system"
        );
      }
    }
    refreshFlowPanel(taskIdRef.id || payload.task_id || activeTaskId);
  }
}

function formatNodeEvent(payload) {
  const node = payload.node || "?";
  const status = payload.status || "";
  const isError = status === "FAILED" || status === "DEAD_LETTER" || status === "WRITING_FAILED";

  if (MISSION_LOOP_NODES.has(node) && !isError) {
    return;
  }

  const line = `[${node}] → ${status}`;
  appendLine(line, isError ? "error" : "node");

  if (node === "planning" && status === "PLANNED") {
    appendLine("  … 计划完成；若含万字续写，正文生成可能需数分钟，请看右上角计时", "system");
  }
  if (node === "tool_execution" && status === "TOOL_EXECUTED") {
    appendLine("  … 工具执行完成，正在收尾", "system");
  }
  if (node === "writing" && status === "WRITTEN") {
    const bytes = payload.body_bytes ? `（${payload.body_bytes} B）` : "";
    appendLine(`  … 手稿已写入 ${payload.body_path || "novel.txt"}${bytes}`, "system");
  }
  if (node === "writing" && status === "WRITING_FAILED") {
    appendLine("  … 写作失败，请查看 trace 或 /audit；手稿可能未生成", "error");
  }
  if (isError) {
    if (payload.last_error) {
      appendLine(`  原因: ${payload.last_error}`, "error");
    }
    if (node === "mission_observe" && status === "FAILED") {
      appendLine(
        "  （observe 沿用上一节点的 FAILED，请看上一条 mission_act/planning/writing 的原因）",
        "system"
      );
    }
    appendLine(`  tip: /status ${payload.task_id}  or  /audit ${payload.task_id}`, "system");
  }
}

async function consumeSseStream(res, taskIdRef) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      const lines = part.split("\n");
      let eventType = "message";
      let dataLine = "";
      for (const line of lines) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        if (line.startsWith("data:")) dataLine = line.slice(5).trim();
      }
      if (!dataLine) continue;
      try {
        handleStreamEvent(eventType, JSON.parse(dataLine), taskIdRef);
      } catch {
        /* skip malformed chunk */
      }
    }
  }
}

function buildDefaultTaskBody(goal, riskLevel = "LOW") {
  return attachSessionFlags({
    task_type: "qa",
    user_id: "web",
    input_payload: {
      goal,
      risk_level: riskLevel,
      // Allow planning LLM to auto-enable mission when appropriate (UI toggle still overrides).
      mission_auto: true,
    },
  });
}

function buildTaskRequestBody(goal, riskLevel = "LOW") {
  return buildDefaultTaskBody(goal, riskLevel);
}

async function runTaskStream(goal, riskLevel = "LOW", endpoint = "/tasks/stream", body = null) {
  setRunning(true);
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
  appendLine(`> ${goal}`, "user");
  const requestBody = body || buildTaskRequestBody(goal, riskLevel);
  const taskIdRef = { id: null };

  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(requestBody),
    });

    if (!res.ok || !res.body) {
      appendLine(`stream failed: ${res.status}`, "error");
      return;
    }

    await consumeSseStream(res, taskIdRef);
  } catch (err) {
    appendLine(`stream error: ${err}`, "error");
  } finally {
    setRunning(false);
    await refreshFlowPanel(taskIdRef?.id || activeTaskId);
  }
}

async function runSupervisorStream(goal, domains = null) {
  const body = { goal, domains, task_type: "supervisor", user_id: "web" };
  await runTaskStream(goal, "LOW", "/supervisor/tasks/stream", body);
}

function printHelp() {
  appendLine("Commands:", "system");
  appendLine("  <text>           Run agent task (SSE stream)", "system");
  appendLine("  /new             Start a new session (new task window)", "system");
  appendLine("  /clear           Clear terminal output", "system");
  appendLine("  /confirm         POST /resume {\"confirm\":true} (pending steer gate)", "system");
  appendLine("  /resume          Continue current paused mission stream", "system");
  appendLine("  /stop            Stop current running session task (best-effort immediate)", "system");
  appendLine("  /stop-all        Stop all in-flight missions in recent task list", "system");
  appendLine("  /append <text>   Add follow-up steer without replacing current goal", "system");
  appendLine("  /session         Show current session id (also in header)", "system");
  appendLine("  /help            Show this help", "system");
  appendLine("  /history         List recent tasks", "system");
  appendLine("  /status <id>     Query task status + errors", "system");
  appendLine("  /audit <id>      Show audit chain (node logs)", "system");
  appendLine("  /result <id>     Show task result", "system");
  appendLine("  /approve <id>    Approve human review", "system");
  appendLine("  /reject <id>     Reject human review", "system");
  appendLine("  /supervisor <text>  Multi-agent supervisor task (SSE)", "system");
  appendLine("  /risk high <text> Run high-risk task (triggers review)", "system");
  appendLine("  /langgraphics       显示 LangGraph 可视化地址与开启说明（可选）", "system");
  appendLine("  /login <user> <pass>  Obtain JWT (when auth enabled)", "system");
  appendLine("  /logout           Clear stored token", "system");
}

async function handleCommand(raw) {
  const text = raw.trim();
  if (!text) return;

  if (text === "/help") {
    printHelp();
    return;
  }
  if (text === "/new") {
    sessionHasInFlightMission = false;
    startNewSession();
    appendLine("tip: use /stop-all if you want to stop old in-flight missions", "system");
    return;
  }
  if (text === "/clear") {
    clearScreen();
    appendLine("screen cleared", "system");
    return;
  }
  if (text === "/confirm") {
    const taskId = activeTaskId || getSessionId();
    await runResumeStream(taskId, { confirm: true });
    sessionHasInFlightMission = false;
    return;
  }
  if (text === "/resume") {
    const taskId = activeTaskId || getSessionId();
    await runResumeStream(taskId, { confirm: false });
    sessionHasInFlightMission = false;
    return;
  }
  if (text === "/stop") {
    const ok = await stopActiveMission();
    if (ok) sessionHasInFlightMission = false;
    return;
  }
  if (text === "/stop-all") {
    const r = await stopAllInFlightMissions();
    appendLine(`stop-all done: ${r.stopped}/${r.total} mission(s) requested to stop`, "system");
    if (r.limited) {
      appendLine("stop-all partially applied due to 429 rate limit; retry after 30-60s.", "error");
    }
    sessionHasInFlightMission = false;
    updateStopButtonState();
    return;
  }
  if (text.startsWith("/append ")) {
    const msg = text.slice("/append ".length).trim();
    if (!msg) {
      appendLine("usage: /append <text>", "error");
      return;
    }
    const ok = await steerActiveMission(msg, {
      preempt: false,
      priority: 0,
      replaceGoal: false,
    });
    if (ok) sessionHasInFlightMission = true;
    return;
  }
  if (text === "/session") {
    appendLine(`session_id: ${getSessionId()}`, "system");
    return;
  }
  if (text === "/history") {
    await listHistory();
    return;
  }
  if (text === "/logout") {
    localStorage.removeItem(TOKEN_KEY);
    appendLine("logged out", "system");
    return;
  }
  if (text === "/langgraphics" || text.startsWith("/langgraphics ")) {
    await showLanggraphicsHelp();
    return;
  }
  if (text.startsWith("/login")) {
    await loginCommand(text.split(/\s+/));
    return;
  }
  if (text.startsWith("/status ")) {
    await showStatus(text.split(/\s+/)[1]);
    return;
  }
  if (text.startsWith("/audit ")) {
    await showAudit(text.split(/\s+/)[1]);
    return;
  }
  if (text.startsWith("/result ")) {
    await showResult(text.split(/\s+/)[1]);
    return;
  }
  if (text.startsWith("/approve ")) {
    await submitReview(text.split(/\s+/)[1], "APPROVE");
    return;
  }
  if (text.startsWith("/reject ")) {
    await submitReview(text.split(/\s+/)[1], "REJECT");
    return;
  }
  if (text.startsWith("/supervisor ")) {
    await runSupervisorStream(text.slice("/supervisor ".length));
    return;
  }
  if (text.startsWith("/risk high ")) {
    await runTaskStream(text.slice("/risk high ".length), "HIGH");
    sessionHasInFlightMission = false;
    return;
  }
  // Only intercept when mission is actively running (steer queue). Paused missions use
  // normal session turn so backend turn_policy + planning LLM interpret intent.
  if (sessionHasInFlightMission && !text.startsWith("/")) {
    const taskId = activeTaskId || getSessionId();
    const st = await getTaskStatusValue(taskId);
    if (st === "MISSION_RUNNING") {
      const ok = await steerActiveMission(text, {
        preempt: true,
        priority: 80,
        replaceGoal: true,
      });
      if (ok) {
        appendLine(
          "任务运行中：已按接管模式处理输入。暂停后续写请等本轮结束，或使用 /append <text>。",
          "system"
        );
      }
      return;
    }
  }
  await runTaskStream(text, "LOW");
  sessionHasInFlightMission = false;
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = inputEl.value.trim();
  inputEl.value = "";
  if (!value) return;
  if (running) {
    // Keep slash commands operational while stream is running (e.g. /stop, /stop-all).
    if (value.startsWith("/")) {
      await handleCommand(value);
      return;
    }
    appendLine(`> ${value}`, "user");
    await steerActiveMission(value, {
      preempt: true,
      priority: 80,
      replaceGoal: true,
    });
    return;
  }
  await handleCommand(value);
});

if (stopBtnEl) {
  stopBtnEl.addEventListener("click", async () => {
    if (!(running || sessionHasInFlightMission)) return;
    stopBtnEl.disabled = true;
    try {
      const ok = await stopActiveMission();
      if (ok) sessionHasInFlightMission = false;
    } finally {
      updateStopButtonState();
    }
  });
}

if (flowRefreshBtnEl) {
  flowRefreshBtnEl.addEventListener("click", async () => {
    await refreshFlowPanel();
  });
}

if (stateDebugSourceTabsEl) {
  stateDebugSourceTabsEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-source]");
    if (!btn || btn.disabled) return;
    stateDebugSelectedSource = String(btn.getAttribute("data-source") || "merged");
    renderStateDebugSourceTabs();
    updateStateDebugMeta(stateDebugCache);
    renderStateDebugKeys();
    renderStateDebugJson();
  });
}

if (stateDebugBtnEl) {
  stateDebugBtnEl.addEventListener("click", async () => {
    await openStateDebugModal();
  });
}

if (stateDebugRefreshBtnEl) {
  stateDebugRefreshBtnEl.addEventListener("click", async () => {
    await refreshStateDebugView();
  });
}

if (stateDebugCloseBtnEl) {
  stateDebugCloseBtnEl.addEventListener("click", () => {
    closeStateDebugModal();
  });
}

if (stateDebugModalEl) {
  stateDebugModalEl.addEventListener("close", () => {
    stopStateDebugAutoRefresh();
  });
  stateDebugModalEl.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    closeStateDebugModal();
  });
}

if (flowOpenBtnEl) {
  flowOpenBtnEl.addEventListener("click", () => {
    if (!flowPopupWin || flowPopupWin.closed) {
      flowPopupWin = window.open("", "agent-flow-graph", "width=1200,height=900");
      if (!flowPopupWin) return;
      flowPopupWin.document.open();
      flowPopupWin.document.write(buildFlowPopupHtml());
      flowPopupWin.document.close();
      setTimeout(() => {
        syncFlowPopup();
      }, 60);
    } else {
      flowPopupWin.focus();
    }
    syncFlowPopup();
  });
}

if (themeSelectEl) {
  themeSelectEl.addEventListener("change", () => {
    applyTheme(themeSelectEl.value);
  });
}

async function warnIfSessionMissionInFlight() {
  const taskId = getSessionId();
  try {
    const res = await fetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const st = String(data.status || "");
    if (st === "MISSION_RUNNING") {
      sessionHasInFlightMission = true;
      appendLine(
        `Note: session ${taskId.slice(0, 8)}… is MISSION_RUNNING (node ${data.current_node}). ` +
          "运行中输入会进入 steer 接管；暂停后请用普通输入续写。",
        "system"
      );
      updateStopButtonState();
    } else if (st === "MISSION_PAUSED") {
      sessionHasInFlightMission = false;
      appendLine(
        `Note: session ${taskId.slice(0, 8)}… is MISSION_PAUSED (node ${data.current_node}). ` +
          "直接输入「继续写作」等即可，由规划/会话策略理解意图；待确认时用 /confirm，不必先 /resume。",
        "system"
      );
      updateStopButtonState();
    } else {
      sessionHasInFlightMission = false;
      updateStopButtonState();
    }
  } catch {
    /* ignore */
  }
}

updateSessionBadge(getSessionId());
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
appendLine("Agent LangGraph Web CLI ready. Type /help for commands.", "system");
updateStopButtonState();
warnIfSessionMissionInFlight();
fetchHealth();
refreshFlowPanel();
