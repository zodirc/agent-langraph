/**
 * 统一顶栏：平台、对话、Skills，以及主题与鉴权。
 *
 * Unified top navigation with theme and auth.
 * Requires platform-auth.js. Set <body data-active-page="home|chat|skills|manage|test|dashboard">.
 */
(function initPlatformNav() {
  const mount = document.getElementById("platform-nav");
  if (!mount) return;

  const active = document.body.dataset.activePage || "";
  const links = [
    { href: "/", page: "home", label: "平台" },
    { href: "/chat", page: "chat", label: "对话" },
    { href: "/skills", page: "skills", label: "Skills" },
  ];

  const extras = (document.body.dataset.navExtra || "").split(/\s+/).filter(Boolean);
  if (extras.includes("manage")) {
    links.push({ href: "/skills/manage", page: "manage", label: "管理" });
  }
  if (extras.includes("test")) {
    links.push({ href: "/skills/test", page: "test", label: "测试台" });
  }

  const brand = document.body.dataset.navBrand || "Agent LangGraph";

  mount.className = "platform-nav";
  mount.innerHTML = `
    <div class="platform-nav-start">
      <a class="platform-brand" href="/">${brand}</a>
      <nav class="platform-links" aria-label="主菜单">${links
        .map(
          (l) =>
            `<a href="${l.href}" class="platform-link${active === l.page ? " active" : ""}">${l.label}</a>`
        )
        .join("")}</nav>
    </div>
    <div class="platform-nav-end">
      <label class="platform-theme-label" title="主题">
        <span class="sr-only">主题</span>
        <select id="platform-theme-select" class="platform-theme-select">
          <option value="dark">深色</option>
          <option value="light">淡色</option>
        </select>
      </label>
      <span id="platform-runtime-badge" class="platform-runtime-badge">…</span>
      <div class="platform-auth-slot">
        <button
          type="button"
          id="platform-auth-badge"
          class="platform-auth-badge auth-checking"
          aria-expanded="false"
          aria-controls="platform-auth-panel"
          title="点击查看账号详情"
        >验证中…</button>
        <div id="platform-auth-panel" class="platform-auth-panel" hidden>
          <div class="platform-auth-panel-head">
            <span id="platform-auth-panel-title">账号</span>
            <button
              type="button"
              id="platform-auth-panel-close"
              class="platform-auth-panel-close"
              title="收起"
              aria-label="收起"
            >×</button>
          </div>
          <div id="platform-auth-disabled-view" class="platform-auth-disabled-view" hidden>
            <p class="platform-auth-hint">当前环境已关闭登录校验（AUTH_ENABLED=false），请求无需 JWT。</p>
          </div>
          <div id="platform-auth-account-view" class="platform-auth-account-view" hidden>
            <dl class="platform-auth-meta">
              <div><dt>状态</dt><dd id="auth-detail-status">—</dd></div>
              <div><dt>用户</dt><dd id="auth-detail-user">—</dd></div>
              <div><dt>角色</dt><dd id="auth-detail-role">—</dd></div>
              <div><dt>租户</dt><dd id="auth-detail-tenant">—</dd></div>
            </dl>
            <button type="button" id="platform-logout-btn" class="platform-btn platform-btn-danger">退出登录</button>
          </div>
          <form id="platform-login-form" class="platform-login-form" hidden autocomplete="on" novalidate>
            <input id="platform-login-user" name="username" type="text" placeholder="用户名" value="admin" autocomplete="username" />
            <input id="platform-login-pass" name="password" type="password" placeholder="密码" autocomplete="current-password" required />
            <input id="platform-login-tenant" name="tenant" type="text" placeholder="租户 ID（可选）" autocomplete="organization" />
            <button type="button" id="platform-login-submit" class="platform-btn platform-btn-primary">确认登录</button>
            <p id="platform-login-error" class="platform-login-error" role="status" aria-live="polite"></p>
          </form>
        </div>
      </div>
    </div>
  `;

  document.body.classList.add("platform-shell", "has-platform-nav");

  const themeSel = document.getElementById("platform-theme-select");
  if (themeSel && window.PlatformAuth) {
    const stored = localStorage.getItem(window.PlatformAuth.THEME_KEY) || "dark";
    themeSel.value = stored === "light" ? "light" : "dark";
  }

  function wireAuth() {
    if (window.PlatformAuth?.attachNavUi) window.PlatformAuth.attachNavUi();
  }
  wireAuth();
  document.addEventListener("platform-auth-ready", wireAuth, { once: true });
  document.dispatchEvent(new CustomEvent("platform-nav-ready"));
})();
