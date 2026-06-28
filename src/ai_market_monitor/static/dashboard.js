(function () {
  const root = document.body;
  const apiBase = root?.dataset.dashboardApi || "/api/v1/dashboard";

  function showToast(message, type = "success") {
    const toast = document.getElementById("dash-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `dash-toast ${type}`;
    toast.hidden = false;
    window.setTimeout(() => {
      toast.hidden = true;
    }, 6000);
  }

  async function api(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || payload.message || response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  }

  const defaultCapabilities = {
    condition_types: [
      { value: "indicator", label: "Indicator" },
      { value: "price_action", label: "Price action" },
      { value: "candle_pattern", label: "Candle pattern" },
      { value: "market_filter", label: "Market filter" },
      { value: "risk", label: "Risk" },
    ],
    indicators: [
      { name: "ema", label: "EMA" },
      { name: "sma", label: "SMA" },
      { name: "rsi", label: "RSI" },
      { name: "macd", label: "MACD" },
      { name: "volume_ratio", label: "Volume ratio" },
      { name: "vwap", label: "VWAP" },
    ],
    price_actions: [
      { name: "range_breakout", label: "Range breakout" },
      { name: "bullish_liquidity_sweep", label: "Bullish liquidity sweep" },
    ],
    candle_patterns: [
      { name: "bullish_engulfing", label: "Bullish engulfing" },
      { name: "strong_close_near_high", label: "Strong close near high" },
    ],
    market_filters: [{ name: "average_volume", label: "Average volume" }],
    risk_rules: [],
    items: [],
    categories: [],
    logic_operators: [
      { key: "and", display_name: "All of", parameters: [] },
      { key: "or", display_name: "Any of", parameters: [] },
      { key: "not", display_name: "Not", parameters: [] },
      { key: "sequence", display_name: "Sequence / Then", parameters: [] },
    ],
  };

  let capabilityRegistry = defaultCapabilities;

  async function loadCapabilityRegistry() {
    try {
      capabilityRegistry = await api("/capabilities");
    } catch {
      capabilityRegistry = defaultCapabilities;
    }
    return capabilityRegistry;
  }

  function csv(value) {
    return String(value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function field(form, name) {
    return form.elements[name];
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function chartTheme() {
    const light =
      document.documentElement.dataset.theme === "light" ||
      root?.classList.contains("theme-light");
    return light
      ? {
          background: "#f7f5fb",
          text: "#282331",
          grid: "rgba(124, 92, 191, .13)",
          up: "#7c3aed",
          down: "#b4234f",
          target: "#7c3aed",
          stop: "#c43b2f",
          entry: "#8b5cf6",
        }
      : {
          background: "#0a0a0a",
          text: "#e6e6eb",
          grid: "rgba(196, 181, 253, .11)",
          up: "#a78bfa",
          down: "#ff8a7a",
          target: "#a78bfa",
          stop: "#ff8a7a",
          entry: "#8b5cf6",
        };
  }

  function safeJson(value, fallback = {}) {
    try {
      return JSON.parse(value || "{}");
    } catch {
      return fallback;
    }
  }

  function renderLoadingState(element, message = "Loading...") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error", "success");
    element.textContent = message;
  }

  function renderSuccessState(element, payload, emptyMessage = "No data returned.") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error");
    element.classList.add("success");
    if (payload === null || payload === undefined || payload === "") {
      element.textContent = emptyMessage;
      return;
    }
    element.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
  }

  function renderWrittenReport(elementId, report) {
    const element = document.getElementById(elementId);
    if (!element) return;
    if (!report) {
      element.textContent = "No written report was produced for this run.";
      return;
    }
    const passed = safeArray(report.passed_conditions)
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
    const blockers = safeArray(report.blocking_conditions)
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
    const bestParts = [];
    if (report.best_symbol) bestParts.push(`Best symbol: ${report.best_symbol}`);
    if (report.best_score !== undefined && report.best_score !== null) {
      bestParts.push(`Best score: ${report.best_score}%`);
    }
    if (report.best_outcome) bestParts.push(`Outcome: ${report.best_outcome}`);
    element.innerHTML = `
      <h3>${escapeHtml(report.title || "Replay Report")}</h3>
      <p><strong>${escapeHtml(report.headline || "Replay completed.")}</strong></p>
      <p>${escapeHtml(report.summary || "")}</p>
      ${bestParts.length ? `<p>${bestParts.map(escapeHtml).join(" - ")}</p>` : ""}
      ${passed ? `<p>Passed highlights:</p><ul>${passed}</ul>` : ""}
      ${blockers ? `<p>Blocking or missing conditions:</p><ul>${blockers}</ul>` : ""}
      ${report.next_step ? `<p>${escapeHtml(report.next_step)}</p>` : ""}
    `;
  }

  function renderEmptyState(element, message = "Nothing to show yet.") {
    if (!element) return;
    element.hidden = false;
    element.classList.remove("error");
    element.textContent = message;
  }

  function renderErrorState(element, error, recovery = "Try again or adjust the inputs.") {
    if (!element) return;
    element.hidden = false;
    element.classList.add("error");
    const message = error?.message || String(error || "Unknown error");
    element.textContent = `${message}\n\n${recovery}`;
  }

  function defaultSchema() {
    return {
      schema_version: "1.0",
      name: "Liquidity Sweep Continuation",
      description: "Research monitor with deterministic trend, volume and price-action conditions.",
      direction: "long",
      base_timeframe: "15m",
      supporting_timeframes: ["4h"],
      trigger_mode: "candle_close",
      universe: {
        exchange: "binance",
        market_type: "spot",
        quote_currencies: ["USDT"],
        include_symbols: [],
        exclude_symbols: [],
        min_quote_volume_24h: 1000000,
        min_listing_age_days: 30,
        max_spread_bps: 25,
        min_historical_candles: 200,
        exclude_stablecoins: true,
        exclude_leveraged_tokens: true,
      },
      conditions: {
        node_type: "group",
        key: "entry_conditions",
        operator: "and",
        children: [
          {
            node_type: "condition",
            key: "trend_filter",
            label: "Price above 4h EMA 200",
            condition_type: "indicator",
            timeframe: "4h",
            left: { kind: "price", field: "close", parameters: {} },
            comparator: "gte",
            right: { kind: "indicator", name: "ema", parameters: { period: 200 } },
            required: true,
            weight: 1,
            required_data: ["ohlcv"],
            explanation_template: "Close must be above the four-hour EMA 200.",
          },
          {
            node_type: "condition",
            key: "volume_multiplier",
            label: "Volume at least 1.5x average",
            condition_type: "market_filter",
            timeframe: "15m",
            left: { kind: "market_metric", name: "volume_multiplier", parameters: { period: 20 } },
            comparator: "gte",
            right: { kind: "constant", value: 1.5 },
            required: true,
            weight: 1,
            forming_tolerance_percent: 10,
            required_data: ["ohlcv"],
            explanation_template: "Volume multiplier must meet or exceed the configured threshold.",
          },
        ],
      },
      entry: { calculation: "signal_close", expires_after_candles: 3 },
      stop: { method: "structure", atr_period: 14, atr_multiplier: 1.5, swing_lookback: 10 },
      targets: [],
      risk: {
        enabled: false,
        stop_method: "structure",
        maximum_stop_percent: null,
        target_method: "risk_multiple",
        target_value: null,
        minimum_reward_to_risk: null,
        estimated_fee_bps: 10,
        estimated_slippage_bps: 5,
      },
      near_miss: {
        enabled: true,
        thresholds: [70, 80, 90],
        mandatory_fail_cap: 90,
        minimum_score_to_store: 40,
        one_condition_remaining_enabled: true,
      },
      alerts: {
        forming_alerts: true,
        near_miss_threshold: 70,
        channels: ["telegram"],
        cooldown_seconds: 900,
        maximum_alerts_per_hour: 10,
        suppress_repetitive_near_miss: true,
        alert_on_one_condition_remaining: true,
      },
      expiry: { expire_after_candles: 3 },
      forward_test: { enabled: false, estimated_fee_bps: 10, estimated_slippage_bps: 5 },
      position_sizing: { enabled: false, store_account_balance: false },
    };
  }

  function loadInitialSchema() {
    const script = document.getElementById("amm-builder-schema");
    if (!script) return defaultSchema();
    const parsed = safeJson(script.textContent || "{}", {});
    return Object.keys(parsed).length ? parsed : defaultSchema();
  }

  function createCondition(index) {
    return {
      node_type: "condition",
      key: `condition_${Date.now()}_${index}`,
      label: "New condition",
      condition_type: "indicator",
      timeframe: "15m",
      left: { kind: "indicator", name: "volume_ratio", parameters: { period: 20 } },
      comparator: "gte",
      right: { kind: "constant", value: 1.5 },
      required: true,
      weight: 1,
      cap_score_on_fail: null,
      forming_tolerance_percent: 10,
      required_data: ["ohlcv"],
      explanation_template: "Condition must meet the configured threshold.",
    };
  }

  function createGroup(index, operator = "and") {
    const operatorSpec = safeArray(capabilityRegistry.logic_operators)
      .find((item) => item.key === operator);
    const parameters = {};
    safeArray(operatorSpec?.parameters).forEach((parameter) => {
      parameters[parameter.name] = parameter.default;
    });
    const childCount = operator === "sequence" ? 2 : operator === "conditional_branch" ? 3 : 1;
    return {
      node_type: "group",
      key: `group_${Date.now()}_${index}`,
      operator,
      parameters,
      children: Array.from({ length: childCount }, (_, offset) => createCondition(index + offset + 1)),
    };
  }

  function uniqueConditionKey(base, index = 0) {
    const normalized = String(base || "condition")
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 72) || "condition";
    return `${normalized}_${Date.now()}_${index}`;
  }

  function conditionFromCapability(capability, timeframe, index) {
    const template = safeJson(
      JSON.stringify(capability.condition_template || {}),
      createCondition(index),
    );
    template.key = uniqueConditionKey(capability.key, index);
    template.timeframe = timeframe || template.timeframe || "15m";
    template.label = capability.display_name || capability.label || template.label;
    template.notes = capability.risk_notes || template.notes || null;
    return template;
  }

  function createPresetCondition(kind, index) {
    const key = `${kind}_${Date.now()}_${index}`;
    const presets = {
      filter: {
        key,
        label: "Price above EMA 200",
        condition_type: "indicator",
        timeframe: "1h",
        left: { kind: "price", field: "close", parameters: {} },
        comparator: "gt",
        right: { kind: "indicator", name: "ema", parameters: { period: 200, field: "close" } },
        explanation_template: "Close must stay above the EMA 200 trend filter.",
      },
      market_context: {
        key,
        label: "BTC trend context",
        condition_type: "indicator",
        timeframe: "1h",
        left: { kind: "price", field: "close", parameters: {} },
        comparator: "gt",
        right: { kind: "indicator", name: "ema", parameters: { period: 200, field: "close" } },
        explanation_template: "Use this as market context; edit source symbol when benchmark support is enabled.",
      },
      time_rule: {
        key,
        label: "Evaluation during New York session",
        condition_type: "market_filter",
        timeframe: "15m",
        left: {
          kind: "market_metric",
          name: "time_window",
          parameters: { start_hour: 13, end_hour: 21, timezone: "UTC" },
        },
        comparator: "is_true",
        right: null,
        explanation_template: "Signal candle timestamp must fall inside the configured session window.",
      },
      risk_rule: {
        key,
        label: "Stop distance within maximum",
        condition_type: "risk",
        timeframe: "15m",
        left: { kind: "risk_metric", name: "stop_distance_percent", parameters: {} },
        comparator: "lte",
        right: { kind: "constant", value: 2 },
        explanation_template: "Stop distance must be within the configured maximum.",
      },
    };
    return {
      ...createCondition(index),
      ...(presets[kind] || presets.filter),
      required: true,
      weight: 1,
      required_data: ["ohlcv"],
      forming_tolerance_percent: 10,
    };
  }

  function conditionValue(node) {
    if (!node.right) return "";
    if (node.right.kind === "constant") return node.right.value ?? "";
    return node.right.name || node.right.field || "";
  }

  function setConditionValue(node, rawValue) {
    const numeric = Number(rawValue);
    node.right = {
      kind: "constant",
      value: Number.isFinite(numeric) && rawValue !== "" ? numeric : rawValue,
    };
  }

  function selectedOperandList(conditionType) {
    const implementedOnly = (items) => safeArray(items).filter((item) => {
      const status = item.implementation_status || (item.executable === false ? "recognized_not_executable" : "implemented");
      return item.executable !== false && !item.provider_required && ["implemented", "available"].includes(status);
    });
    if (conditionType === "price_action") return implementedOnly(capabilityRegistry.price_actions);
    if (conditionType === "candle_pattern") return implementedOnly(capabilityRegistry.candle_patterns);
    if (conditionType === "market_filter") return implementedOnly(capabilityRegistry.market_filters);
    if (conditionType === "risk") return implementedOnly(capabilityRegistry.risk_rules);
    return implementedOnly(capabilityRegistry.indicators);
  }

  function conditionTypeOptions(selected) {
    return safeArray(capabilityRegistry.condition_types)
      .map((item) => {
        const value = item.value || item;
        const label = item.label || value;
        return `<option value="${escapeHtml(value)}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      })
      .join("");
  }

  function operandOptions(node) {
    const selected = node.left?.name || node.left?.field || "";
    const options = node.left?.kind === "price"
      ? [
          { name: "open", label: "Open" },
          { name: "high", label: "High" },
          { name: "low", label: "Low" },
          { name: "close", label: "Close" },
        ]
      : selectedOperandList(node.condition_type);
    const existing = selected && !options.some((item) => item.name === selected)
      ? [{ name: selected, label: selected }]
      : [];
    return [...existing, ...options]
      .map((item) => {
        const value = item.name || item.key || item.value || "";
        const label = item.label || value;
        return `<option value="${escapeHtml(value)}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
      })
      .join("");
  }

  function parameterText(node) {
    const operand = (
      node.left?.kind === "price" && node.right?.kind === "indicator"
        ? node.right
        : node.left
    );
    return escapeHtml(JSON.stringify(operand?.parameters || {}, null, 0));
  }

  function operatorLabel(operator) {
    return {
      and: "ALL OF",
      or: "ANY OF",
      not: "NOT",
      sequence: "SEQUENCE",
      within_last: "WITHIN LAST",
      persisted_for: "PERSISTED FOR",
      count_of: "COUNT OF",
      cooldown_condition: "COOLDOWN",
      first_time_true: "FIRST TIME TRUE",
      changed_state: "CHANGED STATE",
      cross_with_confirmation: "CROSS + CONFIRM",
      conditional_branch: "IF / OTHERWISE",
    }[operator || "and"] || "ALL OF";
  }

  function comparatorLabel(comparator) {
    return {
      gte: "is at least",
      gt: "is above",
      lte: "is at most",
      lt: "is below",
      eq: "equals",
      crosses_above: "crosses above",
      crosses_below: "crosses below",
      is_true: "appears",
      is_false: "does not appear",
    }[comparator || "gte"] || comparator;
  }

  function titleize(value) {
    return String(value || "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function operandText(operand) {
    if (!operand) return "Value";
    if (operand.kind === "price") return titleize(operand.field || operand.name || "close");
    if (operand.kind === "constant") return String(operand.value ?? "");
    const base = titleize(operand.name || operand.field || operand.kind);
    const period = operand.parameters?.period;
    return period ? `${base}(${period})` : base;
  }

  function conditionSentence(node) {
    const left = operandText(node.left);
    const right = operandText(node.right);
    const timeframe = node.timeframe || "15m";
    if (node.comparator === "is_true") return `${left} appears on ${timeframe}`;
    if (node.comparator === "is_false") return `${left} does not appear on ${timeframe}`;
    return `${left} ${comparatorLabel(node.comparator)} ${right} on ${timeframe}`;
  }

  function collectConditionLeaves(node, leaves = []) {
    if (!node) return leaves;
    if (node.node_type === "condition") {
      leaves.push(node);
      return leaves;
    }
    safeArray(node.children).forEach((child) => collectConditionLeaves(child, leaves));
    return leaves;
  }

  function logicOperators() {
    const configured = safeArray(capabilityRegistry.logic_operators);
    return configured.length
      ? configured
      : safeArray(defaultCapabilities.logic_operators);
  }

  function normalizeGroupChildren(node) {
    const single = new Set([
      "not",
      "within_last",
      "persisted_for",
      "first_time_true",
      "changed_state",
      "cross_with_confirmation",
    ]);
    if (single.has(node.operator) && node.children.length > 1) {
      node.children = [node.children[0]];
    }
    const required = node.operator === "conditional_branch"
      ? 3
      : node.operator === "sequence"
      ? 2
      : single.has(node.operator)
      ? 1
      : 0;
    while (node.children.length < required) {
      node.children.push(createCondition(node.children.length));
    }
    const spec = logicOperators().find((item) => item.key === node.operator);
    node.parameters = node.parameters || {};
    safeArray(spec?.parameters).forEach((parameter) => {
      if (node.parameters[parameter.name] === undefined) {
        node.parameters[parameter.name] = parameter.default;
      }
    });
  }

  function groupParameterFields(spec, node) {
    return safeArray(spec?.parameters).map((parameter) => {
      const value = node.parameters?.[parameter.name] ?? parameter.default ?? "";
      const label = titleize(parameter.name);
      const options = parameter.type === "timezone"
        ? ["UTC", "America/New_York", "Europe/London", "Europe/Moscow", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]
        : safeArray(parameter.options);
      if (options.length) {
        return `<label>${escapeHtml(label)}
          <select data-group-parameter="${escapeHtml(parameter.name)}" data-parameter-type="${escapeHtml(parameter.type || "string")}">
            ${options.map((option) => `<option value="${escapeHtml(option)}" ${String(value) === String(option) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
          </select>
        </label>`;
      }
      if (parameter.type === "boolean") {
        return `<label>${escapeHtml(label)}
          <select data-group-parameter="${escapeHtml(parameter.name)}" data-parameter-type="boolean">
            <option value="true" ${value === true ? "selected" : ""}>Yes</option>
            <option value="false" ${value === false ? "selected" : ""}>No</option>
          </select>
        </label>`;
      }
      const inputType = ["integer", "number"].includes(parameter.type) ? "number" : "text";
      return `<label>${escapeHtml(label)}
        <input type="${inputType}" data-group-parameter="${escapeHtml(parameter.name)}" data-parameter-type="${escapeHtml(parameter.type || "string")}" value="${escapeHtml(value)}" ${parameter.minimum !== null && parameter.minimum !== undefined ? `min="${escapeHtml(parameter.minimum)}"` : ""}>
      </label>`;
    }).join("");
  }

  function capabilityParameterOperand(node, capability) {
    if (
      node.right?.kind === "indicator" &&
      capability?.operand_name === node.right?.name
    ) {
      return node.right;
    }
    return node.left;
  }

  function conditionParameterFields(capability, node) {
    if (!capability) return "";
    const operand = capabilityParameterOperand(node, capability);
    return safeArray(capability.parameters)
      .filter((parameter) => !["timeframe", "threshold"].includes(parameter.name))
      .map((parameter) => {
      const value = operand?.parameters?.[parameter.name]
          ?? capability.default_parameters?.[parameter.name]
          ?? parameter.default
          ?? "";
      const label = titleize(parameter.name);
        const options = parameter.type === "timezone"
          ? ["UTC", "America/New_York", "Europe/London", "Europe/Moscow", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]
          : safeArray(parameter.options);
        if (options.length) {
          return `<label>${escapeHtml(label)}
            <select data-condition-parameter="${escapeHtml(parameter.name)}" data-parameter-type="${escapeHtml(parameter.type || "string")}">
              ${options.map((option) => `<option value="${escapeHtml(option)}" ${String(value) === String(option) ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
            </select>
          </label>`;
        }
        if (parameter.type === "boolean") {
          return `<label>${escapeHtml(label)}
            <select data-condition-parameter="${escapeHtml(parameter.name)}" data-parameter-type="boolean">
              <option value="true" ${value === true ? "selected" : ""}>Yes</option>
              <option value="false" ${value === false ? "selected" : ""}>No</option>
            </select>
          </label>`;
        }
        const inputType = ["integer", "number"].includes(parameter.type) ? "number" : "text";
        return `<label>${escapeHtml(label)}
          <input type="${inputType}" data-condition-parameter="${escapeHtml(parameter.name)}" data-parameter-type="${escapeHtml(parameter.type || "string")}" value="${escapeHtml(value)}">
        </label>`;
      })
      .join("");
  }

  let builderUiController = null;

  function capabilityForCondition(node) {
    return safeArray(capabilityRegistry.items).find((item) => (
      item.operand_name === node.left?.name ||
      item.key === node.left?.name ||
      item.display_name === node.label
    ));
  }

  function renderConditionCard(node, container, onChange, parent) {
    const capability = capabilityForCondition(node);
    const wrapper = document.createElement("article");
    wrapper.className = "condition-card";
    wrapper.dataset.testid = "condition-card";
    const dataBadges = safeArray(node.required_data).slice(0, 2);
    wrapper.innerHTML = `
      <div class="condition-card-icon" aria-hidden="true">${escapeHtml((node.condition_type || "C").slice(0, 1).toUpperCase())}</div>
      <div class="condition-card-copy">
        <div class="condition-card-title-row">
          <strong>${escapeHtml(conditionSentence(node))}</strong>
          <span class="condition-validity valid">Valid</span>
        </div>
        <div class="condition-badges">
          <span class="${node.required === false ? "optional" : "required"}">${node.required === false ? "Optional" : "Required"}</span>
          <span>${escapeHtml(node.timeframe || "15m")}</span>
          <span>${escapeHtml(titleize(node.condition_type))}</span>
          ${dataBadges.map((item) => `<span class="data-badge">${escapeHtml(String(item).toUpperCase())}</span>`).join("")}
          ${node.ai_interpreted || node.source_fragment ? '<span class="ai-badge">AI interpreted</span>' : ""}
          ${node.provider_required ? '<span class="provider-badge" data-testid="condition-provider-required-badge">Provider required</span>' : ""}
          ${node.confidence !== undefined && node.confidence !== null ? `<span>${Math.round(Number(node.confidence) * 100)}% confidence</span>` : ""}
        </div>
      </div>
      <div class="condition-card-actions">
        <button type="button" data-edit-condition data-testid="condition-edit">Edit</button>
        <button type="button" data-duplicate-condition>Duplicate</button>
        <button type="button" data-toggle-condition>${node.required === false ? "Make required" : "Make optional"}</button>
        <button type="button" class="danger-link" data-remove-condition>Delete</button>
      </div>
    `;
    wrapper.querySelector("[data-edit-condition]")?.addEventListener("click", () => {
      builderUiController?.openConditionDrawer?.(node, onChange);
    });
    wrapper.querySelector("[data-duplicate-condition]")?.addEventListener("click", () => {
      builderUiController?.duplicateNode?.(node, parent, onChange);
    });
    wrapper.querySelector("[data-toggle-condition]")?.addEventListener("click", () => {
      node.required = node.required === false;
      onChange(true);
    });
    wrapper.querySelector("[data-remove-condition]")?.addEventListener("click", () => {
      builderUiController?.removeNode?.(node, parent, onChange);
    });
    container.appendChild(wrapper);
  }

  function renderLogicGroupCard(node, container, onChange, parent) {
    normalizeGroupChildren(node);
    const leaves = collectConditionLeaves(node);
    const preview = leaves.slice(0, 3).map((condition) => conditionSentence(condition));
    const wrapper = document.createElement("section");
    wrapper.className = `logic-group-visual ${parent ? "nested" : "root"}`;
    wrapper.innerHTML = `
      <div class="logic-group-header">
        <div>
          <span class="logic-badge">${operatorLabel(node.operator)}</span>
          <strong>${leaves.length} condition${leaves.length === 1 ? "" : "s"}</strong>
          <small>${preview.map(escapeHtml).join(" / ") || "Add the first condition to this group."}</small>
        </div>
        <div class="condition-card-actions">
          <button type="button" data-edit-group>Edit group</button>
          <button type="button" data-add-child-condition>Add child</button>
          <button type="button" data-add-child-group>Add group</button>
          ${parent ? '<button type="button" data-duplicate-group>Duplicate</button><button type="button" class="danger-link" data-remove-group>Delete</button>' : ""}
        </div>
      </div>
      <div class="condition-children"></div>
    `;
    wrapper.querySelector("[data-edit-group]")?.addEventListener("click", () => {
      builderUiController?.openGroupDrawer?.(node, onChange);
    });
    wrapper.querySelector("[data-add-child-condition]")?.addEventListener("click", () => {
      builderUiController?.openConditionLibrary?.(node);
    });
    wrapper.querySelector("[data-add-child-group]")?.addEventListener("click", () => {
      builderUiController?.addGroupToGroup?.(node, onChange);
    });
    wrapper.querySelector("[data-duplicate-group]")?.addEventListener("click", () => {
      builderUiController?.duplicateNode?.(node, parent, onChange);
    });
    wrapper.querySelector("[data-remove-group]")?.addEventListener("click", () => {
      builderUiController?.removeNode?.(node, parent, onChange);
    });
    const children = wrapper.querySelector(".condition-children");
    safeArray(node.children).forEach((child) => renderNode(child, children, onChange, node));
    container.appendChild(wrapper);
  }

  function renderNode(node, container, onChange, parent = null) {
    if (!node) return;
    if (node.node_type === "group") {
      renderLogicGroupCard(node, container, onChange, parent);
      return;
    }
    renderConditionCard(node, container, onChange, parent);
  }

  function updateConditionNode(node, field) {
    const name = field.dataset.field;
    if (name === "right_value") {
      setConditionValue(node, field.value);
      return;
    }
    if (name === "left_name") {
      if (node.left?.kind === "price") {
        node.left = {
          kind: "price",
          field: field.value,
          parameters: node.left?.parameters || {},
        };
        return;
      }
      node.left = {
        kind: node.left?.kind || defaultOperandKind(node.condition_type),
        name: field.value,
        parameters: node.left?.parameters || {},
      };
      return;
    }
    if (name === "operand_kind") {
      node.left = {
        kind: field.value,
        ...(field.value === "price"
          ? { field: node.left?.field || "close" }
          : { name: node.left?.name || selectedOperandList(node.condition_type)[0]?.name || "" }),
        parameters: node.left?.parameters || {},
      };
      return;
    }
    if (name === "parameters") {
      const target = (
        node.left?.kind === "price" && node.right?.kind === "indicator"
          ? node.right
          : node.left
      );
      target.parameters = safeJson(field.value || "{}", {});
      return;
    }
    if (name === "weight") {
      node.weight = Number(field.value || 1);
      return;
    }
    if (name === "required") {
      node.required = field.value === "true";
      return;
    }
    if (name === "condition_type") {
      node.condition_type = field.value;
      node.left = {
        kind: defaultOperandKind(field.value),
        name: selectedOperandList(field.value)[0]?.name || "",
        parameters: {},
      };
      return;
    }
    if (name === "forming_tolerance_percent" || name === "cap_score_on_fail") {
      node[name] = field.value === "" ? null : Number(field.value);
      return;
    }
    node[name] = field.value;
  }

  function defaultOperandKind(conditionType) {
    if (conditionType === "price_action") return "price_action";
    if (conditionType === "candle_pattern") return "candle_pattern";
    if (conditionType === "market_filter") return "market_metric";
    if (conditionType === "risk") return "risk_metric";
    return "indicator";
  }

  function hydrateBuilderForm(schema) {
    const form = document.getElementById("strategy-builder-form");
    if (!form) return;
    field(form, "name").value = schema.name || "";
    field(form, "description").value = schema.description || "";
    field(form, "direction").value = schema.direction || "long";
    field(form, "exchange").value = schema.universe?.exchange || "binance";
    field(form, "quote").value = (schema.universe?.quote_currencies || ["USDT"]).join(",");
    field(form, "base_timeframe").value = schema.base_timeframe || "15m";
    field(form, "supporting_timeframes").value = (schema.supporting_timeframes || []).join(",");
    field(form, "trigger_mode").value = schema.trigger_mode || "candle_close";
    field(form, "include_symbols").value = (schema.universe?.include_symbols || []).join(",");
    field(form, "exclude_symbols").value = (schema.universe?.exclude_symbols || []).join(",");
    field(form, "min_quote_volume_24h").value = schema.universe?.min_quote_volume_24h || "";
    field(form, "max_spread_bps").value = schema.universe?.max_spread_bps || "";
    field(form, "risk_enabled").value = String(schema.risk?.enabled === true);
    field(form, "stop_method").value = schema.risk?.stop_method || schema.stop?.method || "structure";
    field(form, "maximum_stop_percent").value = schema.risk?.maximum_stop_percent || "";
    field(form, "target_value").value = schema.risk?.target_value || schema.targets?.[0]?.value || "";
    field(form, "minimum_reward_to_risk").value = schema.risk?.minimum_reward_to_risk || "";
    field(form, "near_miss_enabled").value = String(schema.near_miss?.enabled !== false);
    field(form, "near_miss_threshold").value = schema.alerts?.near_miss_threshold || 70;
    field(form, "alert_channels").value = safeArray(schema.alerts?.channels).join(",") || "telegram";
    field(form, "cooldown_seconds").value = schema.alerts?.cooldown_seconds || 900;
    field(form, "maximum_alerts_per_hour").value = schema.alerts?.maximum_alerts_per_hour || 10;
    field(form, "forming_alerts").value = String(schema.alerts?.forming_alerts !== false);
  }

  function schemaFromForm(schema) {
    const form = document.getElementById("strategy-builder-form");
    if (!form) return schema;
    const targetRaw = field(form, "target_value").value;
    const targetValue = targetRaw ? Number(targetRaw) : null;
    const riskEnabled = field(form, "risk_enabled").value === "true";
    const nearMissEnabled = field(form, "near_miss_enabled").value === "true";
    const supporting = csv(field(form, "supporting_timeframes").value);
    schema.name = field(form, "name").value;
    schema.description = field(form, "description").value || null;
    schema.direction = field(form, "direction").value;
    schema.base_timeframe = field(form, "base_timeframe").value;
    schema.supporting_timeframes = supporting;
    schema.trigger_mode = field(form, "trigger_mode").value;
    schema.universe = {
      ...(schema.universe || {}),
      exchange: field(form, "exchange").value || "binance",
      market_type: "spot",
      quote_currencies: csv(field(form, "quote").value || "USDT"),
      include_symbols: csv(field(form, "include_symbols").value),
      exclude_symbols: csv(field(form, "exclude_symbols").value),
      min_quote_volume_24h: Number(field(form, "min_quote_volume_24h").value || 0) || null,
      max_spread_bps: Number(field(form, "max_spread_bps").value || 0) || null,
      min_historical_candles: schema.universe?.min_historical_candles || 200,
      exclude_stablecoins: schema.universe?.exclude_stablecoins !== false,
      exclude_leveraged_tokens: schema.universe?.exclude_leveraged_tokens !== false,
    };
    schema.stop = {
      ...(schema.stop || {}),
      method: field(form, "stop_method").value,
      value: field(form, "stop_method").value === "fixed_percent" ? Number(field(form, "maximum_stop_percent").value) : null,
      atr_period: schema.stop?.atr_period || 14,
      atr_multiplier: schema.stop?.atr_multiplier || 1.5,
      swing_lookback: schema.stop?.swing_lookback || 10,
    };
    const existingTargets = safeArray(schema.targets);
    schema.targets = riskEnabled
      ? (existingTargets.length
        ? existingTargets.map((target, index) => index === 0 ? { ...target, value: targetValue || 1 } : target)
        : [{ label: "T1", method: "risk_multiple", value: targetValue || 1 }])
      : [];
    schema.risk = {
      ...(schema.risk || {}),
      enabled: riskEnabled,
      stop_method: field(form, "stop_method").value,
      stop_value: field(form, "stop_method").value === "fixed_percent" ? Number(field(form, "maximum_stop_percent").value) : null,
      maximum_stop_percent: riskEnabled ? Number(field(form, "maximum_stop_percent").value || 100) : null,
      target_method: "risk_multiple",
      target_value: riskEnabled ? (targetValue || Number(field(form, "minimum_reward_to_risk").value || 1)) : null,
      minimum_reward_to_risk: riskEnabled ? Number(field(form, "minimum_reward_to_risk").value || 1) : null,
      estimated_fee_bps: schema.risk?.estimated_fee_bps || 10,
      estimated_slippage_bps: schema.risk?.estimated_slippage_bps || 5,
    };
    schema.near_miss = {
      ...(schema.near_miss || {}),
      enabled: nearMissEnabled,
      thresholds: schema.near_miss?.thresholds || [70, 80, 90],
      mandatory_fail_cap: schema.near_miss?.mandatory_fail_cap || 90,
      minimum_score_to_store: schema.near_miss?.minimum_score_to_store || 40,
      one_condition_remaining_enabled: schema.near_miss?.one_condition_remaining_enabled !== false,
    };
    schema.alerts = {
      ...(schema.alerts || {}),
      forming_alerts: field(form, "forming_alerts").value === "true",
      near_miss_threshold: Number(field(form, "near_miss_threshold").value || 70),
      channels: csv(field(form, "alert_channels").value),
      cooldown_seconds: Number(field(form, "cooldown_seconds").value || 900),
      maximum_alerts_per_hour: Number(field(form, "maximum_alerts_per_hour").value || 10),
      suppress_repetitive_near_miss: true,
      alert_on_one_condition_remaining: true,
    };
    return schema;
  }

  async function initBuilder() {
    const form = document.getElementById("strategy-builder-form");
    const tree = document.getElementById("condition-tree");
    if (!form || !tree) return;
    await loadCapabilityRegistry();
    let schema = loadInitialSchema();
    let translatedSchema = null;
    let interpretationMetadata = {
      interpreter: "dashboard-builder-v1",
      assumptions: [],
      ambiguities: [],
      unsupported_conditions: [],
      source_text: null,
    };
    hydrateBuilderForm(schema);

    function rerender(rebuildTree = false) {
      schema = schemaFromForm(schema);
      if (rebuildTree) {
        tree.innerHTML = "";
        renderNode(schema.conditions, tree, rerender);
      }
      const json = JSON.stringify(schema, null, 2);
      const jsonBox = document.getElementById("builder-json");
      if (jsonBox) jsonBox.textContent = json;
      const summary = document.getElementById("builder-summary");
      if (summary) {
        summary.innerHTML = `
          <strong>${schema.name}</strong><br>
          ${schema.direction} · ${schema.universe.exchange} spot · ${schema.universe.quote_currencies.join(", ")}<br>
          ${schema.base_timeframe}${schema.supporting_timeframes.length ? ` + ${schema.supporting_timeframes.join(", ")}` : ""}<br>
          Near-Miss: ${schema.near_miss.enabled ? "enabled" : "off by default"} · threshold ${schema.alerts.near_miss_threshold}%
        `;
      }
    }

    form.querySelectorAll("input, textarea, select").forEach((input) => {
      input.addEventListener("input", () => rerender(false));
      input.addEventListener("change", () => rerender(false));
    });
    tree.innerHTML = "";
    renderNode(schema.conditions, tree, rerender);
    rerender(false);

    document.querySelector("[data-builder-add-condition]")?.addEventListener("click", () => {
      schema.conditions.children.push(createCondition(schema.conditions.children.length));
      rerender(true);
    });
    document.querySelector("[data-builder-add-group]")?.addEventListener("click", () => {
      schema.conditions.children.push(createGroup(schema.conditions.children.length));
      rerender(true);
    });
    document.querySelectorAll("[data-template-schema]").forEach((button) => {
      button.addEventListener("click", () => {
        schema = safeJson(button.dataset.templateSchema || "{}", defaultSchema());
        interpretationMetadata = {
          interpreter: "dashboard-template",
          assumptions: [],
          ambiguities: [],
          unsupported_conditions: [],
          source_text: null,
        };
        hydrateBuilderForm(schema);
        rerender(true);
        showToast("Template loaded into the builder.");
      });
    });
    const translateButton = document.querySelector("[data-interpret-builder-prompt]");
    const applyInterpretationButton = document.querySelector("[data-apply-builder-interpretation]");
    const aiSummary = document.getElementById("builder-ai-summary");
    translateButton?.addEventListener("click", async () => {
      const prompt = field(form, "builder_prompt").value.trim();
      if (!prompt) {
        showToast("Describe the monitor before translating it.", "error");
        return;
      }
      renderLoadingState(aiSummary, "AI is translating the prompt into deterministic rules...");
      translatedSchema = null;
      if (applyInterpretationButton) applyInterpretationButton.hidden = true;
      try {
        schema = schemaFromForm(schema);
        const response = await api("/strategies/interpret", {
          method: "POST",
          body: JSON.stringify({
            raw_prompt: prompt,
            prompt_parts: { goal: prompt },
            current_schema: schema,
            exchange: field(form, "exchange").value || "binance",
            quote_currency: csv(field(form, "quote").value || "USDT")[0] || "USDT",
            timeframe: field(form, "base_timeframe").value || "15m",
            trigger_mode: field(form, "trigger_mode").value || "candle_close",
            symbols: csv(field(form, "include_symbols").value),
            builder_mode: "legacy_prompt",
          }),
        });
        translatedSchema = response.strategy;
        interpretationMetadata = {
          interpreter: response.interpreter || "dashboard-builder-v1",
          assumptions: safeArray(response.assumptions),
          ambiguities: safeArray(response.ambiguities),
          unsupported_conditions: safeArray(response.unsupported_conditions),
          source_text: prompt,
        };
        const rules = safeArray(response.interpreted_rules);
        const issues = [
          ...safeArray(response.ambiguities).map((item) => item.message),
          ...safeArray(response.unsupported_conditions).map((item) => item.message),
        ];
        const coverage = response.prompt_coverage_report || {};
        aiSummary.hidden = false;
        aiSummary.classList.toggle("error", Boolean(response.activation_blocked));
        aiSummary.innerHTML = `
          <strong>${response.ai_used ? "OpenAI interpretation" : "Deterministic fallback interpretation"}</strong>
          <p>${escapeHtml(response.understanding?.name || translatedSchema.name)} - ${escapeHtml(response.understanding?.direction || translatedSchema.direction)} - ${escapeHtml((response.understanding?.timeframes || []).join(", "))}</p>
          <p>Prompt coverage: ${escapeHtml(response.coverage_score ?? coverage.coverage_score ?? "n/a")}% · Confidence: ${escapeHtml(response.confidence_score ?? coverage.confidence_score ?? "n/a")}%</p>
          <p>Mechanical rules:</p>
          <ul>${rules.map((rule) => `<li>${escapeHtml(rule.name)} (${escapeHtml(rule.timeframe)}, ${escapeHtml(rule.operator)})<br><small>From: ${escapeHtml(rule.source_fragment || "not supplied")}</small></li>`).join("") || "<li>No executable rule recognized</li>"}</ul>
          ${safeArray(response.assumptions).length ? `<p>Assumptions:</p><ul>${response.assumptions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
          ${safeArray(coverage.mapping_table).length ? `<p>Coverage map:</p><ul>${safeArray(coverage.mapping_table).map((row) => `<li>${escapeHtml(row.fragment)} → ${escapeHtml(row.bucket)}</li>`).join("")}</ul>` : ""}
          ${issues.length ? `<p>Needs attention:</p><ul>${issues.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : '<p class="dash-flash success">Ready to apply and review before publishing.</p>'}
        `;
        if (applyInterpretationButton) {
          applyInterpretationButton.hidden = Boolean(response.activation_blocked);
        }
        showToast(
          response.activation_blocked
            ? "The translation needs clarification."
            : "Translation ready. Review and apply the mechanics.",
          response.activation_blocked ? "error" : "success",
        );
      } catch (error) {
        renderErrorState(aiSummary, error, "Check the AI API configuration or clarify the prompt.");
        showToast(error.message, "error");
      }
    });
    applyInterpretationButton?.addEventListener("click", () => {
      if (!translatedSchema) return;
      schema = translatedSchema;
      hydrateBuilderForm(schema);
      rerender(true);
      showToast("Translated mechanics applied. Review every rule before publishing.");
    });
    document.querySelector("[data-copy-schema]")?.addEventListener("click", async () => {
      schema = schemaFromForm(schema);
      await navigator.clipboard.writeText(JSON.stringify(schema, null, 2));
      showToast("Strategy schema copied.");
    });
    document.querySelector("[data-save-template]")?.addEventListener("click", async () => {
      try {
        schema = schemaFromForm(schema);
        const response = await api("/templates", {
          method: "POST",
          body: JSON.stringify({
            name: `${schema.name} Template`,
            category: "custom",
            tags: ["dashboard"],
            definition: schema,
            source_strategy_id: form.dataset.strategyId || null,
          }),
        });
        showToast(`Template saved: ${response.template.name}`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        schema = schemaFromForm(schema);
        const shouldPublish = Boolean(event.submitter?.dataset.publishSchema);
        const strategyId = form.dataset.strategyId;
        const path = strategyId ? `/strategies/${strategyId}/versions` : "/strategies";
        const response = await api(path, {
          method: "POST",
          body: JSON.stringify({
            definition: schema,
            source_text: interpretationMetadata.source_text || "dashboard structured builder",
            interpreter: interpretationMetadata.interpreter,
            assumptions: interpretationMetadata.assumptions,
            ambiguities: interpretationMetadata.ambiguities,
            unsupported_conditions: interpretationMetadata.unsupported_conditions,
          }),
        });
        const id = response.strategy?.id || strategyId;
        const version = response.version;
        if (shouldPublish && id && version) {
          await api(`/strategies/${id}/publish`, {
            method: "POST",
            body: JSON.stringify({
              strategy_version_id: version.id,
              expected_schema_hash: version.schema_hash,
            }),
          });
          showToast("Monitor published and marked active.");
          window.location.href = `/dashboard/monitors?message=monitor_published&t=${Date.now()}`;
          return;
        }
        showToast("Draft monitor saved.");
        if (id && !strategyId) window.location.href = `/dashboard/strategies/${id}`;
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  async function initVisualBuilder() {
    const shell = document.querySelector("[data-builder-shell]");
    const form = document.getElementById("strategy-builder-form");
    const tree = document.getElementById("condition-tree");
    if (!form || !tree) return;
    await loadCapabilityRegistry();

    let schema = loadInitialSchema();
    let translatedSchema = null;
    let creationPath = form.dataset.strategyId ? "Saved" : "Visual";
    let validationPassed = false;
    let validationFindings = [];
    let selectedLibraryGroup = null;
    let selectedLibraryCategory = "";
    const selectedTemplateCategories = new Set();
    let drawerSaveHandler = null;
    let lastFocusedElement = null;
    let builderSaving = false;
    let interpretationMetadata = {
      interpreter: "dashboard-builder-v3-canvas",
      assumptions: [],
      ambiguities: [],
      unsupported_conditions: [],
      source_text: null,
      prompt_coverage_report: null,
      coverage_score: null,
      confidence_score: null,
      mapping_table: [],
      visual_diff: null,
    };
    hydrateBuilderForm(schema);

    const drawer = document.getElementById("condition-editor-drawer");
    const drawerContent = document.getElementById("condition-editor-content");
    const drawerBackdrop = document.querySelector("[data-close-builder-drawer].builder-drawer-backdrop");
    const libraryModal = document.getElementById("condition-library-modal");

    function setHiddenField(name, value) {
      const input = field(form, name);
      if (input) input.value = value ?? "";
    }

    function optionMarkup(options, selected) {
      const selectedValues = new Set(safeArray(selected).map(String));
      return options.map((option) => {
        const value = typeof option === "string" ? option : option.value;
        const label = typeof option === "string" ? titleize(option) : option.label;
        const isSelected = selectedValues.size
          ? selectedValues.has(String(value))
          : String(value) === String(selected);
        return `<option value="${escapeHtml(value)}" ${isSelected ? "selected" : ""}>${escapeHtml(label)}</option>`;
      }).join("");
    }

    function factMarkup(label, value, accent = false) {
      return `<div class="${accent ? "accent" : ""}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function warningItems() {
      const leaves = collectConditionLeaves(schema.conditions);
      const warnings = [];
      const blocking = [];
      if (!leaves.length) blocking.push("Add at least one deterministic condition.");
      if (!schema.name || schema.name.trim().length < 3) blocking.push("Give the monitor a clear name.");
      if (!schema.universe?.exchange) blocking.push("Choose an exchange.");
      if (!safeArray(schema.universe?.quote_currencies).length) blocking.push("Choose at least one quote asset.");
      if (!safeArray(schema.alerts?.channels).length) blocking.push("Choose at least one alert destination.");
      safeArray(interpretationMetadata.unsupported_conditions).forEach((item) => {
        blocking.push(item.message || String(item));
      });
      if (leaves.length === 1) warnings.push("One condition may be noisy. Consider a filter or cooldown.");
      if (leaves.length >= 8) warnings.push("Many required conditions may produce very few matches.");
      if (!schema.near_miss?.enabled) warnings.push("Lifecycle forming alerts are limited because Near-Miss is off.");
      if (!safeArray(schema.supporting_timeframes).length) warnings.push("This monitor uses a single timeframe.");
      validationFindings.forEach((finding) => {
        const message = finding.message || String(finding);
        if (["critical", "error"].includes(String(finding.severity).toLowerCase())) {
          blocking.push(message);
        } else if (String(finding.severity).toLowerCase() === "warning") {
          warnings.push(message);
        }
      });
      return {
        leaves,
        warnings: [...new Set(warnings)],
        blocking: [...new Set(blocking)],
      };
    }

    function renderMonitorCard() {
      const title = document.getElementById("builder-canvas-title");
      const nodeName = document.getElementById("builder-monitor-node-name");
      const facts = document.getElementById("builder-monitor-facts");
      if (title) title.textContent = schema.name;
      if (nodeName) nodeName.textContent = schema.name;
      if (facts) {
        facts.innerHTML = [
          factMarkup("Direction", titleize(schema.direction)),
          factMarkup("Main timeframe", schema.base_timeframe),
          factMarkup("Trigger", schema.trigger_mode === "candle_close" ? "Candle close" : "Intrabar"),
          factMarkup("Status", validationPassed ? "Ready" : "Draft", validationPassed),
        ].join("");
      }
    }

    function renderUniverseCard() {
      const target = document.getElementById("builder-universe-facts");
      const empty = document.getElementById("builder-universe-empty");
      const includes = safeArray(schema.universe?.include_symbols);
      const quotes = safeArray(schema.universe?.quote_currencies);
      if (target) {
        target.innerHTML = [
          factMarkup("Exchange", titleize(schema.universe?.exchange || "Not selected")),
          factMarkup("Market", "Spot"),
          factMarkup("Quote", quotes.join(", ") || "Not selected"),
          factMarkup("Symbols", includes.length ? `${includes.length} selected` : "All eligible"),
          factMarkup("Exclusions", `${safeArray(schema.universe?.exclude_symbols).length} symbols`),
          factMarkup("Liquidity", schema.universe?.min_quote_volume_24h ? `Min ${Number(schema.universe.min_quote_volume_24h).toLocaleString()} quote volume` : "No minimum"),
        ].join("");
      }
      if (empty) empty.hidden = Boolean(schema.universe?.exchange && quotes.length);
    }

    function renderFilterBlock() {
      const target = document.getElementById("builder-filter-summary");
      if (!target) return;
      const filters = collectConditionLeaves(schema.conditions).filter((condition) => (
        ["market_filter", "risk"].includes(condition.condition_type) ||
        /trend|volume|session|context|liquidity|volatility/i.test(condition.label || "")
      ));
      target.innerHTML = filters.length
        ? filters.slice(0, 6).map((condition) => `<span>${escapeHtml(conditionSentence(condition))}</span>`).join("")
        : '<span class="muted-chip">No dedicated filters yet</span>';
    }

    function renderAlertCard() {
      const target = document.getElementById("builder-alert-facts");
      const empty = document.getElementById("builder-alert-empty");
      const channels = safeArray(schema.alerts?.channels);
      if (target) {
        target.innerHTML = [
          factMarkup("Confirmed alerts", "On"),
          factMarkup("Forming alerts", schema.alerts?.forming_alerts ? "On" : "Off"),
          factMarkup("Cooldown", `${Math.round(Number(schema.alerts?.cooldown_seconds || 0) / 60)} min`),
          factMarkup("Channels", channels.map(titleize).join(", ") || "None"),
          factMarkup("Max alerts", `${schema.alerts?.maximum_alerts_per_hour || 0}/hour`),
          factMarkup("Near-Miss", schema.near_miss?.enabled ? `${schema.alerts?.near_miss_threshold || 70}%` : "Off"),
        ].join("");
      }
      if (empty) empty.hidden = Boolean(channels.length);
    }

    function renderRiskCard() {
      const target = document.getElementById("builder-risk-facts");
      if (!target) return;
      target.innerHTML = [
        factMarkup("Validation", schema.risk?.enabled === false ? "Off" : "On"),
        factMarkup("Stop logic", titleize(schema.risk?.stop_method || schema.stop?.method || "structure")),
        factMarkup("Min R:R", schema.risk?.minimum_reward_to_risk ? `${schema.risk.minimum_reward_to_risk}R` : "Not set"),
        factMarkup("Max stop", schema.risk?.maximum_stop_percent ? `${schema.risk.maximum_stop_percent}%` : "Not set"),
      ].join("");
    }

    function renderRightPanel() {
      const leaves = collectConditionLeaves(schema.conditions);
      const summary = document.getElementById("builder-summary");
      if (summary) {
        const rules = leaves.slice(0, 4).map((condition) => `<li>${escapeHtml(conditionSentence(condition))}</li>`).join("");
        summary.innerHTML = `
          <p>This monitor scans <strong>${escapeHtml(titleize(schema.universe?.exchange || "selected exchange"))} spot ${escapeHtml(safeArray(schema.universe?.quote_currencies).join(", ") || "markets")}</strong> on <strong>${escapeHtml(schema.base_timeframe)}</strong>.</p>
          <p>It looks for a <strong>${escapeHtml(schema.direction)}</strong> setup where ${escapeHtml(operatorLabel(schema.conditions?.operator).toLowerCase())} configured rules agree.</p>
          ${rules ? `<ul>${rules}</ul>` : "<p>No monitor conditions are configured yet.</p>"}
        `;
      }
      const behavior = document.getElementById("builder-behavior-summary");
      if (behavior) {
        behavior.textContent = `${leaves.length} condition${leaves.length === 1 ? "" : "s"} - ${schema.trigger_mode === "candle_close" ? "candle-close confirmation" : "intrabar checks"} - ${safeArray(schema.alerts?.channels).join(" + ") || "no alert channel"}`;
      }
      renderCoveragePanel();
    }

    function renderCoverageMarkup() {
      const coverage = interpretationMetadata.prompt_coverage_report || {};
      const mapping = safeArray(interpretationMetadata.mapping_table || coverage.mapping_table);
      const assumptions = safeArray(interpretationMetadata.assumptions);
      const ambiguities = safeArray(interpretationMetadata.ambiguities);
      const unsupported = safeArray(interpretationMetadata.unsupported_conditions);
      const leaves = collectConditionLeaves(schema.conditions);
      const sourceRows = leaves
        .filter((condition) => condition.source_fragment)
        .map((condition) => `
          <div class="coverage-source-row">
            <strong>${escapeHtml(condition.label || condition.key)}</strong>
            <span>${escapeHtml(condition.source_fragment)}</span>
          </div>
        `)
        .join("");
      if (!coverage.coverage_score && !mapping.length && !sourceRows) {
        return '<div class="canvas-empty-state"><strong>No prompt coverage yet.</strong><span>Use Describe Strategy to generate a coverage report before opening the board.</span></div>';
      }
      return `
        <div class="understanding-metrics">
          <span>Coverage <strong>${escapeHtml(interpretationMetadata.coverage_score ?? coverage.coverage_score ?? "n/a")}%</strong></span>
          <span>Confidence <strong>${escapeHtml(interpretationMetadata.confidence_score ?? coverage.confidence_score ?? "n/a")}%</strong></span>
          <span>${escapeHtml(leaves.length)} mapped rule${leaves.length === 1 ? "" : "s"}</span>
        </div>
        ${sourceRows ? `<div class="coverage-section"><strong>Condition source trace</strong>${sourceRows}</div>` : ""}
        ${mapping.length ? `<div class="coverage-section"><strong>Prompt coverage map</strong><ul>${mapping.map((row) => `<li><span>${escapeHtml(row.fragment)}</span><em>${escapeHtml(row.bucket)}</em></li>`).join("")}</ul></div>` : ""}
        ${assumptions.length ? `<div class="coverage-section"><strong>Assumptions</strong><ul>${assumptions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
        ${ambiguities.length ? `<div class="coverage-section"><strong>Ambiguities</strong><ul>${ambiguities.map((item) => `<li>${escapeHtml(item.message || String(item))}</li>`).join("")}</ul></div>` : ""}
        ${unsupported.length ? `<div class="coverage-section"><strong>Unsupported / provider-required</strong><ul>${unsupported.map((item) => `<li>${escapeHtml(item.message || String(item))}</li>`).join("")}</ul></div>` : ""}
      `;
    }

    function renderCoveragePanel() {
      const target = document.getElementById("builder-coverage-panel");
      if (target) target.innerHTML = renderCoverageMarkup();
    }

    function renderValidationChecklist() {
      const { blocking, warnings, leaves } = warningItems();
      const checklist = document.getElementById("builder-confirm-checklist");
      if (checklist) {
        const rows = [
          ["Universe selected", Boolean(schema.universe?.exchange && safeArray(schema.universe?.quote_currencies).length)],
          ["Deterministic conditions added", Boolean(leaves.length)],
          ["Alert destination selected", Boolean(safeArray(schema.alerts?.channels).length)],
          ["Critical validation passed", validationPassed && !blocking.length],
        ];
        checklist.innerHTML = rows.map(([label, passed]) => `
          <div class="${passed ? "passed" : "pending"}"><span aria-hidden="true">${passed ? "OK" : "..."}</span><strong>${escapeHtml(label)}</strong></div>
        `).join("");
      }
      const warningsTarget = document.getElementById("builder-warnings");
      if (warningsTarget) {
        const rows = [
          ...blocking.map((item) => ({ type: "blocking", label: "Critical issue", item })),
          ...warnings.map((item) => ({ type: "warning", label: "Warning", item })),
        ];
        warningsTarget.innerHTML = rows.length
          ? rows.map((row) => `<div class="builder-warning ${row.type}"><strong>${row.label}</strong><span>${escapeHtml(row.item)}</span></div>`).join("")
          : '<div class="builder-warning success"><strong>Clear</strong><span>No local conflicts detected.</span></div>';
      }
    }

    function updateBuilderStatus() {
      const { blocking, warnings, leaves } = warningItems();
      const ready = validationPassed && !blocking.length;
      const state = ready ? "Ready" : blocking.length ? "Critical issue" : warnings.length ? "Needs review" : "Draft";
      const badge = document.getElementById("builder-status-badge");
      const readiness = document.getElementById("builder-readiness");
      const bottomStatus = document.getElementById("builder-bottom-status");
      const bottomCounts = document.getElementById("builder-bottom-counts");
      const pathBadge = document.getElementById("builder-path-badge");
      if (badge) {
        badge.textContent = state;
        badge.dataset.state = state.toLowerCase().replace(/\s+/g, "-");
      }
      if (readiness) readiness.textContent = ready ? "Ready" : state;
      if (bottomStatus) bottomStatus.textContent = ready ? "Ready to start" : "Review required";
      if (bottomCounts) bottomCounts.textContent = `${warnings.length} warning${warnings.length === 1 ? "" : "s"} - ${blocking.length} critical error${blocking.length === 1 ? "" : "s"} - ${leaves.length} condition${leaves.length === 1 ? "" : "s"}`;
      if (pathBadge) pathBadge.textContent = creationPath;
      document.querySelectorAll("[data-builder-validation-nudge]").forEach((item) => {
        item.textContent = ready
          ? "Validation passed. You can start monitoring or save this version as a draft."
          : "Validation is required before live activation. Click Validate to see plan, timeframe, universe, and condition checks.";
        item.dataset.state = ready ? "ready" : "required";
      });
      form.querySelectorAll("[data-publish-schema]").forEach((button) => {
        button.dataset.ready = String(ready);
        button.setAttribute("aria-disabled", String(!ready));
        button.title = ready
          ? "Start live monitoring"
          : "Run validation first. If you click now, the system will validate before activation.";
        if (builderSaving && button.dataset.previousLabel) return;
        button.disabled = button.closest(".legacy-builder-cards") ? !ready : false;
      });
    }

    function renderStrategyCanvas(rebuildTree = true) {
      schema = schemaFromForm(schema);
      schema.conditions = schema.conditions || {
        node_type: "group",
        key: "entry_conditions",
        operator: "and",
        parameters: {},
        children: [],
      };
      if (rebuildTree) {
        tree.innerHTML = "";
        renderNode(schema.conditions, tree, () => {
          validationPassed = false;
          validationFindings = [];
          renderStrategyCanvas(true);
        });
      }
      const leaves = collectConditionLeaves(schema.conditions);
      const empty = document.getElementById("builder-condition-empty");
      if (empty) empty.hidden = Boolean(leaves.length);
      const logicHeading = document.getElementById("builder-logic-heading");
      if (logicHeading) logicHeading.textContent = operatorLabel(schema.conditions.operator);
      const jsonBox = document.getElementById("builder-json");
      if (jsonBox) jsonBox.textContent = JSON.stringify(schema, null, 2);
      renderMonitorCard();
      renderUniverseCard();
      renderFilterBlock();
      renderAlertCard();
      renderRiskCard();
      renderRightPanel();
      renderValidationChecklist();
      updateBuilderStatus();
      if (boardContent) renderStrategyBoard();
    }

    const boardDialog = document.getElementById("strategy-board-dialog");
    const embeddedBoardRoot = document.querySelector('[data-board-root="embedded"]');
    let boardRoot = embeddedBoardRoot || boardDialog;
    let boardSurface = boardRoot?.querySelector("[data-board-surface]");
    let boardContent = boardRoot?.querySelector("[data-board-content]");
    let boardSteps = boardRoot?.querySelector("[data-board-steps]");
    let boardZoomLabel = boardRoot?.querySelector("[data-board-zoom-label]");
    const boardPositionKey = () => `traceedge-strategy-board-${form.dataset.strategyId || "draft"}`;
    let boardPositions = safeJson(window.localStorage.getItem(boardPositionKey()), {});
    const boardDeletedKey = () => `${boardPositionKey()}-deleted-connections`;
    let boardDeletedConnections = new Set(
      safeArray(safeJson(window.localStorage.getItem(boardDeletedKey()), [])),
    );
    let boardScale = 1;
    let boardPan = { x: 0, y: 0 };
    let boardDrag = null;
    let reopenBoardAfterDrawer = false;
    let reopenBoardAfterLibrary = false;
    const boardNodeWidth = 270;
    const boardNodeHeight = 128;

    function activateBoard(root) {
      if (!root) return;
      boardRoot = root;
      boardSurface = boardRoot.querySelector("[data-board-surface]");
      boardContent = boardRoot.querySelector("[data-board-content]");
      boardSteps = boardRoot.querySelector("[data-board-steps]");
      boardZoomLabel = boardRoot.querySelector("[data-board-zoom-label]");
      bindBoardSurface(boardSurface);
    }

    function activeBoardIsModal() {
      return boardRoot === boardDialog && Boolean(boardDialog?.open);
    }

    function closeActiveModalBoard() {
      if (activeBoardIsModal()) boardDialog?.close();
    }

    function saveBoardPositions() {
      try {
        window.localStorage.setItem(boardPositionKey(), JSON.stringify(boardPositions));
      } catch {
        // Board layout persistence is a convenience; the strategy schema remains canonical.
      }
    }

    function saveBoardDeletedConnections() {
      try {
        window.localStorage.setItem(
          boardDeletedKey(),
          JSON.stringify(Array.from(boardDeletedConnections)),
        );
      } catch {
        // Deleted visual links are UI-only; the strategy schema remains canonical.
      }
    }

    function setBoardTransform() {
      if (!boardContent) return;
      boardContent.style.transform = `translate(${boardPan.x}px, ${boardPan.y}px) scale(${boardScale})`;
      if (boardZoomLabel) boardZoomLabel.textContent = `${Math.round(boardScale * 100)}%`;
    }

    function boardGridPosition(column, row) {
      return { x: 70 + column * 315, y: 90 + row * 165 };
    }

    function boardNodeDefaults(index, id = "", action = "") {
      const fixed = {
        start: boardGridPosition(0, 1),
        monitor: boardGridPosition(1, 1),
        universe: boardGridPosition(2, 1),
        entry_logic: boardGridPosition(3, 1),
        filters: boardGridPosition(4, 0),
        risk: boardGridPosition(5, 1),
        alerts: boardGridPosition(5, 0),
        proof_review: boardGridPosition(6, 1),
      };
      if (fixed[id]) return fixed[id];
      const conditionIndex = Math.max(0, index - 4);
      if (action === "condition") return boardGridPosition(4, conditionIndex + 1);
      return boardGridPosition(4, conditionIndex);
    }

    function boardCellFromPosition(position) {
      return {
        column: Math.max(0, Math.round((position.x - 70) / 315)),
        row: Math.max(0, Math.round((position.y - 90) / 165)),
      };
    }

    function boardConnectionKey(from, to) {
      return `${from}::${to}`;
    }

    function snapBoardPosition(position, nodeId = "") {
      const otherEntries = Object.entries(boardPositions).filter(([id]) => id !== nodeId);
      let targetCell = boardCellFromPosition(position);
      if (otherEntries.length) {
        const nearest = otherEntries
          .map(([id, other]) => {
            const dx = position.x - other.x;
            const dy = position.y - other.y;
            return { id, other, distance: Math.hypot(dx, dy), dx, dy };
          })
          .sort((left, right) => left.distance - right.distance)[0];
        const nearestCell = boardCellFromPosition(nearest.other);
        if (Math.abs(nearest.dx) >= Math.abs(nearest.dy)) {
          targetCell = {
            column: Math.max(0, nearestCell.column + (nearest.dx >= 0 ? 1 : -1)),
            row: nearestCell.row,
          };
        } else {
          targetCell = {
            column: nearestCell.column,
            row: Math.max(0, nearestCell.row + (nearest.dy >= 0 ? 1 : -1)),
          };
        }
      }
      const occupied = new Set(
        otherEntries.map(([, other]) => {
          const cell = boardCellFromPosition(other);
          return `${cell.column}:${cell.row}`;
        }),
      );
      if (!occupied.has(`${targetCell.column}:${targetCell.row}`)) {
        return boardGridPosition(targetCell.column, targetCell.row);
      }
      for (let radius = 1; radius < 80; radius += 1) {
        const candidates = [];
        for (let dx = -radius; dx <= radius; dx += 1) {
          candidates.push({ column: targetCell.column + dx, row: targetCell.row - radius });
          candidates.push({ column: targetCell.column + dx, row: targetCell.row + radius });
        }
        for (let dy = -radius + 1; dy <= radius - 1; dy += 1) {
          candidates.push({ column: targetCell.column - radius, row: targetCell.row + dy });
          candidates.push({ column: targetCell.column + radius, row: targetCell.row + dy });
        }
        const open = candidates
          .filter((cell) => cell.column >= 0 && cell.row >= 0)
          .filter((cell) => !occupied.has(`${cell.column}:${cell.row}`))
          .sort((left, right) => {
            const leftPosition = boardGridPosition(left.column, left.row);
            const rightPosition = boardGridPosition(right.column, right.row);
            return (
              Math.hypot(leftPosition.x - position.x, leftPosition.y - position.y) -
              Math.hypot(rightPosition.x - position.x, rightPosition.y - position.y)
            );
          })[0];
        if (open) return boardGridPosition(open.column, open.row);
      }
      return boardGridPosition(targetCell.column, targetCell.row);
    }

    function findConditionByKey(key, node = schema.conditions) {
      if (!node) return null;
      if (node.node_type === "condition" && node.key === key) return node;
      for (const child of safeArray(node.children)) {
        const found = findConditionByKey(key, child);
        if (found) return found;
      }
      return null;
    }

    function boardNodes() {
      const leaves = collectConditionLeaves(schema.conditions);
      const filterCount = leaves.filter((condition) => (
        ["market_filter", "risk"].includes(condition.condition_type) ||
        /trend|volume|session|context|liquidity|volatility/i.test(condition.label || "")
      )).length;
      return [
        {
          id: "start",
          type: "Start",
          title: "Start",
          body: `${schema.name || "Untitled monitor"} begins with ${titleize(schema.direction || "long")} logic on ${schema.base_timeframe || "15m"}. Validate before activation.`,
          action: "start",
        },
        {
          id: "monitor",
          type: "Monitor",
          title: schema.name || "Untitled Monitor",
          body: `${titleize(schema.direction)} - ${schema.base_timeframe} - ${schema.trigger_mode === "candle_close" ? "Candle close" : "Intrabar"}`,
          action: "monitor",
        },
        {
          id: "universe",
          type: "Universe",
          title: `${titleize(schema.universe?.exchange || "Exchange")} spot`,
          body: `${safeArray(schema.universe?.quote_currencies).join(", ") || "Quotes"} - ${safeArray(schema.universe?.include_symbols).length ? `${safeArray(schema.universe?.include_symbols).length} symbols` : "All eligible"}`,
          action: "universe",
        },
        {
          id: "entry_logic",
          type: "Condition Logic",
          title: operatorLabel(schema.conditions?.operator),
          body: `${leaves.length} deterministic condition${leaves.length === 1 ? "" : "s"} feeding this monitor.`,
          action: "conditions",
        },
        ...leaves.map((condition, index) => ({
          id: condition.key || `condition-${index}`,
          type: titleize(condition.condition_type || "Condition"),
          title: condition.label || `Condition ${index + 1}`,
          body: conditionSentence(condition),
          action: "condition",
        })),
        {
          id: "filters",
          type: "Filters",
          title: "Noise control",
          body: `${filterCount} filter or context rule${filterCount === 1 ? "" : "s"} currently detected.`,
          action: "filters",
        },
        {
          id: "risk",
          type: "Risk Context",
          title: titleize(schema.risk?.stop_method || schema.stop?.method || "Structure stop"),
          body: schema.risk?.enabled === false
            ? "Risk validation is off."
            : `Max stop ${schema.risk?.maximum_stop_percent || "not set"}%, min ${schema.risk?.minimum_reward_to_risk || "not set"}R.`,
          action: "risk",
        },
        {
          id: "alerts",
          type: "Alerts",
          title: safeArray(schema.alerts?.channels).map(titleize).join(" + ") || "No channels",
          body: `${schema.alerts?.maximum_alerts_per_hour || 0}/hour max - ${Math.round(Number(schema.alerts?.cooldown_seconds || 0) / 60)} min cooldown`,
          action: "alerts",
        },
        {
          id: "proof_review",
          type: "Proof",
          title: validationPassed ? "Ready to activate" : "Review required",
          body: "Condition proof, strategy version and activation checks stay attached to this schema.",
          action: "proof",
        },
      ];
    }

    function boardConnections(nodes) {
      const ids = new Set(nodes.map((node) => node.id));
      const connections = [
        ["start", "monitor"],
        ["monitor", "universe"],
        ["universe", "entry_logic"],
        ["entry_logic", "filters"],
        ["entry_logic", "risk"],
        ["entry_logic", "alerts"],
        ["risk", "proof_review"],
        ["alerts", "proof_review"],
      ];
      collectConditionLeaves(schema.conditions).forEach((condition) => {
        if (condition.key) connections.push(["entry_logic", condition.key]);
      });
      return connections.filter(
        ([from, to]) => ids.has(from) && ids.has(to) && !boardDeletedConnections.has(boardConnectionKey(from, to)),
      );
    }

    function boardArrowPath(source, target) {
      const x1 = source.position.x + boardNodeWidth;
      const y1 = source.position.y + boardNodeHeight / 2;
      const x2 = target.position.x;
      const y2 = target.position.y + boardNodeHeight / 2;
      const mid = Math.max(x1 + 44, Math.round((x1 + x2) / 2));
      return `M ${x1} ${y1} L ${mid} ${y1} L ${mid} ${y2} L ${x2} ${y2}`;
    }

    function positionedBoardNodes() {
      return boardNodes().map((node, index) => {
        const position = boardPositions[node.id] || boardNodeDefaults(index, node.id, node.action);
        boardPositions[node.id] = position;
        return { ...node, position };
      });
    }

    function updateBoardArrowPaths() {
      if (!boardContent) return;
      const positioned = positionedBoardNodes();
      const nodeMap = new Map(positioned.map((node) => [node.id, node]));
      boardContent.querySelectorAll("[data-board-edge]").forEach((path) => {
        const source = nodeMap.get(path.dataset.boardEdgeFrom);
        const target = nodeMap.get(path.dataset.boardEdgeTo);
        if (source && target) path.setAttribute("d", boardArrowPath(source, target));
      });
    }

    function deleteBoardConnection(from, to) {
      if (!from || !to) return;
      if (from === "entry_logic") {
        const condition = findConditionByKey(to);
        const parent = condition ? findConditionParent(schema.conditions, condition) : null;
        if (condition && parent) {
          if (!window.confirm("Delete this condition from the strategy draft?")) return;
          parent.children = safeArray(parent.children).filter((child) => child !== condition && child.key !== condition.key);
          validationPassed = false;
          validationFindings = [];
          renderStrategyCanvas(true);
          renderStrategyBoard();
          showToast("Condition connection removed from the strategy draft.");
          return;
        }
      }
      boardDeletedConnections.add(boardConnectionKey(from, to));
      saveBoardDeletedConnections();
      renderStrategyBoard();
      showToast("Workflow connection hidden on this board. The strategy rules are unchanged.");
    }

    function attachBoardEdgeEvents() {
      if (!boardContent) return;
      boardContent.querySelectorAll("[data-board-edge]").forEach((path) => {
        path.addEventListener("mouseenter", () => {
          boardContent.querySelectorAll("[data-board-edge].highlighted").forEach((item) => {
            item.classList.remove("highlighted");
          });
          path.classList.add("highlighted");
        });
        path.addEventListener("mouseleave", () => {
          path.classList.remove("highlighted");
        });
        path.addEventListener("click", () => {
          deleteBoardConnection(path.dataset.boardEdgeFrom, path.dataset.boardEdgeTo);
        });
      });
    }

    function duplicateBoardNode(nodeId) {
      const condition = findConditionByKey(nodeId);
      if (!condition) {
        showToast("Only condition cards can be duplicated from the board.", "error");
        return;
      }
      const parent = findConditionParent(schema.conditions, condition);
      duplicateNode(condition, parent, () => {
        validationPassed = false;
        validationFindings = [];
        renderStrategyCanvas(true);
        renderStrategyBoard();
      });
      showToast("Condition card duplicated.");
    }

    function deleteOutgoingBoardConnections(nodeId) {
      const positioned = positionedBoardNodes();
      const outgoing = boardConnections(positioned).filter(([from]) => from === nodeId);
      if (!outgoing.length) {
        showToast("No visible outgoing connections to delete.", "error");
        return;
      }
      outgoing.forEach(([from, to]) => boardDeletedConnections.add(boardConnectionKey(from, to)));
      saveBoardDeletedConnections();
      renderStrategyBoard();
      showToast("Outgoing connection hidden on this board.");
    }

    function findConditionParent(parent, target) {
      for (const child of safeArray(parent?.children)) {
        if (child === target || child.key === target.key) return parent;
        const found = findConditionParent(child, target);
        if (found) return found;
      }
      return null;
    }

    function showBoardNodeMenu(nodeId, anchor) {
      const menu = boardContent?.querySelector("[data-board-node-menu-panel]");
      if (!menu || !anchor) return;
      const rect = anchor.getBoundingClientRect();
      const parentRect = boardContent.getBoundingClientRect();
      menu.style.left = `${(rect.left - parentRect.left) / boardScale + 22}px`;
      menu.style.top = `${(rect.top - parentRect.top) / boardScale - 12}px`;
      menu.hidden = false;
      menu.dataset.nodeId = nodeId;
      menu.innerHTML = `
        <button type="button" data-board-menu-action="duplicate">Duplicate card</button>
        <button type="button" data-board-menu-action="delete-outgoing">Delete connection</button>
        <button type="button" data-board-menu-action="close">Close</button>
      `;
      menu.querySelectorAll("[data-board-menu-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const action = button.dataset.boardMenuAction;
          menu.hidden = true;
          if (action === "duplicate") duplicateBoardNode(nodeId);
          if (action === "delete-outgoing") deleteOutgoingBoardConnections(nodeId);
        });
      });
    }

    function renderBoardInspector(selectedTab = "summary") {
      const { warnings, blocking, leaves } = warningItems();
      const summary = boardRoot?.querySelector("[data-board-summary]");
      if (summary) {
        summary.innerHTML = `
          <p><strong>${escapeHtml(schema.name || "Untitled Monitor")}</strong> scans ${escapeHtml(titleize(schema.universe?.exchange || "selected exchange"))} spot on ${escapeHtml(schema.base_timeframe)}.</p>
          <p>${escapeHtml(leaves.length)} condition${leaves.length === 1 ? "" : "s"} are mapped as draggable board nodes.</p>
        `;
      }
      const coverage = boardRoot?.querySelector("[data-board-coverage]");
      if (coverage) coverage.innerHTML = renderCoverageMarkup();
      const validation = boardRoot?.querySelector("[data-board-validation]");
      if (validation) {
        validation.innerHTML = `
          <strong>${blocking.length ? "Needs review" : validationPassed ? "Ready" : "Not validated"}</strong>
          <p>${escapeHtml(blocking[0] || warnings[0] || "No local conflicts detected. Run validation from the dashboard header before starting monitoring.")}</p>
        `;
      }
      const preview = boardRoot?.querySelector("[data-board-preview]");
      if (preview) {
        preview.innerHTML = '<div class="canvas-empty-state"><strong>Preview stays on the dashboard.</strong><span>Close or minimize the board, then use Preview matches in the Strategy Builder header.</span></div>';
      }
      const aiHelp = boardRoot?.querySelector("[data-board-ai-help]");
      if (aiHelp) {
        aiHelp.innerHTML = `
          <span class="eyebrow">AI helper</span>
          <strong>Ask for structure, then approve mechanics.</strong>
          <small>Use the Describe Strategy path for AI translation. This board only visualizes the approved schema draft.</small>
        `;
      }
      boardRoot?.querySelectorAll("[data-board-tab]").forEach((button) => {
        const active = button.dataset.boardTab === selectedTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
      });
      boardRoot?.querySelectorAll("[data-board-panel]").forEach((panel) => {
        const active = panel.dataset.boardPanel === selectedTab;
        panel.hidden = !active;
        panel.classList.toggle("active", active);
      });
    }

    function focusBoardNode(nodeId) {
      const node = boardContent?.querySelector(`[data-board-node="${CSS.escape(nodeId)}"]`);
      if (!node || !boardSurface) return;
      const x = Number.parseFloat(node.style.left) || 0;
      const y = Number.parseFloat(node.style.top) || 0;
      boardPan = {
        x: Math.round(boardSurface.clientWidth / 2 - (x + 130) * boardScale),
        y: Math.round(boardSurface.clientHeight / 2 - (y + 70) * boardScale),
      };
      setBoardTransform();
      node.classList.add("focused");
      window.setTimeout(() => node.classList.remove("focused"), 700);
    }

    function renderStrategyBoard() {
      if (!boardContent || !boardSteps) return;
      schema = schemaFromForm(schema);
      const positioned = positionedBoardNodes();
      const nodes = positioned;
      const nodeMap = new Map(positioned.map((node) => [node.id, node]));
      const arrows = boardConnections(positioned).map(([from, to]) => {
        const source = nodeMap.get(from);
        const target = nodeMap.get(to);
        if (!source || !target) return "";
        return `<path data-testid="strategy-board-edge" data-board-edge="${escapeHtml(boardConnectionKey(from, to))}" data-board-edge-from="${escapeHtml(from)}" data-board-edge-to="${escapeHtml(to)}" d="${escapeHtml(boardArrowPath(source, target))}" />`;
      }).join("");
      boardContent.innerHTML = `
        <svg class="strategy-board-arrows" width="10000" height="8000" viewBox="0 0 10000 8000" aria-label="Strategy board connections">
          <defs><marker id="board-arrow-head" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" /></marker></defs>
          ${arrows}
        </svg>
        ${positioned.map((node) => {
        return `
          <article class="strategy-board-node" data-testid="strategy-board-node" data-board-node="${escapeHtml(node.id)}" data-board-action="${escapeHtml(node.action)}" style="left:${node.position.x}px;top:${node.position.y}px">
            <span>${escapeHtml(node.type)}</span>
            <strong>${escapeHtml(node.title)}</strong>
            <p>${escapeHtml(node.body)}</p>
            <button class="board-node-connector" type="button" data-board-node-menu="${escapeHtml(node.id)}" aria-label="Open connection options for ${escapeHtml(node.title)}"></button>
            ${node.action === "start" ? "" : `<button type="button" data-board-edit="${escapeHtml(node.id)}">Edit node</button>`}
          </article>
        `;
      }).join("")}
        <div class="board-node-menu" data-board-node-menu-panel hidden></div>
      `;
      boardSteps.innerHTML = nodes.map((node, index) => `
        <button type="button" data-board-focus="${escapeHtml(node.id)}"><span>${index + 1}</span>${escapeHtml(node.title)}</button>
      `).join("");
      boardSteps.querySelectorAll("[data-board-focus]").forEach((button) => {
        button.addEventListener("click", () => focusBoardNode(button.dataset.boardFocus));
      });
      boardContent.querySelectorAll("[data-board-node]").forEach((node) => {
        node.addEventListener("pointerdown", (event) => {
          if (event.target.closest("button")) return;
          event.preventDefault();
          node.setPointerCapture(event.pointerId);
          const id = node.dataset.boardNode;
          const current = boardPositions[id] || { x: 0, y: 0 };
          node.classList.add("is-dragging");
          boardDrag = {
            type: "node",
            id,
            startX: event.clientX,
            startY: event.clientY,
            baseX: current.x,
            baseY: current.y,
          };
        });
      });
      boardContent.querySelectorAll("[data-board-node-menu]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          showBoardNodeMenu(button.dataset.boardNodeMenu, button);
        });
      });
      boardContent.querySelectorAll("[data-board-edit]").forEach((button) => {
        button.addEventListener("click", () => {
          const node = button.closest("[data-board-node]");
          const action = node?.dataset.boardAction;
          const id = button.dataset.boardEdit;
          if (action === "monitor" || action === "universe" || action === "alerts" || action === "risk") {
            reopenBoardAfterDrawer = activeBoardIsModal();
            closeActiveModalBoard();
            openSectionEditor(action);
            return;
          }
          if (action === "condition") {
            const condition = findConditionByKey(id);
            if (condition) {
              reopenBoardAfterDrawer = activeBoardIsModal();
              closeActiveModalBoard();
              openConditionDrawer(condition, () => renderStrategyCanvas(true));
            }
            return;
          }
          if (action === "filters") {
            reopenBoardAfterLibrary = activeBoardIsModal();
            closeActiveModalBoard();
            openConditionLibrary(schema.conditions, "filter");
            return;
          }
          if (action === "conditions") {
            reopenBoardAfterLibrary = activeBoardIsModal();
            closeActiveModalBoard();
            openConditionLibrary(schema.conditions);
            return;
          }
          renderBoardInspector("validation");
        });
      });
      attachBoardEdgeEvents();
      renderBoardInspector();
      setBoardTransform();
      saveBoardPositions();
    }

    function openStrategyBoard() {
      renderStrategyCanvas(false);
      activateBoard(boardDialog);
      renderStrategyBoard();
      boardDialog?.classList.add("maximized");
      boardDialog?.showModal();
    }

    function showMode(mode, path = null) {
      const selected = mode || "choose";
      if (path) creationPath = path;
      if (shell) shell.dataset.builderMode = selected;
      form.hidden = selected === "choose";
      document.querySelector("[data-builder-intro]")?.toggleAttribute("hidden", selected !== "choose");
      document.querySelectorAll("[data-builder-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.builderPanel !== selected;
      });
      if (selected === "choose") {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
      if (selected === "canvas") {
        renderStrategyCanvas(true);
      }
    }

    function closeBuilderDrawer() {
      if (!drawer) return;
      drawer.hidden = true;
      if (drawerBackdrop) drawerBackdrop.hidden = true;
      document.body.classList.remove("builder-drawer-open");
      drawerSaveHandler = null;
      if (reopenBoardAfterDrawer) {
        reopenBoardAfterDrawer = false;
        window.setTimeout(openStrategyBoard, 80);
        return;
      }
      lastFocusedElement?.focus?.();
    }

    function openDrawer(title, kicker, content, saveHandler) {
      if (!drawer || !drawerContent) return;
      lastFocusedElement = document.activeElement;
      document.getElementById("condition-editor-title").textContent = title;
      document.getElementById("condition-editor-kicker").textContent = kicker;
      drawerContent.innerHTML = content;
      drawerSaveHandler = saveHandler;
      drawer.hidden = false;
      if (drawerBackdrop) drawerBackdrop.hidden = false;
      document.body.classList.add("builder-drawer-open");
      window.setTimeout(() => drawerContent.querySelector("input, select, textarea, button")?.focus(), 0);
    }

    function applyObject(target, source) {
      Object.keys(target).forEach((key) => delete target[key]);
      Object.assign(target, source);
    }

    function openConditionDrawer(node, onChange) {
      const draft = safeJson(JSON.stringify(node), createCondition(0));
      const capability = capabilityForCondition(draft);
      const rightKind = draft.right?.kind || "constant";
      openDrawer(
        "Edit condition",
        titleize(draft.condition_type),
        `
          <div class="drawer-field-grid">
            <label>Human-readable name<input data-drawer-field="label" data-testid="condition-label" value="${escapeHtml(draft.label || "")}"></label>
            <label>Category<select data-drawer-field="condition_type">${conditionTypeOptions(draft.condition_type)}</select></label>
            <label>Source type<select data-drawer-field="operand_kind">${optionMarkup(["indicator", "price", "price_action", "candle_pattern", "market_metric", "risk_metric"], draft.left?.kind)}</select></label>
            <label>Source / operand<select data-drawer-field="left_name">${operandOptions(draft)}</select></label>
            <label>Comparator<select data-drawer-field="comparator">${optionMarkup(["gte", "gt", "lte", "lt", "eq", "crosses_above", "crosses_below", "is_true", "is_false"], draft.comparator)}</select></label>
            <label>Required value type<select data-drawer-field="right_kind">${optionMarkup(["constant", "indicator", "price"], rightKind)}</select></label>
            <label>Required value<input data-drawer-field="right_value" value="${escapeHtml(conditionValue(draft))}"></label>
            <label>Timeframe<select data-drawer-field="timeframe">${optionMarkup(["1m", "5m", "15m", "30m", "1h", "4h", "1d"], draft.timeframe)}</select></label>
            <label>Required or optional<select data-drawer-field="required">${optionMarkup([{value: "true", label: "Required"}, {value: "false", label: "Optional"}], String(draft.required !== false))}</select></label>
            <label>Near-Miss weight<input data-drawer-field="weight" type="number" min="0.1" step="0.1" value="${escapeHtml(draft.weight || 1)}"></label>
            <label>Forming tolerance %<input data-drawer-field="forming_tolerance_percent" type="number" min="0" max="100" step="0.1" value="${escapeHtml(draft.forming_tolerance_percent ?? "")}"></label>
            <label>Required data<input data-drawer-field="required_data" value="${escapeHtml(safeArray(draft.required_data).join(","))}"></label>
          </div>
          ${capability ? `<div class="drawer-parameter-section"><h3>Condition parameters</h3><div class="drawer-field-grid">${conditionParameterFields(capability, draft)}</div></div>` : ""}
          ${draft.source_fragment ? `<div class="drawer-callout"><strong>Why this condition exists</strong><span>${escapeHtml(draft.source_fragment)}</span></div>` : ""}
          <label>Explanation template<textarea data-drawer-field="explanation_template" rows="3">${escapeHtml(draft.explanation_template || "")}</textarea></label>
          <details class="drawer-advanced">
            <summary>Advanced settings</summary>
            <div class="drawer-field-grid">
              <label>Condition ID<input data-drawer-field="key" value="${escapeHtml(draft.key || "")}"></label>
              <label>Cap score on fail<input data-drawer-field="cap_score_on_fail" type="number" min="0" max="100" value="${escapeHtml(draft.cap_score_on_fail ?? "")}"></label>
              <label>Source parameters JSON<input data-drawer-field="parameters" value="${parameterText(draft)}"></label>
              <label>Source fragment<input data-drawer-field="source_fragment" data-testid="condition-source-fragment" value="${escapeHtml(draft.source_fragment || "")}"></label>
              <label>Confidence<input data-drawer-field="confidence" data-testid="condition-confidence" type="number" min="0" max="1" step="0.01" value="${escapeHtml(draft.confidence ?? "")}"></label>
              <label>AI interpreted<select data-drawer-field="ai_interpreted">${optionMarkup([{value: "true", label: "Yes"}, {value: "false", label: "No"}], String(Boolean(draft.ai_interpreted)))}</select></label>
              <label>Provider required<select data-drawer-field="provider_required" data-testid="condition-provider-required">${optionMarkup([{value: "true", label: "Yes"}, {value: "false", label: "No"}], String(Boolean(draft.provider_required)))}</select></label>
              <label>Availability<input data-drawer-field="availability" data-testid="condition-availability" value="${escapeHtml(draft.availability || "available")}"></label>
              <label>Approximation note<input data-drawer-field="approximation_note" data-testid="condition-approximation-note" value="${escapeHtml(draft.approximation_note || "")}"></label>
              <label>Notes<input data-drawer-field="notes" data-testid="condition-notes" value="${escapeHtml(draft.notes || capability?.risk_notes || "")}"></label>
            </div>
          </details>
        `,
        () => {
          const get = (name) => drawerContent.querySelector(`[data-drawer-field="${name}"]`)?.value ?? "";
          draft.label = get("label");
          draft.condition_type = get("condition_type");
          draft.timeframe = get("timeframe");
          draft.comparator = get("comparator");
          draft.required = get("required") === "true";
          draft.weight = Number(get("weight") || 1);
          draft.forming_tolerance_percent = get("forming_tolerance_percent") === "" ? null : Number(get("forming_tolerance_percent"));
          draft.required_data = csv(get("required_data"));
          draft.explanation_template = get("explanation_template");
          draft.key = get("key") || uniqueConditionKey(draft.label);
          draft.cap_score_on_fail = get("cap_score_on_fail") === "" ? null : Number(get("cap_score_on_fail"));
          draft.notes = get("notes") || null;
          draft.source_fragment = get("source_fragment") || null;
          draft.confidence = get("confidence") === "" ? null : Number(get("confidence"));
          draft.ai_interpreted = get("ai_interpreted") === "true";
          draft.provider_required = get("provider_required") === "true";
          draft.availability = get("availability") || "available";
          draft.approximation_note = get("approximation_note") || null;
          const forcedKind = defaultOperandKind(draft.condition_type);
          const configuredOperandKind = get("operand_kind");
          const operandKind = ["market_filter", "risk", "price_action", "candle_pattern"].includes(draft.condition_type)
            ? forcedKind
            : configuredOperandKind;
          const operandName = get("left_name");
          draft.left = operandKind === "price"
            ? { kind: "price", field: operandName || "close", parameters: safeJson(get("parameters"), {}) }
            : { kind: operandKind, name: operandName, parameters: safeJson(get("parameters"), {}) };
          const configuredRightKind = get("right_kind");
          const rightValue = get("right_value");
          if (["is_true", "is_false"].includes(draft.comparator)) {
            draft.right = null;
          } else if (configuredRightKind === "constant") {
            setConditionValue(draft, rightValue);
          } else if (configuredRightKind === "price") {
            draft.right = { kind: "price", field: rightValue || "close", parameters: {} };
          } else {
            draft.right = { kind: "indicator", name: rightValue, parameters: {} };
          }
          const parameterOperand = capabilityParameterOperand(draft, capability);
          if (parameterOperand) {
            parameterOperand.parameters = parameterOperand.parameters || {};
            drawerContent.querySelectorAll("[data-condition-parameter]").forEach((parameterField) => {
              const type = parameterField.dataset.parameterType;
              parameterOperand.parameters[parameterField.dataset.conditionParameter] = type === "boolean"
                ? parameterField.value === "true"
                : ["integer", "number"].includes(type)
                ? Number(parameterField.value)
                : parameterField.value;
            });
          }
          applyObject(node, draft);
          onChange(true);
        },
      );
      const categorySelect = drawerContent.querySelector('[data-drawer-field="condition_type"]');
      const kindSelect = drawerContent.querySelector('[data-drawer-field="operand_kind"]');
      const sourceSelect = drawerContent.querySelector('[data-drawer-field="left_name"]');
      const refreshSourceOptions = () => {
        if (!categorySelect || !kindSelect || !sourceSelect) return;
        const conditionType = categorySelect.value;
        const forcedKind = defaultOperandKind(conditionType);
        if (["market_filter", "risk", "price_action", "candle_pattern"].includes(conditionType)) {
          kindSelect.value = forcedKind;
        }
        const optionNode = {
          ...draft,
          condition_type: conditionType,
          left: { kind: kindSelect.value, name: "", field: "close", parameters: {} },
        };
        sourceSelect.innerHTML = operandOptions(optionNode);
      };
      categorySelect?.addEventListener("change", refreshSourceOptions);
      kindSelect?.addEventListener("change", refreshSourceOptions);
    }

    function openGroupDrawer(node, onChange) {
      const draft = safeJson(JSON.stringify(node), createGroup(0));
      const operators = logicOperators();
      const groupSpec = operators.find((item) => item.key === draft.operator);
      openDrawer(
        "Edit logic group",
        "Advanced logic",
        `
          <div class="drawer-field-grid">
            <label>Group name<input data-drawer-group="key" value="${escapeHtml(draft.key || "")}"></label>
            <label>Operator<select data-drawer-group="operator">${operators.map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === draft.operator ? "selected" : ""}>${escapeHtml(item.display_name || operatorLabel(item.key))}</option>`).join("")}</select></label>
            ${groupParameterFields(groupSpec, draft)}
          </div>
          <div class="drawer-callout">Use ALL OF for confirmation, ANY OF for alternatives, and SEQUENCE when order matters.</div>
          <details class="drawer-advanced">
            <summary>Advanced group parameters</summary>
            <label>Parameters JSON<textarea data-drawer-group="parameters" rows="5">${escapeHtml(JSON.stringify(draft.parameters || {}, null, 2))}</textarea></label>
          </details>
        `,
        () => {
          draft.key = drawerContent.querySelector('[data-drawer-group="key"]')?.value || draft.key;
          draft.operator = drawerContent.querySelector('[data-drawer-group="operator"]')?.value || "and";
          draft.parameters = safeJson(drawerContent.querySelector('[data-drawer-group="parameters"]')?.value || "{}", {});
          drawerContent.querySelectorAll("[data-group-parameter]").forEach((parameterField) => {
            const type = parameterField.dataset.parameterType;
            draft.parameters[parameterField.dataset.groupParameter] = type === "boolean"
              ? parameterField.value === "true"
              : ["integer", "number"].includes(type)
              ? Number(parameterField.value)
              : parameterField.value;
          });
          normalizeGroupChildren(draft);
          applyObject(node, draft);
          onChange(true);
        },
      );
    }

    function openSectionEditor(section) {
      schema = schemaFromForm(schema);
      const configurations = {
        monitor: {
          title: "Monitor overview",
          content: `
            <div class="drawer-field-grid">
              <label>Monitor name<input data-section-field="name" value="${escapeHtml(schema.name)}"></label>
              <label>Direction<select data-section-field="direction">${optionMarkup(["long", "short", "both"], schema.direction)}</select></label>
              <label>Main timeframe<select data-section-field="base_timeframe">${optionMarkup(["1m", "5m", "15m", "30m", "1h", "4h", "1d"], schema.base_timeframe)}</select></label>
              <label>Trigger mode<select data-section-field="trigger_mode">${optionMarkup([{value: "candle_close", label: "Candle close"}, {value: "intrabar", label: "Intrabar"}], schema.trigger_mode)}</select></label>
              <label>Higher timeframes<select data-section-field="supporting_timeframes" multiple size="5">${optionMarkup(["1m", "5m", "15m", "30m", "1h", "4h", "1d"], safeArray(schema.supporting_timeframes))}</select><small>Hold Ctrl/Cmd to choose more than one.</small></label>
            </div>
            <label>Description<textarea data-section-field="description" rows="4">${escapeHtml(schema.description || "")}</textarea></label>
          `,
          save: () => {
            ["name", "direction", "base_timeframe", "trigger_mode", "supporting_timeframes", "description"].forEach((name) => {
              const control = drawerContent.querySelector(`[data-section-field="${name}"]`);
              const value = control?.multiple
                ? Array.from(control.selectedOptions).map((option) => option.value).join(",")
                : control?.value;
              setHiddenField(name, value);
            });
          },
        },
        universe: {
          title: "Edit universe",
          content: `
            <div class="drawer-field-grid">
              <label>Exchange<select data-section-field="exchange">${optionMarkup([{value: "binance", label: "Binance"}, {value: "bybit", label: "Bybit"}], schema.universe?.exchange)}</select></label>
              <label>Market<input value="Spot" disabled></label>
              <label>Quote assets<select data-section-field="quote" multiple size="2">${optionMarkup(["USDT", "USDC"], safeArray(schema.universe?.quote_currencies).length ? safeArray(schema.universe?.quote_currencies) : ["USDT"])}</select><small>Supported quote assets: USDT and USDC.</small></label>
              <label>Symbols<input data-section-field="include_symbols" value="${escapeHtml(safeArray(schema.universe?.include_symbols).join(","))}" placeholder="Blank scans all eligible symbols"></label>
              <label>Excluded symbols<input data-section-field="exclude_symbols" value="${escapeHtml(safeArray(schema.universe?.exclude_symbols).join(","))}"></label>
              <label>Min 24h quote volume<input data-section-field="min_quote_volume_24h" type="number" min="0" value="${escapeHtml(schema.universe?.min_quote_volume_24h || "")}"></label>
              <label>Max spread (bps)<input data-section-field="max_spread_bps" type="number" min="0" value="${escapeHtml(schema.universe?.max_spread_bps || "")}"></label>
            </div>
          `,
          save: () => {
            ["exchange", "quote", "include_symbols", "exclude_symbols", "min_quote_volume_24h", "max_spread_bps"].forEach((name) => {
              const control = drawerContent.querySelector(`[data-section-field="${name}"]`);
              const value = control?.multiple
                ? Array.from(control.selectedOptions).map((option) => option.value).join(",")
                : control?.value;
              setHiddenField(name, value);
            });
          },
        },
        alerts: {
          title: "Edit alert rules",
          content: `
            <fieldset class="drawer-channel-picker">
              <legend>Alert channels</legend>
              ${["telegram", "discord", "dashboard"].map((channel) => `<label><input type="checkbox" data-alert-channel value="${channel}" ${safeArray(schema.alerts?.channels).includes(channel) ? "checked" : ""}><span>${titleize(channel)}</span></label>`).join("")}
            </fieldset>
            <div class="drawer-field-grid">
              <label>Forming alerts<select data-section-field="forming_alerts">${optionMarkup([{value: "true", label: "On"}, {value: "false", label: "Off"}], String(schema.alerts?.forming_alerts !== false))}</select></label>
              <label>Near-Miss alerts<select data-section-field="near_miss_enabled">${optionMarkup([{value: "true", label: "On"}, {value: "false", label: "Off"}], String(schema.near_miss?.enabled !== false))}</select></label>
              <label>Near-Miss threshold %<input data-section-field="near_miss_threshold" type="number" min="1" max="99" value="${escapeHtml(schema.alerts?.near_miss_threshold || 70)}"></label>
              <label>Cooldown seconds<input data-section-field="cooldown_seconds" type="number" min="0" value="${escapeHtml(schema.alerts?.cooldown_seconds || 900)}"></label>
              <label>Max alerts per hour<input data-section-field="maximum_alerts_per_hour" type="number" min="1" value="${escapeHtml(schema.alerts?.maximum_alerts_per_hour || 10)}"></label>
            </div>
          `,
          save: () => {
            const channels = Array.from(drawerContent.querySelectorAll("[data-alert-channel]:checked")).map((item) => item.value);
            setHiddenField("alert_channels", channels.join(","));
            ["forming_alerts", "near_miss_enabled", "near_miss_threshold", "cooldown_seconds", "maximum_alerts_per_hour"].forEach((name) => {
              setHiddenField(name, drawerContent.querySelector(`[data-section-field="${name}"]`)?.value);
            });
          },
        },
        risk: {
          title: "Edit risk context",
          content: `
            <div class="drawer-callout">Risk context filters and explains setups. TraceEdge does not execute trades.</div>
            <div class="drawer-field-grid">
              <label>Risk validation<select data-section-field="risk_enabled">${optionMarkup([{value: "false", label: "Disabled"}, {value: "true", label: "Enabled"}], String(schema.risk?.enabled === true))}</select></label>
              <label>Stop method<select data-section-field="stop_method">${optionMarkup(["structure", "fixed_percent", "atr", "swing_low", "swing_high"], schema.risk?.stop_method || schema.stop?.method)}</select></label>
              <label>Maximum stop %<input data-section-field="maximum_stop_percent" type="number" min="0.1" step="0.1" value="${escapeHtml(schema.risk?.maximum_stop_percent || "")}"></label>
              <label>Target R multiple<input data-section-field="target_value" type="number" min="0.1" step="0.1" value="${escapeHtml(schema.risk?.target_value || schema.targets?.[0]?.value || "")}"></label>
              <label>Minimum R:R<input data-section-field="minimum_reward_to_risk" type="number" min="0.1" step="0.1" value="${escapeHtml(schema.risk?.minimum_reward_to_risk || "")}"></label>
            </div>
          `,
          save: () => {
            ["risk_enabled", "stop_method", "maximum_stop_percent", "target_value", "minimum_reward_to_risk"].forEach((name) => {
              setHiddenField(name, drawerContent.querySelector(`[data-section-field="${name}"]`)?.value);
            });
          },
        },
      };
      const config = configurations[section];
      if (!config) return;
      openDrawer(config.title, "Strategy Canvas", config.content, () => {
        config.save();
        validationPassed = false;
        validationFindings = [];
        renderStrategyCanvas(true);
      });
    }

    function rekeyNode(node) {
      node.key = uniqueConditionKey(node.key || node.node_type);
      safeArray(node.children).forEach(rekeyNode);
      return node;
    }

    function duplicateNode(node, parent, onChange) {
      if (!parent) return;
      const copy = rekeyNode(safeJson(JSON.stringify(node), {}));
      const index = parent.children.indexOf(node);
      parent.children.splice(index + 1, 0, copy);
      onChange(true);
    }

    function removeNode(node, parent, onChange) {
      if (!parent || !window.confirm("Delete this rule from the strategy map?")) return;
      parent.children = parent.children.filter((child) => child !== node);
      onChange(true);
    }

    function groupAcceptsAnotherChild(group) {
      const singleChildOperators = new Set([
        "not",
        "within_last",
        "persisted_for",
        "first_time_true",
        "changed_state",
        "cross_with_confirmation",
      ]);
      if (singleChildOperators.has(group.operator) && safeArray(group.children).length >= 1) {
        showToast(`${operatorLabel(group.operator)} accepts one child.`, "error");
        return false;
      }
      if (group.operator === "conditional_branch" && safeArray(group.children).length >= 3) {
        showToast("IF / OTHERWISE uses exactly three children.", "error");
        return false;
      }
      return true;
    }

    function addNodeToGroup(node, targetGroup = null) {
      const group = targetGroup || schema.conditions;
      group.children = safeArray(group.children);
      if (!groupAcceptsAnotherChild(group)) return false;
      collectConditionLeaves(node).forEach((condition) => {
        if (
          condition.timeframe &&
          condition.timeframe !== schema.base_timeframe &&
          !safeArray(schema.supporting_timeframes).includes(condition.timeframe)
        ) {
          schema.supporting_timeframes = [...safeArray(schema.supporting_timeframes), condition.timeframe];
          setHiddenField("supporting_timeframes", schema.supporting_timeframes.join(","));
        }
      });
      group.children.push(node);
      validationPassed = false;
      validationFindings = [];
      renderStrategyCanvas(true);
      return true;
    }

    function addGroupToGroup(group, onChange) {
      if (!groupAcceptsAnotherChild(group)) return;
      group.children.push(createGroup(group.children.length, "and"));
      onChange(true);
    }

    function conditionNeedsDetails(capability) {
      return safeArray(capability?.parameters).some((parameter) => (
        parameter.required &&
        parameter.default === undefined &&
        capability.default_parameters?.[parameter.name] === undefined
      ));
    }

    function popularCapabilityKeys() {
      return new Set([
        "rsi_oversold",
        "rsi_overbought",
        "price_above_ema",
        "price_below_ema",
        "relative_volume",
        "volume_spike",
        "breakout",
        "breakdown",
        "bullish_engulfing",
        "bearish_engulfing",
        "liquidity_sweep",
        "vwap_reclaim",
        "bollinger_squeeze",
        "percent_change_up",
        "percent_change_down",
      ]);
    }

    function itemMatchesLibraryCategory(item, category) {
      if (!category || category === "popular") {
        return item.beginner_friendly || popularCapabilityKeys().has(item.key);
      }
      return item.builder_category === category || item.category === category;
    }

    function renderConditionLibrary() {
      const search = document.querySelector("[data-capability-search]");
      const categoriesTarget = document.querySelector("[data-capability-categories]");
      const target = document.querySelector("[data-capability-results]");
      if (!search || !categoriesTarget || !target) return;
      const categories = safeArray(capabilityRegistry.guidebook_categories || capabilityRegistry.categories);
      categoriesTarget.innerHTML = [
        ...categories.map((item) => `<button type="button" class="${(selectedLibraryCategory || "popular") === item.key ? "active" : ""}" data-library-category="${escapeHtml(item.key)}" title="${escapeHtml(item.description || "")}">${escapeHtml(item.display_name)} <span>${escapeHtml(item.count)}</span></button>`),
      ].join("");
      categoriesTarget.querySelectorAll("[data-library-category]").forEach((button) => {
        button.addEventListener("click", () => {
          selectedLibraryCategory = button.dataset.libraryCategory;
          renderConditionLibrary();
        });
      });
      const query = search.value.trim().toLowerCase();
      const items = safeArray(capabilityRegistry.items)
        .filter((item) => query && !selectedLibraryCategory ? true : itemMatchesLibraryCategory(item, selectedLibraryCategory || "popular"))
        .filter((item) => {
          if (!query) return true;
          return [
            item.key,
            item.display_name,
            item.description,
            item.example_sentence,
            item.visual_card_sentence,
            item.builder_category,
            item.provider_badge,
            item.implementation_status,
            ...safeArray(item.prompt_aliases),
          ].join(" ").toLowerCase().includes(query);
        })
        .slice(0, query ? 60 : 24);
      target.innerHTML = items.length ? items.map((item) => {
        const dataTags = Array.from(new Set([
          ...safeArray(item.required_data),
          item.provider_badge || "Public data",
          item.free_plan === false ? "Paid plan" : "Free plan",
          titleize(item.implementation_status || item.availability || "available"),
        ].filter(Boolean).map((value) => String(value).toUpperCase() === "OHLCV" ? "OHLCV" : String(value))));
        const ruleDetail = [
          item.description || "Deterministic condition that can be added to this strategy.",
          item.visual_card_sentence ? `Preview: ${item.visual_card_sentence}` : "",
        ].filter(Boolean).join(" ");
        return `
        <article class="condition-library-card">
          <div class="condition-library-card-head">
            <span class="condition-category">${escapeHtml(titleize(item.builder_category || item.category))}</span>
            <span class="complexity-badge">${item.beginner_friendly ? "Beginner" : "Advanced"}</span>
          </div>
          <strong>${escapeHtml(item.display_name || item.label)}</strong>
          <div class="condition-library-meta">
            ${(dataTags.length ? dataTags : ["OHLCV"]).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
          </div>
          <button class="condition-explain-link" type="button" data-explain-capability="${escapeHtml(item.key)}" data-rule-detail="${escapeHtml(ruleDetail)}">Explain This Rule</button>
          ${item.implementation_status === "implemented"
            ? `<button class="button button-primary" type="button" data-add-capability="${escapeHtml(item.key)}">Add condition</button>`
            : `<button class="button button-secondary" type="button" disabled>${escapeHtml(titleize(item.implementation_status))}</button>`}
        </article>
      `;
      }).join("") : '<div class="canvas-empty-state"><strong>No matching conditions.</strong><span>Try an indicator name, prompt phrase, or another category.</span></div>';
      target.querySelectorAll("[data-add-capability]").forEach((button) => {
        button.addEventListener("click", () => {
          const capability = safeArray(capabilityRegistry.items).find((item) => item.key === button.dataset.addCapability);
          if (!capability) return;
          const condition = conditionFromCapability(capability, schema.base_timeframe, safeArray(selectedLibraryGroup?.children).length);
          if (!addNodeToGroup(condition, selectedLibraryGroup)) return;
          if (conditionNeedsDetails(capability)) {
            reopenBoardAfterLibrary = false;
            reopenBoardAfterDrawer = activeBoardIsModal();
          }
          libraryModal?.close();
          showToast(`${capability.display_name || capability.label} added.`);
          if (conditionNeedsDetails(capability)) openConditionDrawer(condition, () => renderStrategyCanvas(true));
        });
      });
      target.querySelectorAll("[data-explain-capability]").forEach((button) => {
        button.addEventListener("click", () => {
          const isOpen = button.classList.contains("is-open");
          target.querySelectorAll(".condition-explain-link.is-open").forEach((openButton) => {
            if (openButton !== button) openButton.classList.remove("is-open");
          });
          button.classList.toggle("is-open", !isOpen);
          button.setAttribute("aria-expanded", String(!isOpen));
          button.focus();
        });
      });
    }

    function openConditionLibrary(group = null, query = "") {
      selectedLibraryGroup = group || schema.conditions;
      selectedLibraryCategory = "";
      const search = document.querySelector("[data-capability-search]");
      if (search) search.value = query;
      renderConditionLibrary();
      libraryModal?.showModal();
      window.setTimeout(() => search?.focus(), 0);
    }

    async function submitInterpretationFeedback(feedbackType, response) {
      try {
        await api("/strategies/interpret/feedback", {
          method: "POST",
          body: JSON.stringify({
            feedback_type: feedbackType,
            raw_prompt: interpretationMetadata.source_text,
            prompt_coverage_report: response.prompt_coverage_report || interpretationMetadata.prompt_coverage_report || {},
            strategy: response.strategy || translatedSchema || schema,
          }),
        });
        showToast("Interpretation feedback recorded.");
      } catch (error) {
        showToast(error.message, "error");
      }
    }

    function renderPromptUnderstandingPreview(response, metadata) {
      const target = document.getElementById("builder-ai-summary");
      if (!target) return;
      const rules = safeArray(response.interpreted_rules);
      const assumptions = safeArray(response.assumptions);
      const ambiguities = safeArray(response.ambiguities);
      const unsupported = safeArray(response.unsupported_conditions);
      const coverage = response.prompt_coverage_report || {};
      const mapping = safeArray(coverage.mapping_table);
      const ignored = safeArray(response.ignored_fragments || coverage.ignored_fragments);
      const optionalRules = safeArray(response.optional_rules);
      const issueChips = [...ambiguities, ...unsupported].map((item) => `<span>${escapeHtml(item.message || String(item))}</span>`).join("");
      target.hidden = false;
      target.classList.toggle("error", Boolean(response.activation_blocked));
      target.innerHTML = `
        <div class="understanding-preview-head">
          <div><span class="eyebrow">Understanding Preview</span><h3>${escapeHtml(response.understanding?.name || response.strategy?.name || "Monitor draft")}</h3></div>
          <span class="builder-state-badge" data-state="${response.activation_blocked ? "critical-issue" : "needs-review"}">${response.activation_blocked ? "Needs clarification" : "Ready to review"}</span>
        </div>
        <p>${escapeHtml(response.understanding?.direction || response.strategy?.direction || "Both")} direction on ${escapeHtml(safeArray(response.understanding?.timeframes).join(", ") || response.strategy?.base_timeframe || "selected timeframe")}.</p>
        <div class="understanding-metrics">
          <span>Prompt coverage <strong data-testid="prompt-coverage-score">${escapeHtml(response.coverage_score ?? coverage.coverage_score ?? "n/a")}%</strong></span>
          <span>Confidence <strong>${escapeHtml(response.confidence_score ?? coverage.confidence_score ?? "n/a")}%</strong></span>
          <span>${escapeHtml(rules.length)} rule${rules.length === 1 ? "" : "s"} created</span>
        </div>
        <div class="understanding-rule-list">
          ${rules.map((rule) => `<div data-testid="interpreted-rule-card"><strong>${escapeHtml(rule.name)}</strong><span>${escapeHtml(rule.timeframe)} - ${escapeHtml(comparatorLabel(rule.operator))}</span><small>From: ${escapeHtml(rule.source_fragment || "not supplied")}</small></div>`).join("") || "<div><strong>No executable rule recognized</strong><span>Clarify the monitor condition.</span></div>"}
        </div>
        ${optionalRules.length ? `<div class="understanding-section"><strong>Optional confirmations</strong><ul>${optionalRules.map((rule) => `<li>${escapeHtml(rule.name || rule.condition_id)}</li>`).join("")}</ul></div>` : ""}
        ${assumptions.length ? `<div class="understanding-section"><strong>Assumptions</strong><ul>${assumptions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
        ${mapping.length ? `<div class="understanding-section"><strong>Prompt coverage map</strong><ul>${mapping.map((row) => `<li>${escapeHtml(row.fragment)} -> ${escapeHtml(row.bucket)}</li>`).join("")}</ul></div>` : ""}
        ${ignored.length ? `<div class="understanding-section"><strong>Ignored filler</strong><ul>${ignored.map((item) => `<li>${escapeHtml(item.fragment || item)}</li>`).join("")}</ul></div>` : ""}
        ${issueChips ? `<div class="understanding-section" data-testid="${response.activation_blocked ? "critical-activation-blocker" : "provider-required-warning"}"><strong>Clarifications</strong><div class="clarification-chip-list">${issueChips}</div></div>` : ""}
        <div class="understanding-feedback">
          <strong>Mark this interpretation</strong>
          <div>
            <button type="button" data-interpretation-feedback="correct">This is correct</button>
            <button type="button" data-interpretation-feedback="wrong_timeframe">Wrong timeframe</button>
            <button type="button" data-interpretation-feedback="missed_condition">Missed a condition</button>
            <button type="button" data-interpretation-feedback="wrong_direction">Wrong direction</button>
            <button type="button" data-interpretation-feedback="too_strict">Too strict</button>
            <button type="button" data-interpretation-feedback="too_loose">Too loose</button>
            <button type="button" data-interpretation-feedback="start_over">Start over</button>
          </div>
        </div>
        <div class="button-row">
          <button class="button button-primary confirm-highlight" type="button" data-open-interpreted-map data-testid="open-strategy-board" ${response.activation_blocked ? "disabled" : ""}>Open Strategy Board</button>
          <button class="button button-secondary back-highlight" type="button" data-focus-builder-prompt>Go back</button>
        </div>
      `;
      target.querySelector("[data-open-interpreted-map]")?.addEventListener("click", () => {
        if (!translatedSchema) return;
        applySchema(translatedSchema, metadata, "Prompt converted to an editable strategy map.", "Prompt");
      });
      target.querySelector("[data-focus-builder-prompt]")?.addEventListener("click", () => {
        document.querySelector("[data-builder-prompt-part]")?.focus();
      });
      target.querySelectorAll("[data-interpretation-feedback]").forEach((button) => {
        button.addEventListener("click", () => submitInterpretationFeedback(button.dataset.interpretationFeedback, response));
      });
    }

    function applySchema(nextSchema, metadata, message, path = "Template") {
      schema = nextSchema;
      interpretationMetadata = metadata;
      creationPath = path;
      validationPassed = false;
      validationFindings = [];
      hydrateBuilderForm(schema);
      showMode("canvas", path);
      showToast(message);
    }

    function previewTemplate(templateSchema) {
      const preview = templateSchema || defaultSchema();
      const leaves = collectConditionLeaves(preview.conditions);
      const dialog = document.createElement("dialog");
      dialog.className = "template-preview-dialog";
      dialog.innerHTML = `
        <div class="modal-header">
          <div><span class="eyebrow">Template preview</span><h2>${escapeHtml(preview.name || "Blank monitor")}</h2></div>
          <button class="icon-button" type="button" aria-label="Close template preview">x</button>
        </div>
        <p>${escapeHtml(preview.description || "A guided blank strategy map.")}</p>
        <div class="template-preview-flow">
          <span>${escapeHtml(titleize(preview.universe?.exchange || "Exchange"))} spot</span><i></i>
          <span>${escapeHtml(operatorLabel(preview.conditions?.operator))}</span><i></i>
          <span>${leaves.length} condition${leaves.length === 1 ? "" : "s"}</span><i></i>
          <span>${escapeHtml(safeArray(preview.alerts?.channels).map(titleize).join(" + ") || "Choose alerts")}</span>
        </div>
        <ul>${leaves.slice(0, 5).map((condition) => `<li>${escapeHtml(conditionSentence(condition))}</li>`).join("") || "<li>Add your first condition in the canvas.</li>"}</ul>
        <div class="drawer-actions"><button class="button button-secondary" type="button" data-close-preview>Close</button></div>
      `;
      dialog.querySelectorAll("[data-close-preview], .icon-button").forEach((button) => {
        button.addEventListener("click", () => dialog.close());
      });
      dialog.addEventListener("close", () => dialog.remove());
      document.body.appendChild(dialog);
      dialog.showModal();
    }

    function markValidation(message, passed = false) {
      validationPassed = passed;
      const box = document.getElementById("builder-validation-status");
      if (box) {
        box.classList.toggle("success", passed);
        box.classList.toggle("error", !passed);
        box.innerHTML = `<strong>${passed ? "Ready" : "Needs review"}</strong><p>${escapeHtml(message)}</p>`;
      }
      renderValidationChecklist();
      updateBuilderStatus();
    }

    async function validateMonitor() {
      schema = schemaFromForm(schema);
      validationFindings = [];
      const local = warningItems();
      if (local.blocking.length) {
        markValidation(local.blocking.join(" "), false);
        showToast("Fix the critical details before activation.", "error");
        document.querySelector('[data-builder-right-tab="validation"]')?.click();
        return false;
      }
      markValidation("Checking conflicts, data requirements, plan limits, and condition shape...", false);
      try {
        const result = await api("/cockpit/strategies/validate", {
          method: "POST",
          body: JSON.stringify({
            definition: schema,
            strategy_id: form.dataset.strategyId || null,
          }),
        });
        validationFindings = safeArray(result.findings);
        const serverBlocking = Boolean(result.blocking);
        markValidation(
          serverBlocking
            ? "Critical conflicts need attention."
            : `${collectConditionLeaves(schema.conditions).length} deterministic conditions passed validation.`,
          !serverBlocking,
        );
        renderStrategyCanvas(false);
        showToast(serverBlocking ? "Critical conflicts need attention." : "Validation passed.", serverBlocking ? "error" : "success");
        return !serverBlocking;
      } catch (error) {
        markValidation(`Validation could not run: ${error.message}`, false);
        showToast(error.message, "error");
        return false;
      } finally {
        document.querySelector('[data-builder-right-tab="validation"]')?.click();
      }
    }

    async function previewMatches() {
      schema = schemaFromForm(schema);
      const target = document.getElementById("builder-preview-results");
      if (target) renderLoadingState(target, "Scanning a current market sample...");
      document.querySelector('[data-builder-right-tab="preview"]')?.click();
      try {
        const response = await api("/scan-now", {
          method: "POST",
          body: JSON.stringify({
            strategy: schema,
            symbols: schema.universe.include_symbols,
            max_symbols: 100000,
            light_scan: true,
            idempotency_key: `builder-preview-${Date.now()}`,
          }),
        });
        const results = safeArray(response.results).slice(0, 5);
        if (target) {
          target.innerHTML = `
            <div class="preview-stat-row">
              <div><span>Universe sample</span><strong>${escapeHtml(response.symbols_scanned || 0)}</strong></div>
              <div><span>Matches shown</span><strong>${results.length}</strong></div>
              <div><span>Validated</span><strong>${validationPassed ? "Yes" : "Not yet"}</strong></div>
            </div>
            ${results.length ? results.map((item) => `
              <article class="builder-match-card"><strong>${escapeHtml(item.symbol)}</strong><span>${Math.round(item.match_percentage)}%</span></article>
            `).join("") : '<div class="canvas-empty-state"><strong>No current matches.</strong><span>The monitor can still be valid; the market may not meet it now.</span></div>'}
          `;
        }
        const estimate = document.getElementById("builder-alert-estimate");
        if (estimate) estimate.textContent = results.length ? `${results.length} in sample` : "No current matches";
        showToast("Preview completed.");
      } catch (error) {
        renderErrorState(target, error, "Review the universe and data provider, then try again.");
        showToast(error.message, "error");
      }
    }

    function renderAiDiff(action) {
      const target = document.getElementById("builder-ai-diff");
      if (!target) return;
      const suggestions = {
        simpler: ["Remove one optional filter", "Keep the primary trigger unchanged"],
        stricter: ["Add volume confirmation", "Require candle-close confirmation"],
        "less-noisy": ["Increase cooldown", "Add a trend filter"],
        earlier: ["Use intrabar evaluation", "Keep confirmation conditions visible"],
        blockers: warningItems().blocking.length ? warningItems().blocking : ["No critical local blockers"],
        "market-context": ["Add a BTC trend context condition", "Review before applying"],
        volume: ["Add relative-volume confirmation", "Use the current timeframe"],
      };
      target.hidden = false;
      target.innerHTML = `
        <span class="eyebrow">Proposed diff</span>
        <div class="visual-diff">
          ${safeArray(suggestions[action]).map((item) => `<div><span>+</span><strong>${escapeHtml(item)}</strong></div>`).join("")}
        </div>
        <small>Suggestion only. No strategy fields were changed.</small>
      `;
    }

    function setBuilderActionStatus(message, type = "processing") {
      const target = document.getElementById("builder-action-status");
      if (!target) return;
      target.hidden = false;
      target.dataset.state = type;
      target.innerHTML = `
        <span>${type === "success" ? "Done" : type === "error" ? "Needs attention" : "Processing"}</span>
        <strong>${escapeHtml(message)}</strong>
      `;
    }

    function setBuilderSubmitBusy(busy, label = "Processing...") {
      form.querySelectorAll('[data-save-draft], [data-publish-schema], button[type="submit"]').forEach((button) => {
        if (busy) {
          button.dataset.previousDisabled = String(button.disabled);
          button.dataset.previousLabel = button.textContent.trim();
          button.disabled = true;
          button.textContent = label;
          return;
        }
        const wasDisabled = button.dataset.previousDisabled === "true";
        if (button.dataset.previousLabel) button.textContent = button.dataset.previousLabel;
        button.disabled = wasDisabled;
        delete button.dataset.previousDisabled;
        delete button.dataset.previousLabel;
      });
      if (!busy) updateBuilderStatus();
    }

    async function saveBuilderStrategy(shouldPublish = false, submitter = null) {
      if (builderSaving) return;
      builderSaving = true;
      const busyLabel = shouldPublish ? "Starting..." : "Saving...";
      try {
        schema = schemaFromForm(schema);
        setBuilderSubmitBusy(true, busyLabel);
        if (shouldPublish && !validationPassed) {
          setBuilderActionStatus("Validating this monitor before live activation...", "processing");
          const valid = await validateMonitor();
          if (!valid) {
            setBuilderActionStatus("Activation is blocked. Review the validation panel and fix the listed items.", "error");
            return;
          }
        }
        setBuilderActionStatus(
          shouldPublish
            ? "Starting live monitoring and locking this strategy version..."
            : "Saving this monitor as a draft version...",
          "processing",
        );
        const strategyId = form.dataset.strategyId;
        const path = strategyId ? `/strategies/${strategyId}/versions` : "/strategies";
        const response = await api(path, {
          method: "POST",
          body: JSON.stringify({
            definition: schema,
            source_text: interpretationMetadata.source_text || "dashboard strategy canvas",
            interpreter: interpretationMetadata.interpreter,
            assumptions: interpretationMetadata.assumptions,
            ambiguities: interpretationMetadata.ambiguities,
            unsupported_conditions: interpretationMetadata.unsupported_conditions,
          }),
        });
        const id = response.strategy?.id || strategyId;
        const version = response.version;
        if (id && !form.dataset.strategyId) {
          form.dataset.strategyId = id;
          window.history.replaceState({}, "", `/dashboard/strategies/${id}/builder`);
        }
        if (shouldPublish && id && version) {
          await api(`/strategies/${id}/publish`, {
            method: "POST",
            body: JSON.stringify({
              strategy_version_id: version.id,
              expected_schema_hash: version.schema_hash,
            }),
          });
          setBuilderActionStatus("Monitor is live. Redirecting to My Monitors...", "success");
          showToast("Monitor published and marked active.");
          window.location.href = `/dashboard/monitors?message=monitor_published&t=${Date.now()}`;
          return;
        }
        setBuilderActionStatus("Draft saved successfully. You can keep editing, validate, or start monitoring.", "success");
        showToast("Draft strategy map saved.");
        if (submitter?.dataset.saveDraft !== undefined) {
          document.querySelector('[data-builder-right-tab="summary"]')?.click();
        }
      } catch (error) {
        setBuilderActionStatus(error.message, "error");
        showToast(error.message, "error");
      } finally {
        builderSaving = false;
        setBuilderSubmitBusy(false);
      }
    }

    builderUiController = {
      openConditionDrawer,
      openGroupDrawer,
      openConditionLibrary,
      duplicateNode,
      removeNode,
      addGroupToGroup,
      isAiInterpreted: () => creationPath === "Prompt",
    };

    renderConditionLibrary();
    renderStrategyCanvas(true);
    showMode(form.dataset.strategyId ? "canvas" : "choose", form.dataset.strategyId ? "Saved" : "Visual");

    document.querySelectorAll("[data-builder-direction]").forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.builderDirection;
        if (mode === "canvas") {
          showMode("canvas", "Visual");
          return;
        }
        showMode(mode);
      });
    });
    document.querySelectorAll("[data-clarification-template]").forEach((button) => {
      button.addEventListener("click", () => {
        const prompt = document.querySelector('[data-builder-prompt-part="extra"]') || field(form, "builder_prompt");
        prompt.value = `${prompt.value.trim()} ${button.dataset.clarificationTemplate}`.trim();
        prompt.focus?.();
      });
    });
    document.querySelectorAll("[data-prompt-example-chip]").forEach((button) => {
      button.addEventListener("click", () => {
        const goal = document.querySelector('[data-builder-prompt-part="goal"]') || field(form, "builder_prompt");
        goal.value = `${goal.value.trim()} ${button.dataset.promptExampleChip}`.trim();
        goal.focus?.();
      });
    });
    document.querySelector("[data-improve-builder-prompt]")?.addEventListener("click", () => {
      const optional = document.querySelector('[data-builder-prompt-part="optional"]');
      const avoid = document.querySelector('[data-builder-prompt-part="avoid"]');
      const alerts = document.querySelector('[data-builder-prompt-part="alerts"]');
      if (optional && !optional.value.trim()) optional.value = "Volume confirmation is optional unless specified.";
      if (avoid && !avoid.value.trim()) avoid.value = "Avoid low-liquidity pairs and unsupported discretionary wording.";
      if (alerts && !alerts.value.trim()) alerts.value = "Candle close only, dashboard alerts, default cooldown.";
      showToast("Prompt helper added missing optional, avoid, and alert sections. Review before generating.");
    });
    document.querySelector("[data-check-builder-meaning]")?.addEventListener("click", () => {
      translateButton?.click();
    });
    document.querySelectorAll("[data-builder-nav]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll("[data-builder-nav]").forEach((item) => {
          item.classList.toggle("active", item.dataset.builderNav === button.dataset.builderNav);
        });
        document.getElementById(button.dataset.builderNav)?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    document.querySelectorAll("[data-builder-right-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        const tab = button.dataset.builderRightTab;
        document.querySelectorAll("[data-builder-right-tab]").forEach((item) => {
          const active = item === button;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", String(active));
        });
        document.querySelectorAll("[data-builder-right-panel]").forEach((panel) => {
          const active = panel.dataset.builderRightPanel === tab;
          panel.hidden = !active;
          panel.classList.toggle("active", active);
        });
      });
    });
    document.querySelectorAll("[data-open-section-editor]").forEach((button) => {
      button.addEventListener("click", () => openSectionEditor(button.dataset.openSectionEditor));
    });
    document.querySelector("[data-toggle-builder-panel]")?.addEventListener("click", (event) => {
      const panel = event.currentTarget.closest(".builder-right-panel");
      const collapsed = panel?.classList.toggle("collapsed");
      event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
    });
    document.querySelectorAll("[data-open-condition-library]").forEach((button) => {
      button.addEventListener("click", () => openConditionLibrary(null, button.dataset.libraryQuery || ""));
    });
    document.querySelectorAll("[data-close-condition-library]").forEach((button) => {
      button.addEventListener("click", () => libraryModal?.close());
    });
    libraryModal?.addEventListener("close", () => {
      if (!reopenBoardAfterLibrary) return;
      reopenBoardAfterLibrary = false;
      window.setTimeout(openStrategyBoard, 80);
    });
    document.querySelector("[data-capability-search]")?.addEventListener("input", renderConditionLibrary);
    document.querySelectorAll("[data-close-builder-drawer]").forEach((button) => {
      button.addEventListener("click", closeBuilderDrawer);
    });
    document.querySelector("[data-save-builder-drawer]")?.addEventListener("click", () => {
      drawerSaveHandler?.();
      closeBuilderDrawer();
      showToast("Strategy map updated.");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drawer && !drawer.hidden) closeBuilderDrawer();
      if (event.key === "Tab" && drawer && !drawer.hidden) {
        const focusable = Array.from(drawer.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'));
        if (focusable.length) {
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }
      if (
        event.key === "/" &&
        drawer?.hidden !== false &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
      ) {
        event.preventDefault();
        openConditionLibrary();
      }
    });
    document.querySelector("[data-builder-add-group]")?.addEventListener("click", () => {
      addNodeToGroup(createGroup(safeArray(schema.conditions.children).length, "and"));
    });
    document.querySelector("[data-builder-add-raw-condition]")?.addEventListener("click", () => {
      const condition = createCondition(safeArray(schema.conditions.children).length);
      if (!addNodeToGroup(condition)) return;
      openConditionDrawer(condition, () => renderStrategyCanvas(true));
    });
    document.querySelectorAll("[data-builder-add-filter]").forEach((button) => {
      button.addEventListener("click", () => addNodeToGroup(createPresetCondition("filter", safeArray(schema.conditions.children).length)));
    });
    document.querySelectorAll("[data-builder-add-market-context]").forEach((button) => {
      button.addEventListener("click", () => addNodeToGroup(createPresetCondition("market_context", safeArray(schema.conditions.children).length)));
    });
    document.querySelectorAll("[data-builder-add-time-rule]").forEach((button) => {
      button.addEventListener("click", () => addNodeToGroup(createPresetCondition("time_rule", safeArray(schema.conditions.children).length)));
    });
    document.querySelector("[data-quick-universe]")?.addEventListener("click", () => {
      setHiddenField("exchange", "binance");
      setHiddenField("quote", "USDT");
      setHiddenField("include_symbols", "");
      setHiddenField("min_quote_volume_24h", "1000000");
      validationPassed = false;
      renderStrategyCanvas(true);
    });
    document.querySelector("[data-builder-blank-template]")?.addEventListener("click", () => {
      const blank = defaultSchema();
      blank.name = "Untitled Monitor";
      blank.description = "Build a deterministic market monitor.";
      blank.conditions.children = [];
      applySchema(blank, {
        interpreter: "dashboard-blank-template",
        assumptions: [],
        ambiguities: [],
        unsupported_conditions: [],
        source_text: null,
      }, "Blank canvas loaded. Add the first condition.", "Visual");
    });
    document.querySelector("[data-preview-blank-template]")?.addEventListener("click", () => {
      const blank = defaultSchema();
      blank.name = "Custom Blank Template";
      blank.conditions.children = [];
      previewTemplate(blank);
    });
    document.querySelectorAll("[data-preview-template]").forEach((button) => {
      button.addEventListener("click", () => previewTemplate(safeJson(button.dataset.previewTemplate || "{}", defaultSchema())));
    });
    document.querySelectorAll("[data-template-schema]").forEach((button) => {
      button.addEventListener("click", () => applySchema(
        safeJson(button.dataset.templateSchema || "{}", defaultSchema()),
        {
          interpreter: "dashboard-template",
          assumptions: [],
          ambiguities: [],
          unsupported_conditions: [],
          source_text: null,
        },
        "Template loaded. Review the map before monitoring.",
        "Template",
      ));
    });
    function updateTemplateFilter() {
      const selectedCategories = Array.from(selectedTemplateCategories);
      let visible = 0;
      document.querySelectorAll("[data-template-category-card]").forEach((card) => {
        const categories = String(card.dataset.templateCategories || card.dataset.templateCategoryCard || "")
          .split("|")
          .map((item) => item.trim())
          .filter(Boolean);
        const matches = !selectedCategories.length ||
          selectedCategories.some((category) => categories.includes(category));
        card.hidden = !matches;
        if (matches) visible += 1;
      });
      const empty = document.querySelector("[data-template-empty]");
      if (empty) empty.hidden = visible > 0;
    }
    document.querySelectorAll("[data-template-category]").forEach((button) => {
      button.addEventListener("click", () => {
        const isAll = button.dataset.templateCategory === "All";
        if (isAll) {
          selectedTemplateCategories.clear();
        } else if (selectedTemplateCategories.has(button.dataset.templateCategory)) {
          selectedTemplateCategories.delete(button.dataset.templateCategory);
        } else {
          selectedTemplateCategories.add(button.dataset.templateCategory);
        }
        document.querySelectorAll("[data-template-category]").forEach((item) => {
          const active = item.dataset.templateCategory === "All"
            ? selectedTemplateCategories.size === 0
            : selectedTemplateCategories.has(item.dataset.templateCategory);
          item.classList.toggle("active", active);
          item.setAttribute("aria-pressed", String(active));
        });
        updateTemplateFilter();
      });
    });
    updateTemplateFilter();

    document.querySelectorAll("[data-open-strategy-board]").forEach((button) => {
      button.addEventListener("click", openStrategyBoard);
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-board-node-menu], [data-board-node-menu-panel]")) return;
      document.querySelectorAll("[data-board-node-menu-panel]").forEach((menu) => {
        menu.hidden = true;
      });
    });
    document.querySelectorAll("[data-close-strategy-board]").forEach((button) => {
      button.addEventListener("click", () => boardDialog?.close());
    });
    document.querySelector("[data-board-maximize]")?.addEventListener("click", () => {
      boardDialog?.classList.toggle("maximized");
      setBoardTransform();
    });
    document.querySelectorAll("[data-board-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        activateBoard(button.closest("[data-board-root]") || boardRoot);
        renderBoardInspector(button.dataset.boardTab);
      });
    });
    document.querySelectorAll("[data-board-add]").forEach((button) => {
      button.addEventListener("click", () => {
        activateBoard(button.closest("[data-board-root]") || boardRoot);
        const action = button.dataset.boardAdd;
        const reopenModalBoard = activeBoardIsModal();
        reopenBoardAfterDrawer = reopenModalBoard;
        reopenBoardAfterLibrary = reopenModalBoard;
        if (action === "condition") {
          closeActiveModalBoard();
          openConditionLibrary(schema.conditions);
          return;
        }
        if (action === "filter") {
          closeActiveModalBoard();
          openConditionLibrary(schema.conditions, "filter");
          return;
        }
        if (action === "universe" || action === "alerts") {
          closeActiveModalBoard();
          openSectionEditor(action);
        }
      });
    });
    document.querySelectorAll("[data-board-zoom]").forEach((button) => {
      button.addEventListener("click", () => {
        activateBoard(button.closest("[data-board-root]") || boardRoot);
        const direction = button.dataset.boardZoom === "in" ? 1 : -1;
        boardScale = Math.min(1.8, Math.max(0.55, boardScale + direction * 0.1));
        setBoardTransform();
      });
    });
    document.querySelectorAll("[data-board-reset]").forEach((button) => {
      button.addEventListener("click", () => {
        activateBoard(button.closest("[data-board-root]") || boardRoot);
        boardScale = 1;
        boardPan = { x: 0, y: 0 };
        setBoardTransform();
      });
    });
    function bindBoardSurface(surface) {
      if (!surface || surface.dataset.boardBound === "true") return;
      surface.dataset.boardBound = "true";
      surface.addEventListener("wheel", (event) => {
        activateBoard(surface.closest("[data-board-root]") || boardRoot);
        event.preventDefault();
        const delta = event.deltaY > 0 ? -0.08 : 0.08;
        boardScale = Math.min(1.8, Math.max(0.55, boardScale + delta));
        setBoardTransform();
      }, { passive: false });
      surface.addEventListener("pointerdown", (event) => {
        activateBoard(surface.closest("[data-board-root]") || boardRoot);
        if (event.target.closest("[data-board-node]")) return;
        surface.setPointerCapture(event.pointerId);
        boardDrag = {
          type: "pan",
          startX: event.clientX,
          startY: event.clientY,
          baseX: boardPan.x,
          baseY: boardPan.y,
        };
      });
      surface.addEventListener("pointermove", (event) => {
        activateBoard(surface.closest("[data-board-root]") || boardRoot);
        if (!boardDrag) return;
        if (boardDrag.type === "pan") {
          boardPan = {
            x: boardDrag.baseX + event.clientX - boardDrag.startX,
            y: boardDrag.baseY + event.clientY - boardDrag.startY,
          };
          setBoardTransform();
          return;
        }
        if (boardDrag.type === "node") {
          const next = {
            x: Math.max(0, boardDrag.baseX + (event.clientX - boardDrag.startX) / boardScale),
            y: Math.max(0, boardDrag.baseY + (event.clientY - boardDrag.startY) / boardScale),
          };
          boardPositions[boardDrag.id] = next;
          const node = boardContent?.querySelector(`[data-board-node="${CSS.escape(boardDrag.id)}"]`);
          if (node) {
            node.style.left = `${next.x}px`;
            node.style.top = `${next.y}px`;
          }
          updateBoardArrowPaths();
        }
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach((eventName) => {
        surface.addEventListener(eventName, () => {
          activateBoard(surface.closest("[data-board-root]") || boardRoot);
          if (boardDrag?.type === "node") {
            const snapped = snapBoardPosition(boardPositions[boardDrag.id] || { x: 0, y: 0 }, boardDrag.id);
            boardPositions[boardDrag.id] = snapped;
            const node = boardContent?.querySelector(`[data-board-node="${CSS.escape(boardDrag.id)}"]`);
            if (node) {
              node.classList.remove("is-dragging");
              node.style.left = `${snapped.x}px`;
              node.style.top = `${snapped.y}px`;
            }
            saveBoardPositions();
            renderStrategyBoard();
          }
          boardDrag = null;
        });
      });
    }
    document.querySelectorAll("[data-board-surface]").forEach(bindBoardSurface);
    boardDialog?.addEventListener("close", () => {
      activateBoard(embeddedBoardRoot || boardDialog);
      renderStrategyBoard();
    });

    function buildStructuredPrompt(prefix) {
      const selector = prefix === "builder" ? "[data-builder-prompt-part]" : "[data-scan-prompt-part]";
      const labels = {
        goal: "Find",
        must: "Must include",
        optional: "Optional confirmations",
        universe: "Universe",
        timeframe: "Timeframe",
        alerts: "Alert and risk preferences",
        avoid: "Things to avoid",
        notes: "Pasted notes",
        filters: "Optional filters",
        extra: "Extra instructions",
      };
      return Array.from(document.querySelectorAll(selector))
        .map((input) => {
          const key = prefix === "builder" ? input.dataset.builderPromptPart : input.dataset.scanPromptPart;
          const value = input.value.trim();
          if (!value) return "";
          return `${labels[key] || "Detail"}: ${value}`;
        })
        .filter(Boolean)
        .join("\n");
    }

    function buildStructuredPromptParts(prefix) {
      const selector = prefix === "builder" ? "[data-builder-prompt-part]" : "[data-scan-prompt-part]";
      return Object.fromEntries(
        Array.from(document.querySelectorAll(selector))
          .map((input) => {
            const key = prefix === "builder" ? input.dataset.builderPromptPart : input.dataset.scanPromptPart;
            const value = input.value.trim();
            return value ? [key, value] : null;
          })
          .filter(Boolean),
      );
    }

    const translateButton = document.querySelector("[data-interpret-builder-prompt]");
    const aiSummary = document.getElementById("builder-ai-summary");
    translateButton?.addEventListener("click", async () => {
      const prompt = buildStructuredPrompt("builder");
      field(form, "builder_prompt").value = prompt;
      if (!prompt) {
        showToast("Describe the monitor before generating a preview.", "error");
        return;
      }
      renderLoadingState(aiSummary, "Processing your strategy into deterministic mechanics...");
      translatedSchema = null;
      try {
        schema = schemaFromForm(schema);
        const response = await api("/strategies/interpret", {
          method: "POST",
          body: JSON.stringify({
            raw_prompt: prompt,
            prompt_parts: buildStructuredPromptParts("builder"),
            current_schema: schema,
            exchange: field(form, "exchange").value || "binance",
            quote_currency: csv(field(form, "quote").value || "USDT")[0] || "USDT",
            timeframe: field(form, "base_timeframe").value || "15m",
            trigger_mode: field(form, "trigger_mode").value || "candle_close",
            symbols: csv(field(form, "include_symbols").value),
            builder_mode: creationPath || "prompt",
          }),
        });
        translatedSchema = response.strategy;
        const metadata = {
          interpreter: response.interpreter || "dashboard-builder-v3-canvas",
          assumptions: safeArray(response.assumptions),
          ambiguities: safeArray(response.ambiguities),
          unsupported_conditions: safeArray(response.unsupported_conditions),
          source_text: prompt,
          prompt_coverage_report: response.prompt_coverage_report || null,
          coverage_score: response.coverage_score ?? response.prompt_coverage_report?.coverage_score ?? null,
          confidence_score: response.confidence_score ?? response.prompt_coverage_report?.confidence_score ?? null,
          mapping_table: safeArray(response.mapping_table || response.prompt_coverage_report?.mapping_table),
          visual_diff: response.visual_diff || null,
        };
        interpretationMetadata = metadata;
        renderPromptUnderstandingPreview(response, metadata);
        showToast(response.activation_blocked ? "Clarification is required." : "Understanding preview ready.", response.activation_blocked ? "error" : "success");
      } catch (error) {
        renderErrorState(aiSummary, error, "Check the AI API configuration or clarify the prompt.");
        showToast(error.message, "error");
      }
    });
    document.querySelectorAll("[data-builder-validate]").forEach((button) => button.addEventListener("click", validateMonitor));
    document.querySelectorAll("[data-builder-preview-matches]").forEach((button) => button.addEventListener("click", previewMatches));
    document.querySelectorAll("[data-builder-ai-help]").forEach((button) => {
      button.addEventListener("click", () => renderAiDiff(button.dataset.builderAiHelp));
    });
    document.querySelectorAll("[data-copy-schema]").forEach((button) => {
      button.addEventListener("click", async () => {
        schema = schemaFromForm(schema);
        await navigator.clipboard.writeText(JSON.stringify(schema, null, 2));
        showToast("Strategy schema copied.");
      });
    });
    document.querySelector("[data-save-template]")?.addEventListener("click", async () => {
      try {
        schema = schemaFromForm(schema);
        const response = await api("/templates", {
          method: "POST",
          body: JSON.stringify({
            name: `${schema.name} Template`,
            category: "custom",
            tags: ["strategy-canvas"],
            definition: schema,
            source_strategy_id: form.dataset.strategyId || null,
          }),
        });
        showToast(`Template saved: ${response.template.name}`);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
    document.querySelectorAll("[data-save-draft]").forEach((button) => {
      button.addEventListener("click", () => saveBuilderStrategy(false, button));
    });
    document.querySelectorAll("[data-publish-schema]").forEach((button) => {
      button.addEventListener("click", () => saveBuilderStrategy(true, button));
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await saveBuilderStrategy(Boolean(event.submitter?.dataset.publishSchema), event.submitter);
    });
  }

  function initScanNow() {
    const form = document.getElementById("scan-now-form");
    if (!form) return;
    const mode = field(form, "scan_mode");
    const strategyFields = document.getElementById("scan-strategy-fields");
    const promptFields = document.getElementById("scan-prompt-fields");
    const interpretationBox = document.getElementById("scan-interpretation");
    const interpretButton = document.querySelector("[data-interpret-scan-prompt]");
    let interpretedStrategy = null;
    let approvedSchemaHash = null;

    function buildScanPrompt() {
      const labels = {
        goal: "Find",
        must: "Must include",
        filters: "Optional filters",
        extra: "Extra instructions",
      };
      const prompt = Array.from(document.querySelectorAll("[data-scan-prompt-part]"))
        .map((input) => {
          const value = input.value.trim();
          if (!value) return "";
          return `${labels[input.dataset.scanPromptPart] || "Detail"}: ${value}`;
        })
        .filter(Boolean)
        .join("\n");
      field(form, "prompt").value = prompt;
      return prompt;
    }

    function toggleMode() {
      const promptMode = mode.value === "prompt";
      if (strategyFields) strategyFields.hidden = promptMode;
      if (promptFields) promptFields.hidden = !promptMode;
    }

    function renderScanProgress(element, message = "Scanning the selected universe...") {
      if (!element) return;
      const symbols = csv(field(form, "symbols").value);
      const scope = symbols.length
        ? `${symbols.length} selected symbol${symbols.length === 1 ? "" : "s"}`
        : "the full plan-allowed universe";
      element.hidden = false;
      element.classList.remove("error", "success");
      element.innerHTML = `
        <div class="scan-progress-shell">
          <div class="scan-progress-orb" aria-hidden="true"></div>
          <div>
            <span class="eyebrow">Quick Scan running</span>
            <strong>${escapeHtml(message)}</strong>
            <p>Scope: ${escapeHtml(scope)}. Results will show requested, scanned, returned, and displayed counts separately.</p>
          </div>
          <div class="scan-progress-track" aria-hidden="true"><i></i></div>
          <div class="scan-progress-steps" aria-label="Scan progress steps">
            <span>Resolve universe</span>
            <span>Fetch candles</span>
            <span>Evaluate rules</span>
            <span>Rank matches</span>
          </div>
        </div>
      `;
      form.closest(".dash-grid")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

  function renderInterpretation(payload) {
      const understanding = payload.understanding || {};
      const conditions = understanding.entry_conditions || [];
      const requiredRules = safeArray(payload.required_rules).map((item) => item.name);
      const optionalRules = safeArray(payload.optional_rules).map((item) => item.name);
      const ignoredOptional = safeArray(payload.ignored_optional_rules).map((item) => item.message);
      const blockingUnsupported = safeArray(payload.blocking_unsupported_rules).map((item) => item.message);
      const warnings = safeArray(payload.warnings);
      const riskSummary = understanding.risk?.enabled
        ? `Risk: max stop ${escapeHtml(understanding.risk.maximum_stop_percent)}%, minimum R:R ${escapeHtml(understanding.risk.minimum_reward_to_risk)}`
        : "Risk: no stop or R:R filter requested for this scan.";
      const issues = [
        ...(payload.ambiguities || []).map((item) => `Ambiguous: ${item.message}`),
        ...blockingUnsupported.map((item) => `Unsupported: ${item}`),
      ];
      if (!interpretationBox) return;
      interpretationBox.hidden = false;
      interpretationBox.innerHTML = `
        <strong>How the system understood this trigger</strong>
        <p>${escapeHtml(understanding.direction)} on ${escapeHtml(understanding.exchange)}
        ${escapeHtml(understanding.market_type)} - ${escapeHtml(understanding.pair_universe)}</p>
        <p>Timeframes: ${escapeHtml((understanding.timeframes || []).join(", "))} -
        trigger: ${escapeHtml(understanding.trigger_mode)}</p>
        <p>Conditions:</p>
        <ul>${conditions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        <p>Required rules:</p>
        <ul>${requiredRules.map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None</li>"}</ul>
        <p>Optional rules:</p>
        <ul>${optionalRules.map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>None</li>"}</ul>
        <p>${riskSummary}</p>
        <p>Safety: ${escapeHtml(payload.scan_safety_level || "strict")} -
        light scan compatible: ${payload.light_mode_compatible ? "yes" : "no"}</p>
        ${
          ignoredOptional.length
            ? `<p>Ignored optional ideas:</p><ul>${ignoredOptional.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
            : ""
        }
        ${
          warnings.length
            ? `<p>Warnings:</p><ul>${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
            : ""
        }
        ${
          issues.length
            ? `<p class="dash-flash error">Needs clarification: ${(blockingUnsupported.length ? blockingUnsupported : issues).map(escapeHtml).join("; ")}</p>`
            : '<p class="dash-flash success">Ready to scan after your confirmation.</p>'
        }
      `;
    }

    function renderScanResults(element, response) {
      if (!element) return;
      const scan = response.scan || response;
      const allResults = safeArray(scan.results);
      const displayLimit = 250;
      const results = allResults.slice(0, displayLimit);
      const interpretation = response.interpretation;
      const warnings = safeArray(response.warnings || scan.warnings);
      element.hidden = false;
      element.classList.remove("error");
      if (!results.length) {
        element.innerHTML = `
          <strong>No matching symbols found.</strong>
          <p>Requested ${escapeHtml(scan.symbols_requested ?? 0)} market(s) and scanned ${escapeHtml(scan.symbols_scanned ?? 0)}. Try a broader condition, a larger universe, or a higher timeframe.</p>
          ${warnings.length ? `<p>Warnings: ${warnings.map(escapeHtml).join("; ")}</p>` : ""}
        `;
        return;
      }
      const rows = results
        .map((item, index) => {
          const passed = safeArray(item.passed_conditions)
            .slice(0, 3)
            .map((condition) => condition.name)
            .join(", ") || "none";
          const missing = safeArray(item.missing_conditions)
            .slice(0, 3)
            .map((condition) => condition.name)
            .join(", ") || "none";
          return `
            <div class="scan-result-card">
              <div>
                <strong>${index + 1}. ${escapeHtml(item.symbol)}</strong>
                <small>${escapeHtml(item.exchange)} - ${escapeHtml(item.timeframe)} - ${escapeHtml(item.outcome)}</small>
              </div>
              <span>${safeNumber(item.match_percentage, 100).toFixed(0)}%</span>
              <p>Passed: ${escapeHtml(passed)}</p>
              <p>Missing: ${escapeHtml(missing)}</p>
            </div>
          `;
        })
        .join("");
      element.innerHTML = `
        <strong>${allResults.length} matching symbol${allResults.length === 1 ? "" : "s"} returned</strong>
        <p>Requested ${escapeHtml(scan.symbols_requested ?? 0)} market(s), scanned ${escapeHtml(scan.symbols_scanned ?? 0)}, and displayed ${results.length}${allResults.length > results.length ? ` of ${allResults.length}` : ""}. Status: ${escapeHtml(scan.status || "succeeded")}. Quota remaining today: ${escapeHtml(scan.quota_remaining ?? "n/a")}.</p>
        ${
          interpretation
            ? `<p>Understood trigger: ${escapeHtml(interpretation.understanding?.name || "custom scan")} on ${escapeHtml(interpretation.understanding?.pair_universe || "selected universe")}.</p>`
            : ""
        }
        ${warnings.length ? `<p>Warnings: ${warnings.map(escapeHtml).join("; ")}</p>` : ""}
        <div class="scan-result-list">${rows}</div>
      `;
    }

    mode?.addEventListener("change", toggleMode);
    toggleMode();

    interpretButton?.addEventListener("click", async () => {
      if (interpretationBox) {
        renderLoadingState(interpretationBox, "Processing trigger interpretation...");
      }
      interpretedStrategy = null;
      approvedSchemaHash = null;
      try {
        const prompt = buildScanPrompt();
        const payload = {
          prompt,
          exchange: field(form, "prompt_exchange").value || "binance",
          quote_currency: field(form, "prompt_quote_currency").value || "USDT",
          timeframe: field(form, "prompt_timeframe").value || "15m",
          trigger_mode: field(form, "prompt_trigger_mode").value || "candle_close",
          symbols: csv(field(form, "symbols").value),
        };
        const response = await api("/scan-now/interpret", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        interpretedStrategy = response.activation_blocked ? null : response.strategy;
        approvedSchemaHash = response.activation_blocked ? null : response.approved_schema_hash;
        renderInterpretation(response);
        if (response.activation_blocked) {
          showToast("Clarify the trigger before scanning.", "error");
        } else {
          showToast("Trigger interpreted. Review it, then run scan.");
        }
      } catch (error) {
        renderErrorState(interpretationBox, error, "Clarify the prompt and try again.");
        showToast(error.message, "error");
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("scan-result");
      renderScanProgress(result, "Scanning live market data now...");
      try {
        const symbols = csv(field(form, "symbols").value);
        let response;
        if (mode.value === "prompt") {
          if (!interpretedStrategy || !approvedSchemaHash) {
            throw new Error("Interpret and review the trigger before running this scan.");
          }
          const prompt = buildScanPrompt();
          const payload = {
            prompt,
            exchange: field(form, "prompt_exchange").value || "binance",
            quote_currency: field(form, "prompt_quote_currency").value || "USDT",
            timeframe: field(form, "prompt_timeframe").value || "15m",
            trigger_mode: field(form, "prompt_trigger_mode").value || "candle_close",
            symbols,
            max_results: 250,
          };
          response = await api("/light-scan", {
            method: "POST",
            body: JSON.stringify(payload),
          });
        } else {
          const payload = {
            strategy_version_id: field(form, "strategy_version_id").value,
            symbols,
          };
          response = await api("/scan-now", {
            method: "POST",
            body: JSON.stringify(payload),
          });
        }
        renderScanResults(result, response);
        showToast("Scan completed.");
      } catch (error) {
        renderErrorState(result, error, "Adjust the prompt, symbols or timeframe and run again.");
        showToast(error.message, "error");
      }
    });
  }

  function initBacktests() {
    const form = document.getElementById("backtest-form");
    const chartHost = document.getElementById("backtest-trading-chart");
    const annotationLayer = document.getElementById("backtest-annotation-layer");
    const toolbar = document.getElementById("backtest-chart-toolbar");
    const timeframeSelect = document.getElementById("backtest-chart-timeframe");
    const chartStatus = document.getElementById("backtest-chart-status");
    const colors = chartTheme();
    const workspace = {
      jobId: null,
      timeframe: null,
      chart: null,
      series: null,
      annotations: [],
      dirty: false,
      tool: "pointer",
      firstPoint: null,
      resizeObserver: null,
      rendering: false,
    };

    function setChartStatus(message, dirty = false) {
      if (!chartStatus) return;
      chartStatus.textContent = message;
      chartStatus.classList.toggle("dirty", dirty);
    }

    function destroyBacktestChart() {
      workspace.resizeObserver?.disconnect();
      workspace.resizeObserver = null;
      workspace.chart?.remove();
      workspace.chart = null;
      workspace.series = null;
      chartHost?.replaceChildren();
      annotationLayer?.replaceChildren();
    }

    function svgElement(name, attributes) {
      const element = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attributes).forEach(([key, value]) => {
        element.setAttribute(key, String(value));
      });
      return element;
    }

    function renderBacktestAnnotations() {
      if (
        workspace.rendering ||
        !annotationLayer ||
        !workspace.chart ||
        !workspace.series
      ) return;
      workspace.rendering = true;
      window.requestAnimationFrame(() => {
        const width = annotationLayer.clientWidth;
        const height = annotationLayer.clientHeight;
        annotationLayer.setAttribute("viewBox", `0 0 ${width} ${height}`);
        annotationLayer.replaceChildren();
        workspace.annotations.forEach((annotation) => {
          const y1 = workspace.series.priceToCoordinate(Number(annotation.price1));
          if (!Number.isFinite(y1)) return;
          if (annotation.type === "horizontal") {
            annotationLayer.appendChild(svgElement("line", {
              x1: 0,
              y1,
              x2: width,
              y2: y1,
              stroke: annotation.color || colors.entry,
              "stroke-width": 2,
              "stroke-dasharray": "7 5",
            }));
            return;
          }
          const x1 = workspace.chart.timeScale().timeToCoordinate(Number(annotation.time1));
          if (!Number.isFinite(x1)) return;
          if (annotation.type === "line") {
            const x2 = workspace.chart.timeScale().timeToCoordinate(Number(annotation.time2));
            const y2 = workspace.series.priceToCoordinate(Number(annotation.price2));
            if (!Number.isFinite(x2) || !Number.isFinite(y2)) return;
            annotationLayer.appendChild(svgElement("line", {
              x1,
              y1,
              x2,
              y2,
              stroke: annotation.color || colors.entry,
              "stroke-width": 2,
            }));
            return;
          }
          const text = svgElement("text", {
            x: Math.min(width - 10, x1 + 7),
            y: Math.max(16, y1 - 8),
            fill: annotation.color || colors.entry,
          });
          text.textContent = annotation.text || "Note";
          annotationLayer.appendChild(text);
        });
        workspace.rendering = false;
      });
    }

    function annotationTimestamp(value) {
      if (typeof value === "number") return Math.floor(value);
      if (value && typeof value === "object" && "year" in value) {
        return Math.floor(Date.UTC(value.year, value.month - 1, value.day) / 1000);
      }
      return null;
    }

    function chartPoint(event) {
      if (!annotationLayer || !workspace.chart || !workspace.series) return null;
      const rect = annotationLayer.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const time = annotationTimestamp(workspace.chart.timeScale().coordinateToTime(x));
      const price = workspace.series.coordinateToPrice(y);
      return time !== null && Number.isFinite(price) ? { time, price: Number(price) } : null;
    }

    function annotationId() {
      return window.crypto?.randomUUID?.() || `annotation-${Date.now()}-${Math.random()}`;
    }

    function setBacktestTool(tool) {
      workspace.tool = tool;
      workspace.firstPoint = null;
      document.querySelectorAll("[data-backtest-tool]").forEach((button) => {
        button.classList.toggle("active", button.dataset.backtestTool === tool);
      });
      annotationLayer?.classList.toggle("drawing", tool !== "pointer");
      setChartStatus(
        tool === "pointer"
          ? "Pan and zoom the chart, or choose a drawing tool."
          : tool === "line"
            ? "Click two chart points to draw a trend line."
            : `Click the chart to place a ${tool === "text" ? "short note" : "horizontal level"}.`,
        workspace.dirty,
      );
    }

    function addBacktestAnnotation(point) {
      if (workspace.tool === "line" && !workspace.firstPoint) {
        workspace.firstPoint = point;
        setChartStatus("First point placed. Click the second point.", workspace.dirty);
        return;
      }
      let annotation;
      if (workspace.tool === "line") {
        annotation = {
          id: annotationId(),
          type: "line",
          time1: workspace.firstPoint.time,
          price1: workspace.firstPoint.price,
          time2: point.time,
          price2: point.price,
          color: colors.entry,
        };
      } else if (workspace.tool === "horizontal") {
        annotation = {
          id: annotationId(),
          type: "horizontal",
          price1: point.price,
          color: colors.entry,
        };
      } else if (workspace.tool === "text") {
        const requested = window.prompt("Short chart note (1 to 5 words):", "");
        const text = String(requested || "").trim().split(/\s+/).slice(0, 5).join(" ");
        if (!text) return;
        annotation = {
          id: annotationId(),
          type: "text",
          time1: point.time,
          price1: point.price,
          text,
          color: colors.entry,
        };
      }
      if (!annotation) return;
      workspace.annotations.push(annotation);
      workspace.firstPoint = null;
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("Unsaved drawing changes.", true);
    }

    async function saveBacktestAnnotations() {
      if (!workspace.jobId || !workspace.timeframe) return;
      setChartStatus("Saving drawings...");
      try {
        await api(`/charts/backtest/${workspace.jobId}/annotations`, {
          method: "PUT",
          body: JSON.stringify({
            timeframe: workspace.timeframe,
            annotations: workspace.annotations,
          }),
        });
        workspace.dirty = false;
        setChartStatus("Drawings saved for this symbol and timeframe.");
      } catch (error) {
        setChartStatus(`Could not save drawings: ${error.message}`, true);
        showToast(error.message, "error");
      }
    }

    async function loadBacktest(jobId, timeframe = null) {
      const result = document.getElementById("backtest-result");
      renderLoadingState(result, "Loading historical replay...");
      try {
        if (workspace.dirty) await saveBacktestAnnotations();
        const suffix = timeframe ? `?timeframe=${encodeURIComponent(timeframe)}` : "";
        const payload = await api(`/charts/backtest/${jobId}${suffix}`);
        destroyBacktestChart();
        workspace.jobId = jobId;
        workspace.timeframe = payload.chart.timeframe;
        workspace.annotations = safeArray(payload.annotations);
        workspace.dirty = false;
        workspace.firstPoint = null;
        if (toolbar) toolbar.hidden = false;
        if (timeframeSelect) {
          timeframeSelect.innerHTML = safeArray(payload.timeframes)
            .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
            .join("");
          timeframeSelect.value = workspace.timeframe;
        }
        if (!window.LightweightCharts || !chartHost || !payload.chart?.candles?.length) {
          throw new Error("Interactive replay chart data is unavailable");
        }
        workspace.chart = window.LightweightCharts.createChart(chartHost, {
          width: chartHost.clientWidth || 720,
          height: chartHost.clientHeight || 520,
          layout: { background: { color: colors.background }, textColor: colors.text },
          grid: {
            vertLines: { color: colors.grid },
            horzLines: { color: colors.grid },
          },
          timeScale: {
            timeVisible: workspace.timeframe !== "1d",
            secondsVisible: false,
            borderColor: colors.grid,
          },
          rightPriceScale: { borderColor: colors.grid },
          crosshair: { mode: 1 },
        });
        workspace.series = workspace.chart.addCandlestickSeries({
          upColor: colors.up,
          downColor: colors.down,
          borderVisible: false,
          wickUpColor: colors.up,
          wickDownColor: colors.down,
        });
        workspace.series.setData(payload.chart.candles.map((candle) => ({
          time: candleTime(candle.timestamp),
          open: safeNumber(candle.open),
          high: safeNumber(candle.high),
          low: safeNumber(candle.low),
          close: safeNumber(candle.close),
        })));
        overlayPrices(payload.chart.overlays).forEach((line) => {
          workspace.series.createPriceLine({
            price: line.value,
            color: line.color,
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: line.label,
          });
        });
        workspace.series.setMarkers(safeArray(payload.chart.markers).map((marker) => ({
          time: candleTime(marker.time),
          position: marker.position || "belowBar",
          color: marker.kind === "condition" ? colors.entry : colors.up,
          shape: marker.shape || "circle",
          text: marker.text,
        })));
        workspace.chart.timeScale().fitContent();
        workspace.chart.timeScale().subscribeVisibleLogicalRangeChange(
          renderBacktestAnnotations,
        );
        workspace.resizeObserver = new ResizeObserver(() => {
          if (!workspace.chart || !chartHost) return;
          workspace.chart.resize(chartHost.clientWidth, chartHost.clientHeight);
          renderBacktestAnnotations();
        });
        workspace.resizeObserver.observe(chartHost);
        window.setTimeout(renderBacktestAnnotations, 40);
        setBacktestTool("pointer");
        setChartStatus(
          payload.annotations_saved_at
            ? "Saved drawings restored for this symbol and timeframe."
            : "Pan and zoom the chart, or choose a drawing tool.",
        );
        renderWrittenReport("backtest-report", payload.report);
        renderSuccessState(result, payload);
      } catch (error) {
        renderErrorState(result, error, "Run the replay again or reduce the time window.");
        showToast(error.message, "error");
      }
    }
    document.querySelectorAll("[data-load-backtest]").forEach((button) => {
      button.addEventListener("click", () => loadBacktest(button.dataset.loadBacktest));
    });
    document.querySelectorAll("[data-backtest-tool]").forEach((button) => {
      button.addEventListener("click", () => setBacktestTool(button.dataset.backtestTool));
    });
    annotationLayer?.addEventListener("click", (event) => {
      if (workspace.tool === "pointer") return;
      const point = chartPoint(event);
      if (point) addBacktestAnnotation(point);
    });
    timeframeSelect?.addEventListener("change", () => {
      loadBacktest(workspace.jobId, timeframeSelect.value);
    });
    document.querySelector("[data-backtest-undo]")?.addEventListener("click", () => {
      if (!workspace.annotations.length) return;
      workspace.annotations.pop();
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("Last drawing removed. Save to keep this change.", true);
    });
    document.querySelector("[data-backtest-clear]")?.addEventListener("click", () => {
      workspace.annotations = [];
      workspace.dirty = true;
      renderBacktestAnnotations();
      setChartStatus("All drawings cleared. Save to keep this change.", true);
    });
    document.querySelector("[data-backtest-save]")?.addEventListener(
      "click",
      saveBacktestAnnotations,
    );
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("backtest-result");
      renderLoadingState(result, "Processing historical replay...");
      try {
        const selected = field(form, "strategy_id").selectedOptions[0];
        const payload = {
          strategy_id: field(form, "strategy_id").value,
          strategy_version_id: selected?.dataset.versionId || null,
          exchange: field(form, "exchange").value || "binance",
          symbols: csv(field(form, "symbols").value),
          timeframe: field(form, "timeframe").value || "15m",
          started_at_range: new Date(field(form, "started_at_range").value).toISOString(),
          ended_at_range: new Date(field(form, "ended_at_range").value).toISOString(),
          parameters: { source: "dashboard" },
        };
        const created = await api("/backtests", { method: "POST", body: JSON.stringify(payload) });
        const run = await api(`/backtests/${created.job.id}/run`, { method: "POST" });
        renderSuccessState(result, run.result);
        await loadBacktest(created.job.id, payload.timeframe);
        showToast("Historical replay completed.");
      } catch (error) {
        renderErrorState(result, error, "Check the symbols and replay dates.");
        showToast(error.message, "error");
      }
    });
  }

  function initExports() {
    const form = document.getElementById("export-form");
    async function runExport(jobId) {
      const result = document.getElementById("export-result");
      renderLoadingState(result, "Generating export...");
      try {
        const payload = await api(`/exports/${jobId}/run`, { method: "POST" });
        renderSuccessState(result, payload);
        showToast("Export generated.");
        if (payload.job?.file_url) window.location.href = payload.job.file_url;
      } catch (error) {
        renderErrorState(result, error, "Try again or reduce the export filter scope.");
        showToast(error.message, "error");
      }
    }
    document.querySelectorAll("[data-run-export]").forEach((button) => {
      button.addEventListener("click", () => runExport(button.dataset.runExport));
    });
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("export-result");
      renderLoadingState(result, "Creating export job...");
      try {
        const created = await api("/exports", {
          method: "POST",
          body: JSON.stringify({
            export_type: field(form, "export_type").value,
            format: field(form, "format").value,
            filters: { source: "dashboard" },
          }),
        });
        await runExport(created.job.id);
      } catch (error) {
        renderErrorState(result, error, "Check the export type and try again.");
        showToast(error.message, "error");
      }
    });
  }

  function initSupport() {
    const form = document.getElementById("support-ticket-form");
    if (!form) return;
    const screenshotInput = field(form, "screenshots");
    const screenshotName = form.querySelector("[data-support-file-name]");
    screenshotInput?.addEventListener("change", () => {
      const files = Array.from(screenshotInput.files || []);
      if (screenshotName) {
        screenshotName.textContent = files.length
          ? files.map((file) => file.name).join(", ")
          : "No files chosen";
      }
    });
    async function screenshotPayload(file) {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        throw new Error(`${file.name} is not a supported screenshot type.`);
      }
      if (file.size > 5 * 1024 * 1024) {
        throw new Error(`${file.name} is larger than 5 MB.`);
      }
      const buffer = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      const chunkSize = 32_768;
      for (let index = 0; index < buffer.length; index += chunkSize) {
        binary += String.fromCharCode(...buffer.subarray(index, index + chunkSize));
      }
      return {
        filename: file.name,
        content_type: file.type,
        data_base64: window.btoa(binary),
      };
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = document.getElementById("support-result");
      renderLoadingState(result, "Sending support request...");
      try {
        const files = Array.from(field(form, "screenshots").files || []);
        if (files.length > 3) throw new Error("Attach no more than three screenshots.");
        const screenshots = await Promise.all(files.map(screenshotPayload));
        await api("/support/tickets", {
          method: "POST",
          body: JSON.stringify({
            category: "general",
            email: field(form, "email").value,
            subject: field(form, "subject").value,
            description: field(form, "description").value,
            context: { source: "dashboard" },
            screenshots,
          }),
        });
        renderSuccessState(result, "Support request sent. We will contact you by email.");
        showToast("Support request sent.");
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        renderErrorState(result, error, "Review the email, description and screenshots.");
        showToast(error.message, "error");
      }
    });
  }

  function candleTime(value) {
    const millis = new Date(value).getTime();
    return Number.isFinite(millis) ? Math.floor(millis / 1000) : value;
  }

  function overlayPrices(overlays) {
    const colors = chartTheme();
    const prices = [];
    if (!overlays) return prices;
    const entry = overlays.entry_zone;
    if (entry && typeof entry === "object") {
      ["low", "high", "entry_low", "entry_high"].forEach((key) => {
        if (Number.isFinite(Number(entry[key]))) {
          prices.push({ label: `entry ${key}`, value: safeNumber(entry[key]), color: colors.entry });
        }
      });
    }
    ["entry_zone_low", "entry_zone_high", "stop_price", "invalidation_level"].forEach((key) => {
      if (Number.isFinite(Number(overlays[key]))) {
        prices.push({
          label: key,
          value: safeNumber(overlays[key]),
          color: key.includes("stop") || key.includes("invalidation") ? colors.stop : colors.entry,
        });
      }
    });
    safeArray(overlays.targets || overlays.target_levels).forEach((target, index) => {
      const value = typeof target === "object" ? target.price || target.value : target;
      if (Number.isFinite(Number(value))) {
        prices.push({ label: `target ${index + 1}`, value: safeNumber(value), color: colors.target });
      }
    });
    const grouped = [];
    prices.forEach((line) => {
      const existing = grouped.find(
        (item) => Math.abs(item.value - line.value) <= Math.max(1e-10, Math.abs(line.value) * 1e-8),
      );
      if (existing) {
        if (!existing.label.includes(line.label)) existing.label += ` / ${line.label}`;
        if (line.color === colors.stop) existing.color = colors.stop;
      } else {
        grouped.push({ ...line });
      }
    });
    return grouped;
  }

  function renderReplayChart(payload) {
    const candles = safeArray(payload.candles);
    const container = document.getElementById("replay-chart");
    const fallback = document.getElementById("replay-chart-fallback");
    const colors = chartTheme();
    if (window.LightweightCharts && container && candles.length) {
      container.innerHTML = "";
      container.style.display = "block";
      if (fallback) fallback.hidden = true;
      const chart = window.LightweightCharts.createChart(container, {
        height: 456,
        width: container.clientWidth || undefined,
        layout: { background: { color: colors.background }, textColor: colors.text },
        grid: {
          vertLines: { color: colors.grid },
          horzLines: { color: colors.grid },
        },
      });
      window.setTimeout(() => {
        chart.resize(container.clientWidth || 720, 456);
        chart.timeScale().fitContent();
      }, 0);
      const series = chart.addCandlestickSeries({
        upColor: colors.up,
        downColor: colors.down,
        borderVisible: false,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
      });
      series.setData(candles.map((candle) => ({
        time: candleTime(candle.timestamp),
        open: safeNumber(candle.open),
        high: safeNumber(candle.high),
        low: safeNumber(candle.low),
        close: safeNumber(candle.close),
      })));
      overlayPrices(payload.overlays).forEach((line) => {
        series.createPriceLine({
          price: line.value,
          color: line.color,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: line.label,
        });
      });
      series.setMarkers(safeArray(payload.markers).map((marker) => ({
        time: candleTime(marker.time),
        position: marker.label === "confirmed" ? "aboveBar" : "belowBar",
        color: marker.label === "confirmed" ? colors.up : colors.entry,
        shape: marker.label === "confirmed" ? "arrowUp" : "circle",
        text: marker.text || `${safeNumber(marker.score).toFixed(0)}%`,
      })));
      chart.timeScale().fitContent();
      return;
    }
    if (container) container.style.display = "none";
    if (fallback) {
      fallback.hidden = false;
      drawCandles(fallback, candles, payload.overlays, safeArray(payload.markers));
    }
  }

  async function initChart() {
    const canvas = document.getElementById("amm-chart");
    if (!canvas) return;
    const url = canvas.dataset.candlesUrl;
    if (!url) return;
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Chart data unavailable");
      drawCandles(canvas, safeArray(payload.items));
    } catch (error) {
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#94a3bd";
      ctx.font = "14px DM Sans, Arial";
      ctx.fillText(error.message, 20, 40);
    }
  }

  function drawCandles(canvas, candles, overlays = null, markers = []) {
    candles = safeArray(candles).map((candle) => ({
      ...candle,
      open: safeNumber(candle.open),
      high: safeNumber(candle.high),
      low: safeNumber(candle.low),
      close: safeNumber(candle.close),
      volume: safeNumber(candle.volume),
    }));
    markers = safeArray(markers);
    const parent = canvas.parentElement;
    const width = Math.max(300, (parent?.clientWidth || 720) - 24);
    const height = 456;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    const colors = chartTheme();
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = colors.background;
    ctx.fillRect(0, 0, width, height);
    if (!candles.length) {
      ctx.fillStyle = colors.text;
      ctx.fillText("No candles returned.", 20, 40);
      return;
    }
    const pad = 24;
    const highs = candles.map((candle) => safeNumber(candle.high));
    const lows = candles.map((candle) => safeNumber(candle.low));
    overlayPrices(overlays).forEach((line) => {
      highs.push(line.value);
      lows.push(line.value);
    });
    const max = Math.max(...highs);
    const min = Math.min(...lows);
    const span = max - min || 1;
    const xStep = (width - pad * 2) / candles.length;
    const y = (price) => height - pad - ((price - min) / span) * (height - pad * 2);
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i < 5; i += 1) {
      const gridY = pad + (i * (height - pad * 2)) / 4;
      ctx.beginPath();
      ctx.moveTo(pad, gridY);
      ctx.lineTo(width - pad, gridY);
      ctx.stroke();
    }
    candles.forEach((candle, index) => {
      const x = pad + index * xStep + xStep / 2;
      const up = candle.close >= candle.open;
      ctx.strokeStyle = up ? colors.up : colors.down;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(x, y(candle.high));
      ctx.lineTo(x, y(candle.low));
      ctx.stroke();
      const top = y(Math.max(candle.open, candle.close));
      const bottom = y(Math.min(candle.open, candle.close));
      const bodyWidth = Math.max(2, xStep * 0.58);
      ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, Math.max(2, bottom - top));
    });
    const usedLabelRows = new Set();
    const labelY = (baseY) => {
      let row = Math.max(1, Math.min(Math.floor((height - 18) / 16), Math.round(baseY / 16)));
      while (usedLabelRows.has(row) && row < Math.floor((height - 18) / 16)) row += 1;
      usedLabelRows.add(row);
      return row * 16;
    };
    overlayPrices(overlays).forEach((line) => {
      ctx.strokeStyle = line.color;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(pad, y(line.value));
      ctx.lineTo(width - pad, y(line.value));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = line.color;
      ctx.fillText(line.label, Math.max(pad, width - pad - 130), labelY(y(line.value)));
    });
    const markerSlots = new Map();
    markers.forEach((marker) => {
      const markerTime = new Date(marker.time).getTime();
      let index = candles.findIndex((candle) => new Date(candle.timestamp).getTime() === markerTime);
      if (index < 0) {
        index = candles.reduce((best, candle, currentIndex) => {
          const distance = Math.abs(new Date(candle.timestamp).getTime() - markerTime);
          const bestDistance = Math.abs(new Date(candles[best].timestamp).getTime() - markerTime);
          return distance < bestDistance ? currentIndex : best;
        }, 0);
      }
      const candle = candles[index];
      if (!candle) return;
      const x = pad + index * xStep + xStep / 2;
      const eventLabel = marker.text || marker.label || marker.outcome || "event";
      const confirmed = String(eventLabel).includes("confirmed");
      const slotKey = `${index}:${confirmed ? "top" : "bottom"}`;
      const slot = markerSlots.get(slotKey) || 0;
      markerSlots.set(slotKey, slot + 1);
      const pointY = confirmed ? y(candle.high) - 10 - slot * 16 : y(candle.low) + 10 + slot * 16;
      ctx.fillStyle = confirmed ? colors.up : colors.entry;
      ctx.beginPath();
      ctx.arc(x, pointY, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillText(eventLabel, Math.min(x + 6, width - pad - 140), pointY - 6);
    });
    ctx.fillStyle = colors.text;
    ctx.font = "12px DM Sans, Arial";
    ctx.fillText(`High ${max.toFixed(4)} · Low ${min.toFixed(4)}`, pad, 16);
  }

  function drawLineChart(canvas, rows, key) {
    rows = safeArray(rows);
    const parent = canvas.parentElement;
    const width = Math.max(300, (parent?.clientWidth || 720) - 24);
    const height = 456;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    const colors = chartTheme();
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = colors.background;
    ctx.fillRect(0, 0, width, height);
    if (!rows.length) {
      ctx.fillStyle = colors.text;
      ctx.fillText("No historical replay points returned.", 20, 40);
      return;
    }
    const pad = 24;
    const values = rows.map((row) => safeNumber(row[key], 0));
    const max = Math.max(...values);
    const min = Math.min(...values);
    const span = max - min || 1;
    const xStep = (width - pad * 2) / Math.max(1, rows.length - 1);
    const y = (value) => height - pad - ((value - min) / span) * (height - pad * 2);
    ctx.strokeStyle = colors.up;
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach((value, index) => {
      const x = pad + index * xStep;
      const pointY = y(value);
      if (index === 0) ctx.moveTo(x, pointY);
      else ctx.lineTo(x, pointY);
    });
    ctx.stroke();
    ctx.fillStyle = colors.text;
    ctx.font = "12px DM Sans, Arial";
    ctx.fillText(`Hypothetical quality index ${min.toFixed(3)} - ${max.toFixed(3)}`, pad, 16);
  }

  function initLifecycles() {
    const dialog = document.getElementById("lifecycle-chart-dialog");
    document.querySelectorAll("[data-lifecycle-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const details = document.querySelector(
          `[data-lifecycle-details="${button.dataset.lifecycleToggle}"]`,
        );
        if (!details) return;
        const expanded = button.getAttribute("aria-expanded") === "true";
        button.setAttribute("aria-expanded", String(!expanded));
        details.hidden = expanded;
      });
    });
    document.querySelectorAll("[data-lifecycle-mute]").forEach((button) => {
      button.addEventListener("click", async () => {
        const setupId = button.dataset.lifecycleMute;
        if (!setupId) return;
        try {
          await api(`/lifecycles/${setupId}/mute`, { method: "POST" });
          document.querySelector(`[data-lifecycle-card="${setupId}"]`)?.remove();
          showToast("Lifecycle muted and removed from this view.");
        } catch (error) {
          showToast(error.message, "error");
        }
      });
    });
    if (!dialog) return;

    const widgetHost = document.getElementById("lifecycle-tradingview-widget");
    const title = document.getElementById("lifecycle-chart-title");
    const subtitle = document.getElementById("lifecycle-chart-subtitle");
    const status = document.getElementById("lifecycle-chart-status");
    const missingConditions = document.getElementById("lifecycle-missing-conditions");
    const completedConditions = document.getElementById("lifecycle-completed-conditions");
    const timeframeSelect = { innerHTML: "", value: "" };
    const workspace = {
      setupId: null,
      timeframe: null,
      widget: null,
      payloadCache: new Map(),
    };

    function setStatus(message, dirty = false) {
      if (!status) return;
      status.textContent = message;
      status.classList.toggle("dirty", dirty);
    }

    function destroyChart() {
      if (workspace.widget?.remove) {
        try {
          workspace.widget.remove();
        } catch (_) {
          // TradingView cleanup should not block reopening the evidence view.
        }
      }
      workspace.widget = null;
      workspace.payloadCache.clear();
      if (widgetHost) widgetHost.replaceChildren();
    }

    function renderConditionList(container, conditions, emptyText) {
      if (!container) return;
      const rows = safeArray(conditions);
      if (!rows.length) {
        container.innerHTML = `<p class="dash-muted">${escapeHtml(emptyText)}</p>`;
        return;
      }
      container.innerHTML = `<div class="lifecycle-condition-list">${rows
        .map((condition) => {
          const actual = condition.actual ?? "unavailable";
          const required = condition.required ?? "defined by rule";
          const candleTime = condition.candle_timestamp ? new Date(condition.candle_timestamp) : null;
          const evaluatedAt = condition.evaluated_at ? new Date(condition.evaluated_at) : null;
          const candleLabel = candleTime && !Number.isNaN(candleTime.getTime())
            ? candleTime.toLocaleString(undefined, {
              month: "short",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
            : "No candle timestamp";
          const evaluatedLabel = evaluatedAt && !Number.isNaN(evaluatedAt.getTime())
            ? evaluatedAt.toLocaleString(undefined, {
              month: "short",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
            : "No evaluation timestamp";
          return `
            <div class="lifecycle-condition-item">
              <strong>${escapeHtml(condition.name || condition.key)}</strong>
              <span>${escapeHtml(condition.timeframe || workspace.timeframe)} - ${escapeHtml(condition.state)}</span>
              <span>Actual: ${escapeHtml(actual)} - Required: ${escapeHtml(required)}</span>
              <small>Candle: ${escapeHtml(candleLabel)} - Evaluated: ${escapeHtml(evaluatedLabel)}</small>
            </div>
          `;
        })
        .join("")}</div>`;
    }

    function ensureTradingViewChartingLibrary() {
      if (window.__traceedgeChartingLibraryLoaded && window.TradingView?.widget) {
        return Promise.resolve();
      }
      const existing = document.querySelector("script[data-tradingview-charting-library]");
      if (existing) {
        if (existing.dataset.loaded === "true" && window.TradingView?.widget) {
          return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
          existing.addEventListener("load", () => {
            window.__traceedgeChartingLibraryLoaded = true;
            existing.dataset.loaded = "true";
            if (window.TradingView?.widget) resolve();
            else reject(new Error("TradingView Charting Library loaded, but window.TradingView.widget was not available."));
          }, { once: true });
          existing.addEventListener("error", reject, { once: true });
        });
      }
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/charting_library/charting_library.js";
        script.async = true;
        script.dataset.tradingviewChartingLibrary = "true";
        script.addEventListener("load", () => {
          window.__traceedgeChartingLibraryLoaded = true;
          script.dataset.loaded = "true";
          if (window.TradingView?.widget) resolve();
          else reject(new Error("TradingView Charting Library loaded, but window.TradingView.widget was not available."));
        }, { once: true });
        script.addEventListener("error", () => reject(new Error(
          "Licensed TradingView package unavailable; using the native TraceEdge chart.",
        )), { once: true });
        document.head.appendChild(script);
      });
    }

    function ensureTradingViewLightweightCharts() {
      if (window.LightweightCharts?.createChart) {
        return Promise.resolve();
      }
      const existing = document.querySelector("script[data-tradingview-lightweight]");
      if (existing) {
        if (existing.dataset.loaded === "true" && window.LightweightCharts?.createChart) {
          return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
          existing.addEventListener("load", () => {
            existing.dataset.loaded = "true";
            if (window.LightweightCharts?.createChart) resolve();
            else reject(new Error("TradingView Lightweight Charts loaded, but createChart was unavailable."));
          }, { once: true });
          existing.addEventListener("error", reject, { once: true });
        });
      }
      const cdn = root?.dataset.chartLibraryCdn || "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js";
      if (!cdn) {
        return Promise.reject(new Error("TradingView Lightweight Charts CDN is not configured."));
      }
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = cdn;
        script.async = true;
        script.dataset.tradingviewLightweight = "true";
        script.addEventListener("load", () => {
          script.dataset.loaded = "true";
          if (window.LightweightCharts?.createChart) resolve();
          else reject(new Error("TradingView Lightweight Charts loaded, but createChart was unavailable."));
        }, { once: true });
        script.addEventListener("error", () => reject(new Error("TradingView Lightweight Charts could not be loaded.")), { once: true });
        document.head.appendChild(script);
      });
    }

    function tradingViewInterval(timeframe) {
      const normalized = String(timeframe || "15m").toLowerCase();
      const mapping = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "1d": "D",
        "1w": "W",
      };
      return mapping[normalized] || "15";
    }

    function resolutionToTimeframe(resolution) {
      const normalized = String(resolution || "15").toUpperCase();
      const mapping = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "D": "1d",
        "1D": "1d",
      };
      return mapping[normalized] || "15m";
    }

    function tradingViewSymbol(exchange, symbol) {
      const provider = String(exchange || "binance").toUpperCase().replace(/[^A-Z0-9]/g, "");
      const pair = String(symbol || "BTC/USDT").toUpperCase().replace(/[^A-Z0-9]/g, "");
      return `${provider || "BINANCE"}:${pair || "BTCUSDT"}`;
    }

    function candleSeconds(value) {
      const date = value ? new Date(value) : null;
      if (!date || Number.isNaN(date.getTime())) return 0;
      return Math.floor(date.getTime() / 1000);
    }

    function chartingLibraryDatafeed(initialPayload) {
      const supportedResolutions = ["1", "3", "5", "15", "30", "60", "120", "240", "D"];
      const symbolName = tradingViewSymbol(initialPayload.setup.exchange, initialPayload.setup.symbol);
      workspace.payloadCache.set(workspace.timeframe, initialPayload);

      async function payloadForResolution(resolution) {
        const timeframe = resolutionToTimeframe(resolution);
        if (workspace.payloadCache.has(timeframe)) return workspace.payloadCache.get(timeframe);
        const payload = await api(
          `/lifecycles/${workspace.setupId}/chart?timeframe=${encodeURIComponent(timeframe)}`,
        );
        workspace.payloadCache.set(timeframe, payload);
        return payload;
      }

      function applySideEvidence(payload) {
        workspace.timeframe = payload.setup.selected_timeframe;
        renderConditionList(
          missingConditions,
          payload.missing_conditions,
          "No unmet deterministic conditions remain.",
        );
        renderConditionList(
          completedConditions,
          payload.completed_conditions,
          "No condition has passed yet.",
        );
      }

      function barsFromPayload(payload, from, to) {
        return safeArray(payload.candles)
          .map((candle) => ({
            time: candleSeconds(candle.timestamp) * 1000,
            open: safeNumber(candle.open),
            high: safeNumber(candle.high),
            low: safeNumber(candle.low),
            close: safeNumber(candle.close),
            volume: safeNumber(candle.volume),
          }))
          .filter((bar) => {
            const seconds = Math.floor(bar.time / 1000);
            return (!from || seconds >= from) && (!to || seconds <= to);
          });
      }

      return {
        onReady(callback) {
          window.setTimeout(() => callback({
            supports_marks: true,
            supports_time: true,
            supports_timescale_marks: false,
            supported_resolutions: supportedResolutions,
          }), 0);
        },
        searchSymbols(_userInput, _exchange, _symbolType, onResultReadyCallback) {
          onResultReadyCallback([]);
        },
        resolveSymbol(_symbolName, onSymbolResolvedCallback) {
          window.setTimeout(() => onSymbolResolvedCallback({
            name: symbolName,
            ticker: symbolName,
            description: `${initialPayload.setup.symbol} lifecycle evidence`,
            type: "crypto",
            session: "24x7",
            exchange: initialPayload.setup.exchange.toUpperCase(),
            listed_exchange: initialPayload.setup.exchange.toUpperCase(),
            timezone: "Etc/UTC",
            minmov: 1,
            pricescale: 100000,
            has_intraday: true,
            has_daily: true,
            volume_precision: 2,
            data_status: "streaming",
            supported_resolutions: supportedResolutions,
          }), 0);
        },
        async getBars(_symbolInfo, resolution, periodParams, onHistoryCallback, onErrorCallback) {
          try {
            const payload = await payloadForResolution(resolution);
            applySideEvidence(payload);
            const bars = barsFromPayload(payload, periodParams?.from, periodParams?.to);
            onHistoryCallback(bars, { noData: bars.length === 0 });
          } catch (error) {
            onErrorCallback(error.message);
          }
        },
        async getMarks(_symbolInfo, from, to, onDataCallback, resolution) {
          try {
            const payload = await payloadForResolution(resolution);
            const marks = safeArray(payload.markers)
              .map((marker, index) => {
                const time = candleSeconds(marker.time);
                if (!time || time < from || time > to) return null;
                const condition = marker.kind === "condition";
                return {
                  id: `${marker.kind || "event"}-${index}-${time}`,
                  time,
                  color: condition ? "#8b5cf6" : "#c4b5fd",
                  text: marker.text || marker.label || "Lifecycle event",
                  label: condition ? "C" : "L",
                  labelFontColor: condition ? "#ffffff" : "#17131f",
                  minSize: 22,
                };
              })
              .filter(Boolean);
            onDataCallback(marks);
          } catch (_) {
            onDataCallback([]);
          }
        },
        subscribeBars() {},
        unsubscribeBars() {},
        getServerTime(callback) {
          callback(Math.floor(Date.now() / 1000));
        },
      };
    }

    async function renderTradingView(payload) {
      if (!widgetHost) return;
      await ensureTradingViewChartingLibrary();
      widgetHost.replaceChildren();
      const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const background = theme === "light" ? "#f3efff" : "#151021";
      workspace.widget = new window.TradingView.widget({
        autosize: true,
        symbol: tradingViewSymbol(payload.setup.exchange, payload.setup.symbol),
        interval: tradingViewInterval(workspace.timeframe),
        datafeed: chartingLibraryDatafeed(payload),
        library_path: "/static/charting_library/",
        timezone: "Etc/UTC",
        theme,
        style: "1",
        locale: "en",
        toolbar_bg: theme === "light" ? "#f3efff" : "#151021",
        enable_publishing: false,
        allow_symbol_change: false,
        hide_side_toolbar: false,
        withdateranges: true,
        save_image: true,
        studies: [],
        enabled_features: ["study_templates", "chart_property_page_trading"],
        disabled_features: ["header_symbol_search", "symbol_search_hot_key"],
        overrides: {
          "paneProperties.background": background,
          "paneProperties.backgroundType": "solid",
          "paneProperties.vertGridProperties.color": theme === "light" ? "#ddd6fe" : "#241737",
          "paneProperties.horzGridProperties.color": theme === "light" ? "#ddd6fe" : "#241737",
          "scalesProperties.textColor": theme === "light" ? "#17131f" : "#e6e6eb",
          "mainSeriesProperties.candleStyle.upColor": "#8b5cf6",
          "mainSeriesProperties.candleStyle.downColor": "#c4b5fd",
          "mainSeriesProperties.candleStyle.borderUpColor": "#8b5cf6",
          "mainSeriesProperties.candleStyle.borderDownColor": "#c4b5fd",
          "mainSeriesProperties.candleStyle.wickUpColor": "#8b5cf6",
          "mainSeriesProperties.candleStyle.wickDownColor": "#c4b5fd",
        },
        container_id: "lifecycle-tradingview-widget",
      });
    }

    async function renderTradingViewLightweight(payload) {
      if (!widgetHost) return;
      await ensureTradingViewLightweightCharts();
      widgetHost.innerHTML = '<div id="lifecycle-lightweight-chart" class="lifecycle-lightweight-chart"></div>';
      const container = document.getElementById("lifecycle-lightweight-chart");
      if (!container) return;
      const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const chart = window.LightweightCharts.createChart(container, {
        autoSize: true,
        layout: {
          background: { color: theme === "light" ? "#f3efff" : "#151021" },
          textColor: theme === "light" ? "#17131f" : "#f7f3ff",
          fontFamily: "Inter, Satoshi, system-ui, sans-serif",
        },
        grid: {
          vertLines: { color: theme === "light" ? "rgba(139,92,246,.16)" : "rgba(196,181,253,.12)" },
          horzLines: { color: theme === "light" ? "rgba(139,92,246,.16)" : "rgba(196,181,253,.12)" },
        },
        rightPriceScale: { borderColor: "rgba(196,181,253,.22)" },
        timeScale: { borderColor: "rgba(196,181,253,.22)", timeVisible: true },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
      });
      const series = chart.addCandlestickSeries({
        upColor: "#8b5cf6",
        downColor: "#c4b5fd",
        borderUpColor: "#8b5cf6",
        borderDownColor: "#c4b5fd",
        wickUpColor: "#8b5cf6",
        wickDownColor: "#c4b5fd",
      });
      const data = safeArray(payload.candles)
        .map((candle) => ({
          time: Math.floor(new Date(candle.timestamp).getTime() / 1000),
          open: safeNumber(candle.open),
          high: safeNumber(candle.high),
          low: safeNumber(candle.low),
          close: safeNumber(candle.close),
        }))
        .filter((candle) => Number.isFinite(candle.time))
        .sort((left, right) => left.time - right.time);
      series.setData(data);
      const markerPayload = safeArray(payload.markers)
        .map((marker) => ({
          time: Math.floor(new Date(marker.time).getTime() / 1000),
          position: marker.position === "aboveBar" ? "aboveBar" : "belowBar",
          color: marker.kind === "lifecycle" ? "#c4b5fd" : "#8b5cf6",
          shape: marker.kind === "lifecycle" ? "arrowDown" : "circle",
          text: String(marker.text || marker.label || "event").slice(0, 20),
        }))
        .filter((marker) => Number.isFinite(marker.time));
      if (series.setMarkers) series.setMarkers(markerPayload);
      overlayPrices(payload.overlays).forEach((line) => {
        series.createPriceLine({
          price: line.value,
          color: line.color,
          lineStyle: window.LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: line.label,
        });
      });
      chart.timeScale().fitContent();
      workspace.widget = {
        remove: () => chart.remove(),
        chart,
        series,
      };
    }

    function renderLifecycleNativeChart(payload, error = null) {
      if (!widgetHost) return;
      const candles = safeArray(payload.candles);
      const markers = safeArray(payload.markers).map((marker) => ({
        ...marker,
        text: marker.text || marker.label || marker.kind || "event",
      }));
      widgetHost.innerHTML = `
        <div class="lifecycle-native-chart">
          <div class="lifecycle-native-chart-head">
            <span>
              <img src="https://api.iconify.design/lucide:chart-candlestick.svg?color=%23c4b5fd" alt="" aria-hidden="true">
              Native evidence chart
            </span>
            <small>${escapeHtml(candles.length)} candles | ${escapeHtml(markers.length)} evidence marks</small>
          </div>
          <div class="lifecycle-native-chart-frame">
            <canvas id="lifecycle-native-canvas" aria-label="Lifecycle candle evidence chart"></canvas>
          </div>
          ${error ? `<p class="lifecycle-native-chart-note">Using the built-in TraceEdge chart for this session.</p>` : ""}
        </div>
      `;
      const canvas = document.getElementById("lifecycle-native-canvas");
      if (canvas) {
        drawCandles(canvas, candles, payload.overlays || null, markers);
      }
    }

    async function loadLifecycleChart(setupId, timeframe) {
      destroyChart();
      workspace.setupId = setupId;
      workspace.timeframe = timeframe;
      setStatus("Loading TradingView chart and deterministic evidence...");
      try {
        const payload = await api(
          `/lifecycles/${setupId}/chart?timeframe=${encodeURIComponent(timeframe)}`,
        );
        workspace.timeframe = payload.setup.selected_timeframe;
        title.textContent = `${payload.setup.symbol} - ${payload.setup.state_label}`;
        subtitle.textContent =
          `${payload.setup.exchange} - ${payload.setup.direction} - ${payload.setup.completion_score.toFixed(0)}% complete`;
        timeframeSelect.innerHTML = safeArray(payload.timeframes)
          .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
          .join("");
        timeframeSelect.value = workspace.timeframe;
        renderConditionList(
          missingConditions,
          payload.missing_conditions,
          "No unmet deterministic conditions remain.",
        );
        renderConditionList(
          completedConditions,
          payload.completed_conditions,
          "No condition has passed yet.",
        );
        try {
          await renderTradingView(payload);
          setStatus("TradingView Charting Library is using deterministic lifecycle candles and condition marks.");
        } catch (chartError) {
          try {
            await renderTradingViewLightweight(payload);
            setStatus("TradingView Lightweight Charts rendered deterministic lifecycle candles and condition marks.");
          } catch (fallbackError) {
            renderLifecycleNativeChart(payload, fallbackError);
            setStatus("Native lifecycle chart rendered from deterministic candles and condition marks.");
          }
        }
      } catch (error) {
        setStatus(`Chart unavailable: ${error.message}`);
        showToast(error.message, "error");
      }
    }

    document.querySelectorAll("[data-lifecycle-chart]").forEach((button) => {
      button.addEventListener("click", () => {
        dialog.showModal();
        loadLifecycleChart(button.dataset.lifecycleChart, button.dataset.timeframe || "15m");
      });
    });
    async function closeDialog() {
      dialog.close();
      destroyChart();
    }
    document.querySelector("[data-lifecycle-dialog-close]")?.addEventListener("click", closeDialog);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog();
    });
  }

  function initSettings() {
    const form = document.getElementById("dashboard-settings-form");
    const saveButton = form?.querySelector("[data-settings-save]");
    const markDirty = () => {
      if (!saveButton) return;
      saveButton.hidden = false;
      saveButton.classList.add("visible");
    };
    form?.addEventListener("input", markDirty);
    form?.addEventListener("change", markDirty);
    document.querySelectorAll("[data-toggle-list]").forEach((listbox) => {
      listbox.addEventListener("mousedown", (event) => {
        if (event.target.tagName !== "OPTION") return;
        event.preventDefault();
        event.target.selected = !event.target.selected;
        listbox.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });
    document.querySelectorAll("[data-schedule-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.scheduleAction;
        const days = document.querySelector('[data-toggle-list="days"]');
        const hours = document.querySelector('[data-toggle-list="hours"]');
        if (action?.startsWith("days") && days) {
          Array.from(days.options).forEach((option) => {
            if (action === "days-all") option.selected = option.value === "Every Day";
            if (action === "days-weekdays") {
              option.selected = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"].includes(option.value);
            }
            if (action === "days-clear") option.selected = false;
          });
          days.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (action?.startsWith("hours") && hours) {
          Array.from(hours.options).forEach((option) => {
            const hour = Number(option.value.slice(0, 2));
            if (action === "hours-all") option.selected = true;
            if (action === "hours-business") option.selected = hour >= 8 && hour <= 18;
            if (action === "hours-clear") option.selected = false;
          });
          hours.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
  }

  function initReferralCopy() {
    const button = document.querySelector("[data-copy-referral]");
    const link = document.querySelector("[data-referral-link]");
    if (!button || !link) return;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(link.textContent.trim());
        button.classList.remove("copied");
        void button.offsetWidth;
        button.classList.add("copied");
        showToast("Referral link copied.");
      } catch {
        showToast("The referral link could not be copied.", "error");
      }
    });
  }

  function initInboxFilter() {
    const filter = document.querySelector("[data-inbox-filter]");
    if (!filter) return;
    const items = Array.from(document.querySelectorAll("[data-inbox-item]"));
    const empty = document.createElement("div");
    empty.className = "dash-empty inbox-filter-empty";
    empty.hidden = true;
    empty.textContent = "No review cards match this category.";
    document.querySelector(".inbox-list")?.after(empty);
    function applyFilter() {
      const selected = filter.value;
      let visible = 0;
      items.forEach((item) => {
        const match = !selected || item.dataset.itemType === selected;
        item.hidden = !match;
        if (match) visible += 1;
      });
      empty.hidden = visible > 0;
    }
    filter.addEventListener("change", applyFilter);
    applyFilter();
  }

  function initWebNotifications() {
    let stack = document.getElementById("web-notification-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "web-notification-stack";
      stack.className = "web-notification-stack";
      document.body.appendChild(stack);
    }

    function pushNotification(item) {
      const card = document.createElement("article");
      card.className = "web-notification-popup";
      const completion =
        item.completion_rate === null || item.completion_rate === undefined
          ? ""
          : `<span>${escapeHtml(item.completion_rate)}%</span>`;
      card.innerHTML = `
        <button type="button" aria-label="Dismiss notification">x</button>
        <strong>${escapeHtml(item.title || "Market update")}</strong>
        <p>${escapeHtml(item.symbol || "Market")}</p>
        ${completion}
      `;
      card.querySelector("button")?.addEventListener("click", () => card.remove());
      stack.prepend(card);
      window.setTimeout(() => card.remove(), 9000);
    }

    async function poll() {
      if (document.hidden) return;
      try {
        const payload = await api("/notifications/web");
        safeArray(payload.items).reverse().forEach(pushNotification);
      } catch {
        return;
      }
    }

    poll();
    window.setInterval(poll, 15000);
  }

  function initSidebar() {
    const storageKey = "amm-sidebar-collapsed";
    const scrollKey = "amm-sidebar-scroll-top";
    const mobile = window.matchMedia("(max-width: 900px)");
    const nav = document.querySelector(".dash-nav");

    if (nav) {
      const storedScroll = Number(window.localStorage.getItem(scrollKey) || 0);
      if (Number.isFinite(storedScroll) && storedScroll > 0) {
        window.requestAnimationFrame(() => {
          nav.scrollTop = storedScroll;
        });
      }
      nav.addEventListener("scroll", () => {
        window.localStorage.setItem(scrollKey, String(nav.scrollTop));
      }, { passive: true });
    }

    function setCollapsed(collapsed, persist = true) {
      root.classList.toggle("sidebar-collapsed", collapsed);
      root.classList.toggle("sidebar-open", !collapsed);
      document.querySelector("[data-sidebar-toggle]")?.setAttribute(
        "aria-expanded",
        String(!collapsed),
      );
      if (persist) window.localStorage.setItem(storageKey, String(collapsed));
    }

    const stored = window.localStorage.getItem(storageKey);
    setCollapsed(stored === null ? mobile.matches : stored === "true", false);
    document.querySelector("[data-sidebar-toggle]")?.addEventListener("click", () => {
      setCollapsed(!root.classList.contains("sidebar-collapsed"));
    });
    document.querySelector("[data-sidebar-close]")?.addEventListener("click", () => {
      setCollapsed(true);
    });
    document.querySelectorAll(".dash-nav a").forEach((link) => {
      link.addEventListener("click", () => {
        if (nav) window.localStorage.setItem(scrollKey, String(nav.scrollTop));
        if (mobile.matches) setCollapsed(true);
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !root.classList.contains("sidebar-collapsed")) {
        setCollapsed(true);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initVisualBuilder();
    initScanNow();
    initExports();
    initSupport();
    initChart();
    initLifecycles();
    initSettings();
    initReferralCopy();
    initInboxFilter();
    initWebNotifications();
  });
})();
