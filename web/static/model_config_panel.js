/**
 * Runtime model config panel — hot-swap provider/model/key via PUT /model/config.
 */
(function initModelConfigPanel(global) {
  const panelEl = document.getElementById("model-config-panel");
  if (!panelEl) return;

  const toggleBtnEl = document.getElementById("model-config-toggle");
  const badgeEl = document.getElementById("model-config-badge");
  const sourceEl = document.getElementById("model-config-source");
  const formEl = document.getElementById("model-config-form");
  const providerEl = document.getElementById("model-config-provider");
  const nameEl = document.getElementById("model-config-name");
  const baseUrlEl = document.getElementById("model-config-base-url");
  const apiKeyEl = document.getElementById("model-config-api-key");
  const keyHintEl = document.getElementById("model-config-key-hint");
  const enabledEl = document.getElementById("model-config-enabled");
  const statusEl = document.getElementById("model-config-status");
  const probeBtnEl = document.getElementById("model-config-probe-btn");
  const resetBtnEl = document.getElementById("model-config-reset-btn");

  let providers = [];
  let loaded = false;

  function runtime() {
    return global.AgentChatRuntime || {};
  }

  async function apiFetch(url, options) {
    const fn = runtime().apiFetch;
    if (typeof fn === "function") return fn(url, options);
    return fetch(url, options);
  }

  function getAuthHeaders() {
    const fn = runtime().getAuthHeaders;
    return typeof fn === "function" ? fn() : {};
  }

  function setCollapsed(collapsed) {
    panelEl.classList.toggle("is-collapsed", collapsed);
    if (toggleBtnEl) {
      toggleBtnEl.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }
    localStorage.setItem("model_config_panel_collapsed", collapsed ? "1" : "0");
    if (!collapsed && !loaded) {
      loadConfig();
    }
  }

  function shortModelName(name) {
    const text = String(name || "").trim();
    if (!text) return "—";
    if (text.length <= 18) return text;
    return `${text.slice(0, 16)}…`;
  }

  function renderBadge(config) {
    if (!badgeEl) return;
    if (!config) {
      badgeEl.textContent = "—";
      badgeEl.className = "ctx-gov-badge-inline ctx-gov-badge-idle";
      return;
    }
    badgeEl.textContent = shortModelName(config.model_name);
    badgeEl.className = `ctx-gov-badge-inline ${config.enabled ? "" : "ctx-gov-badge-warn"}`.trim();
    badgeEl.title = `${config.provider} · ${config.model_name}${config.enabled ? "" : " (已禁用)"}`;
  }

  function fillProviderOptions(items, selected) {
    if (!providerEl) return;
    providerEl.innerHTML = "";
    for (const item of items) {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = item.label || item.id;
      if (item.default_model) {
        opt.title = `默认: ${item.default_model}`;
      }
      providerEl.appendChild(opt);
    }
    if (selected) {
      providerEl.value = selected;
    }
  }

  function applyConfigToForm(config) {
    if (!config) return;
    fillProviderOptions(providers, config.provider);
    if (nameEl) nameEl.value = config.model_name || "";
    if (baseUrlEl) baseUrlEl.value = config.base_url || "";
    if (enabledEl) enabledEl.checked = Boolean(config.enabled);
    if (apiKeyEl) apiKeyEl.value = "";
    if (keyHintEl) {
      keyHintEl.textContent = config.api_key_configured
        ? `当前: ${config.api_key_hint || "已配置"}`
        : "未配置 API Key";
    }
    if (sourceEl) {
      const label = config.source === "runtime" ? "运行时覆盖" : ".env 启动配置";
      sourceEl.textContent = `来源 · ${label}`;
    }
    renderBadge(config);
  }

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.className = `model-config-status${kind ? ` is-${kind}` : ""}`;
  }

  async function loadConfig() {
    setStatus("加载中…");
    try {
      const res = await apiFetch("/model/config", { headers: getAuthHeaders() });
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      providers = Array.isArray(data.providers) ? data.providers : [];
      loaded = true;
      applyConfigToForm(data);
      setStatus("");
    } catch (err) {
      setStatus(`加载失败: ${err.message || err}`, "error");
    }
  }

  async function submitConfig(event) {
    event.preventDefault();
    const body = {
      provider: providerEl ? providerEl.value : undefined,
      model_name: nameEl ? nameEl.value.trim() : undefined,
      base_url: baseUrlEl ? baseUrlEl.value.trim() : undefined,
      enabled: enabledEl ? enabledEl.checked : undefined,
    };
    const key = apiKeyEl ? apiKeyEl.value.trim() : "";
    if (key) body.api_key = key;

    setStatus("应用中…");
    try {
      const res = await apiFetch("/model/config", {
        method: "PUT",
        headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `${res.status} ${res.statusText}`);
      }
      applyConfigToForm(data);
      setStatus("已应用", "ok");
      global.dispatchEvent(new CustomEvent("agent:model-config-changed", { detail: data }));
    } catch (err) {
      setStatus(`应用失败: ${err.message || err}`, "error");
    }
  }

  async function probeConfig() {
    setStatus("测试中…");
    try {
      const res = await apiFetch("/model/config/probe", {
        method: "POST",
        headers: getAuthHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `${res.status} ${res.statusText}`);
      }
      setStatus(`连通 OK: ${String(data.response_preview || "").slice(0, 80)}`, "ok");
    } catch (err) {
      setStatus(`测试失败: ${err.message || err}`, "error");
    }
  }

  async function resetConfig() {
    if (!global.confirm("恢复为 .env / 启动时的模型配置？")) return;
    setStatus("重置中…");
    try {
      const res = await apiFetch("/model/config/reset", {
        method: "POST",
        headers: getAuthHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || `${res.status} ${res.statusText}`);
      }
      applyConfigToForm(data);
      setStatus("已重置", "ok");
      global.dispatchEvent(new CustomEvent("agent:model-config-changed", { detail: data }));
    } catch (err) {
      setStatus(`重置失败: ${err.message || err}`, "error");
    }
  }

  if (toggleBtnEl) {
    toggleBtnEl.addEventListener("click", () => {
      setCollapsed(!panelEl.classList.contains("is-collapsed"));
    });
  }

  document.addEventListener("click", (event) => {
    if (panelEl.classList.contains("is-collapsed")) return;
    if (!panelEl.contains(event.target)) {
      setCollapsed(true);
    }
  });

  if (formEl) formEl.addEventListener("submit", submitConfig);
  if (probeBtnEl) probeBtnEl.addEventListener("click", probeConfig);
  if (resetBtnEl) resetBtnEl.addEventListener("click", resetConfig);

  if (providerEl) {
    providerEl.addEventListener("change", () => {
      const selected = providers.find((p) => p.id === providerEl.value);
      if (!selected) return;
      if (nameEl && !nameEl.value.trim() && selected.default_model) {
        nameEl.value = selected.default_model;
      }
      if (baseUrlEl && !baseUrlEl.value.trim() && selected.default_base_url) {
        baseUrlEl.value = selected.default_base_url;
      }
    });
  }

  setCollapsed(localStorage.getItem("model_config_panel_collapsed") !== "0");
  loadConfig();
})(window);
