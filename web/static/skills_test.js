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

async function runDryRun() {
  const skillId = document.getElementById("test-skill-id").value.trim();
  const goal = document.getElementById("test-goal").value.trim();
  const out = document.getElementById("test-output");
  if (!skillId) {
    out.textContent = "请输入 skill_id";
    return;
  }
  out.textContent = "运行中…";
  const res = await fetch(`/skills/${encodeURIComponent(skillId)}/dry-run`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: JSON.stringify({ goal, skill_params: { goal } }),
  });
  const text = await res.text();
  try {
    out.textContent = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    out.textContent = text;
  }
}

document.getElementById("btn-dry-run").addEventListener("click", () => runDryRun().catch((e) => {
  document.getElementById("test-output").textContent = String(e);
}));

const params = new URLSearchParams(window.location.search);
if (params.get("skill_id")) {
  document.getElementById("test-skill-id").value = params.get("skill_id");
}
