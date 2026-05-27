const outputEl = document.getElementById("output");
const formEl = document.getElementById("command-form");
const inputEl = document.getElementById("command-input");
const envBadge = document.getElementById("env-badge");
const sessionBadgeEl = document.getElementById("session-badge");

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

let writingPendingText = "";
let writingFlushScheduled = false;
let writingTraceHintShown = false;
let thinkingPendingText = "";
let thinkingFlushScheduled = false;

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

function scrollOutputIfPinned() {
  scrollToBottomIfPinned(outputEl);
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
const LONGFORM_MISSION_KEY = "agent_longform_mission";
const MISSION_TOTAL_CHARS_KEY = "agent_mission_total_chars";
const MISSION_CHARS_PER_STEP_KEY = "agent_mission_chars_per_step";
const DEFAULT_MISSION_TOTAL_CHARS = 600000;
const DEFAULT_MISSION_CHARS_PER_STEP = 4000;

const longformToggleEl = document.getElementById("longform-mission-toggle");
const missionBarEl = document.getElementById("mission-bar");
const missionFieldsEl = document.getElementById("mission-fields");
const missionTotalCharsEl = document.getElementById("mission-total-chars");
const missionCharsPerStepEl = document.getElementById("mission-chars-per-step");

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
}

function startNewSession() {
  const id = newSessionId();
  localStorage.setItem(SESSION_KEY, id);
  updateSessionBadge(id);
  appendLine(`new session: ${id.slice(0, 8)}…`, "system");
  return id;
}

function clearScreen() {
  outputEl.replaceChildren();
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
  } else {
    stopRunTimer();
  }
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
  } catch {
    envBadge.textContent = "offline";
  }
}

async function listHistory() {
  const res = await fetch("/tasks?limit=10", { headers: getAuthHeaders() });
  if (!res.ok) {
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

async function steerActiveMission(message) {
  const taskId = activeTaskId || getSessionId();
  const res = await fetch(`/tasks/${taskId}/steer`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ message }),
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
    const taskIdRef = { id: taskId };
    activeTaskId = taskId;
    await consumeSseStream(res, taskIdRef);
    return { task_id: taskIdRef.id || taskId };
  } catch (err) {
    appendLine(`resume stream error: ${err}`, "error");
    return null;
  } finally {
    setRunning(false);
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
  }

  if (eventType === "task_created") {
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
    const msg = payload.message || "";
    if (msg) appendLine(msg, "system");
    if (payload.url) {
      window.__langgraphicsUrl = payload.url;
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

function isLongformMissionEnabled() {
  return Boolean(longformToggleEl && longformToggleEl.checked);
}

function syncMissionBarUi() {
  if (!longformToggleEl || !missionBarEl || !missionFieldsEl) return;
  const on = longformToggleEl.checked;
  missionFieldsEl.hidden = !on;
  missionBarEl.classList.toggle("mission-on", on);
  localStorage.setItem(LONGFORM_MISSION_KEY, on ? "1" : "0");
  if (missionTotalCharsEl && missionTotalCharsEl.value) {
    localStorage.setItem(MISSION_TOTAL_CHARS_KEY, missionTotalCharsEl.value);
  }
  if (missionCharsPerStepEl && missionCharsPerStepEl.value) {
    localStorage.setItem(MISSION_CHARS_PER_STEP_KEY, missionCharsPerStepEl.value);
  }
}

function restoreMissionBarUi() {
  if (!longformToggleEl) return;
  longformToggleEl.checked = localStorage.getItem(LONGFORM_MISSION_KEY) === "1";
  if (missionTotalCharsEl) {
    const saved = localStorage.getItem(MISSION_TOTAL_CHARS_KEY);
    if (saved) missionTotalCharsEl.value = saved;
  }
  if (missionCharsPerStepEl) {
    const saved = localStorage.getItem(MISSION_CHARS_PER_STEP_KEY);
    if (saved) missionCharsPerStepEl.value = saved;
  }
  syncMissionBarUi();
}

/** Explicit writing mission contract — only when UI toggle is on (no goal parsing). */
function buildWritingMissionBody(goal, riskLevel = "LOW") {
  const totalRaw = missionTotalCharsEl && missionTotalCharsEl.value.trim();
  const stepRaw = missionCharsPerStepEl && missionCharsPerStepEl.value.trim();
  const totalTarget = totalRaw ? parseInt(totalRaw, 10) : DEFAULT_MISSION_TOTAL_CHARS;
  const charsPerStep = stepRaw ? parseInt(stepRaw, 10) : DEFAULT_MISSION_CHARS_PER_STEP;
  return {
    task_type: "qa",
    user_id: "web",
    session_id: getSessionId(),
    new_session: false,
    input_payload: {
      goal,
      risk_level: riskLevel,
      execution_mode: "mission",
      mission_auto: false,
      mission: {
        kind: "writing",
        objective: goal,
        total_target_chars: Number.isFinite(totalTarget) ? totalTarget : DEFAULT_MISSION_TOTAL_CHARS,
        autonomous: true,
        step_policy: {
          first_step: "outline",
          then: "append_body",
          chars_per_step: Number.isFinite(charsPerStep)
            ? charsPerStep
            : DEFAULT_MISSION_CHARS_PER_STEP,
        },
      },
    },
  };
}

function buildDefaultTaskBody(goal, riskLevel = "LOW") {
  return {
    task_type: "qa",
    user_id: "web",
    session_id: getSessionId(),
    new_session: false,
    input_payload: {
      goal,
      risk_level: riskLevel,
      // Allow planning LLM to auto-enable mission when appropriate (UI toggle still overrides).
      mission_auto: true,
    },
  };
}

function buildTaskRequestBody(goal, riskLevel = "LOW") {
  if (isLongformMissionEnabled()) {
    return buildWritingMissionBody(goal, riskLevel);
  }
  return buildDefaultTaskBody(goal, riskLevel);
}

async function runTaskStream(goal, riskLevel = "LOW", endpoint = "/tasks/stream", body = null) {
  setRunning(true);
  shownConfirmationKeys.clear();
  writingStreamCharsThisTurn = 0;
  appendLine(`> ${goal}`, "user");
  const requestBody = body || buildTaskRequestBody(goal, riskLevel);
  if (!body && isLongformMissionEnabled()) {
    const m = requestBody.input_payload.mission || {};
    const sp = m.step_policy || {};
    appendLine(
      `mission: writing | total=${m.total_target_chars} | step=${sp.chars_per_step} | outline→append`,
      "system"
    );
  }

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

    const taskIdRef = { id: null };
    await consumeSseStream(res, taskIdRef);
  } catch (err) {
    appendLine(`stream error: ${err}`, "error");
  } finally {
    setRunning(false);
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
  appendLine("  /mission on|off   强开长篇 Mission（勾选同）；未开时由规划模型自动判定", "system");
  appendLine("  /langgraphics       显示 LangGraph 可视化地址与开启说明", "system");
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
    startNewSession();
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
    return;
  }
  if (text === "/mission" || text.startsWith("/mission ")) {
    const arg = text === "/mission" ? "" : text.slice("/mission ".length).trim().toLowerCase();
    if (arg === "on" || arg === "1" || arg === "true") {
      if (longformToggleEl) longformToggleEl.checked = true;
      syncMissionBarUi();
      appendLine("长篇 Mission 已开启（下次提交将带显式 mission 合同）", "system");
      return;
    }
    if (arg === "off" || arg === "0" || arg === "false") {
      if (longformToggleEl) longformToggleEl.checked = false;
      syncMissionBarUi();
      appendLine("长篇 Mission 已关闭", "system");
      return;
    }
    appendLine(
      `长篇 Mission: ${isLongformMissionEnabled() ? "on" : "off"} (use /mission on|off)`,
      "system"
    );
    return;
  }

  await runTaskStream(text, "LOW");
}

formEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = inputEl.value.trim();
  inputEl.value = "";
  if (!value) return;
  if (running) {
    appendLine(`> ${value}`, "user");
    await steerActiveMission(value);
    return;
  }
  await handleCommand(value);
});

if (longformToggleEl) {
  longformToggleEl.addEventListener("change", syncMissionBarUi);
  if (missionTotalCharsEl) {
    missionTotalCharsEl.addEventListener("change", syncMissionBarUi);
  }
  if (missionCharsPerStepEl) {
    missionCharsPerStepEl.addEventListener("change", syncMissionBarUi);
  }
  restoreMissionBarUi();
}

async function warnIfSessionMissionInFlight() {
  const taskId = getSessionId();
  try {
    const res = await fetch(`/tasks/${taskId}/status`, { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const st = String(data.status || "");
    if (st === "MISSION_RUNNING" || st === "MISSION_PAUSED") {
      appendLine(
        `Note: session ${taskId.slice(0, 8)}… is ${st} (node ${data.current_node}). ` +
          "While a run is active, new input is steered; after refresh, submit steers via session turn — " +
          "wait for pause or use /confirm if a gate is shown.",
        "system"
      );
    }
  } catch {
    /* ignore */
  }
}

updateSessionBadge(getSessionId());
appendLine("Agent LangGraph Web CLI ready. Type /help for commands.", "system");
warnIfSessionMissionInFlight();
if (isLongformMissionEnabled()) {
  appendLine("长篇 Mission 已开启 — 提交时将自动附带 writing mission 合同", "system");
}
fetchHealth();
