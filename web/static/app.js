/**
 * Web CLI（/chat）：消费 graph_runner 的 SSE。
 *
 * 请求链：handleCommand → runTaskStream → fetch /tasks/stream → consumeSseStream → handleStreamEvent。
 * 事件：task_created、progress、trace、node、plan、writing_delta、answer_delta、done 等。
 * Steer：/append、/confirm、/resume、/stop；Skill 经 buildTaskRequestBody 与 URL 参数注入。
 *
 * Web CLI SSE client for graph_runner.stream_task.
 * Parses event-stream and updates terminal plus flow-graph sidebar.
 */

const outputEl = document.getElementById("output");
const formEl = document.getElementById("command-form");
const inputEl = document.getElementById("command-input");
const stopBtnEl = document.getElementById("stop-btn");
const runStatusEl = document.getElementById("terminal-run-status");
const sessionBadgeEl = document.getElementById("session-badge");
const flowMetaEl = document.getElementById("flow-meta");
const flowTaskEl = document.getElementById("flow-task");
const flowGraphEl = document.getElementById("flow-graph");
const flowTimelineEl = document.getElementById("flow-timeline");
const flowRefreshBtnEl = document.getElementById("flow-refresh-btn");
const flowOpenBtnEl = document.getElementById("flow-open-btn");
const stateDebugBtnEl = document.getElementById("state-debug-btn");
const historyListEl = document.getElementById("history-list");
const historyRefreshBtnEl = document.getElementById("history-refresh-btn");
const historyNewSessionBtnEl = document.getElementById("history-new-session-btn");
const rightRailPaneEl = document.getElementById("right-rail-pane");
const rightRailTabEl = document.getElementById("right-rail-tab");
const rightRailPinEl = document.getElementById("right-rail-pin");
const rightRailTabHistoryEl = document.getElementById("right-rail-tab-history");
const rightRailTabFilesEl = document.getElementById("right-rail-tab-files");
const historyPanelEl = document.getElementById("history-panel");
const filesPanelEl = document.getElementById("files-panel");
const sessionFilesUpBtnEl = document.getElementById("session-files-up-btn");
const sessionFilesRefreshBtnEl = document.getElementById("session-files-refresh-btn");
const sessionFilesMetaEl = document.getElementById("session-files-meta");
const sessionFilesBreadcrumbEl = document.getElementById("session-files-breadcrumb");
const sessionFilesListEl = document.getElementById("session-files-list");
const stateDebugModalEl = document.getElementById("state-debug-modal");
const stateDebugMetaEl = document.getElementById("state-debug-meta");
const stateDebugKeysEl = document.getElementById("state-debug-keys");
const stateDebugJsonEl = document.getElementById("state-debug-json");
const stateDebugRefreshBtnEl = document.getElementById("state-debug-refresh-btn");
const stateDebugCloseBtnEl = document.getElementById("state-debug-close-btn");
const stateDebugLiveEl = document.getElementById("state-debug-live");
const stateDebugSourceTabsEl = document.getElementById("state-debug-source-tabs");
const themeSelectEl = document.getElementById("theme-select");
const interactionModeSelectEl = document.getElementById("interaction-mode-select");
const commandSlashMenuEl = document.getElementById("command-slash-menu");

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
/** @type {{ toggleEl: HTMLElement, panelEl: HTMLElement, bodyEl: HTMLElement, filename: string, status: string, collapsed: boolean } | null} */
let activeWritingBlock = null;
/** @type {Map<string, { toggleEl: HTMLElement, panelEl: HTMLElement, bodyEl: HTMLElement, filename: string, status: string, collapsed: boolean }>} */
const writingFileBlocks = new Map();
let writingWorkspaceEl = null;
let writingWorkspaceHeaderEl = null;
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
/** 当前任务 SSE 的 AbortController（Stop 时立即断开）。 */
let activeSseAbortController = null;
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
let historyRefreshRunning = false;
let historyRefreshSeq = 0;
let sessionFilesRefreshSeq = 0;
let sessionFilesPollingTimer = null;
const sessionFileViewerMap = new Map();
let sessionFilesCurrentPath = ".";
let sessionFilesTextIndex = [];
/** @type {string | null} `taskId:relativeDir` */
let sessionFilesTextIndexCacheKey = null;
let sessionFileViewerPollTimer = null;
const SESSION_TEXT_FILE_RE =
  /\.(txt|md|markdown|json|yaml|yml|log|cpp|cc|cxx|hpp|h|c|py|js|css|html|htm)$/i;
const CHAPTER_HEADER_RE = /^(?:#{1,3}\s*)?第\s*([一二三四五六七八九十百零两\d]+)\s*章[^\n]*/gm;
const CN_DIGITS = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 百: 100 };
const POPUP_VIEWER_FONT_KEY = "session_file_viewer_font_px";
const POPUP_VIEWER_FONT_MIN = 10;
const POPUP_VIEWER_FONT_MAX = 28;
const POPUP_VIEWER_FONT_DEFAULT = 13;
const COMMAND_SUGGESTIONS = [
  { cmd: "/help", hint: "显示命令帮助" },
  { cmd: "/new", hint: "开启新会话" },
  { cmd: "/clear", hint: "清空终端输出" },
  { cmd: "/confirm", hint: "确认并 resume（confirm:true）" },
  { cmd: "/resume", hint: "继续暂停的任务" },
  { cmd: "/stop", hint: "停止当前任务" },
  { cmd: "/stop-all", hint: "停止所有 in-flight 任务" },
  { cmd: "/append ", hint: "向运行中任务追加消息（不抢占）" },
  { cmd: "/session", hint: "打印当前 session_id" },
  { cmd: "/mode", hint: "查看或切换交互模式 auto|chat|engineering|writing" },
  { cmd: "/history", hint: "在终端列出最近任务" },
  { cmd: "/status ", hint: "查看任务状态（后跟 task_id）" },
  { cmd: "/audit ", hint: "查看审计日志" },
  { cmd: "/result ", hint: "查看任务结果" },
  { cmd: "/approve ", hint: "审批通过" },
  { cmd: "/reject ", hint: "审批拒绝" },
  { cmd: "/supervisor ", hint: "Supervisor 任务" },
  { cmd: "/risk high ", hint: "高风险任务运行" },
  { cmd: "/login ", hint: "登录获取 JWT" },
  { cmd: "/logout", hint: "退出登录" },
];
let slashMenuActiveIndex = -1;
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

function pickFlowHistory(persistedHistory, liveHistory) {
  if (!liveHistory.length) return persistedHistory;
  if (!persistedHistory.length) return liveHistory;
  if (persistedHistory.length > liveHistory.length) return persistedHistory;
  if (liveHistory.length > persistedHistory.length) return liveHistory;
  const pLast = String(persistedHistory[persistedHistory.length - 1]?.at || "");
  const lLast = String(liveHistory[liveHistory.length - 1]?.at || "");
  if (pLast && lLast) return pLast >= lLast ? persistedHistory : liveHistory;
  return persistedHistory;
}

function renderFlowTimeline(data, taskId, { preferStore = false } = {}) {
  if (!flowMetaEl || !flowTaskEl || !flowTimelineEl) return;
  const status = data?.status || "-";
  const node = data?.current_node || "-";
  flowMetaEl.textContent = `状态: ${status} | 当前节点: ${node}`;
  flowTaskEl.textContent = `task: ${(taskId || "-").toString().slice(0, 12)}${taskId ? "…" : ""}`;
  const persistedHistory = Array.isArray(data?.node_history) ? data.node_history : [];
  const liveHistory = flowLiveHistoryByTask.get(taskId) || [];
  const history = preferStore
    ? persistedHistory
    : pickFlowHistory(persistedHistory, liveHistory);
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

async function refreshFlowPanel(taskId = null, { preferStore = false } = {}) {
  if (!flowTimelineEl) return;
  const useTaskId = taskId || activeTaskId || getSessionId();
  if (!useTaskId) return;
  try {
    const res = await fetchWithTimeout(
      `/tasks/${useTaskId}/status`,
      { headers: getAuthHeaders() },
      12000
    );
    if (!res.ok) return;
    const data = await res.json();
    renderFlowTimeline(data, useTaskId, { preferStore });
  } catch (err) {
    if (flowMetaEl) {
      const timeout = err?.name === "AbortError";
      flowMetaEl.textContent = timeout ? "状态拉取超时（Flow）" : "状态拉取失败（Flow）";
    }
  }
}

function ensureFlowAutoRefresh() {
  if (flowAutoRefreshTimer) return;
  flowAutoRefreshTimer = setInterval(() => {
    if (document.hidden) return;
    if (!running && !activeTaskId && !sessionHasInFlightMission) return;
    refreshFlowPanel();
  }, 4500);
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
    const res = await fetchWithTimeout(
      `/tasks/${taskId}/state?truncate=true`,
      { headers: getAuthHeaders() },
      12000
    );
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
  } catch (err) {
    if (stateDebugMetaEl) {
      const timeout = err?.name === "AbortError";
      stateDebugMetaEl.textContent = timeout
        ? `状态拉取超时 (task/session: ${taskId.slice(0, 12)}…)`
        : `状态拉取失败 (task/session: ${taskId.slice(0, 12)}…)`;
    }
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
/** 常规 QA 流水线节点：成功态不刷 `[node] → STATUS`，细节见「推理过程」面板。 */
const PIPELINE_QUIET_NODES = new Set([
  "planning",
  "retrieval",
  "reasoning",
  "policy",
  "output_guard",
  "output",
  "memory_writeback",
]);
const TOKEN_KEY = "agent_access_token";
const SESSION_KEY = "agent_session_id";
const INTERACTION_MODE_KEY = "agent_interaction_mode";
const THEME_KEY = "agent_theme";

/** @type {Record<string, { label: string, hint: string, placeholder: string }>} */
const INTERACTION_MODE_META = {
  auto: {
    label: "自动",
    hint: "由服务端根据 goal 推断 Ask / 工程交付 / 写作（pre_planning）",
    placeholder: "描述任务；模式选「自动」时由系统推断…",
  },
  chat: {
    label: "Ask · 问答",
    hint: "对话与推理为主，代码可走 code_artifact 校验；不落盘工程交付",
    placeholder: "提问、解释、讨论方案…",
  },
  engineering: {
    label: "Agent · 工程交付",
    hint: "会话目录落盘 + 白名单编译/构建（g++/py_compile/make demo/node --check）；禁止任意 shell",
    placeholder: "例如：做 2048 网页游戏并落盘、写可编译的 C++ main.cpp…",
  },
  writing: {
    label: "写作 · 长篇",
    hint: "手稿 / Mission 写作路径；与工程交付隔离",
    placeholder: "例如：续写小说、生成大纲、审阅章节…",
  },
};

const VALID_INTERACTION_MODES = new Set(["auto", "chat", "engineering", "writing"]);
/** Next stream submit uses new_session=true once (after /new). */
let pendingNewSession = false;
let sessionHasInFlightMission = false;
/** True when this process is executing the mission graph (SSE may be disconnected). */
let sessionMissionExecutorActive = false;
const ORCHESTRATION_COMPLETED_DISPLAY_MAX = 8;

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
  sessionBadgeEl.title = `会话 ID（完整）: ${id}\n点击查看；新会话请用侧栏「新会话」或 /new`;
}

function normalizeInteractionMode(raw) {
  const key = String(raw || "auto").trim().toLowerCase();
  return VALID_INTERACTION_MODES.has(key) ? key : "auto";
}

function getInteractionMode() {
  const stored = localStorage.getItem(INTERACTION_MODE_KEY);
  return normalizeInteractionMode(stored || interactionModeSelectEl?.value || "auto");
}

function setInteractionMode(mode, { announce = true, persist = true } = {}) {
  const normalized = normalizeInteractionMode(mode);
  if (persist) localStorage.setItem(INTERACTION_MODE_KEY, normalized);
  syncInteractionModeUi(normalized);
  if (announce) {
    const meta = INTERACTION_MODE_META[normalized];
    appendLine(`交互模式: ${meta.label} — ${meta.hint}`, "system");
  }
  return normalized;
}

function syncInteractionModeUi(mode) {
  const normalized = normalizeInteractionMode(mode);
  if (interactionModeSelectEl) {
    interactionModeSelectEl.value = normalized;
    interactionModeSelectEl.dataset.mode = normalized;
    const meta = INTERACTION_MODE_META[normalized];
    interactionModeSelectEl.title = `${meta.label}\n${meta.hint}`;
  }
  if (inputEl) {
    const ph = INTERACTION_MODE_META[normalized]?.placeholder;
    if (ph) inputEl.placeholder = ph;
  }
}

function applyInteractionModeToPayload(payload) {
  const out = { ...(payload || {}) };
  const mode = getInteractionMode();
  if (mode !== "auto") {
    out.interaction_mode = mode;
  } else {
    delete out.interaction_mode;
  }
  if (mode === "writing") {
    out.mission_auto = true;
  } else if (mode === "engineering" || mode === "chat") {
    out.mission_auto = false;
    out.disable_mission_auto = mode === "engineering";
  }
  return out;
}

function showSessionIdInfo() {
  const id = getSessionId();
  appendLine(`session_id: ${id}`, "system");
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(id).then(
      () => appendLine("（session_id 已复制到剪贴板）", "system"),
      () => {}
    );
  }
}

function applyTheme(theme) {
  if (window.PlatformAuth) {
    window.PlatformAuth.applyTheme(theme);
    syncFlowPopup();
    for (const [, view] of sessionFileViewerMap) {
      if (view?.win && !view.win.closed) syncSessionFileViewerTheme(view.win);
    }
    return;
  }
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.classList.remove("theme-light", "theme-dark");
  document.documentElement.classList.add(`theme-${t}`);
  document.body.classList.remove("theme-light", "theme-dark");
  document.body.classList.add(`theme-${t}`);
  if (themeSelectEl) themeSelectEl.value = t;
  localStorage.setItem(THEME_KEY, t);
  syncFlowPopup();
  for (const [, view] of sessionFileViewerMap) {
    if (view?.win && !view.win.closed) syncSessionFileViewerTheme(view.win);
  }
}

function requestNewSession(sourceLabel) {
  if (running && activeTaskId) {
    appendLine("当前有任务运行中，无法新建会话。请先 Stop 或 /stop。", "error");
    return null;
  }
  sessionHasInFlightMission = false;
  return startNewSession(sourceLabel);
}

function startNewSession(sourceLabel) {
  const id = newSessionId();
  localStorage.setItem(SESSION_KEY, id);
  pendingNewSession = true;
  activeTaskId = null;
  sessionFilesCurrentPath = ".";
  updateSessionBadge(id);
  clearScreen();
  const via = sourceLabel ? ` (${sourceLabel})` : "";
  appendLine(`new session: ${id.slice(0, 8)}… (server task isolated)${via}`, "system");
  refreshFlowPanel(id);
  refreshHistorySidebar();
  refreshSessionFilesPane();
  skillsRailEnabled.clear();
  activeSkillId = null;
  loadSkillsRailState();
  syncSkillHeaderUi();
  loadSkillInputForm(null);
  renderSkillsRailList();
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
  if (window.PlatformAuth) return window.PlatformAuth.getAuthHeaders();
  const headers = { "Content-Type": "application/json", Accept: "application/json" };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  const role = localStorage.getItem("user_role");
  if (role) headers["X-User-Role"] = role;
  const tenant = localStorage.getItem("tenant_id");
  if (tenant) headers["X-Tenant-Id"] = tenant;
  return headers;
}

async function apiFetch(url, options = {}) {
  if (window.PlatformAuth?.authFetch) {
    return window.PlatformAuth.authFetch(url, options);
  }
  return fetch(url, {
    ...options,
    headers: { ...getAuthHeaders(), ...(options.headers || {}) },
  });
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await apiFetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Liveness before SSE POST. Prefer /health/live (no DB/MCP probes).
 * Retries cover uvicorn --reload gaps after hot deploy (~2–8s).
 */
async function probeRuntimeHealth(timeoutMs = 4000, maxAttempts = 4) {
  const paths = ["/health/live", "/health"];
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    for (const path of paths) {
      try {
        const res = await fetchWithTimeout(path, { method: "GET" }, timeoutMs);
        if (res.ok) return true;
      } catch {
        /* try next path / attempt */
      }
    }
    if (attempt < maxAttempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, 600 * (attempt + 1)));
    }
  }
  return false;
}

function formatStreamFetchError(err) {
  const msg = String(err?.message || err || "unknown");
  if (!msg.includes("Failed to fetch")) {
    return `stream error: ${msg}`;
  }
  const origin = window.location.origin || "(当前页面)";
  const lines = [
    `stream error: ${msg}`,
    "浏览器在收到 HTTP 响应前断开（多为服务未启动、TLS/证书、或网络不可达）。",
    `排查：在部署机执行 make ps && make logs；curl -sk ${origin}/health 应返回 200。`,
    "若用局域网 IP 访问，请把该 IP 写入 .env 的 PUBLIC_DOMAIN 后 make up，并在浏览器接受自签证书。",
    "写作/工程/自动模式均走 POST /tasks/stream；若仅写作失败，请打开 DevTools → Network 查看该请求。",
    "热更新后请等几秒再发首条消息，或 curl -sk …/health/live（比 /health 更快）。",
    "「新会话」本身不改网络，只是多等几秒或清屏后重试；旧 session_id 不会导致 /health 失败。",
  ];
  return lines.join("\n");
}

function formatHistoryTime(raw) {
  if (!raw) return "-";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return String(raw);
  return d.toLocaleString();
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}

function selectHistorySession(taskId) {
  if (!taskId) return;
  if (getSessionId() === taskId) {
    requestNewSession("再次点击当前会话");
    return;
  }
  if (running && activeTaskId && activeTaskId !== taskId) {
    appendLine("当前有任务运行中，无法切换会话。请先 /stop。", "error");
    return;
  }
  localStorage.setItem(SESSION_KEY, taskId);
  sessionFilesCurrentPath = ".";
  updateSessionBadge(taskId);
  activeTaskId = null;
  sessionHasInFlightMission = false;
  clearScreen();
  appendLine(`switched session: ${taskId.slice(0, 8)}…`, "system");
  refreshFlowPanel(taskId);
  refreshHistorySidebar();
  refreshSessionFilesPane();
}

async function deleteHistoryTask(taskId) {
  if (!taskId) return;
  if (running && activeTaskId === taskId) {
    appendLine("当前会话正在运行，无法删除。请先停止任务。", "error");
    return;
  }
  const confirmed = window.confirm(`确认删除会话 ${taskId.slice(0, 8)}… ?`);
  if (!confirmed) return;
  try {
    const res = await apiFetch(`/tasks/${taskId}`, {
      method: "DELETE",
      headers: getAuthHeaders(),
    });
    if (!res.ok) {
      appendLine(`delete failed: ${res.status}`, "error");
      return;
    }
    const current = getSessionId();
    if (current === taskId) {
      startNewSession();
      sessionHasInFlightMission = false;
      activeTaskId = null;
      sessionFilesCurrentPath = ".";
    }
    appendLine(`deleted session: ${taskId.slice(0, 8)}…`, "system");
    await refreshHistorySidebar();
    await refreshSessionFilesPane();
  } catch (err) {
    appendLine(`delete error: ${err}`, "error");
  }
}

function renderHistorySidebarError(msg) {
  if (!historyListEl) return;
  historyListEl.innerHTML = `<p class="flow-empty">${escapeHtml(msg)}</p>`;
}

async function refreshHistorySidebar() {
  if (!historyListEl || historyRefreshRunning) return;
  historyRefreshRunning = true;
  const seq = ++historyRefreshSeq;
  try {
    historyListEl.innerHTML = '<p class="flow-empty">历史会话加载中…</p>';
    const res = await apiFetch("/tasks?limit=50", { headers: getAuthHeaders() });
    if (seq !== historyRefreshSeq) return;
    if (!res.ok) {
      if (res.status === 429) {
        renderHistorySidebarError("请求过快（429），请稍后重试。");
      } else {
        renderHistorySidebarError("历史会话加载失败。");
      }
      return;
    }
    const data = await res.json();
    if (seq !== historyRefreshSeq) return;
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];
    if (!tasks.length) {
      historyListEl.innerHTML = `<p class="flow-empty">暂无历史会话。</p>
        <button type="button" class="flow-btn flow-btn-primary history-new-inline" id="history-new-session-inline">开启新会话</button>`;
      return;
    }
    const current = getSessionId();
    historyListEl.innerHTML = tasks
      .map((task) => {
        const id = String(task.task_id || "");
        const status = String(task.status || "-");
        const goal = String(task.goal || "");
        const updated = formatHistoryTime(task.updated_at);
        const currentCls = current === id ? " current" : "";
        return `<div class="history-item${currentCls}" data-history-task="${escapeAttr(id)}">
          <div class="history-item-head">
            <span class="history-item-id">${escapeHtml(id.slice(0, 8))}…</span>
            <span class="history-item-status">${escapeHtml(status)}</span>
          </div>
          <div class="history-item-goal">${escapeHtml(goal || "(no goal)")}</div>
          <div class="history-item-time">${escapeHtml(updated)}</div>
          <div class="history-item-actions">
            <button type="button" class="history-delete-btn" data-history-delete="${escapeAttr(id)}">删除</button>
          </div>
        </div>`;
      })
      .join("");
  } catch {
    if (seq === historyRefreshSeq) renderHistorySidebarError("历史会话加载失败。");
  } finally {
    if (seq === historyRefreshSeq) historyRefreshRunning = false;
  }
}

function getSessionFilesTaskId() {
  return activeTaskId || getSessionId();
}

function updateSessionFilesMeta(text) {
  if (!sessionFilesMetaEl) return;
  sessionFilesMetaEl.textContent = text;
}

function renderSessionFilesBreadcrumb() {
  if (!sessionFilesBreadcrumbEl) return;
  const path = String(sessionFilesCurrentPath || ".").trim() || ".";
  const parts = path === "." ? [] : path.split("/").filter(Boolean);
  const crumbs = [{ label: ".", path: "." }];
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    crumbs.push({ label: part, path: acc });
  }
  sessionFilesBreadcrumbEl.innerHTML = crumbs
    .map((c, idx) => {
      const isCurrent = idx === crumbs.length - 1;
      const sep = idx < crumbs.length - 1 ? '<span class="session-breadcrumb-sep">/</span>' : "";
      return `<button type="button" class="session-breadcrumb-btn${isCurrent ? " current" : ""}" data-breadcrumb-path="${escapeAttr(c.path)}">${escapeHtml(c.label)}</button>${sep}`;
    })
    .join("");
}

let rightRailPinned = false;
const RIGHT_RAIL_PINNED_KEY = "chat_right_rail_pinned";
const RIGHT_RAIL_PANEL_KEY = "chat_right_rail_panel";

function showRightRailPanel(panelName) {
  const name = panelName === "files" ? "files" : "history";
  const pairs = [
    { panel: historyPanelEl, tab: rightRailTabHistoryEl, id: "history" },
    { panel: filesPanelEl, tab: rightRailTabFilesEl, id: "files" },
  ];
  for (const { panel, tab, id } of pairs) {
    const on = id === name;
    if (panel) {
      panel.classList.toggle("is-active", on);
      panel.hidden = !on;
    }
    if (tab) {
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    }
  }
  try {
    sessionStorage.setItem(RIGHT_RAIL_PANEL_KEY, name);
  } catch {
    /* ignore */
  }
  if (name === "files") {
    refreshSessionFilesPane();
  }
}

function syncRightRailPinUi() {
  if (!rightRailPinEl) return;
  rightRailPinEl.textContent = rightRailPinned ? "取消固定" : "固定";
  rightRailPinEl.title = rightRailPinned ? "取消固定展开，恢复为悬停展开" : "固定展开侧栏";
  rightRailPinEl.setAttribute("aria-pressed", rightRailPinned ? "true" : "false");
}

function setRightRailPinned(pinned) {
  rightRailPinned = Boolean(pinned);
  if (rightRailPaneEl) {
    rightRailPaneEl.classList.toggle("expanded", rightRailPinned);
  }
  syncRightRailPinUi();
  try {
    sessionStorage.setItem(RIGHT_RAIL_PINNED_KEY, rightRailPinned ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function loadRightRailState() {
  try {
    rightRailPinned = sessionStorage.getItem(RIGHT_RAIL_PINNED_KEY) === "1";
    if (rightRailPaneEl) {
      rightRailPaneEl.classList.toggle("expanded", rightRailPinned);
    }
    const panel = sessionStorage.getItem(RIGHT_RAIL_PANEL_KEY);
    if (panel === "files" || panel === "history") {
      showRightRailPanel(panel);
    }
  } catch {
    /* ignore */
  }
  syncRightRailPinUi();
}

function initRightRail() {
  loadRightRailState();
  if (rightRailTabEl && rightRailPaneEl) {
    rightRailTabEl.addEventListener("click", () => {
      setRightRailPinned(!rightRailPinned);
    });
  }
  if (rightRailPinEl && rightRailPaneEl) {
    rightRailPinEl.addEventListener("click", (e) => {
      e.stopPropagation();
      setRightRailPinned(!rightRailPinned);
    });
  }
  if (rightRailTabHistoryEl) {
    rightRailTabHistoryEl.addEventListener("click", () => showRightRailPanel("history"));
  }
  if (rightRailTabFilesEl) {
    rightRailTabFilesEl.addEventListener("click", () => showRightRailPanel("files"));
  }
}

/** @deprecated use showRightRailPanel('files') */
function setSessionFilesCollapsed(collapsed) {
  if (!collapsed) {
    showRightRailPanel("files");
    if (!rightRailPinned && rightRailPaneEl) {
      rightRailPaneEl.classList.add("expanded");
    }
  }
}

function buildSessionFileViewerHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"/><link rel="icon" href="/static/favicon.svg" type="image/svg+xml"/><title>会话文件</title>
  <style>
    html,body{height:100%;margin:0;overflow:hidden}
    body{display:flex;flex-direction:column;background:#0d1117;color:#c9d1d9;font-family:ui-monospace,Menlo,Consolas,monospace}
    body.theme-light{background:#f6f8fb;color:#1f2937}
    .head{flex-shrink:0;padding:10px 12px;border-bottom:1px solid #30363d;background:#010409}
    .title{margin:0;font-size:12px;color:#93c5fd;word-break:break-all}
    .meta{margin:4px 0 0;font-size:11px;color:#8b949e}
    .toolbar{margin-top:8px;display:flex;flex-direction:column;gap:6px}
    .toolbar-row{display:flex;gap:6px;align-items:center;flex-wrap:nowrap}
    .toolbar-controls{justify-content:flex-start}
    .btn,.sel{border:1px solid #4b5563;background:#0d1117;color:#cbd5e1;border-radius:6px;padding:4px 8px;font:inherit;font-size:11px;cursor:pointer;box-sizing:border-box}
    .btn:hover{background:rgba(88,166,255,.12)}
    .btn-nav{min-width:28px;width:28px;padding-inline:0;flex-shrink:0;text-align:center}
    .sel{min-width:0;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .sel-file,.sel-chapter{flex:1 1 0;width:0}
    .sel:disabled{opacity:.5;cursor:not-allowed}
    .nav-group,.font-group{display:flex;gap:4px;align-items:center;flex-shrink:0}
    .chapter-now-wrap{display:flex;align-items:center;gap:4px;flex:0 0 192px;width:192px;min-width:192px;overflow:hidden}
    .chapter-now-label{flex-shrink:0;font-size:11px;color:#8b949e}
    .chapter-now{flex:1;min-width:0;font-size:11px;color:#fbbf24;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .chapter-now.is-empty{color:#6b7280}
    .font-size-label{font-size:11px;color:#8b949e;min-width:2.5em;text-align:center;flex-shrink:0}
    .save-btn{flex-shrink:0;margin-left:auto}
    .body-wrap{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
    textarea{flex:1;min-height:0;width:100%;height:100%;padding:12px;border:none;outline:none;background:transparent;color:inherit;font:inherit;line-height:1.55;resize:none;overflow-y:auto;overscroll-behavior:contain;box-sizing:border-box;white-space:pre-wrap;word-wrap:break-word}
    body.theme-light .head{background:#e2e8f0;border-bottom-color:#cbd5e1}
    body.theme-light .sel{background:#fff}
  </style></head><body>
    <div class="head">
      <p id="title" class="title">-</p>
      <p id="m" class="meta">loading…</p>
      <div class="toolbar">
        <div class="toolbar-row">
          <select id="file-switcher" class="sel sel-file" title="切换同会话文件"><option value="">文件…</option></select>
          <select id="chapter-jump" class="sel sel-chapter" title="章节跳转" disabled><option value="">章节</option></select>
        </div>
        <div class="toolbar-row toolbar-controls">
          <div class="nav-group">
            <button id="prev-btn" class="btn btn-nav" type="button" title="上一章">‹</button>
            <button id="next-btn" class="btn btn-nav" type="button" title="下一章">›</button>
          </div>
          <span class="chapter-now-wrap" title="当前阅读位置">
            <span class="chapter-now-label">当前</span>
            <span id="current-chapter" class="chapter-now is-empty">-</span>
          </span>
          <div class="font-group">
            <button id="font-down-btn" class="btn btn-nav" type="button" title="缩小字体">A-</button>
            <button id="font-up-btn" class="btn btn-nav" type="button" title="放大字体">A+</button>
            <span id="font-size-label" class="font-size-label">13px</span>
          </div>
          <button id="save-btn" class="btn save-btn" type="button">保存</button>
        </div>
      </div>
    </div>
    <div class="body-wrap"><textarea id="content" spellcheck="false"></textarea></div>
  </body></html>`;
}

function syncSessionFileViewerTheme(win) {
  if (!win || win.closed) return;
  try {
    const currentTheme = document.body.classList.contains("theme-light") ? "theme-light" : "theme-dark";
    win.document.body.classList.remove("theme-light", "theme-dark");
    win.document.body.classList.add(currentTheme);
  } catch {
    /* ignore */
  }
}

function loadPopupViewerFontSize() {
  try {
    const raw = Number(localStorage.getItem(POPUP_VIEWER_FONT_KEY));
    if (!Number.isFinite(raw)) return POPUP_VIEWER_FONT_DEFAULT;
    return Math.min(POPUP_VIEWER_FONT_MAX, Math.max(POPUP_VIEWER_FONT_MIN, Math.round(raw)));
  } catch {
    return POPUP_VIEWER_FONT_DEFAULT;
  }
}

function savePopupViewerFontSize(size) {
  try {
    localStorage.setItem(POPUP_VIEWER_FONT_KEY, String(size));
  } catch {
    /* ignore */
  }
}

function applyPopupViewerFontSize(view, sizePx) {
  const doc = popupViewerDoc(view);
  const area = doc?.getElementById("content");
  const label = doc?.getElementById("font-size-label");
  const px = Math.min(POPUP_VIEWER_FONT_MAX, Math.max(POPUP_VIEWER_FONT_MIN, Math.round(sizePx)));
  view.fontSize = px;
  if (area) area.style.fontSize = `${px}px`;
  if (label) label.textContent = `${px}px`;
  savePopupViewerFontSize(px);
}

function bumpPopupViewerFontSize(view, delta) {
  const current = view.fontSize || loadPopupViewerFontSize();
  applyPopupViewerFontSize(view, current + delta);
  updatePopupCurrentChapter(view);
}

function charOffsetAtScrollTop(textarea) {
  if (!textarea) return 0;
  const style = textarea.ownerDocument.defaultView.getComputedStyle(textarea);
  const lineHeight = parseFloat(style.lineHeight) || 20;
  const paddingTop = parseFloat(style.paddingTop) || 0;
  const firstVisibleLine = Math.max(0, Math.floor(Math.max(0, textarea.scrollTop - paddingTop) / lineHeight));
  const text = String(textarea.value || "");
  if (!text || firstVisibleLine === 0) return 0;
  let line = 0;
  for (let i = 0; i < text.length; i++) {
    if (line >= firstVisibleLine) return i;
    if (text.charCodeAt(i) === 10) line += 1;
  }
  return text.length;
}

function chapterAtOffset(markers, offset) {
  if (!markers?.length) return null;
  let current = null;
  for (const marker of markers) {
    if (marker.offset <= offset) current = marker;
    else break;
  }
  return current;
}

function updatePopupCurrentChapter(view) {
  const doc = popupViewerDoc(view);
  const area = doc?.getElementById("content");
  const el = doc?.getElementById("current-chapter");
  const wrap = doc?.querySelector(".chapter-now-wrap");
  if (!el) return;
  const markers = view.chapterMarkers || [];
  if (!markers.length) {
    el.textContent = "-";
    el.classList.add("is-empty");
    if (wrap) wrap.title = "未识别到章节标题";
    el.title = "未识别到章节标题";
    return;
  }
  const offset = area ? charOffsetAtScrollTop(area) : 0;
  const current = chapterAtOffset(markers, offset);
  if (!current) {
    el.textContent = "序文";
    el.classList.remove("is-empty");
    if (wrap) wrap.title = "位于第一章之前";
    el.title = "位于第一章之前";
    return;
  }
  const label = current.label || `第${current.chapter}章`;
  el.textContent = label;
  el.classList.remove("is-empty");
  if (wrap) wrap.title = label;
  el.title = label;
}

function popupViewerDoc(view) {
  if (!view?.win || view.win.closed) return null;
  try {
    return view.win.document;
  } catch {
    return null;
  }
}

function renderPopupFileSwitcher(view) {
  const doc = popupViewerDoc(view);
  const sel = doc?.getElementById("file-switcher");
  if (!sel) return;
  const paths = (view.fileIndex || []).slice().sort((a, b) => a.localeCompare(b));
  sel.innerHTML =
    '<option value="">文件…</option>' +
    paths
      .map((path) => {
        const selected = path === view.path ? " selected" : "";
        const label = path.includes("/") ? path.split("/").pop() : path;
        return `<option value="${escapeAttr(path)}"${selected}>${escapeHtml(label || path)}</option>`;
      })
      .join("");
  syncPopupViewerToolbar(view);
}

function syncPopupViewerToolbar(view) {
  const doc = popupViewerDoc(view);
  if (!doc) return;
  const paths = view.fileIndex || [];
  const markers = view.chapterMarkers || [];
  const multiChapter = markers.length > 1;
  const fileSel = doc.getElementById("file-switcher");
  const chapterSel = doc.getElementById("chapter-jump");
  const fileRow = fileSel?.closest(".toolbar-row");
  const navGroup = doc.querySelector(".nav-group");
  const chapterNowWrap = doc.querySelector(".chapter-now-wrap");
  if (fileSel) fileSel.hidden = paths.length <= 1;
  if (chapterSel) chapterSel.hidden = !multiChapter;
  if (fileRow) fileRow.hidden = paths.length <= 1 && !multiChapter;
  if (navGroup) navGroup.hidden = !multiChapter;
  if (chapterNowWrap) chapterNowWrap.hidden = !multiChapter;
}

function renderPopupChapterJump(view, markers) {
  const doc = popupViewerDoc(view);
  const sel = doc?.getElementById("chapter-jump");
  if (!sel) return;
  view.chapterMarkers = markers.slice();
  if (!markers.length) {
    sel.innerHTML = '<option value="">章节</option>';
    sel.disabled = true;
    syncPopupViewerToolbar(view);
    return;
  }
  sel.disabled = false;
  sel.innerHTML =
    '<option value="">章节</option>' +
    markers
      .map((m) => `<option value="${String(m.offset)}">${escapeHtml(m.label || `第${m.chapter}章`)}</option>`)
      .join("");
  updatePopupCurrentChapter(view);
  syncPopupViewerToolbar(view);
}

function lineStartOffsetAt(text, offset) {
  const pos = Math.max(0, Math.min(Number(offset) || 0, text.length));
  let i = pos - 1;
  while (i >= 0 && text.charCodeAt(i) !== 10) i -= 1;
  return i + 1;
}

/** scrollTop so the line containing `charOffset` aligns with the top of the textarea viewport. */
function measureTextareaScrollTopForLineTop(area, charOffset) {
  const text = String(area.value || "");
  const lineStart = lineStartOffsetAt(text, charOffset);
  const win = area.ownerDocument.defaultView;
  const cs = win.getComputedStyle(area);
  const paddingLeft = parseFloat(cs.paddingLeft) || 0;
  const paddingRight = parseFloat(cs.paddingRight) || 0;
  const innerWidth = Math.max(1, area.clientWidth - paddingLeft - paddingRight);

  const mirror = area.ownerDocument.createElement("div");
  mirror.setAttribute("aria-hidden", "true");
  Object.assign(mirror.style, {
    position: "absolute",
    left: "-99999px",
    top: "0",
    visibility: "hidden",
    overflow: "hidden",
    whiteSpace: "pre-wrap",
    wordWrap: "break-word",
    width: `${innerWidth}px`,
    font: cs.font,
    fontSize: cs.fontSize,
    fontFamily: cs.fontFamily,
    lineHeight: cs.lineHeight,
    letterSpacing: cs.letterSpacing,
    tabSize: cs.tabSize,
    padding: "0",
    margin: "0",
    border: "0",
    boxSizing: "content-box",
  });
  mirror.textContent = text.slice(0, lineStart);
  area.ownerDocument.body.appendChild(mirror);
  const beforeHeight = mirror.offsetHeight;
  mirror.remove();
  const maxScroll = Math.max(0, area.scrollHeight - area.clientHeight);
  return Math.min(Math.max(0, beforeHeight), maxScroll);
}

/** Scroll so the chapter line (offset) sits flush at the top of the visible area. */
function scrollPopupViewerToOffset(view, offset) {
  const doc = popupViewerDoc(view);
  const area = doc?.getElementById("content");
  if (!area) return;
  const text = String(area.value || "");
  const pos = Math.max(0, Math.min(Number(offset) || 0, text.length));
  const lineStart = lineStartOffsetAt(text, pos);
  const desired = measureTextareaScrollTopForLineTop(area, lineStart);

  const applyScroll = () => {
    area.scrollTop = desired;
  };

  area.focus();
  applyScroll();
  area.setSelectionRange(lineStart, lineStart);
  applyScroll();
  view.win.requestAnimationFrame(() => {
    if (!area.isConnected) return;
    applyScroll();
    updatePopupCurrentChapter(view);
  });
  updatePopupCurrentChapter(view);
}

function formatPopupViewerUpdatedAt(view) {
  if (view?.lastMtimeMs) {
    return new Date(view.lastMtimeMs).toLocaleTimeString();
  }
  return new Date().toLocaleTimeString();
}

function updatePopupViewerMeta(view, extra = "") {
  const doc = popupViewerDoc(view);
  const metaEl = doc?.getElementById("m");
  const contentEl = doc?.getElementById("content");
  const titleEl = doc?.getElementById("title");
  const path = view.path || "-";
  const titleLabel = path.includes("/") ? path.split("/").pop() : path;
  if (titleEl) titleEl.textContent = titleLabel || path;
  if (!metaEl || !contentEl) return;
  const total = view.totalChars != null ? view.totalChars : contentEl.value.length;
  const dirtyFlag = view.dirty ? " · 未保存" : "";
  metaEl.textContent = `chars ${contentEl.value.length}/${total} · updated ${formatPopupViewerUpdatedAt(view)}${dirtyFlag}${extra ? ` · ${extra}` : ""}`;
}

function updateSessionFileViewer(taskId, data) {
  const view = sessionFileViewerMap.get(taskId);
  if (!view || !view.win || view.win.closed) return;
  try {
    syncSessionFileViewerTheme(view.win);
    const doc = popupViewerDoc(view);
    const contentEl = doc?.getElementById("content");
    if (!contentEl) return;
    const current = String(data?.content || "");
    const mtimeMs = Number(data?.mtime_ms) || 0;
    if (mtimeMs) view.lastMtimeMs = mtimeMs;
    if (!view.dirty) {
      const unchanged =
        current === view.lastContent && (!mtimeMs || mtimeMs === view.lastSyncedMtimeMs);
      if (!unchanged) {
        const scrollTop = contentEl.scrollTop;
        const selStart = contentEl.selectionStart;
        const selEnd = contentEl.selectionEnd;
        contentEl.value = current;
        view.lastContent = current;
        if (mtimeMs) view.lastSyncedMtimeMs = mtimeMs;
        contentEl.scrollTop = scrollTop;
        try {
          contentEl.setSelectionRange(selStart, selEnd);
        } catch {
          /* ignore */
        }
        renderPopupChapterJump(view, parseChapterHeadings(current));
      }
    }
    view.totalChars = Number(data?.total_chars || current.length || 0);
    updatePopupViewerMeta(view);
    updatePopupCurrentChapter(view);
    syncPopupViewerToolbar(view);
  } catch {
    /* ignore */
  }
}

/** 预览窗内容由右侧文件列表轮询顺带刷新，避免与 startSessionFilesPolling 双重打 API。 */
function syncSessionFileViewerPolling() {
  if (sessionFileViewerPollTimer) clearInterval(sessionFileViewerPollTimer);
  sessionFileViewerPollTimer = null;
}

function pathsMatchSessionFile(viewPath, eventName) {
  const filePath = String(viewPath || "").trim();
  const name = String(eventName || "").trim();
  if (!filePath || !name) return false;
  if (filePath === name) return true;
  return filePath.endsWith(`/${name}`);
}

function maybeRefreshOpenFileViewer(filename) {
  const taskId = activeTaskId || getSessionId();
  const view = sessionFileViewerMap.get(taskId);
  if (!view || !view.win || view.win.closed || view.dirty || !view.path) return;
  if (!pathsMatchSessionFile(view.path, filename)) return;
  refreshSessionFileViewer(taskId);
}

async function refreshSessionFileViewer(taskId) {
  const view = sessionFileViewerMap.get(taskId);
  if (!view || !view.taskId || !view.path || !view.win || view.win.closed) {
    sessionFileViewerMap.delete(taskId);
    syncSessionFileViewerPolling();
    return;
  }
  try {
    const params = new URLSearchParams({ path: view.path, offset: "0", max_chars: "200000" });
    const res = await fetchWithTimeout(
      `/tasks/${view.taskId}/files/content?${params.toString()}`,
      { headers: getAuthHeaders() },
      12000
    );
    if (!res.ok) return;
    const data = await res.json();
    updateSessionFileViewer(taskId, data);
  } catch {
    const doc = popupViewerDoc(view);
    const metaEl = doc?.getElementById("m");
    if (metaEl) metaEl.textContent = "文件读取超时/失败";
  }
}

async function loadSessionFileInViewer(view, relativePath) {
  if (!view || !relativePath) return;
  if (view.dirty && view.path !== relativePath) {
    const ok = view.win.confirm("当前文件有未保存修改，切换将丢弃。继续？");
    if (!ok) {
      renderPopupFileSwitcher(view);
      return;
    }
    view.dirty = false;
  }
  view.path = relativePath;
  view.lastContent = "";
  view.lastMtimeMs = 0;
  view.lastSyncedMtimeMs = 0;
  view.totalChars = null;
  const dir = parentSessionPath(relativePath);
  ensureSessionFilesTextIndex(view.taskId, dir).then((idx) => {
    view.fileIndex = idx;
    renderPopupFileSwitcher(view);
  });
  const doc = popupViewerDoc(view);
  const contentEl = doc?.getElementById("content");
  const metaEl = doc?.getElementById("m");
  if (contentEl) contentEl.value = "";
  if (metaEl) metaEl.textContent = "loading…";
  renderPopupFileSwitcher(view);
  renderPopupChapterJump(view, []);
  updatePopupCurrentChapter(view);
  try {
    view.win.document.title = relativePath;
  } catch {
    /* ignore */
  }
  await refreshSessionFileViewer(view.taskId);
}

function navigatePopupChapter(view, step) {
  const doc = popupViewerDoc(view);
  const area = doc?.getElementById("content");
  const markers = view.chapterMarkers || [];
  if (!area || !markers.length) return;

  const offset = charOffsetAtScrollTop(area);
  const current = chapterAtOffset(markers, offset);
  const idx = current ? markers.findIndex((m) => m.offset === current.offset) : -1;

  if (step > 0) {
    if (idx < 0) {
      scrollPopupViewerToOffset(view, markers[0].offset);
      return;
    }
    const next = markers[idx + 1];
    if (next) scrollPopupViewerToOffset(view, next.offset);
    return;
  }

  if (idx <= 0) {
    scrollPopupViewerToOffset(view, 0);
    return;
  }
  scrollPopupViewerToOffset(view, markers[idx - 1].offset);
}

function wireSessionFileViewerWindow(view) {
  const doc = popupViewerDoc(view);
  if (!doc) return;
  const contentEl = doc.getElementById("content");
  const saveBtn = doc.getElementById("save-btn");
  const fileSwitcher = doc.getElementById("file-switcher");
  const chapterJump = doc.getElementById("chapter-jump");
  const prevBtn = doc.getElementById("prev-btn");
  const nextBtn = doc.getElementById("next-btn");
  const fontDownBtn = doc.getElementById("font-down-btn");
  const fontUpBtn = doc.getElementById("font-up-btn");

  applyPopupViewerFontSize(view, view.fontSize || loadPopupViewerFontSize());

  let chapterScrollTimer = null;
  const scheduleChapterUpdate = () => {
    if (chapterScrollTimer) return;
    chapterScrollTimer = view.win.setTimeout(() => {
      chapterScrollTimer = null;
      updatePopupCurrentChapter(view);
    }, 80);
  };

  if (contentEl) {
    contentEl.addEventListener("input", () => {
      view.dirty = true;
      updatePopupViewerMeta(view);
      renderPopupChapterJump(view, parseChapterHeadings(contentEl.value));
    });
    contentEl.addEventListener("scroll", scheduleChapterUpdate, { passive: true });
    contentEl.addEventListener("keyup", scheduleChapterUpdate);
    contentEl.addEventListener("click", scheduleChapterUpdate);
    contentEl.addEventListener("wheel", (ev) => {
      if (ev.ctrlKey || ev.metaKey) {
        ev.preventDefault();
        bumpPopupViewerFontSize(view, ev.deltaY < 0 ? 1 : -1);
        return;
      }
      ev.stopPropagation();
    }, { passive: false });
  }
  if (fontDownBtn) {
    fontDownBtn.addEventListener("click", () => bumpPopupViewerFontSize(view, -1));
  }
  if (fontUpBtn) {
    fontUpBtn.addEventListener("click", () => bumpPopupViewerFontSize(view, 1));
  }
  doc.addEventListener("keydown", (ev) => {
    if (!(ev.ctrlKey || ev.metaKey)) return;
    if (ev.key === "=" || ev.key === "+") {
      ev.preventDefault();
      bumpPopupViewerFontSize(view, 1);
    } else if (ev.key === "-") {
      ev.preventDefault();
      bumpPopupViewerFontSize(view, -1);
    }
  });
  if (fileSwitcher) {
    fileSwitcher.addEventListener("change", () => {
      const nextPath = String(fileSwitcher.value || "").trim();
      if (!nextPath || nextPath === view.path) return;
      loadSessionFileInViewer(view, nextPath);
    });
  }
  if (chapterJump) {
    chapterJump.addEventListener("change", () => {
      const raw = String(chapterJump.value || "").trim();
      if (!raw) return;
      scrollPopupViewerToOffset(view, raw);
      chapterJump.value = "";
    });
  }
  if (prevBtn) {
    prevBtn.addEventListener("click", () => navigatePopupChapter(view, -1));
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", () => navigatePopupChapter(view, 1));
  }
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const area = doc.getElementById("content");
      const meta = doc.getElementById("m");
      if (!area) return;
      try {
        const res = await apiFetch(`/tasks/${view.taskId}/files/content`, {
          method: "PUT",
          headers: getAuthHeaders(),
          body: JSON.stringify({ path: view.path, content: area.value, append: false }),
        });
        if (!res.ok) {
          if (meta) {
            meta.textContent =
              res.status === 401 ? "保存失败: 请重新登录" : `保存失败: ${res.status}`;
          }
          return;
        }
        view.dirty = false;
        view.lastContent = area.value;
        updatePopupViewerMeta(view, "已保存");
        refreshSessionFilesPane({ silent: true });
      } catch {
        if (meta) meta.textContent = "保存失败: network";
      }
    });
  }
  updatePopupCurrentChapter(view);
}

async function openSessionFileViewer(taskId, relativePath) {
  const key = taskId;
  let view = sessionFileViewerMap.get(key);
  if (view && view.win && !view.win.closed) {
    view.win.focus();
    const dir = parentSessionPath(relativePath);
    view.fileIndex = await ensureSessionFilesTextIndex(taskId, dir);
    await loadSessionFileInViewer(view, relativePath);
    syncSessionFileViewerPolling();
    return;
  }

  const win = window.open("", `session-file-${encodeURIComponent(taskId)}`, "width=980,height=760");
  if (!win) {
    appendLine("预览窗口被浏览器拦截，请允许弹窗。", "error");
    return;
  }
  win.document.open();
  win.document.write(buildSessionFileViewerHtml());
  win.document.close();

  const dir = parentSessionPath(relativePath);
  const fileIndex = await ensureSessionFilesTextIndex(taskId, dir);
  view = {
    win,
    taskId,
    path: relativePath,
    dirty: false,
    lastContent: "",
    lastMtimeMs: 0,
    lastSyncedMtimeMs: 0,
    fileIndex,
    chapterMarkers: [],
    totalChars: null,
    fontSize: loadPopupViewerFontSize(),
  };
  sessionFileViewerMap.set(key, view);
  wireSessionFileViewerWindow(view);
  syncSessionFileViewerTheme(win);
  await loadSessionFileInViewer(view, relativePath);
  syncSessionFileViewerPolling();
}

function joinSessionPath(base, child) {
  const b = String(base || ".").trim();
  const c = String(child || "").trim();
  if (!c || c === ".") return b || ".";
  if (b === "." || !b) return c;
  return `${b}/${c}`;
}

function parentSessionPath(path) {
  const p = String(path || ".").trim();
  if (!p || p === ".") return ".";
  const idx = p.lastIndexOf("/");
  if (idx < 0) return ".";
  return p.slice(0, idx) || ".";
}

function cnNumeralToInt(raw) {
  const text = String(raw || "").trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) return parseInt(text, 10);
  if (text === "十") return 10;
  if (text.includes("十")) {
    const parts = text.split("十");
    const high = parts[0] ? CN_DIGITS[parts[0]] ?? 1 : 1;
    const low = parts[1] ? CN_DIGITS[parts[1]] ?? 0 : 0;
    return high * 10 + low;
  }
  let total = 0;
  for (const ch of text) {
    if (CN_DIGITS[ch] != null) total = total * 10 + CN_DIGITS[ch];
  }
  return total > 0 ? total : null;
}

function parseChapterHeadings(text) {
  const markers = [];
  const content = String(text || "");
  CHAPTER_HEADER_RE.lastIndex = 0;
  let match = CHAPTER_HEADER_RE.exec(content);
  while (match) {
    const chapter = cnNumeralToInt(match[1]);
    if (chapter != null) {
      markers.push({
        chapter,
        offset: match.index,
        label: match[0].trim().replace(/^#+\s*/, ""),
      });
    }
    match = CHAPTER_HEADER_RE.exec(content);
  }
  markers.sort((a, b) => a.offset - b.offset);
  return markers;
}

function isSessionTextFile(path) {
  return SESSION_TEXT_FILE_RE.test(String(path || ""));
}

async function ensureSessionFilesTextIndex(taskId, dirPath = ".") {
  if (!taskId) return [];
  const dir = String(dirPath || ".").trim() || ".";
  const cacheKey = `${taskId}:${dir}`;
  if (sessionFilesTextIndexCacheKey === cacheKey && sessionFilesTextIndex.length) {
    return sessionFilesTextIndex;
  }
  try {
    const params = new URLSearchParams({ path: dir, recursive: "false", max_entries: "500" });
    const res = await fetchWithTimeout(
      `/tasks/${taskId}/files?${params.toString()}`,
      { headers: getAuthHeaders() },
      12000
    );
    if (!res.ok) return [];
    const data = await res.json();
    const entries = Array.isArray(data.entries) ? data.entries : [];
    const prefix = dir === "." ? "" : `${dir}/`;
    sessionFilesTextIndex = entries
      .filter((entry) => String(entry?.type || "") === "file" && isSessionTextFile(entry.path))
      .map((entry) => {
        const rel = String(entry.path || "");
        return prefix && rel ? `${prefix}${rel}` : rel;
      })
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    sessionFilesTextIndexCacheKey = cacheKey;
    return sessionFilesTextIndex;
  } catch {
    return [];
  }
}

function invalidateSessionFilesTextIndex() {
  sessionFilesTextIndex = [];
  sessionFilesTextIndexCacheKey = null;
}

async function refreshOpenSessionFileViewersMeta() {
  for (const [taskKey, view] of sessionFileViewerMap) {
    if (!view?.win || view.win.closed || !view.path) continue;
    const dir = parentSessionPath(view.path);
    view.fileIndex = await ensureSessionFilesTextIndex(taskKey, dir);
    renderPopupFileSwitcher(view);
  }
}

async function refreshSessionFilesPane(options = {}) {
  if (!sessionFilesListEl) return;
  const silent = Boolean(options.silent);
  const taskId = getSessionFilesTaskId();
  if (!taskId) return;
  renderSessionFilesBreadcrumb();
  const seq = ++sessionFilesRefreshSeq;
  if (!silent) {
    updateSessionFilesMeta(`session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} 加载中…`);
  }
  try {
    const params = new URLSearchParams({ path: sessionFilesCurrentPath, recursive: "false", max_entries: "500" });
    const res = await fetchWithTimeout(
      `/tasks/${taskId}/files?${params.toString()}`,
      { headers: getAuthHeaders() },
      12000
    );
    if (seq !== sessionFilesRefreshSeq) return;
    if (!res.ok) {
      if (res.status === 404) {
        sessionFilesListEl.innerHTML = '<p class="flow-empty">当前目录不存在或暂无文件。</p>';
        updateSessionFilesMeta(`session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} 无目录`);
      } else {
        sessionFilesListEl.innerHTML = '<p class="flow-empty">文件列表加载失败。</p>';
        updateSessionFilesMeta(`session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} 加载失败`);
      }
      return;
    }
    const data = await res.json();
    if (seq !== sessionFilesRefreshSeq) return;
    const entries = Array.isArray(data.entries) ? data.entries : [];
    if (!entries.length) {
      sessionFilesListEl.innerHTML = '<p class="flow-empty">当前目录为空。</p>';
      updateSessionFilesMeta(`session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} 0 项`);
      return;
    }
    entries.sort((a, b) => {
      const aDir = String(a?.type || "") === "dir";
      const bDir = String(b?.type || "") === "dir";
      if (aDir !== bDir) return aDir ? -1 : 1;
      const aPath = String(a?.path || "");
      const bPath = String(b?.path || "");
      return aPath.localeCompare(bPath);
    });
    sessionFilesListEl.innerHTML = entries
      .map((entry) => {
        const p = String(entry.path || "");
        const isDir = String(entry.type || "") === "dir";
        const label = p.includes("/") ? p.split("/").pop() : p;
        const size = isDir ? "" : formatBytes(entry.size);
        const mtime = entry.mtime_ms ? new Date(entry.mtime_ms).toLocaleTimeString() : "";
        const meta = [size, mtime].filter(Boolean).join(" · ");
        return `<div class="session-file-item ${isDir ? "dir" : "file"}" data-file-path="${escapeAttr(p)}" data-file-type="${isDir ? "dir" : "file"}">
          <span class="file-icon" aria-hidden="true">${isDir ? "📁" : "📄"}</span>
          <span class="file-path">${escapeHtml(label || p)}</span>
          ${meta ? `<span class="file-meta">${escapeHtml(meta)}</span>` : ""}
        </div>`;
      })
      .join("");
    updateSessionFilesMeta(`session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} ${entries.length} 项`);
    invalidateSessionFilesTextIndex();
    refreshOpenSessionFileViewersMeta();
    for (const [taskKey] of sessionFileViewerMap) {
      refreshSessionFileViewer(taskKey);
    }
  } catch (err) {
    if (seq !== sessionFilesRefreshSeq) return;
    sessionFilesListEl.innerHTML = '<p class="flow-empty">文件列表加载失败。</p>';
    const timeout = err?.name === "AbortError";
    updateSessionFilesMeta(
      `session ${taskId.slice(0, 8)}… ${sessionFilesCurrentPath} ${timeout ? "加载超时" : "加载异常"}`
    );
  }
}

function sessionFilesPollIntervalMs() {
  if (document.hidden) return 30000;
  if (running || sessionHasInFlightMission) return 12000;
  return 5000;
}

function scheduleSessionFilesPoll() {
  if (sessionFilesPollingTimer) clearTimeout(sessionFilesPollingTimer);
  sessionFilesPollingTimer = setTimeout(async () => {
    sessionFilesPollingTimer = null;
    await refreshSessionFilesPane({ silent: true });
    scheduleSessionFilesPoll();
  }, sessionFilesPollIntervalMs());
}

function startSessionFilesPolling() {
  if (sessionFilesPollingTimer) clearTimeout(sessionFilesPollingTimer);
  scheduleSessionFilesPoll();
}

document.addEventListener("visibilitychange", () => {
  if (sessionFilesPollingTimer) startSessionFilesPolling();
});

async function loginCommand(parts) {
  const username = parts[1] || "admin";
  const password = parts[2] || "admin";
  try {
    if (window.PlatformAuth) {
      const data = await window.PlatformAuth.login(username, password);
      appendLine(`logged in as ${data.user_id} (${data.role})`, "system");
      return;
    }
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
  } catch (err) {
    appendLine(`login failed: ${err.message || err}`, "error");
  }
}

function appendLine(text, className = "system") {
  if (className === "user") {
    const wrap = document.createElement("div");
    wrap.className = "user-bubble-wrap";

    const bubble = document.createElement("div");
    bubble.className = "user-bubble";
    bubble.textContent = text;
    wrap.appendChild(bubble);

    const actions = document.createElement("div");
    actions.className = "user-bubble-actions";
    const resendBtn = document.createElement("button");
    resendBtn.type = "button";
    resendBtn.className = "user-resend-btn";
    resendBtn.textContent = "重新发送";
    resendBtn.title = "按原消息重新发送";
    resendBtn.addEventListener("click", async () => {
      const raw = String(text || "").replace(/^>\s*/, "").trim();
      if (!raw) return;
      const taskId = activeTaskId || getSessionId();
      const statusData = await fetchTaskStatus(taskId);
      const st = String(statusData?.status || "");
      if (running && !isTerminalTaskStatus(st)) {
        appendLine("当前任务仍在运行，请先停止或等待完成后再重发。", "error");
        return;
      }
      if (running && isTerminalTaskStatus(st)) {
        setRunning(false);
      }
      if (isTerminalTaskStatus(st)) {
        await runTaskStream(raw, "LOW", "/tasks/stream", null, { suppressUserEcho: true });
        return;
      }
      await handleCommand(raw);
    });
    actions.appendChild(resendBtn);
    wrap.appendChild(actions);

    outputEl.appendChild(wrap);
    scrollOutputIfPinned();
    return;
  }

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
  if (runStatusEl) {
    runStatusEl.hidden = false;
    runStatusEl.classList.add("is-busy");
  }
  runTimer = setInterval(() => {
    const sec = Math.floor((Date.now() - runStartedAt) / 1000);
    const phase = lastPhaseMessage || "处理中";
    if (runStatusEl) runStatusEl.textContent = `运行中 ${sec}s · ${phase}`;
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
  if (runStatusEl) {
    runStatusEl.classList.remove("is-busy");
    runStatusEl.hidden = true;
    runStatusEl.textContent = "";
  }
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
  writingFileBlocks.clear();
  writingWorkspaceEl = null;
  writingWorkspaceHeaderEl = null;
  writingStreamCharsThisTurn = 0;
}

function isOutlineArtifactFilename(filename) {
  const name = String(filename || "").toLowerCase();
  return name.includes("大纲") || name.includes("outline");
}

function writingFileStatusLabel(status, filename = "") {
  const outline = isOutlineArtifactFilename(filename);
  if (status === "stopped") return "已停止";
  if (status === "preview") return outline ? "待确认大纲节选" : "待确认节选";
  if (status === "done") return outline ? "大纲已写入" : "已追加";
  if (status === "writing") return outline ? "正在生成大纲" : "正在追加";
  return "等待";
}

function bindActiveSseAbort(controller) {
  if (activeSseAbortController) {
    try {
      activeSseAbortController.abort("superseded");
    } catch {
      /* ignore */
    }
  }
  activeSseAbortController = controller;
}

function abortActiveSseStream(reason = "user_stop") {
  if (!activeSseAbortController) return false;
  try {
    activeSseAbortController.abort(reason);
  } catch {
    /* ignore */
  }
  activeSseAbortController = null;
  return true;
}

function markActiveWritingStreamStopped() {
  if (activeWritingBlock) {
    activeWritingBlock.status = "stopped";
    syncWritingFileToggleLabel(activeWritingBlock);
  }
}

function isSseAbortError(err) {
  if (!err) return false;
  if (err.name === "AbortError") return true;
  const msg = String(err.message || err);
  return msg.includes("aborted") || msg.includes("Abort");
}

/** 从标题里提取文件名（工具/确认面板可能只给标题不给 filename）。 */
function extractFilenameFromTitle(title) {
  const raw = String(title || "");
  const m = raw.match(/([^\s/─·]+?\.(?:txt|md|json|yaml|yml|csv|py|js|ts|html|xml))/i);
  return m ? m[1] : "";
}

function syncWritingFileToggleLabel(block) {
  if (!block?.toggleEl) return;
  const status = writingFileStatusLabel(block.status, block.filename);
  const active = block === activeWritingBlock ? " · 当前" : "";
  const chevron = block.collapsed ? "▸" : "▾";
  block.toggleEl.textContent = `${chevron} ${block.filename} — ${status}${active}`;
  block.toggleEl.setAttribute("aria-expanded", block.collapsed ? "false" : "true");
}

function setWritingFileCollapsed(block, collapsed) {
  if (!block) return;
  block.collapsed = Boolean(collapsed);
  block.panelEl.classList.toggle("collapsed", block.collapsed);
  syncWritingFileToggleLabel(block);
}

function focusWritingFileBlock(block) {
  if (!block) return;
  if (activeWritingBlock && activeWritingBlock !== block) {
    setWritingFileCollapsed(activeWritingBlock, true);
    activeWritingBlock.panelEl.classList.remove("is-active");
  }
  activeWritingBlock = block;
  writingHeaderEl = block.toggleEl;
  writingPanelEl = block.panelEl;
  writingStreamEl = block.bodyEl;
  writingStreamFilename = block.filename;
  block.panelEl.classList.add("is-active");
  setWritingFileCollapsed(block, false);
  syncWritingFileToggleLabel(block);
  for (const [, other] of writingFileBlocks) {
    syncWritingFileToggleLabel(other);
  }
}

function ensureWritingWorkspace() {
  if (writingWorkspaceEl) return writingWorkspaceEl;
  writingWorkspaceHeaderEl = document.createElement("p");
  writingWorkspaceHeaderEl.className = "line trace-header file-workspace-header";
  writingWorkspaceHeaderEl.textContent = "── 文件（流式追加）──";
  writingWorkspaceEl = document.createElement("div");
  writingWorkspaceEl.className = "file-workspace";
  writingWorkspaceEl.id = "file-workspace";
  outputEl.appendChild(writingWorkspaceHeaderEl);
  outputEl.appendChild(writingWorkspaceEl);
  return writingWorkspaceEl;
}

function getOrCreateWritingFileBlock(filename, { reset = false, phase = "" } = {}) {
  const fname = (filename || "").trim() || "artifact";
  ensureWritingWorkspace();
  let block = writingFileBlocks.get(fname);
  if (!block) {
    const sectionEl = document.createElement("section");
    sectionEl.className = "file-stream-block";
    sectionEl.dataset.filename = fname;
    const toggleEl = document.createElement("button");
    toggleEl.type = "button";
    toggleEl.className = "file-stream-toggle";
    toggleEl.addEventListener("click", () => {
      const entry = writingFileBlocks.get(fname);
      if (!entry) return;
      setWritingFileCollapsed(entry, !entry.collapsed);
    });
    const panelEl = document.createElement("div");
    panelEl.className = "file-stream-body trace-panel content-panel";
    const bodyEl = document.createElement("pre");
    bodyEl.className = "line content-body file-stream-content";
    panelEl.appendChild(bodyEl);
    sectionEl.appendChild(toggleEl);
    sectionEl.appendChild(panelEl);
    writingWorkspaceEl.appendChild(sectionEl);
    block = {
      toggleEl,
      panelEl,
      bodyEl,
      filename: fname,
      status: "pending",
      collapsed: true,
    };
    writingFileBlocks.set(fname, block);
    syncWritingFileToggleLabel(block);
  }
  if (reset || phase === "start") {
    block.status = "writing";
    block.bodyEl.replaceChildren();
  } else if (phase === "done") {
    block.status = "done";
  } else if (phase === "preview") {
    block.status = "preview";
  } else if (block.status === "pending") {
    block.status = "writing";
  }
  focusWritingFileBlock(block);
  syncWritingFileToggleLabel(block);
  return block;
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

function openWritingContentBlock(filename = "", options = {}) {
  const block = getOrCreateWritingFileBlock(filename, options);
  writingStreamText = "";
  writingPendingText = "";
  if (options.reset) {
    block.bodyEl.replaceChildren();
  }
  return block.panelEl;
}

/**
 * 向统一文件区追加/覆盖内容（流式 writing_delta、确认节选、工具读文件等共用）。
 */
function appendToFileWorkspace(filename, text, { replace = false, phase = "", focus = true } = {}) {
  const body = (text || "").trim();
  if (!body) return null;
  const block = getOrCreateWritingFileBlock(filename, {
    reset: replace,
    phase: replace ? "start" : phase,
  });
  if (replace) {
    block.bodyEl.replaceChildren();
    block.bodyEl.appendChild(document.createTextNode(body));
    if (block === activeWritingBlock) {
      writingStreamText = body.length;
    }
  } else {
    const sep = block.bodyEl.textContent ? "\n\n" : "";
    block.bodyEl.appendChild(document.createTextNode(sep + body));
    if (block === activeWritingBlock) {
      writingStreamText += sep.length + body.length;
    }
  }
  if (phase === "preview") {
    block.status = "preview";
    syncWritingFileToggleLabel(block);
  }
  if (focus) {
    focusWritingFileBlock(block);
  }
  scrollToBottomIfPinned(block.panelEl);
  scrollOutputIfPinned();
  return block;
}

/** 有文件名则进入文件区；否则退化为独立内容块（无「手稿/大纲」等类型样式）。 */
function appendFileContentBlock(title, text, opts = {}) {
  const body = (text || "").trim();
  if (!body) return null;
  const fname = String(opts.filename || extractFilenameFromTitle(title) || "").trim();
  if (fname) {
    return appendToFileWorkspace(fname, body, {
      replace: Boolean(opts.replace),
      phase: opts.phase || (opts.preview ? "preview" : ""),
      focus: opts.focus !== false,
    });
  }
  const block = createContentBlock({
    title: title || "── 内容 ──",
    panelClass: "content-fallback-panel",
    bodyClass: "file-stream-content",
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

/**
 * 按文件名打开/切换文件流区块（同一文件区，不区分大纲/正文类型）。
 *
 * Open or switch the per-filename stream block in the unified file workspace.
 */
function ensureWritingPanel(filename = "") {
  const fname = (filename || "").trim();
  if (
    activeWritingBlock &&
    (!fname || fname === activeWritingBlock.filename)
  ) {
    writingStreamEl = activeWritingBlock.bodyEl;
    writingPanelEl = activeWritingBlock.panelEl;
    writingHeaderEl = activeWritingBlock.toggleEl;
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
  const phase = String(payload.phase || "");
  if (
    payload.reset ||
    phase === "start" ||
    !activeWritingBlock ||
    (fname && fname !== activeWritingBlock.filename)
  ) {
    openWritingContentBlock(fname, { reset: Boolean(payload.reset || phase === "start"), phase });
  } else {
    ensureWritingPanel(fname);
    if (phase === "done") {
      const block = writingFileBlocks.get(fname || activeWritingBlock.filename);
      if (block) {
        block.status = "done";
        syncWritingFileToggleLabel(block);
      }
    }
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

async function fetchHealth() {
  try {
    await fetch("/health");
    if (window.PlatformAuth) {
      await window.PlatformAuth.fetchRuntimeMeta();
      await window.PlatformAuth.renderNavAuth();
    }
  } catch {
    if (window.PlatformAuth) await window.PlatformAuth.renderNavAuth();
  }
}

async function listHistory() {
  const res = await apiFetch("/tasks?limit=10", { headers: getAuthHeaders() });
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
  refreshHistorySidebar();
}

async function showStatus(taskId) {
  const res = await apiFetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
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
  const res = await apiFetch(`/tasks/${taskId}/audit`, { headers: getAuthHeaders() });
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
  const res = await apiFetch(`/tasks/${taskId}/result`, { headers: getAuthHeaders() });
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
  const res = await apiFetch("/reviews", {
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
  /* Mission pause hints only — resume is always user-initiated (/resume or continue goal). */
}

function isMissionStatusQuery(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  return /(你正在做什么|你在做什么|你在干嘛|在做什么|正在做什么|写到哪|当前进度|什么进度|任务状态|what are you doing)/i.test(
    t
  );
}

/** Status/meta question during mission: pause + factual snapshot, do not replan append. */
async function handleMissionStatusInquiry(message, opts = {}) {
  const taskId = activeTaskId || getSessionId();
  if (!opts.suppressUserEcho) {
    appendLine(`> ${message}`, "user");
  }
  const ok = await steerActiveMission(message, {
    preempt: true,
    priority: 100,
    replaceGoal: false,
    suppressUserEcho: true,
    intervention: {
      action: "pause",
      force: true,
      reason: "status inquiry",
    },
  });
  if (!ok) return false;
  const statusData = await fetchTaskStatus(taskId);
  const st = String(statusData?.status || "");
  const executorActive = Boolean(statusData?.executor_active);
  if (!executorActive || st === "MISSION_PAUSED" || st === "REASONED") {
    await runTaskStream(message, "LOW", "/tasks/stream", null, {
      suppressUserEcho: true,
    });
  }
  return true;
}

async function steerActiveMission(message, opts = {}) {
  const taskId = activeTaskId || getSessionId();
  const statusData = await fetchTaskStatus(taskId);
  const st = String(statusData?.status || "");
  if (isTerminalTaskStatus(st)) {
    appendLine("任务已结束，正在开启新轮次…", "system");
    await runTaskStream(message, "LOW", "/tasks/stream", null, {
      suppressUserEcho: Boolean(opts.suppressUserEcho),
    });
    return true;
  }
  const payload = {
    message,
    preempt: Boolean(opts.preempt),
    priority: Number.isFinite(opts.priority) ? opts.priority : 0,
    replace_goal: Boolean(opts.replaceGoal),
  };
  if (opts.intervention && typeof opts.intervention === "object") {
    payload.intervention = opts.intervention;
  }
  const res = await apiFetch(`/tasks/${taskId}/steer`, {
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
  const hadClientStream = Boolean(activeSseAbortController) || running;
  const taskId = activeTaskId || getSessionId();
  if (hadClientStream) {
    try {
      await apiFetch(`/tasks/${taskId}/interrupt-stream`, {
        method: "POST",
        headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "user_requested", requested_by: "web" }),
      });
    } catch {
      /* best-effort */
    }
    abortActiveSseStream("user_stop");
    markActiveWritingStreamStopped();
    if (running) {
      setRunning(false);
    }
  }
  const res = await apiFetch(`/tasks/${taskId}/pause`, {
    method: "POST",
    headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ reason: "user_requested", requested_by: "web" }),
  });
  if (!res.ok) {
    if (res.status !== 401) {
      appendLine(`stop failed: ${res.status} ${await res.text()}`, "error");
    }
    return false;
  }
  const data = await res.json();
  const display = data.client_display || {};
  appendSystemLines(display.system_lines);
  if (!display.system_lines?.length) {
    appendLine(
      hadClientStream
        ? "已停止接收流式输出；任务将在当前步骤安全点暂停…"
        : "pause requested, waiting current step to yield…",
      "system"
    );
  }
  sessionHasInFlightMission = false;
  updateStopButtonState();
  return true;
}

async function stopTaskById(taskId) {
  const res = await apiFetch(`/tasks/${taskId}/stop`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) return false;
  return true;
}

async function fetchTaskStatus(taskId) {
  try {
    const res = await apiFetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function getTaskStatusValue(taskId) {
  const data = await fetchTaskStatus(taskId);
  return data ? String(data.status || "") : "";
}

async function stopAllInFlightMissions({ limit = 100 } = {}) {
  const res = await apiFetch(`/tasks?limit=${Math.min(limit, 100)}`, {
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
    const r = await apiFetch(`/tasks/${t.task_id}/stop`, {
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
      return {
        kind: "file",
        filename: fname,
        text: String(section.content || ""),
        preview: true,
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
      appendFileContentBlock("", rendered.text, {
        filename: rendered.filename,
        preview: Boolean(rendered.preview),
        focus: false,
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
    const fname = String(confirmation.artifact_filename || "artifact.txt");
    appendFileContentBlock("", excerpt, {
      filename: fname,
      preview: true,
      focus: false,
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
  const res = await apiFetch(`/tasks/${taskId}/resume`, {
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
async function runResumeStream(taskId, { confirm = false, fromPendingQueue = false } = {}) {
  if (running && !fromPendingQueue) {
    appendLine("已有任务在运行，请稍候", "error");
    return null;
  }
  setRunning(true);
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
  appendLine(`> /resume${confirm ? " confirm" : ""}`, "user");
  const taskIdRef = { id: taskId };
  const sseAbort = new AbortController();
  bindActiveSseAbort(sseAbort);
  try {
    const res = await apiFetch(`/tasks/${taskId}/resume/stream`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ confirm: Boolean(confirm) }),
      signal: sseAbort.signal,
    });
    if (!res.ok || !res.body) {
      if (res.status !== 401) {
        appendLine(`resume stream failed: ${res.status} ${await res.text()}`, "error");
      }
      return null;
    }
    activeTaskId = taskId;
    await consumeSseStream(res, taskIdRef, { signal: sseAbort.signal });
    return { task_id: taskIdRef.id || taskId };
  } catch (err) {
    if (!isSseAbortError(err)) {
      appendLine(`resume stream error: ${err}`, "error");
    }
    return null;
  } finally {
    if (activeSseAbortController === sseAbort) {
      activeSseAbortController = null;
    }
    setRunning(false);
    await refreshFlowPanel(taskIdRef?.id || taskId || activeTaskId);
  }
}

function formatOrchestration(summary, detail) {
  if (detail && typeof detail === "object") {
    const done = detail.done ?? 0;
    const total = detail.total ?? 0;
    const labels = (detail.completed || []).map((x) => String(x));
    let completed = "—";
    if (labels.length) {
      if (labels.length <= ORCHESTRATION_COMPLETED_DISPLAY_MAX) {
        completed = labels.join(",");
      } else {
        const head = labels.slice(0, ORCHESTRATION_COMPLETED_DISPLAY_MAX).join(",");
        completed = `${head}…(+${labels.length - ORCHESTRATION_COMPLETED_DISPLAY_MAX})`;
      }
    }
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

/**
 * 单条 SSE 事件分发到终端与 flow 面板。
 *
 * Dispatch one SSE event; binds taskIdRef.id on first task_id payload.
 */
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
    refreshHistorySidebar();
  } else if (eventType === "subtasks") {
    appendLine(`subtasks planned: ${payload.count}`, "system");
    for (const st of payload.subtasks || []) {
      appendLine(`  - [${st.domain}] ${st.description}`, "system");
    }
  } else if (eventType === "worker") {
    appendLine(`  worker ${payload.domain}: ${payload.status} — ${payload.summary || ""}`, "node");
  } else if (eventType === "stream_open") {
    updateProgressLine({
      message: payload.message || "已连接服务端…",
      phase: payload.phase || "connecting",
      elapsed_sec: 0,
    });
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
    if (activeSseAbortController?.signal?.aborted) {
      return;
    }
    if (payload.reset || payload.phase === "start") {
      ensureTracePanel();
      if (!writingTraceHintShown) {
        writingTraceHintShown = true;
        appendTraceLine(
          "文件正文流式可能滞后；请关注本栏 writing/status 进度（模型缓冲与 content 解析）",
          { node: "writing", phase: "hint", level: "status" }
        );
      }
    }
    appendWritingDelta(payload.text || "", payload);
    maybeRefreshOpenFileViewer(payload.filename || "");
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
    refreshHistorySidebar();
  }
}

function formatNodeEvent(payload) {
  const node = payload.node || "?";
  const status = payload.status || "";
  const isError = status === "FAILED" || status === "DEAD_LETTER" || status === "WRITING_FAILED";

  if ((MISSION_LOOP_NODES.has(node) || PIPELINE_QUIET_NODES.has(node)) && !isError) {
    return;
  }

  const line = `[${node}] → ${status}`;
  appendLine(line, isError ? "error" : "node");
  if (node === "tool_execution" && status === "TOOL_EXECUTED") {
    appendLine("  … 工具执行完成，正在收尾", "system");
  }
  if (node === "writing" && status === "WRITTEN") {
    const action = String(payload.writing_action || "");
    const isOutline = action === "write_outline" || action === "rewrite_outline";
    const path =
      payload.written_path ||
      (isOutline ? payload.outline_path : payload.body_path) ||
      (isOutline ? "outline.txt" : "novel.txt");
    const nbytes = isOutline ? payload.outline_bytes : payload.body_bytes;
    const bytes = nbytes ? `（${nbytes} B）` : "";
    appendLine(`  … 已写入文件 ${path}${bytes}`, "system");
  }
  if (node === "writing" && status === "WRITING_FAILED") {
    appendLine("  … 写入失败，请查看 trace 或 /audit；目标文件可能未生成", "error");
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

function getSlashCommandLine(value) {
  const raw = String(value || "");
  const line = raw.split("\n")[0];
  const trimmed = line.trimStart();
  if (!trimmed.startsWith("/")) return null;
  if (raw.includes("\n") && raw.trim() !== trimmed) return null;
  return trimmed;
}

function filterSlashCommands(line) {
  const normalized = line.toLowerCase();
  const filtered = COMMAND_SUGGESTIONS.filter((entry) => entry.cmd.toLowerCase().startsWith(normalized));
  return filtered.length ? filtered : COMMAND_SUGGESTIONS;
}

function hideSlashCommandMenu() {
  slashMenuActiveIndex = -1;
  if (!commandSlashMenuEl) return;
  commandSlashMenuEl.hidden = true;
  commandSlashMenuEl.replaceChildren();
  if (inputEl) inputEl.setAttribute("aria-expanded", "false");
}

function applySlashCommand(cmd) {
  if (!inputEl) return;
  inputEl.value = cmd;
  hideSlashCommandMenu();
  autoResizeCommandInput();
  inputEl.focus();
}

function renderSlashCommandMenu(value) {
  if (!commandSlashMenuEl || !inputEl) return;
  const line = getSlashCommandLine(value);
  if (!line) {
    hideSlashCommandMenu();
    return;
  }
  const candidates = filterSlashCommands(line);
  if (!candidates.length) {
    hideSlashCommandMenu();
    return;
  }
  if (slashMenuActiveIndex >= candidates.length) slashMenuActiveIndex = candidates.length - 1;
  if (slashMenuActiveIndex < 0 && candidates.length) slashMenuActiveIndex = 0;

  commandSlashMenuEl.hidden = false;
  inputEl.setAttribute("aria-expanded", "true");
  commandSlashMenuEl.innerHTML = candidates
    .map((entry, idx) => {
      const selected = idx === slashMenuActiveIndex ? " is-selected" : "";
      return `<button type="button" class="command-slash-item${selected}" role="option" data-slash-cmd="${escapeAttr(entry.cmd)}" aria-selected="${idx === slashMenuActiveIndex}">
        <span class="command-slash-cmd">${escapeHtml(entry.cmd)}</span>
        <span class="command-slash-hint">${escapeHtml(entry.hint)}</span>
      </button>`;
    })
    .join("");
}

function refreshCommandSuggestions(value) {
  slashMenuActiveIndex = -1;
  renderSlashCommandMenu(value);
}

function moveSlashMenuSelection(delta, value) {
  const line = getSlashCommandLine(value);
  if (!line || !commandSlashMenuEl || commandSlashMenuEl.hidden) return false;
  const candidates = filterSlashCommands(line);
  if (!candidates.length) return false;
  if (slashMenuActiveIndex < 0) slashMenuActiveIndex = 0;
  else slashMenuActiveIndex = (slashMenuActiveIndex + delta + candidates.length) % candidates.length;
  renderSlashCommandMenu(value);
  const selected = commandSlashMenuEl.querySelector(".command-slash-item.is-selected");
  selected?.scrollIntoView({ block: "nearest" });
  return true;
}

/**
 * 解析 SSE 流（按空行分块，解析 event 与 data 行）。
 *
 * Read ReadableStream and parse SSE event/data lines.
 */
async function consumeSseStream(res, taskIdRef, { signal } = {}) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
  while (true) {
    if (signal?.aborted) {
      try {
        await reader.cancel();
      } catch {
        /* ignore */
      }
      break;
    }
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
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* ignore */
    }
  }
}

let activeSkillId = null;
const skillDetailCache = new Map();
const skillsRailCatalog = [];
const skillsRailEnabled = new Set();
let skillsRailPinned = false;
let skillsRailFilter = "";

const skillsPaneEl = document.getElementById("skills-pane");
const skillsPaneTabEl = document.getElementById("skills-pane-tab");
const skillsPanePinEl = document.getElementById("skills-pane-pin");
const skillsRailListEl = document.getElementById("skills-rail-list");
const skillsPaneSearchEl = document.getElementById("skills-pane-search");
const skillsEnabledCountEl = document.getElementById("skills-enabled-count");
const skillActiveBadgeEl = document.getElementById("skill-active-badge");

const SCENARIO_LABELS_RAIL = {
  general: "通用",
  code: "代码",
  document: "文档",
  analysis: "分析",
  writing: "写作",
};

function skillsRailStorageKey() {
  return `chat_skills_rail_${getSessionId()}`;
}

function saveSkillsRailState() {
  try {
    sessionStorage.setItem(
      skillsRailStorageKey(),
      JSON.stringify({
        enabled: [...skillsRailEnabled],
        active: activeSkillId,
        pinned: skillsRailPinned,
      })
    );
  } catch {
    /* ignore */
  }
}

function loadSkillsRailState() {
  try {
    const raw = sessionStorage.getItem(skillsRailStorageKey());
    if (!raw) return;
    const data = JSON.parse(raw);
    skillsRailEnabled.clear();
    for (const id of data.enabled || []) {
      if (id) skillsRailEnabled.add(id);
    }
    activeSkillId = data.active || null;
    skillsRailPinned = Boolean(data.pinned);
    if (skillsPaneEl) skillsPaneEl.classList.toggle("expanded", skillsRailPinned);
    syncSkillsPanePinUi();
  } catch {
    /* ignore */
  }
}

function syncSkillHeaderUi() {
  const sel = document.getElementById("skill-select");
  if (sel) sel.value = activeSkillId || "";
  if (skillActiveBadgeEl) {
    if (activeSkillId) {
      const item = skillsRailCatalog.find((s) => s.skill_id === activeSkillId);
      skillActiveBadgeEl.textContent = item?.name || activeSkillId;
      skillActiveBadgeEl.hidden = false;
    } else {
      skillActiveBadgeEl.hidden = true;
    }
  }
  if (skillsEnabledCountEl) {
    const n = skillsRailEnabled.size;
    if (n > 0) {
      skillsEnabledCountEl.textContent = String(n);
      skillsEnabledCountEl.hidden = false;
    } else {
      skillsEnabledCountEl.hidden = true;
    }
  }
}

function setActiveSkill(skillId, { announce } = { announce: false }) {
  if (skillId && !skillsRailEnabled.has(skillId)) return;
  activeSkillId = skillId || null;
  syncSkillHeaderUi();
  saveSkillsRailState();
  loadSkillInputForm(activeSkillId);
  renderSkillsRailList();
  if (announce && skillId) {
    const item = skillsRailCatalog.find((s) => s.skill_id === skillId);
    appendLine(`下一条任务将使用 Skill: ${item?.name || skillId}`, "system");
  }
}

function toggleSkillEnabled(skillId) {
  if (skillsRailEnabled.has(skillId)) {
    skillsRailEnabled.delete(skillId);
    if (activeSkillId === skillId) {
      activeSkillId = skillsRailEnabled.size ? [...skillsRailEnabled][0] : null;
    }
  } else {
    skillsRailEnabled.add(skillId);
    if (!activeSkillId) activeSkillId = skillId;
  }
  syncSkillHeaderUi();
  saveSkillsRailState();
  loadSkillInputForm(activeSkillId);
  renderSkillsRailList();
}

function enableSkillFromOutside(skillId) {
  if (!skillId) return;
  skillsRailEnabled.add(skillId);
  activeSkillId = skillId;
  setSkillsPanePinned(true);
  syncSkillHeaderUi();
  saveSkillsRailState();
  loadSkillInputForm(skillId);
  renderSkillsRailList();
}

async function loadSkillInputForm(skillId) {
  const panel = document.getElementById("skill-params-panel");
  if (!panel || !window.SkillForm) return;
  if (!skillId) {
    window.SkillForm.clearContainer(panel);
    return;
  }
  try {
    let detail = skillDetailCache.get(skillId);
    if (!detail) {
      const res = await apiFetch(`/skills/${encodeURIComponent(skillId)}`, { headers: getAuthHeaders() });
      if (!res.ok) {
        window.SkillForm.clearContainer(panel);
        return;
      }
      detail = await res.json();
      skillDetailCache.set(skillId, detail);
    }
    const pres = detail.presentation || detail.definition?.presentation || {};
    const schema =
      pres.input_form_schema ||
      detail.definition?.presentation?.input_form_schema ||
      null;
    window.SkillForm.renderSkillInputForm(schema, panel);
  } catch {
    window.SkillForm.clearContainer(panel);
  }
}

function renderSkillsRailList() {
  if (!skillsRailListEl) return;
  const q = skillsRailFilter.trim().toLowerCase();
  const items = skillsRailCatalog.filter((s) => {
    if (!q) return true;
    const hay = `${s.skill_id} ${s.name} ${s.summary || ""}`.toLowerCase();
    return hay.includes(q);
  });
  if (!items.length) {
    skillsRailListEl.innerHTML = '<p class="flow-empty">没有匹配的技能。</p>';
    return;
  }
  skillsRailListEl.innerHTML = items
    .map((s) => {
      const enabled = skillsRailEnabled.has(s.skill_id);
      const active = activeSkillId === s.skill_id;
      const origin = s.source_type === "tenant" ? "自定义" : "内置";
      const scenario = SCENARIO_LABELS_RAIL[s.category] || s.category || "—";
      return `<article class="skills-rail-item${enabled ? " is-enabled" : ""}${active ? " is-active" : ""}" data-skill-id="${escapeHtml(s.skill_id)}">
        <button type="button" class="skills-toggle" aria-pressed="${enabled ? "true" : "false"}">${enabled ? "已启用" : "启用"}</button>
        <div class="skills-rail-info" role="button" tabindex="0" title="设为下一条任务所用">
          <strong>${escapeHtml(s.name || s.skill_id)}</strong>
          <span class="meta">${escapeHtml(scenario)} · ${escapeHtml(origin)}</span>
        </div>
      </article>`;
    })
    .join("");

  skillsRailListEl.querySelectorAll(".skills-rail-item").forEach((row) => {
    const id = row.dataset.skillId;
    row.querySelector(".skills-toggle")?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSkillEnabled(id);
    });
    const info = row.querySelector(".skills-rail-info");
    const pick = () => {
      if (!skillsRailEnabled.has(id)) {
        toggleSkillEnabled(id);
        return;
      }
      setActiveSkill(id, { announce: true });
    };
    info?.addEventListener("click", pick);
    info?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
  });
}

async function loadSkillsRailCatalog() {
  if (!skillsRailListEl) return;
  skillsRailListEl.innerHTML = '<p class="flow-empty">加载技能目录…</p>';
  try {
    const res = await apiFetch("/skills?scope=all&status=published", { headers: getAuthHeaders() });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const err = await res.json();
        if (err.detail) detail = String(err.detail);
      } catch {
        /* ignore */
      }
      skillsRailListEl.innerHTML = `<p class="flow-empty">无法加载 Skills：${escapeHtml(detail)}</p>`;
      return;
    }
    const data = await res.json();
    skillsRailCatalog.length = 0;
    skillsRailCatalog.push(...(data.skills || []));
    skillsRailCatalog.sort(
      (a, b) => (a.display_order ?? 0) - (b.display_order ?? 0) || a.skill_id.localeCompare(b.skill_id)
    );
    const sel = document.getElementById("skill-select");
    if (sel) {
      while (sel.options.length > 1) sel.remove(1);
      for (const s of skillsRailCatalog) {
        const opt = document.createElement("option");
        opt.value = s.skill_id;
        opt.textContent = s.name || s.skill_id;
        sel.appendChild(opt);
      }
    }
    reconcileSkillsRailWithCatalog();
    renderSkillsRailList();
    syncSkillHeaderUi();
    loadSkillInputForm(activeSkillId);
  } catch {
    skillsRailListEl.innerHTML = '<p class="flow-empty">加载失败。</p>';
  }
}

function reconcileSkillsRailWithCatalog() {
  const ids = new Set(skillsRailCatalog.map((s) => s.skill_id));
  for (const id of [...skillsRailEnabled]) {
    if (!ids.has(id)) skillsRailEnabled.delete(id);
  }
  if (activeSkillId && !ids.has(activeSkillId)) {
    activeSkillId = skillsRailEnabled.size ? [...skillsRailEnabled][0] : null;
  }
}

function syncSkillsPanePinUi() {
  if (!skillsPanePinEl) return;
  skillsPanePinEl.textContent = skillsRailPinned ? "取消固定" : "固定";
  skillsPanePinEl.title = skillsRailPinned ? "取消固定展开，恢复为悬停展开" : "固定展开侧栏";
  skillsPanePinEl.setAttribute("aria-pressed", skillsRailPinned ? "true" : "false");
}

function setSkillsPanePinned(pinned) {
  skillsRailPinned = Boolean(pinned);
  if (skillsPaneEl) {
    skillsPaneEl.classList.toggle("expanded", skillsRailPinned);
  }
  syncSkillsPanePinUi();
  saveSkillsRailState();
}

function toggleSkillsPanePinned() {
  setSkillsPanePinned(!skillsRailPinned);
}

function initSkillsRail() {
  loadSkillsRailState();
  syncSkillsPanePinUi();

  if (skillsPaneTabEl && skillsPaneEl) {
    skillsPaneTabEl.addEventListener("click", () => {
      toggleSkillsPanePinned();
    });
  }

  if (skillsPanePinEl && skillsPaneEl) {
    skillsPanePinEl.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSkillsPanePinned();
    });
  }

  if (skillsPaneSearchEl) {
    skillsPaneSearchEl.addEventListener("input", () => {
      skillsRailFilter = skillsPaneSearchEl.value;
      renderSkillsRailList();
    });
  }
}

function buildDefaultTaskBody(goal, riskLevel = "LOW") {
  const body = attachSessionFlags({
    task_type: "qa",
    user_id: "web",
    input_payload: applyInteractionModeToPayload({
      goal,
      risk_level: riskLevel,
      mission_auto: true,
    }),
  });
  const skillId = activeSkillId || document.getElementById("skill-select")?.value || "";
  if (skillId) {
    body.skill_id = skillId;
    const panel = document.getElementById("skill-params-panel");
    const params = window.SkillForm
      ? window.SkillForm.collectSkillParams(panel, goal)
      : { goal: String(goal || "").trim() };
    body.skill_params = params;
  }
  return body;
}

function buildTaskRequestBody(goal, riskLevel = "LOW") {
  return buildDefaultTaskBody(goal, riskLevel);
}

function initSkillFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const sid = params.get("skill_id");
  const goal = params.get("goal");
  if (sid) {
    enableSkillFromOutside(sid);
    if (goal && inputEl) inputEl.value = goal;
    const item = skillsRailCatalog.find((s) => s.skill_id === sid);
    appendLine(`已启用 Skill: ${item?.name || sid}`, "system");
  }
}

/**
 * 发起流式任务，结束后刷新 flow 面板。
 *
 * Start streamed task via POST /tasks/stream; refresh flow panel in finally.
 */
async function runTaskStream(
  goal,
  riskLevel = "LOW",
  endpoint = "/tasks/stream",
  body = null,
  opts = {}
) {
  setRunning(true);
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
  if (!opts.suppressUserEcho) {
    appendLine(`> ${goal}`, "user");
  }
  const requestBody = body || buildTaskRequestBody(goal, riskLevel);
  const mode = getInteractionMode();
  if (mode !== "auto" && !opts.suppressUserEcho) {
    const meta = INTERACTION_MODE_META[mode];
    appendLine(`[模式 ${meta.label}]`, "system");
  }
  const taskIdRef = { id: null };
  const sseAbort = new AbortController();
  bindActiveSseAbort(sseAbort);

  try {
    const healthy = await probeRuntimeHealth();
    if (!healthy) {
      for (const line of formatStreamFetchError(new TypeError("Failed to fetch")).split("\n")) {
        appendLine(line, "error");
      }
      appendLine(
        "（/health/live 不可达，已跳过 POST /tasks/stream；热更新后请等 agent 就绪再试）",
        "error"
      );
      return;
    }

    const res = await apiFetch(endpoint, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(requestBody),
      signal: sseAbort.signal,
    });

    if (!res.ok || !res.body) {
      if (res.status !== 401) {
        appendLine(`stream failed: ${res.status}`, "error");
      }
      return;
    }

    await consumeSseStream(res, taskIdRef, { signal: sseAbort.signal });
  } catch (err) {
    if (!isSseAbortError(err)) {
      for (const line of formatStreamFetchError(err).split("\n")) {
        appendLine(line, "error");
      }
    }
  } finally {
    if (activeSseAbortController === sseAbort) {
      activeSseAbortController = null;
    }
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
  appendLine("  /mode [auto|chat|engineering|writing]  Interaction mode (header dropdown)", "system");
  appendLine("  /help            Show this help", "system");
  appendLine("  /history         List recent tasks", "system");
  appendLine("  /status <id>     Query task status + errors", "system");
  appendLine("  /audit <id>      Show audit chain (node logs)", "system");
  appendLine("  /result <id>     Show task result", "system");
  appendLine("  /approve <id>    Approve human review", "system");
  appendLine("  /reject <id>     Reject human review", "system");
  appendLine("  /supervisor <text>  Multi-agent supervisor task (SSE)", "system");
  appendLine("  /risk high <text> Run high-risk task (triggers review)", "system");
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
  if (text === "/mode" || text.startsWith("/mode ")) {
    const arg = text === "/mode" ? "" : text.slice("/mode ".length).trim().toLowerCase();
    if (!arg) {
      const cur = getInteractionMode();
      const meta = INTERACTION_MODE_META[cur];
      appendLine(`当前交互模式: ${cur} (${meta.label})`, "system");
      appendLine(meta.hint, "system");
      return;
    }
    setInteractionMode(arg);
    return;
  }
  if (text === "/new") {
    if (requestNewSession("/new")) {
      appendLine("tip: use /stop-all if you want to stop old in-flight missions", "system");
    }
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
    if (window.PlatformAuth) window.PlatformAuth.logout();
    else localStorage.removeItem(TOKEN_KEY);
    appendLine("logged out", "system");
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
  // Steer queue only while the executor is actually running on the server.
  // After worker_lost / PAUSED, use normal session turn (insert + explicit /resume).
  if (!text.startsWith("/")) {
    const taskId = activeTaskId || getSessionId();
    const statusData = await fetchTaskStatus(taskId);
    const st = String(statusData?.status || "");
    const executorActive = Boolean(statusData?.executor_active);
    sessionMissionExecutorActive = st === "MISSION_RUNNING" && executorActive;
    sessionHasInFlightMission = st === "MISSION_RUNNING" || st === "MISSION_PAUSED";
    updateStopButtonState();
    if (st === "MISSION_RUNNING" && executorActive) {
      appendLine(`> ${text}`, "user");
      if (isMissionStatusQuery(text)) {
        await handleMissionStatusInquiry(text, { suppressUserEcho: true });
      } else {
        await steerActiveMission(text, {
          preempt: true,
          priority: 80,
          replaceGoal: true,
        });
      }
      return;
    }
  }
  await runTaskStream(text, "LOW");
  sessionHasInFlightMission = false;
}

function resetCommandInputHeight() {
  if (!inputEl || inputEl.tagName !== "TEXTAREA") return;
  inputEl.style.height = "auto";
}

function autoResizeCommandInput() {
  if (!inputEl || inputEl.tagName !== "TEXTAREA") return;
  inputEl.style.height = "auto";
  const maxPx = 160;
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, maxPx)}px`;
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = inputEl.value.trim();
  inputEl.value = "";
  resetCommandInputHeight();
  if (!value) return;
  if (running) {
    // Keep slash commands operational while stream is running (e.g. /stop, /stop-all).
    if (value.startsWith("/")) {
      await handleCommand(value);
      return;
    }
    const taskId = activeTaskId || getSessionId();
    const statusData = await fetchTaskStatus(taskId);
    const st = String(statusData?.status || "");
    if (isTerminalTaskStatus(st)) {
      setRunning(false);
      await handleCommand(value);
      return;
    }
    appendLine(`> ${value}`, "user");
    if (isMissionStatusQuery(value)) {
      await handleMissionStatusInquiry(value, { suppressUserEcho: true });
    } else {
      await steerActiveMission(value, {
        preempt: true,
        priority: 80,
        replaceGoal: true,
      });
    }
    return;
  }
  await handleCommand(value);
});

if (inputEl) {
  inputEl.addEventListener("input", () => {
    autoResizeCommandInput();
    refreshCommandSuggestions(inputEl.value);
  });
  inputEl.addEventListener("focus", () => {
    refreshCommandSuggestions(inputEl.value);
  });
  inputEl.addEventListener("keydown", (e) => {
    const menuOpen = commandSlashMenuEl && !commandSlashMenuEl.hidden;
    if (menuOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      moveSlashMenuSelection(e.key === "ArrowDown" ? 1 : -1, inputEl.value);
      return;
    }
    if (menuOpen && e.key === "Tab") {
      e.preventDefault();
      const selected = commandSlashMenuEl.querySelector(".command-slash-item.is-selected");
      const cmd = selected?.getAttribute("data-slash-cmd");
      if (cmd) applySlashCommand(cmd);
      return;
    }
    if (e.key === "Escape" && menuOpen) {
      e.preventDefault();
      hideSlashCommandMenu();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      hideSlashCommandMenu();
      formEl.requestSubmit();
    }
  });
  autoResizeCommandInput();
}

if (commandSlashMenuEl) {
  commandSlashMenuEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-slash-cmd]");
    if (!btn) return;
    applySlashCommand(String(btn.getAttribute("data-slash-cmd") || ""));
  });
}

document.addEventListener("click", (ev) => {
  if (!commandSlashMenuEl || commandSlashMenuEl.hidden) return;
  if (ev.target.closest(".command-input-wrap")) return;
  hideSlashCommandMenu();
});

if (stopBtnEl) {
  stopBtnEl.addEventListener("click", async () => {
    if (!(running || sessionHasInFlightMission)) return;
    stopBtnEl.disabled = true;
    try {
      await stopActiveMission();
    } finally {
      updateStopButtonState();
    }
  });
}

if (flowRefreshBtnEl) {
  flowRefreshBtnEl.addEventListener("click", async () => {
    await refreshFlowPanel(null, { preferStore: true });
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

if (historyRefreshBtnEl) {
  historyRefreshBtnEl.addEventListener("click", async () => {
    await refreshHistorySidebar();
  });
}

if (historyNewSessionBtnEl) {
  historyNewSessionBtnEl.addEventListener("click", () => {
    requestNewSession("侧栏");
  });
}

if (sessionBadgeEl) {
  sessionBadgeEl.addEventListener("click", () => {
    showSessionIdInfo();
  });
}

if (interactionModeSelectEl) {
  interactionModeSelectEl.addEventListener("change", () => {
    setInteractionMode(interactionModeSelectEl.value, { announce: true });
  });
}

if (historyListEl) {
  historyListEl.addEventListener("click", async (ev) => {
    const newInline = ev.target.closest("#history-new-session-inline");
    if (newInline) {
      requestNewSession("侧栏");
      return;
    }
    const deleteBtn = ev.target.closest("[data-history-delete]");
    if (deleteBtn) {
      ev.stopPropagation();
      const taskId = String(deleteBtn.getAttribute("data-history-delete") || "");
      if (taskId) await deleteHistoryTask(taskId);
      return;
    }
    const item = ev.target.closest("[data-history-task]");
    if (!item) return;
    const taskId = String(item.getAttribute("data-history-task") || "");
    if (taskId) selectHistorySession(taskId);
  });
}

if (sessionFilesRefreshBtnEl) {
  sessionFilesRefreshBtnEl.addEventListener("click", () => {
    refreshSessionFilesPane();
  });
}

if (sessionFilesUpBtnEl) {
  sessionFilesUpBtnEl.addEventListener("click", () => {
    sessionFilesCurrentPath = parentSessionPath(sessionFilesCurrentPath);
    refreshSessionFilesPane();
  });
}

if (sessionFilesListEl) {
  sessionFilesListEl.addEventListener("click", (ev) => {
    const item = ev.target.closest("[data-file-path]");
    if (!item) return;
    const type = String(item.getAttribute("data-file-type") || "");
    const path = String(item.getAttribute("data-file-path") || "");
    if (type !== "dir" || !path) return;
    sessionFilesCurrentPath = joinSessionPath(sessionFilesCurrentPath, path);
    refreshSessionFilesPane();
  });
  sessionFilesListEl.addEventListener("dblclick", (ev) => {
    const item = ev.target.closest("[data-file-path]");
    if (!item) return;
    const type = String(item.getAttribute("data-file-type") || "");
    if (type !== "file") return;
    const path = String(item.getAttribute("data-file-path") || "");
    const taskId = getSessionFilesTaskId();
    if (!path || !taskId) return;
    const fullPath = joinSessionPath(sessionFilesCurrentPath, path);
    openSessionFileViewer(taskId, fullPath);
  });
}

if (sessionFilesBreadcrumbEl) {
  sessionFilesBreadcrumbEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-breadcrumb-path]");
    if (!btn) return;
    const path = String(btn.getAttribute("data-breadcrumb-path") || ".").trim() || ".";
    if (path === sessionFilesCurrentPath) return;
    sessionFilesCurrentPath = path;
    refreshSessionFilesPane();
  });
}

if (themeSelectEl) {
  themeSelectEl.addEventListener("change", () => {
    applyTheme(themeSelectEl.value);
  });
}

async function warnIfSessionMissionInFlight() {
  const taskId = getSessionId();
  const data = await fetchTaskStatus(taskId);
  if (!data) return;
  const st = String(data.status || "");
  const executorActive = Boolean(data.executor_active);
  const pauseReason = String(data.pause_reason || "");
  sessionMissionExecutorActive = st === "MISSION_RUNNING" && executorActive;
  sessionHasInFlightMission = st === "MISSION_RUNNING" || st === "MISSION_PAUSED";
  if (st === "MISSION_RUNNING" && executorActive) {
    ensureFlowAutoRefresh();
  } else if (st === "MISSION_PAUSED") {
    if (pauseReason === "worker_lost") {
      appendLine(
        `Note: session ${taskId.slice(0, 8)}… 执行已中断 (worker_lost, node ${data.current_node})。` +
          " 直接输入为插入/纠偏（已写入状态）；不会自动续跑，请 /resume 或「继续写作」。",
        "system"
      );
    } else {
      appendLine(
        `Note: session ${taskId.slice(0, 8)}… is MISSION_PAUSED (node ${data.current_node}). ` +
          "直接输入「继续写作」等即可，由规划/会话策略理解意图；待确认时用 /confirm，不必先 /resume。",
        "system"
      );
    }
  }
  updateStopButtonState();
}

window.AgentChatRuntime = {
  getTaskId: () => activeTaskId || getSessionId(),
  apiFetch,
  appendSystemLine: (text) => appendLine(text, "system"),
};

updateSessionBadge(getSessionId());
syncInteractionModeUi(getInteractionMode());
applyTheme(localStorage.getItem(THEME_KEY) || "dark");
appendLine("Agent LangGraph Web CLI ready. Type /help for commands.", "system");
appendLine(
  `交互模式: ${INTERACTION_MODE_META[getInteractionMode()].label}（顶栏可切换；工程交付仅在沙箱内编译）`,
  "system"
);
refreshCommandSuggestions("");
updateStopButtonState();
warnIfSessionMissionInFlight();
fetchHealth();
refreshFlowPanel();
refreshHistorySidebar();
refreshSessionFilesPane();
startSessionFilesPolling();
initSkillsRail();
initRightRail();
function bootSkillsRail() {
  loadSkillsRailCatalog().then(() => {
    loadSkillsRailState();
    reconcileSkillsRailWithCatalog();
    renderSkillsRailList();
    syncSkillHeaderUi();
    loadSkillInputForm(activeSkillId);
    initSkillFromUrl();
  });
}

bootSkillsRail();
window.addEventListener("platform-auth-login", () => bootSkillsRail());
window.addEventListener("platform-auth-logout", () => bootSkillsRail());
window.addEventListener("platform-auth-expired", (e) => {
  const msg = e.detail?.message || "登录已过期或令牌无效，请重新登录后再试。";
  appendLine(msg, "error");
});
document.addEventListener("platform-auth-ready", () => bootSkillsRail(), { once: true });
