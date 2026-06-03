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

let selectedId = null;
let cloneSourceId = null;

function splitTools(raw) {
  return String(raw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseJsonField(raw, fallback) {
  const text = String(raw || "").trim();
  if (!text) return fallback;
  return JSON.parse(text);
}

function formToBody() {
  let output_contract = {};
  let examples = [];
  let input_form_schema = null;
  try {
    output_contract = parseJsonField(document.getElementById("f-output-contract").value, {});
  } catch (e) {
    throw new Error(`Output contract JSON: ${e.message}`);
  }
  try {
    examples = parseJsonField(document.getElementById("f-examples").value, []);
  } catch (e) {
    throw new Error(`Examples JSON: ${e.message}`);
  }
  try {
    const schemaRaw = document.getElementById("f-input-schema").value;
    if (String(schemaRaw || "").trim()) {
      input_form_schema = parseJsonField(schemaRaw, null);
    }
  } catch (e) {
    throw new Error(`Input form schema JSON: ${e.message}`);
  }
  const tags = splitTools(document.getElementById("f-tags").value);
  const body = {
    skill_id: document.getElementById("f-skill-id").value.trim() || undefined,
    name: document.getElementById("f-name").value.trim(),
    description: document.getElementById("f-description").value.trim(),
    summary: document.getElementById("f-description").value.trim(),
    category: document.getElementById("f-category").value.trim(),
    base_domain: document.getElementById("f-domain").value.trim(),
    preferred_execution_mode: document.getElementById("f-exec-mode").value,
    allowed_tools: splitTools(document.getElementById("f-allowed").value),
    blocked_tools: splitTools(document.getElementById("f-blocked").value),
    planning_overlay: document.getElementById("f-planning").value,
    reasoning_overlay: document.getElementById("f-reasoning").value,
    reflection_overlay: document.getElementById("f-reflection").value,
    risk_level: document.getElementById("f-risk").value,
    visibility: document.getElementById("f-visibility").value,
    output_contract,
    examples,
    tags: tags.length ? tags : ["custom"],
  };
  if (input_form_schema) {
    body.presentation = { input_form_schema };
  }
  return body;
}

async function loadManageList() {
  const status = document.getElementById("manage-status-filter").value;
  const res = await fetch(`/skills?scope=tenant&status=${status}`, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`list ${res.status}`);
  const data = await res.json();
  const list = document.getElementById("manage-skill-list");
  const skills = data.skills || [];
  if (!skills.length) {
    list.innerHTML = '<p class="flow-empty">暂无自定义技能。</p>';
    return;
  }
  list.innerHTML = skills
    .map(
      (s) =>
        `<article class="skill-card${s.skill_id === selectedId ? " selected" : ""}" data-id="${s.skill_id}">` +
        `<h3>${s.name}</h3><p class="meta">${s.status} · v${s.version}</p></article>`
    )
    .join("");
  list.querySelectorAll(".skill-card").forEach((el) => {
    el.addEventListener("click", () => openSkill(el.dataset.id));
  });
}

async function openSkill(skillId) {
  selectedId = skillId;
  cloneSourceId = null;
  document.getElementById("f-skill-id").disabled = true;
  const res = await fetch(`/skills/${encodeURIComponent(skillId)}`, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`get ${res.status}`);
  const data = await res.json();
  const d = data.definition || {};
  document.getElementById("f-skill-id").value = d.skill_id || "";
  document.getElementById("f-name").value = d.name || "";
  document.getElementById("f-description").value = d.description || "";
  document.getElementById("f-category").value = d.category || "general";
  document.getElementById("f-domain").value = d.base_domain || "single_turn";
  document.getElementById("f-exec-mode").value = d.preferred_execution_mode || "single";
  document.getElementById("f-allowed").value = (d.allowed_tools || []).join(", ");
  document.getElementById("f-blocked").value = (d.blocked_tools || []).join(", ");
  document.getElementById("f-planning").value = d.planning_overlay || "";
  document.getElementById("f-reasoning").value = d.reasoning_overlay || "";
  document.getElementById("f-reflection").value = d.reflection_overlay || "";
  document.getElementById("f-risk").value = d.risk_level || "LOW";
  document.getElementById("f-visibility").value = d.visibility || "tenant_private";
  document.getElementById("f-tags").value = (d.tags || []).join(", ");
  document.getElementById("f-output-contract").value = JSON.stringify(d.output_contract || {}, null, 2);
  document.getElementById("f-examples").value = JSON.stringify(d.examples || [], null, 2);
  const schema = (d.presentation && d.presentation.input_form_schema) || null;
  document.getElementById("f-input-schema").value = schema ? JSON.stringify(schema, null, 2) : "";
  await loadVersions(skillId);
  await loadManageList();
}

async function loadVersions(skillId) {
  const ul = document.getElementById("version-list");
  ul.innerHTML = "<li>加载中…</li>";
  const res = await fetch(`/skills/${encodeURIComponent(skillId)}/versions`, { headers: getAuthHeaders() });
  if (!res.ok) {
    ul.innerHTML = "<li>无版本记录</li>";
    return;
  }
  const data = await res.json();
  const versions = data.versions || [];
  if (!versions.length) {
    ul.innerHTML = "<li>尚无发布版本</li>";
    return;
  }
  ul.innerHTML = versions
    .map(
      (v) =>
        `<li><span>v${v.version} — ${v.published_at || ""}</span>` +
        `<button type="button" class="flow-btn" data-ver="${v.version}">回滚</button></li>`
    )
    .join("");
  ul.querySelectorAll("button[data-ver]").forEach((btn) => {
    btn.addEventListener("click", () => rollback(skillId, btn.dataset.ver));
  });
}

async function rollback(skillId, version) {
  if (!confirm(`回滚到 ${version}？`)) return;
  const res = await fetch(
    `/skills/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(version)}/rollback`,
    { method: "POST", headers: getAuthHeaders() }
  );
  if (!res.ok) alert(`回滚失败: ${res.status}`);
  else openSkill(skillId);
}

function newSkill() {
  selectedId = null;
  cloneSourceId = null;
  document.getElementById("skill-form").reset();
  document.getElementById("f-skill-id").disabled = false;
  document.getElementById("version-list").innerHTML = "";
}

document.getElementById("skill-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = formToBody();
  try {
    let res;
    if (selectedId) {
      delete body.skill_id;
      res = await fetch(`/skills/${encodeURIComponent(selectedId)}`, {
        method: "PUT",
        headers: getAuthHeaders(),
        body: JSON.stringify(body),
      });
    } else {
      res = await fetch("/skills", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify(body),
      });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `保存失败 ${res.status}`);
      return;
    }
    const data = await res.json();
    await openSkill(data.skill.skill_id);
  } catch (err) {
    alert(err.message || err);
  }
});

document.getElementById("btn-publish").addEventListener("click", async () => {
  if (!selectedId) return alert("请先选择或保存技能");
  const change_log = document.getElementById("f-changelog").value.trim();
  const res = await fetch(`/skills/${encodeURIComponent(selectedId)}/publish`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ change_log }),
  });
  if (!res.ok) alert(`发布失败: ${res.status}`);
  else openSkill(selectedId);
});

document.getElementById("btn-archive").addEventListener("click", async () => {
  if (!selectedId || !confirm("归档后不可再发布，确定？")) return;
  const res = await fetch(`/skills/${encodeURIComponent(selectedId)}/archive`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) alert(`归档失败: ${res.status}`);
  else loadManageList();
});

document.getElementById("btn-disable").addEventListener("click", async () => {
  if (!selectedId) return;
  const res = await fetch(`/skills/${encodeURIComponent(selectedId)}/disable`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) alert(`停用失败: ${res.status}`);
  else loadManageList();
});

document.getElementById("btn-delete").addEventListener("click", async () => {
  if (!selectedId || !confirm("确定删除？")) return;
  const res = await fetch(`/skills/${encodeURIComponent(selectedId)}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) alert(`删除失败: ${res.status}`);
  else {
    newSkill();
    loadManageList();
  }
});

document.getElementById("btn-clone-builtin").addEventListener("click", async () => {
  const source = cloneSourceId || selectedId || prompt("源 skill_id（如 code_review）");
  if (!source) return;
  const newId = prompt("新 skill_id", `${source}_custom`);
  if (!newId) return;
  const res = await fetch(`/skills/${encodeURIComponent(source)}/clone`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ new_skill_id: newId }),
  });
  if (!res.ok) alert(`克隆失败: ${res.status}`);
  else openSkill((await res.json()).skill.skill_id);
});

document.getElementById("btn-new").addEventListener("click", newSkill);
document.getElementById("btn-refresh-list").addEventListener("click", () => loadManageList().catch(alert));
document.getElementById("manage-status-filter").addEventListener("change", () => loadManageList().catch(alert));

const params = new URLSearchParams(window.location.search);
if (params.get("skill_id")) {
  openSkill(params.get("skill_id")).catch(alert);
} else {
  loadManageList().catch(alert);
}
