/**
 * Render JSON Schema (subset) for skill input_form_schema and collect skill_params.
 */
(function initSkillForm(global) {
  const GOAL_FIELD = "goal";

  function schemaProperties(schema) {
    if (!schema || typeof schema !== "object") return {};
    return schema.properties && typeof schema.properties === "object" ? schema.properties : {};
  }

  function clearContainer(container) {
    if (!container) return;
    container.innerHTML = "";
    container.hidden = true;
  }

  function renderField(key, prop, container, required) {
    const label = document.createElement("label");
    label.className = "skill-param-label";
    const title = prop.title || key;
    const isReq = required.includes(key);
    label.textContent = isReq ? `${title} *` : title;

    let input;
    const t = prop.type || "string";
    if (t === "string" && (prop.format === "textarea" || (prop.maxLength || 0) > 200)) {
      input = document.createElement("textarea");
      input.rows = 3;
    } else if (t === "boolean") {
      input = document.createElement("input");
      input.type = "checkbox";
    } else if (t === "number" || t === "integer") {
      input = document.createElement("input");
      input.type = "number";
      if (prop.minimum != null) input.min = String(prop.minimum);
      if (prop.maximum != null) input.max = String(prop.maximum);
    } else {
      input = document.createElement("input");
      input.type = "text";
    }
    input.dataset.skillParam = key;
    input.id = `skill-param-${key}`;
    if (prop.placeholder) input.placeholder = prop.placeholder;
    if (prop.default != null && t !== "boolean") input.value = String(prop.default);
    if (t === "boolean" && prop.default === true) input.checked = true;

    label.appendChild(input);
    container.appendChild(label);
  }

  function renderSkillInputForm(schema, container) {
    clearContainer(container);
    if (!container) return;
    const props = schemaProperties(schema);
    const keys = Object.keys(props);
    if (!keys.length) return;

    const required = Array.isArray(schema.required) ? schema.required : [];
    container.hidden = false;
    for (const key of keys) {
      renderField(key, props[key] || {}, container, required);
    }
  }

  function collectSkillParams(container, fallbackGoal) {
    const params = {};
    if (!container) {
      const g = String(fallbackGoal || "").trim();
      return g ? { [GOAL_FIELD]: g } : {};
    }
    container.querySelectorAll("[data-skill-param]").forEach((el) => {
      const key = el.dataset.skillParam;
      if (!key) return;
      if (el.type === "checkbox") {
        if (el.checked) params[key] = true;
        return;
      }
      const val = String(el.value || "").trim();
      if (val) params[key] = val;
    });
    const goalFromForm = params[GOAL_FIELD];
    const goal = goalFromForm || String(fallbackGoal || "").trim();
    if (goal && !params[GOAL_FIELD]) params[GOAL_FIELD] = goal;
    return params;
  }

  global.SkillForm = {
    renderSkillInputForm,
    collectSkillParams,
    clearContainer,
  };
})(typeof window !== "undefined" ? window : globalThis);
