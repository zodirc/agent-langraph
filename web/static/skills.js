function getAuthHeaders() {
  if (window.PlatformAuth) return window.PlatformAuth.getAuthHeaders();
  const headers = { "Content-Type": "application/json", Accept: "application/json" };
  const token = localStorage.getItem("agent_access_token") || localStorage.getItem("auth_token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const role = localStorage.getItem("user_role");
  if (role) headers["X-User-Role"] = role;
  const tenant = localStorage.getItem("tenant_id");
  if (tenant) headers["X-Tenant-Id"] = tenant;
  return headers;
}

const SCENARIO_LABELS = {
  general: "通用",
  code: "代码",
  document: "文档",
  analysis: "分析",
  writing: "写作",
};

const DOMAIN_LABELS = {
  single_turn: "单轮问答",
  code: "代码",
  document: "文档",
  analysis: "分析",
  writing: "写作",
  general: "通用",
};

const SOURCE_LABELS = {
  system: "平台内置",
  tenant: "自定义",
};

let selectedSkillId = null;
let allSkills = [];

function labelFor(map, id) {
  return map[id] || id;
}

function sourceBadge(sourceType) {
  const isCustom = sourceType === "tenant";
  const cls = isCustom ? "skill-origin-custom" : "skill-origin-builtin";
  const text = isCustom ? "自定义" : "内置";
  return `<span class="skill-origin-badge ${cls}">${text}</span>`;
}

async function fetchSkills() {
  const q = document.getElementById("skill-q")?.value?.trim() || "";
  const domain = document.getElementById("skill-domain")?.value || "";
  const category = document.getElementById("skill-category")?.value || "";
  const source = document.getElementById("skill-source")?.value || "";
  const params = new URLSearchParams({ status: "published" });
  params.set("scope", source || "all");
  if (q) params.set("q", q);
  if (domain) params.set("domain", domain);
  if (category) params.set("category", category);
  const res = await fetch(`/skills?${params}`, { headers: getAuthHeaders() });
  if (!res.ok) {
    const errBody = await res.json().catch(() => ({}));
    const detail = errBody.detail || errBody.message;
    throw new Error(detail ? String(detail) : `HTTP ${res.status}`);
  }
  const data = await res.json();
  allSkills = data.skills || [];
  renderSkillList();
}

function fillSelectOptions(sel, items, labelMap) {
  if (!sel) return;
  const current = sel.value;
  while (sel.options.length > 1) sel.remove(1);
  for (const item of items || []) {
    const opt = document.createElement("option");
    opt.value = item.id;
    const label = labelFor(labelMap, item.id);
    opt.textContent = `${label} (${item.count})`;
    sel.appendChild(opt);
  }
  if (current && [...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  }
}

async function fetchCategories() {
  const res = await fetch("/skills/catalog/categories", { headers: getAuthHeaders() });
  if (!res.ok) {
    if (res.status === 503) throw new Error("Skill 平台未启用，请检查 config 中 skill.enabled");
    return;
  }
  const data = await res.json();
  const sourceSel = document.getElementById("skill-source");
  const domainSel = document.getElementById("skill-domain");
  const catSel = document.getElementById("skill-category");
  for (const group of data.groups || []) {
    if (group.type === "source") fillSelectOptions(sourceSel, group.items, SOURCE_LABELS);
    else if (group.type === "scenario" || group.type === "category") {
      fillSelectOptions(catSel, group.items, SCENARIO_LABELS);
    } else if (group.type === "domain") {
      fillSelectOptions(domainSel, group.items, DOMAIN_LABELS);
    }
  }
}

function renderSkillList() {
  const list = document.getElementById("skills-list");
  if (!list) return;
  if (!allSkills.length) {
    list.innerHTML = '<p class="flow-empty">没有匹配的技能。</p>';
    return;
  }
  list.innerHTML = allSkills
    .map(
      (s) => `
    <article class="skill-card${s.skill_id === selectedSkillId ? " selected" : ""}" data-id="${s.skill_id}">
      <div class="skill-card-head">
        <h3>${escapeHtml(s.name)}</h3>
        ${sourceBadge(s.source_type)}
      </div>
      <p class="meta">${labelFor(SCENARIO_LABELS, s.category)} · ${labelFor(DOMAIN_LABELS, s.base_domain)} · v${escapeHtml(s.version)}</p>
      <p>${escapeHtml(s.summary || s.description || "")}</p>
    </article>`
    )
    .join("");
  list.querySelectorAll(".skill-card").forEach((el) => {
    el.addEventListener("click", () => selectSkill(el.dataset.id));
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function selectSkill(skillId) {
  selectedSkillId = skillId;
  renderSkillList();
  const panel = document.getElementById("skills-detail");
  if (!panel) return;
  panel.innerHTML = '<p class="flow-empty">加载详情…</p>';
  const res = await fetch(`/skills/${encodeURIComponent(skillId)}`, { headers: getAuthHeaders() });
  if (!res.ok) {
    panel.innerHTML = `<p class="flow-empty">加载失败: ${res.status}</p>`;
    return;
  }
  const data = await res.json();
  const d = data.definition || {};
  const tools = data.tools || {};
  const examples = (data.examples || [])
    .map((ex) => `<li><strong>输入</strong>: ${escapeHtml(ex.input || "")}</li>`)
    .join("");
  const prompts = (data.presentation?.example_prompts || [])
    .map((p) => `<li>${escapeHtml(p)}</li>`)
    .join("");
  const origin =
    d.source_type === "tenant"
      ? "自定义（本租户在 Skills 管理页创建）"
      : "平台内置（config/skills 预置）";
  panel.innerHTML = `
    <h2>${escapeHtml(d.name || skillId)}</h2>
    <p>${escapeHtml(d.description || "")}</p>
    <p><strong>来源</strong>: ${escapeHtml(origin)}</p>
    <p><strong>场景</strong>: ${escapeHtml(labelFor(SCENARIO_LABELS, d.category || ""))} <span class="skills-detail-muted">（目录分类）</span></p>
    <p><strong>运行域</strong>: ${escapeHtml(labelFor(DOMAIN_LABELS, d.base_domain || ""))} <span class="skills-detail-muted">（Runtime 工具包 / 图路由）</span></p>
    <p><strong>执行模式</strong>: ${escapeHtml(d.preferred_execution_mode || "single")}</p>
    <p><strong>工具</strong>: ${escapeHtml((tools.allowed || []).join(", ") || "(默认)")}</p>
    <p><strong>输出契约</strong>: <code>${escapeHtml(JSON.stringify(d.output_contract || {}))}</code></p>
    ${prompts ? `<p><strong>示例提示</strong></p><ul>${prompts}</ul>` : ""}
    ${examples ? `<p><strong>示例</strong></p><ul>${examples}</ul>` : ""}
    <button type="button" class="flow-btn use-btn" id="use-skill-btn">在对话页使用此 Skill</button>
  `;
  const pres = data.presentation || {};
  document.getElementById("use-skill-btn")?.addEventListener("click", () => useSkillInCli(skillId, pres));
}

function useSkillInCli(skillId, presentation) {
  const prompts = presentation?.example_prompts || [];
  const prompt = prompts[0] || `使用 skill ${skillId} 完成任务`;
  const params = new URLSearchParams({
    skill_id: skillId,
    goal: prompt,
  });
  window.location.href = `/chat?${params.toString()}`;
}

function bindFilters() {
  document.getElementById("skill-refresh")?.addEventListener("click", () => fetchSkills().catch(showErr));
  document.getElementById("skill-q")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchSkills().catch(showErr);
  });
  document.getElementById("skill-source")?.addEventListener("change", () => fetchSkills().catch(showErr));
  document.getElementById("skill-domain")?.addEventListener("change", () => fetchSkills().catch(showErr));
  document.getElementById("skill-category")?.addEventListener("change", () => fetchSkills().catch(showErr));
}

function showErr(err) {
  const list = document.getElementById("skills-list");
  if (list) list.innerHTML = `<p class="flow-empty">错误: ${escapeHtml(err.message || err)}</p>`;
}

function applyUrlSkill() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get("skill_id");
  if (id) selectSkill(id).catch(showErr);
}

async function loadPackageCount() {
  try {
    const res = await fetch("/skills/marketplace/packages", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const el = document.getElementById("pkg-count");
    if (el) el.textContent = String(data.total ?? 0);
  } catch {
    /* optional */
  }
}

document.addEventListener("DOMContentLoaded", () => {
  bindFilters();
  loadPackageCount();
  fetchCategories()
    .then(() => fetchSkills())
    .then(applyUrlSkill)
    .catch(showErr);
});

window.addEventListener("platform-auth-login", () => fetchSkills().catch(showErr));
window.addEventListener("platform-auth-logout", () => fetchSkills().catch(showErr));
