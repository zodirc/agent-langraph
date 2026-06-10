/**
 * Shared auth, theme, and runtime status for all platform pages.
 */
(function initPlatformAuth(global) {
  const TOKEN_KEY = "agent_access_token";
  const ROLE_KEY = "user_role";
  const TENANT_KEY = "tenant_id";
  const USER_KEY = "user_id";
  const THEME_KEY = "agent_theme";
  const PANEL_COLLAPSED_KEY = "platform_auth_panel_collapsed";

  const THEMES = [
    { id: "dark", label: "深色", family: "dark" },
    { id: "light", label: "淡色", family: "light" },
    { id: "warm", label: "暖夜", family: "dark" },
    { id: "warm-light", label: "暖纸", family: "light" },
    { id: "forest", label: "森林", family: "dark" },
    { id: "rose", label: "玫瑰", family: "light" },
    { id: "ocean", label: "海洋", family: "dark" },
  ];

  const THEME_IDS = THEMES.map((t) => t.id);

  function normalizeTheme(theme) {
    const id = String(theme || "dark").trim();
    return THEME_IDS.includes(id) ? id : "dark";
  }

  function getThemeFamily(themeId) {
    const item = THEMES.find((t) => t.id === themeId);
    return item?.family || "dark";
  }

  function getThemeId() {
    return normalizeTheme(localStorage.getItem(THEME_KEY) || "dark");
  }

  function populateThemeSelect(selectEl) {
    if (!selectEl) return;
    selectEl.innerHTML = THEMES.map(
      (t) => `<option value="${t.id}">${t.label}</option>`
    ).join("");
    selectEl.value = getThemeId();
  }

  function applyThemeClasses(root, themeId) {
    const family = getThemeFamily(themeId);
    for (const id of THEME_IDS) {
      root.classList.remove(`theme-${id}`);
    }
    root.classList.add(`theme-${themeId}`);
    root.dataset.themeFamily = family;
    return family;
  }

  let navUiAttached = false;
  let loginInFlight = false;
  let skipVerifyUntil = 0;
  let authExpiredNotifyUntil = 0;

  /** @type {{ authEnabled: boolean | null, env: string, version: string, status: string }} */
  let runtimeMeta = { authEnabled: null, env: "", version: "", status: "" };

  function migrateLegacyToken() {
    const legacy = localStorage.getItem("auth_token");
    if (legacy && !localStorage.getItem(TOKEN_KEY)) {
      localStorage.setItem(TOKEN_KEY, legacy);
      localStorage.removeItem("auth_token");
    }
  }

  function getToken() {
    migrateLegacyToken();
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function parseApiError(payload, status) {
    if (!payload) return `登录失败 (${status})`;
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => d.msg || d.message || JSON.stringify(d)).join("; ");
    }
    if (typeof detail === "object") return JSON.stringify(detail);
    return `登录失败 (${status})`;
  }

  function getAuthHeaders() {
    const headers = { "Content-Type": "application/json", Accept: "application/json" };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const role = localStorage.getItem(ROLE_KEY);
    if (role) headers["X-User-Role"] = role;
    const tenant = localStorage.getItem(TENANT_KEY);
    if (tenant) headers["X-Tenant-Id"] = tenant;
    return headers;
  }

  function getState() {
    const token = getToken();
    const userId = localStorage.getItem(USER_KEY) || "";
    const role = localStorage.getItem(ROLE_KEY) || "";
    const tenantId = localStorage.getItem(TENANT_KEY) || "";
    const authEnabled = runtimeMeta.authEnabled;

    let authStatus = "checking";
    if (authEnabled === false) authStatus = "disabled";
    else if (token) authStatus = "authenticated";
    else if (authEnabled === true || authEnabled === null) authStatus = "anonymous";

    return { token, userId, role, tenantId, authEnabled, authStatus };
  }

  function applyTheme(theme) {
    const t = normalizeTheme(theme);
    applyThemeClasses(document.documentElement, t);
    applyThemeClasses(document.body, t);
    localStorage.setItem(THEME_KEY, t);
    const sel = document.getElementById("platform-theme-select");
    if (sel) sel.value = t;
    const chatSel = document.getElementById("theme-select");
    if (chatSel) chatSel.value = t;
    global.dispatchEvent(
      new CustomEvent("platform-theme-change", { detail: { theme: t, family: getThemeFamily(t) } })
    );
  }

  function initThemeFromStorage() {
    applyTheme(localStorage.getItem(THEME_KEY) || "dark");
  }

  async function fetchRuntimeMeta() {
    try {
      const res = await fetch("/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      runtimeMeta = {
        authEnabled: Boolean(data.auth_enabled),
        env: String(data.env || ""),
        version: String(data.version || ""),
        status: String(data.status || "ok"),
      };
      return runtimeMeta;
    } catch {
      runtimeMeta = { authEnabled: null, env: "", version: "", status: "offline" };
      return runtimeMeta;
    }
  }

  function setLoginMessage(text, kind) {
    const errEl = document.getElementById("platform-login-error");
    if (!errEl) return;
    errEl.textContent = text || "";
    errEl.classList.remove("login-msg-error", "login-msg-success", "login-msg-info");
    if (kind) errEl.classList.add(`login-msg-${kind}`);
  }

  function setLoginSubmitting(busy) {
    const btn = document.getElementById("platform-login-submit");
    if (!btn) return;
    btn.disabled = busy;
    btn.textContent = busy ? "登录中…" : "确认登录";
  }

  function getAuthPanel() {
    return document.getElementById("platform-auth-panel");
  }

  function getAuthBadge() {
    return document.getElementById("platform-auth-badge");
  }

  function isAuthPanelOpen() {
    const panel = getAuthPanel();
    return Boolean(panel && !panel.hidden);
  }

  function setBadgeExpanded(open) {
    const badge = getAuthBadge();
    if (!badge) return;
    badge.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function closeAuthPanel(clearMessage) {
    const panel = getAuthPanel();
    if (panel) panel.hidden = true;
    if (clearMessage) setLoginMessage("");
    setBadgeExpanded(false);
    sessionStorage.setItem(PANEL_COLLAPSED_KEY, "1");
  }

  function openAuthPanel() {
    const panel = getAuthPanel();
    if (!panel) return;
    panel.hidden = false;
    setLoginMessage("");
    setBadgeExpanded(true);
    sessionStorage.removeItem(PANEL_COLLAPSED_KEY);
    const state = getState();
    if (state.authStatus === "anonymous") {
      document.getElementById("platform-login-pass")?.focus();
    }
  }

  function toggleAuthPanel() {
    if (isAuthPanelOpen()) closeAuthPanel(true);
    else openAuthPanel();
  }

  function shouldDefaultOpenPanel(state) {
    if (state.authStatus === "disabled") return false;
    if (state.token) return false;
    if (sessionStorage.getItem(PANEL_COLLAPSED_KEY) === "1") return false;
    return state.authStatus === "anonymous";
  }

  function syncPanelBody(state, verified) {
    const panelTitle = document.getElementById("platform-auth-panel-title");
    const loginForm = document.getElementById("platform-login-form");
    const accountView = document.getElementById("platform-auth-account-view");
    const disabledView = document.getElementById("platform-auth-disabled-view");

    const isDisabled = state.authStatus === "disabled";
    const isLoggedIn =
      Boolean(state.token) && state.authStatus === "authenticated" && verified;

    if (disabledView) disabledView.hidden = !isDisabled;
    if (loginForm) loginForm.hidden = isDisabled || isLoggedIn;
    if (accountView) accountView.hidden = isDisabled || !isLoggedIn;

    if (panelTitle) {
      if (isDisabled) panelTitle.textContent = "认证已关闭";
      else if (isLoggedIn) panelTitle.textContent = "账号详情";
      else panelTitle.textContent = "登录";
    }

    if (isLoggedIn) {
      const statusEl = document.getElementById("auth-detail-status");
      const userEl = document.getElementById("auth-detail-user");
      const roleEl = document.getElementById("auth-detail-role");
      const tenantEl = document.getElementById("auth-detail-tenant");
      if (statusEl) statusEl.textContent = verified ? "已验证" : "令牌无效";
      if (userEl) userEl.textContent = state.userId || "—";
      if (roleEl) roleEl.textContent = state.role || "—";
      if (tenantEl) tenantEl.textContent = state.tenantId || "（默认）";
    }
  }

  async function login(username, password, tenantId) {
    if (!password) throw new Error("请输入密码");
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const raw = await res.text();
    let payload = null;
    try {
      payload = raw ? JSON.parse(raw) : null;
    } catch {
      payload = null;
    }
    if (!res.ok) {
      throw new Error(parseApiError(payload, res.status));
    }
    const data = payload || {};
    if (!data.access_token) throw new Error("登录响应缺少 access_token");
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, data.user_id || username);
    localStorage.setItem(ROLE_KEY, data.role || "user");
    const tid = tenantId || data.tenant_id || "";
    if (tid) localStorage.setItem(TENANT_KEY, tid);
    else localStorage.removeItem(TENANT_KEY);
    localStorage.removeItem("auth_token");
    skipVerifyUntil = Date.now() + 8000;
    await fetchRuntimeMeta();
    await renderNavAuth();
    return data;
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(TENANT_KEY);
    localStorage.removeItem("auth_token");
    skipVerifyUntil = 0;
    authExpiredNotifyUntil = 0;
    sessionStorage.removeItem(PANEL_COLLAPSED_KEY);
    setLoginMessage("");
    renderNavAuth().then(() => openAuthPanel());
  }

  /**
   * Clear invalid JWT and surface re-login UI (debounced notifications).
   */
  async function handleUnauthorizedResponse(res) {
    if (!res || res.status !== 401) return false;
    if (runtimeMeta.authEnabled === null) await fetchRuntimeMeta();
    if (runtimeMeta.authEnabled === false) return false;

    const hadToken = Boolean(getToken());
    const now = Date.now();
    const shouldNotify = now >= authExpiredNotifyUntil;
    if (shouldNotify) authExpiredNotifyUntil = now + 4000;

    if (hadToken) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem("auth_token");
      skipVerifyUntil = 0;
    }

    const message = hadToken
      ? "登录已过期或令牌无效，请重新登录。"
      : "需要登录后才能继续操作。";
    if (shouldNotify) {
      setLoginMessage(message, "error");
      global.dispatchEvent(
        new CustomEvent("platform-auth-expired", { detail: { hadToken, message } })
      );
    }

    await renderNavAuth();
    openAuthPanel();
    return true;
  }

  async function authFetch(url, options = {}) {
    const headers = { ...getAuthHeaders(), ...(options.headers || {}) };
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) await handleUnauthorizedResponse(res);
    return res;
  }

  async function verifyToken() {
    const token = getToken();
    if (!token || runtimeMeta.authEnabled === false) return true;
    if (Date.now() < skipVerifyUntil) return true;
    try {
      const res = await authFetch("/skills?scope=all&status=published");
      if (res.status === 401) return false;
      return res.ok;
    } catch {
      return false;
    }
  }

  function authBadgeClass(state, verified) {
    if (state.authStatus === "checking") return "auth-checking";
    if (state.authStatus === "disabled") return "auth-disabled";
    if (state.authStatus === "anonymous") return "auth-anonymous";
    if (!verified) return "auth-invalid";
    return "auth-ok";
  }

  function authBadgeLabel(state, verified, meta) {
    if (state.authStatus === "checking") return "验证中…";
    if (meta.status === "offline") return "运行时离线";
    if (state.authStatus === "disabled") return "免认证";
    if (state.authStatus === "anonymous") return "未登录 · 点击登录";
    if (!verified) return "令牌无效 · 点击处理";
    const who = state.userId || "用户";
    return `已登录 · ${who}`;
  }

  function authBadgeTitle(state, verified, meta) {
    if (state.authStatus === "checking") return "正在检查认证状态，点击展开";
    if (meta.status === "offline") return "运行时不可用";
    if (state.authStatus === "disabled") return "服务端 AUTH_ENABLED=false，点击说明";
    if (state.authStatus === "anonymous") return "点击展开登录表单";
    if (!verified) return "JWT 无效或已过期，点击重新登录";
    const role = state.role ? `，角色 ${state.role}` : "";
    const tenant = state.tenantId ? `，租户 ${state.tenantId}` : "";
    return `已验证${role}${tenant}。点击查看详情或退出`;
  }

  function runtimeBadgeText(meta) {
    if (meta.status === "offline") return "offline";
    const env = meta.env || "runtime";
    const ver = meta.version ? ` v${meta.version}` : "";
    return `${env}${ver}`;
  }

  async function renderNavAuth() {
    const badge = getAuthBadge();
    const runtimeEl = document.getElementById("platform-runtime-badge");
    if (!badge) return;

    const state = getState();
    const meta = runtimeMeta;
    let verified = true;
    if (state.authStatus === "authenticated") {
      verified = await verifyToken();
    }

    badge.className = `platform-auth-badge ${authBadgeClass(state, verified)}`;
    badge.textContent = authBadgeLabel(state, verified, meta);
    badge.title = authBadgeTitle(state, verified, meta);

    if (runtimeEl) {
      runtimeEl.textContent = runtimeBadgeText(meta);
      runtimeEl.classList.toggle("runtime-offline", meta.status === "offline");
    }

    syncPanelBody(state, verified);

    if (!isAuthPanelOpen() && shouldDefaultOpenPanel(state)) {
      openAuthPanel();
    }
  }

  async function handleLoginSubmit() {
    if (loginInFlight) return;
    const loginForm = document.getElementById("platform-login-form");
    if (!loginForm) return;

    const user =
      document.getElementById("platform-login-user")?.value?.trim() ||
      loginForm.querySelector('[name="username"]')?.value?.trim() ||
      "admin";
    const pass = document.getElementById("platform-login-pass")?.value || "";
    const tenant = document.getElementById("platform-login-tenant")?.value?.trim() || "";

    setLoginMessage("正在登录…", "info");
    setLoginSubmitting(true);
    loginInFlight = true;

    try {
      const data = await login(user, pass, tenant);
      setLoginMessage(`登录成功：${data.user_id} (${data.role})`, "success");
      global.dispatchEvent(new CustomEvent("platform-auth-login", { detail: data }));
      window.setTimeout(() => {
        closeAuthPanel(false);
        setLoginMessage("");
      }, 1200);
    } catch (err) {
      setLoginMessage(err.message || String(err), "error");
    } finally {
      loginInFlight = false;
      setLoginSubmitting(false);
    }
  }

  function attachNavUi() {
    if (navUiAttached) return;
    const badge = getAuthBadge();
    if (!badge) return;
    navUiAttached = true;

    const panel = getAuthPanel();
    const panelClose = document.getElementById("platform-auth-panel-close");
    const logoutBtn = document.getElementById("platform-logout-btn");
    const themeSel = document.getElementById("platform-theme-select");
    const submitBtn = document.getElementById("platform-login-submit");
    const loginForm = document.getElementById("platform-login-form");

    if (themeSel) {
      populateThemeSelect(themeSel);
      themeSel.addEventListener("change", () => applyTheme(themeSel.value));
    }

    badge.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (isAuthPanelOpen()) {
        closeAuthPanel(true);
        return;
      }
      openAuthPanel();
      await renderNavAuth();
    });

    if (panelClose) {
      panelClose.addEventListener("click", (e) => {
        e.stopPropagation();
        closeAuthPanel(true);
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isAuthPanelOpen()) closeAuthPanel(true);
    });

    document.addEventListener("mousedown", (e) => {
      if (!isAuthPanelOpen() || !panel) return;
      const target = e.target;
      if (panel.contains(target)) return;
      if (badge.contains(target)) return;
      closeAuthPanel(true);
    });

    if (loginForm) {
      loginForm.addEventListener("submit", (e) => {
        e.preventDefault();
        handleLoginSubmit();
      });
    }

    if (submitBtn) {
      submitBtn.addEventListener("click", (e) => {
        e.preventDefault();
        handleLoginSubmit();
      });
    }

    if (logoutBtn) {
      logoutBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        logout();
        global.dispatchEvent(new CustomEvent("platform-auth-logout"));
      });
    }
  }

  async function init() {
    migrateLegacyToken();
    initThemeFromStorage();
    attachNavUi();
    await fetchRuntimeMeta();
    await renderNavAuth();
  }

  const api = {
    TOKEN_KEY,
    ROLE_KEY,
    TENANT_KEY,
    USER_KEY,
    THEME_KEY,
    THEMES,
    THEME_IDS,
    normalizeTheme,
    getThemeId,
    getThemeFamily,
    populateThemeSelect,
    getAuthHeaders,
    authFetch,
    handleUnauthorizedResponse,
    getState,
    getToken,
    login,
    logout,
    applyTheme,
    initThemeFromStorage,
    fetchRuntimeMeta,
    renderNavAuth,
    verifyToken,
    attachNavUi,
    closeAuthPanel,
    openAuthPanel,
    toggleAuthPanel,
    closeLoginPanel: closeAuthPanel,
    openLoginPanel: openAuthPanel,
    toggleLoginPanel: toggleAuthPanel,
    getRuntimeMeta: () => ({ ...runtimeMeta }),
  };

  global.PlatformAuth = api;
  global.dispatchEvent(new CustomEvent("platform-auth-ready"));
  global.addEventListener("platform-nav-ready", () => {
    attachNavUi();
    renderNavAuth();
  });
  init().catch(() => renderNavAuth());
})(typeof window !== "undefined" ? window : globalThis);
