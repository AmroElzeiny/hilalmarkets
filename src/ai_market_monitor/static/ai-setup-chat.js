(() => {
  const root = document.querySelector("[data-ai-setup-chat]");
  if (!root) return;

  const apiBase = "/api/v1/dashboard/setup-chat";
  const messagesTarget = root.querySelector("[data-ai-chat-messages]");
  const suggestionsTarget = root.querySelector("[data-ai-chat-suggestions]");
  const guidedStarts = root.querySelector(".ai-chat-guided-starts");
  const form = root.querySelector("[data-ai-chat-form]");
  const input = root.querySelector("[data-ai-chat-input]");
  const sendButton = root.querySelector("[data-ai-chat-send]");
  const statusTarget = root.querySelector("[data-ai-chat-status]");
  const previewStatus = root.querySelector("[data-ai-preview-status]");
  const previewEmpty = root.querySelector("[data-ai-preview-empty]");
  const previewContent = root.querySelector("[data-ai-preview-content]");
  const previewActions = root.querySelector("[data-ai-preview-actions]");
  const approvalNote = root.querySelector("[data-ai-approval-note]");
  const approveButton = root.querySelector("[data-ai-chat-approve]");
  const scanButton = root.querySelector("[data-ai-chat-scan]");
  const errorBox = root.querySelector("[data-ai-chat-error]");
  const errorText = root.querySelector("[data-ai-chat-error-text]");
  const openCanvasButton = document.querySelector("[data-ai-open-canvas]");
  const returnChatButton = root.querySelector("[data-ai-return-chat]");
  const minimizeChatButton = root.querySelector("[data-ai-minimize-chat]");
  input.dataset.testid = "ai-setup-input";
  sendButton.dataset.testid = "ai-setup-send";
  let chat = null;
  let loading = false;
  let lastAction = null;
  let initialized = false;
  const optimisticMessages = new Map();

  const newClientMessageId = () => globalThis.crypto?.randomUUID?.()
    || `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const clean = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  // Persisted assistant records remain auditable. The current product name is
  // adapted only when we render assistant content in the HilalMarkets UI.
  const brandText = (value) => String(value ?? "").replace(/\bTraceEdge\b/gi, "HilalMarkets");

  async function request(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch (_) { payload = {}; }
      const detail = payload.detail;
      const message = typeof detail === "object"
        ? detail.message || detail.code
        : detail;
      const error = new Error(message || "The request could not be completed.");
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function recordInterpretationFeedback(feedbackType) {
    const response = await fetch("/api/v1/dashboard/strategies/interpret/feedback", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        feedback_type: feedbackType,
        raw_prompt: chat?.translation_sheet?.original_idea || chat?.original_idea || null,
        prompt_coverage_report: {
          mapping_table: chat?.translation_sheet?.clause_coverage || [],
        },
        strategy: chat?.draft_strategy || {},
      }),
    });
    if (!response.ok) throw new Error("Interpretation feedback could not be recorded.");
    return response.json();
  }

  function setLoading(value, label = "Thinking through your setup") {
    loading = value;
    input.disabled = value;
    sendButton.disabled = value || !input.value.trim();
    statusTarget.textContent = value ? label : statusLabel(chat?.status);
    root.querySelector("[data-ai-chat-new]").disabled = value;
    if (!root.hidden) renderConversation();
  }

  function statusLabel(status) {
    return ({
      interviewing: "Listening for your setup",
      needs_clarification: "Waiting for one detail",
      ready_for_approval: "Translation ready for review",
      ready_to_scan: "Scanner ready to run",
      building_mechanic: "Building and testing your mechanic",
      approved: "Setup approved",
    })[status] || "Ready to listen";
  }

  function showError(error) {
    errorText.textContent = error.message || "Something went wrong. Please retry.";
    errorBox.hidden = false;
  }

  function pendingOption() {
    return [...optimisticMessages.values()].find((item) => item.payload?.option_key);
  }

  function clearError() {
    errorBox.hidden = true;
    errorText.textContent = "";
  }

  function renderMessage(item) {
    const user = item.role === "user";
    if (!user && item.message_type === "mechanic_build_status") {
      const stage = item.payload?.stage || "building";
      const wrapper = document.createElement("aside");
      wrapper.className = "ai-chat-process-state mechanic";
      wrapper.setAttribute("role", "status");
      wrapper.dataset.testid = "ai-setup-assistant-message";
      if (item.id) wrapper.dataset.messageId = item.id;
      wrapper.dataset.processState = stage;
      wrapper.innerHTML = `
        ${window.icon?.("settings", "icon") || ""}
        <span><strong>${clean(stage.replaceAll("_", " "))}</strong>${clean(brandText(item.content))}</span>`;
      return wrapper;
    }
    if (!user && item.message_type === "process_state") {
      const state = item.payload?.state || "clarifying";
      const wrapper = document.createElement("aside");
      wrapper.className = "ai-chat-process-state";
      wrapper.setAttribute("role", "status");
      wrapper.dataset.testid = "ai-setup-assistant-message";
      if (item.id) wrapper.dataset.messageId = item.id;
      wrapper.dataset.processState = state;
      wrapper.innerHTML = `
        ${window.icon?.("workflow", "icon") || ""}
        <span><strong>Current step: ${clean(state.replaceAll("_", " "))}</strong>${clean(brandText(item.content))}</span>`;
      return wrapper;
    }
    const wrapper = document.createElement("article");
    wrapper.className = `ai-chat-message ${user ? "user" : "assistant"}${item.pending ? " pending" : ""}${item.failed ? " failed" : ""}`;
    if (!user) wrapper.dataset.testid = "ai-setup-assistant-message";
    if (item.id) wrapper.dataset.messageId = item.id;
    if (item.client_message_id) wrapper.dataset.clientMessageId = item.client_message_id;
    const avatarIcon = window.icon?.(user ? "user" : "spark", "icon") || "";
    const timestamp = item.created_at
      ? new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit"}).format(new Date(item.created_at))
      : "";
    const payload = item.payload || {};
    const understanding = !user && payload.understanding_summary
      ? `<section class="ai-understanding-card"><span>What I understood</span><p>${clean(brandText(payload.understanding_summary))}</p></section>`
      : "";
    const jargon = !user && Array.isArray(payload.jargon) && payload.jargon.length
      ? `<section class="ai-jargon-card">${payload.jargon.map((entry) => `<p><strong>${clean(entry.term)}</strong> ${clean(brandText(entry.explanation))}</p>`).join("")}</section>`
      : "";
    const snapshot = !user && item.message_type === "market_snapshot" && payload.status === "available"
      ? `<section class="ai-snapshot-card">
          <strong>${clean(payload.provider_name)} · ${clean(payload.symbols_checked)} symbols</strong>
          <span>BTC ${clean(payload.btc_status?.percentage_24h ?? "n/a")}% · ETH ${clean(payload.eth_status?.percentage_24h ?? "n/a")}%</span>
          <span>${clean(payload.advancing)} advancing · ${clean(payload.declining)} declining · ${clean(payload.unchanged)} unchanged</span>
          <span>${clean(payload.volatility_label)} dispersion · average ${clean(payload.average_change_24h)}%</span>
        </section>`
      : "";
    const scannerResult = payload.scanner_result;
    const scannerRows = Array.isArray(scannerResult?.results) ? scannerResult.results.slice(0, 5) : [];
    const scanner = !user && item.message_type === "scanner_result" && scannerResult
      ? `<section class="ai-scanner-result-card">
          <strong>Scanner result</strong>
          <span>${clean(payload.scanner_result.symbols_scanned)} symbols scanned · ${clean(payload.scanner_result.confirmed_count)} confirmed · ${clean(payload.scanner_result.forming_count)} forming</span>
          ${scannerRows.length ? `<div class="ai-scanner-result-list">${scannerRows.map((row) => `<div><strong>${clean(row.symbol)}</strong><span>${clean(String(Math.round(Number(row.match_percentage || 0))))}% · ${clean(String(row.outcome || "evaluated").replaceAll("_", " "))}</span></div>`).join("")}</div>` : ""}
          ${scannerResult.common_missing_reasons?.length ? `<small>Common misses: ${scannerResult.common_missing_reasons.map((entry) => `${clean(entry.condition)} (${clean(entry.count)})`).join(", ")}</small>` : ""}
          <small>${clean(scannerResult.disclaimer || "Research only. Not buy or sell advice.")}</small>
        </section>`
      : "";
    const delivery = item.failed
      ? `<small class="ai-chat-delivery failed">Not sent · Retry below</small>`
      : item.pending ? `<small class="ai-chat-delivery">Sending…</small>` : "";
    wrapper.innerHTML = `
      <span class="ai-chat-avatar">${avatarIcon}</span>
      <div class="ai-chat-bubble"><p>${clean(user ? item.content : brandText(item.content))}</p>${understanding}${jargon}${snapshot}${scanner}${delivery}<small class="ai-chat-message-meta">${clean(timestamp)}</small></div>`;
    return wrapper;
  }

  function latestAssistant() {
    return [...(chat?.messages || [])].reverse().find((item) => item.role === "assistant") || {};
  }

  function latestAssistantPayload() {
    return latestAssistant().payload || {};
  }

  function reportReasons(sheet) {
    const explained = latestAssistantPayload().refusal_reasons || [];
    const sources = explained.length ? [explained] : [
      chat?.lint_warnings || [],
      chat?.ambiguities || [],
      chat?.unsupported_conditions || [],
      sheet?.unsupported_conditions || [],
    ];
    const seen = new Set();
    return sources.flat().reduce((reasons, item) => {
      const message = String(item?.message || "").trim();
      if (!message) return reasons;
      const code = String(item?.code || "validation_issue");
      const key = `${code.toLowerCase()}::${message.toLowerCase().replace(/\s+/g, " ")}`;
      if (seen.has(key)) return reasons;
      seen.add(key);
      const severity = String(item?.severity || "critical").toLowerCase();
      const blocking = Boolean(item?.blocking) || severity === "critical";
      reasons.push({
        code,
        message,
        title: item?.title || (blocking ? "One detail must be fixed" : "Review this detail"),
        nextStep: item?.next_step || "Answer or revise this detail in the chat.",
        category: item?.category || "Review",
        severity,
        blocking,
        label: item?.label || (blocking ? "Blocking rule" : "Review note"),
      });
      return reasons;
    }, []);
  }

  function renderSuggestions() {
    suggestionsTarget.innerHTML = "";
    const assistant = latestAssistant();
    const payload = assistant.payload || {};
    const startModes = Array.isArray(payload.start_modes) ? payload.start_modes : [];
    if (startModes.length) {
      if (guidedStarts) guidedStarts.hidden = true;
      const cards = document.createElement("div");
      cards.className = "ai-chat-start-modes";
      startModes.forEach((option) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `ai-chat-start-card ${option.value}`;
        button.disabled = loading;
        const iconName = option.value === "scanner" ? "scan" : "radar";
        const modeIcon = window.icon ? window.icon(iconName, "icon-sm") : "";
        button.innerHTML = `<span>${modeIcon}</span><strong>${clean(option.label)}</strong><small>${clean(option.description)}</small>`;
        button.addEventListener("click", () => sendMessage({
          message: "",
          option_key: option.key,
          option_value: option.value,
          option_label: option.label,
        }, option.label));
        cards.append(button);
      });
      suggestionsTarget.append(cards);
      return;
    }
    const clarifications = Array.isArray(payload.clarifications) ? payload.clarifications : [];
    const clarificationOptions = clarifications.flatMap((item) =>
      (item.options || []).map((option) => ({...option, key: option.key || item.key})),
    );
    const actionLabels = {
      answer_clarification: "Answer in chat",
      review_draft: "Review draft",
      run_scan: "Run Scanner",
      open_monitor: "Open monitor",
      retry: "Retry",
      start_revision: "Start revision",
    };
    const agentActions = Array.isArray(payload.suggested_actions)
      ? payload.suggested_actions
        .filter((action) => actionLabels[action?.type])
        .map((action) => ({
          agentAction: action.type,
          label: actionLabels[action.type],
          value: action.type,
        }))
      : [];
    const suggestions = clarificationOptions.length
      ? clarificationOptions
      : agentActions.length
        ? agentActions
      : (payload.suggestions || []).map((value, index) => ({
        key: assistant.message_type === "translation" ? "apply_suggestion" : "setup_example",
        value,
        label: assistant.message_type === "translation" ? `Apply: ${value}` : value,
        id: index,
      }));
    if (guidedStarts) guidedStarts.hidden = suggestions.length > 0;
    const selected = pendingOption();
    suggestions.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ai-chat-chip";
      const isSelected = selected?.payload?.option_key === option.key
        && selected?.payload?.option_value === option.value;
      button.classList.toggle("selected", isSelected);
      button.disabled = Boolean(selected) || loading;
      button.setAttribute("aria-pressed", String(isSelected));
      button.innerHTML = `<strong>${clean(option.label || option.value)}</strong>${option.description ? `<small>${clean(option.description)}</small>` : ""}`;
      button.addEventListener("click", () => {
        if (loading) return;
        if (option.agentAction) {
          runAgentAction(option.agentAction);
          return;
        }
        const label = option.label || option.value;
        sendMessage({
          message: option.key === "apply_suggestion" ? label : "",
          option_key: option.key,
          option_value: option.value,
          option_label: label,
        }, label);
      });
      suggestionsTarget.append(button);
    });
  }

  function runAgentAction(action) {
    if (action === "answer_clarification") {
      input.focus();
      return;
    }
    if (action === "review_draft") {
      if (window.matchMedia("(max-width: 700px)").matches) openCanvas();
      else previewContent.scrollIntoView({behavior: "smooth", block: "start"});
      return;
    }
    if (action === "run_scan") {
      if (!scanButton.disabled) scanButton.click();
      return;
    }
    if (action === "open_monitor") {
      const monitorId = latestAssistantPayload().monitor_status?.monitor_id;
      if (monitorId) window.location.assign(`/dashboard/strategies/${encodeURIComponent(monitorId)}`);
      return;
    }
    if (action === "retry") {
      lastAction?.();
      return;
    }
    if (action === "start_revision") {
      sendMessage({message: "Start a revision"}, "Start a revision");
    }
  }

  function renderConversation() {
    messagesTarget.innerHTML = "";
    const canonicalIds = new Set(
      (chat?.messages || []).map((item) => item.client_message_id).filter(Boolean),
    );
    canonicalIds.forEach((id) => optimisticMessages.delete(id));
    const visible = [...(chat?.messages || []), ...optimisticMessages.values()];
    visible.forEach((item) => messagesTarget.append(renderMessage(item)));
    if (loading) {
      const typing = document.createElement("div");
      typing.className = "ai-chat-message assistant";
      typing.dataset.aiTyping = "true";
      typing.innerHTML = `
        <span class="ai-chat-avatar">${window.icon?.("spark", "icon") || ""}</span>
        <div class="ai-chat-bubble"><span class="ai-chat-typing" aria-label="Assistant is responding"><i></i><i></i><i></i></span></div>`;
      messagesTarget.append(typing);
    }
    renderSuggestions();
    messagesTarget.scrollTop = messagesTarget.scrollHeight;
    statusTarget.textContent = statusLabel(chat?.status);
  }

  function confidenceFor(key) {
    return (chat?.rule_confidence || []).find((item) => item.rule_key === key) || {
      confidence: "medium", score: 0.75, requires_confirmation: false,
    };
  }

  function icon(name) {
    const aliases = {
      "file-search": "search",
      "list-checks": "list",
      "triangle-alert": "alert",
      "wand-sparkles": "spark",
      "shield-check": "compliance",
    };
    return window.icon?.(aliases[name] || name, "icon") || "";
  }

  function renderPreview() {
    const sheet = chat?.translation_sheet || {};
    const hasSheet = Boolean(chat?.draft_strategy && Object.keys(sheet).length);
    previewEmpty.hidden = hasSheet;
    previewContent.hidden = !hasSheet;
    previewActions.hidden = !hasSheet;
    const needsClarification = !["ready_for_approval", "ready_to_scan", "approved"].includes(chat?.status);
    root.querySelector("[data-ai-preview-panel]")?.classList.toggle("needs-clarification", needsClarification);
    previewStatus.textContent = chat?.status === "ready_for_approval"
      ? "Ready to review"
      : chat?.status === "ready_to_scan" ? "Scanner ready"
      : chat?.status === "approved" ? "Approved" : "Needs clarification";
    if (!hasSheet) return;

    const rules = sheet.conditions || [];
    const lint = chat.lint_warnings || [];
    const assumptions = sheet.assumptions || [];
    const reasons = reportReasons(sheet);
    const improvements = latestAssistantPayload().suggestions || [];
    const clauseCoverage = Array.isArray(sheet.clause_coverage) ? sheet.clause_coverage : [];
    const lowRules = (chat.rule_confidence || []).filter((item) => item.requires_confirmation);
    const fallbackFields = [
      {label: "Original idea", value: sheet.original_idea || chat.original_idea},
      {label: "Monitor name", value: sheet.monitor_name},
      {label: "Market", value: `${sheet.exchange || "-"} ${sheet.market_type || "spot"}`},
      {label: "Direction", value: sheet.direction || "both"},
      {label: "Market universe", value: (sheet.symbols_watchlist || []).join(", ") || `All eligible ${(sheet.quote_currencies || []).join(", ")} pairs`},
      {label: "Timeframes", value: (sheet.timeframes || []).join(", ")},
      {label: "Alert timing", value: (sheet.alert_timing?.trigger_mode || "candle_close").replaceAll("_", " ")},
      {label: "Delivery", value: (sheet.delivery_channels || []).join(", ")},
    ];
    const fields = Array.isArray(sheet.fields) && sheet.fields.length ? sheet.fields : fallbackFields;
    previewContent.innerHTML = `
      <section class="ai-sheet-card">
        <h3>${icon("file-search")} Translation sheet</h3>
        <p class="ai-sheet-lead">The chat gives the short explanation. These fields are the exact rule set to review.</p>
        <div class="ai-sheet-grid">
          ${fields.map((field) => `<div class="ai-sheet-field"><span>${clean(field.label)}</span><strong>${clean(field.value ?? "Not provided")}</strong></div>`).join("")}
        </div>
      </section>
      <section class="ai-sheet-card">
        <h3>${icon("list-checks")} Interpreted rules</h3>
        <div class="ai-rule-list">
          ${rules.map((rule) => {
            const confidence = confidenceFor(rule.key);
            return `<article class="ai-rule-row">
              <strong>${clean(rule.name)}</strong>
              <span class="ai-confidence ${clean(confidence.confidence)}">${clean(confidence.confidence)} confidence</span>
              <small>${clean(`${roleLabel(rule.role, rule.required)} · ${rule.timeframe} · ${rule.operator}`)}</small>
              ${confidence.requires_confirmation ? `<label class="ai-low-confirm"><input type="checkbox" data-low-confidence-rule="${clean(rule.key)}"> I confirm this interpretation</label>` : ""}
            </article>`;
          }).join("") || "<p>No executable rules are ready yet.</p>"}
        </div>
      </section>
      ${clauseCoverage.length ? `<section class="ai-sheet-card">
        <h3>${icon("search")} Your wording, accounted for</h3>
        <p class="ai-sheet-lead">Every meaningful phrase must be covered or clearly held for review.</p>
        <div class="ai-clause-coverage-list">
          ${clauseCoverage.map((item) => `<article class="ai-clause-coverage" data-status="${clean(item.status)}">
            <strong>${clean(item.source_fragment || "Instruction")}</strong>
            <span>${clean(String(item.status || "needs review").replaceAll("_", " "))}</span>
            <small>${clean(item.explanation || "Review this instruction.")}</small>
          </article>`).join("")}
        </div>
      </section>` : ""}
      ${reasons.length ? `<section class="ai-sheet-card ai-refusal-report" data-testid="ai-setup-validation-errors">
        <h3>${icon("triangle-alert", "%23f59e0b")} What needs attention</h3>
        <p class="ai-refusal-intro">${reasons.some((item) => item.blocking) ? "Approval is paused until these exact items are resolved." : "These notes are shown once so the rule set stays consistent."}</p>
        <div class="ai-warning-list ai-refusal-reasons">
          ${reasons.map((item, index) => `<article class="ai-warning ai-refusal-reason ${item.blocking ? "critical" : clean(item.severity)}"><span class="ai-attention-number">${index + 1}</span><span class="ai-attention-copy"><small class="ai-attention-category">${clean(item.category)}</small><strong>${clean(item.title)}</strong><p>${clean(item.message)}</p><small><b>Next:</b> ${clean(item.nextStep)}</small></span></article>`).join("")}
        </div>
      </section>` : ""}
      <section class="ai-sheet-card" data-testid="ai-setup-assumptions">
        <h3>${icon("eye")} Assumptions shown openly</h3>
        ${assumptions.length ? `<ul class="ai-assumption-list">${assumptions.map((item) => `<li>${clean(item)}</li>`).join("")}</ul>` : "<p>No hidden assumptions were added.</p>"}
      </section>
      ${improvements.length ? `<section class="ai-sheet-card">
        <h3>${icon("wand-sparkles")} Optional improvements</h3>
        <div class="ai-improvement-list">${improvements.map((item) => `<article><span>${clean(item)}</span><button type="button" data-ai-apply-suggestion="${clean(item)}">Use</button></article>`).join("")}</div>
        <p><small>Suggestions only. Nothing is added unless you ask for it and approve the result.</small></p>
      </section>` : ""}
      <section class="ai-sheet-card ai-translation-feedback">
        <h3>${icon("message")} Did I understand this correctly?</h3>
        <div class="ai-translation-feedback-actions">
          <button type="button" data-ai-translation-feedback="correct">Correct</button>
          <button type="button" data-ai-translation-feedback="partially_correct">Partially correct</button>
          <button type="button" data-ai-translation-feedback="wrong_condition">Wrong condition</button>
          <button type="button" data-ai-translation-feedback="wrong_timeframe">Wrong timeframe</button>
          <button type="button" data-ai-translation-feedback="missing_condition">Missing condition</button>
          <button type="button" data-ai-translation-feedback="wrong_required_optional">Wrong required or optional status</button>
          <button type="button" data-ai-translation-feedback="unnecessary_question">Asked an unnecessary question</button>
        </div>
        <p><small>Feedback becomes review evidence. It never changes production capabilities automatically.</small></p>
      </section>
      <section class="ai-sheet-card">
        <h3>${icon("shield-check")} Safety boundary</h3>
        <p>${clean(sheet.execution || "Deterministic crypto spot monitoring only. No automatic trade execution.")}</p>
      </section>`;
    previewContent.querySelectorAll("[data-low-confidence-rule]").forEach((checkbox) => {
      checkbox.addEventListener("change", updateApprovalState);
    });
    previewContent.querySelectorAll("[data-ai-apply-suggestion]").forEach((button) => {
      button.addEventListener("click", () => {
        const suggestion = button.dataset.aiApplySuggestion;
        sendMessage({message: `Apply: ${suggestion}`}, `Apply: ${suggestion}`);
      });
    });
    previewContent.querySelectorAll("[data-ai-translation-feedback]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await recordInterpretationFeedback(button.dataset.aiTranslationFeedback);
          button.textContent = "Recorded";
        } catch (error) {
          button.disabled = false;
          showError(error);
        }
      });
    });
    approvalNote.textContent = chat?.setup_mode === "scanner"
      ? "Scanner will run this exact temporary rule set. It does not create alerts or a lifecycle."
      : lint.some((item) => item.severity === "critical")
      ? "Resolve critical findings in chat before approval."
      : lowRules.length
        ? "Confirm every low-confidence rule, then approve the exact translation."
        : "Approve the exact translation to create a draft strategy version.";
    updateApprovalState();
  }

  function updateApprovalState() {
    const low = [...previewContent.querySelectorAll("[data-low-confidence-rule]")];
    approveButton.disabled = !chat?.can_approve || low.some((item) => !item.checked) || loading;
    scanButton.hidden = !chat?.can_scan;
    scanButton.disabled = !chat?.can_scan || loading;
    approveButton.hidden = Boolean(chat?.can_scan);
  }

  function roleLabel(role, required) {
    return ({
      primary_trigger: "Primary trigger",
      current_match_condition: "Current-match condition",
      required_filter: "Required filter",
      required_confirmation: "Required confirmation",
      optional_suggestion: "Optional suggestion",
    })[role] || (required ? "Required rule" : "Optional suggestion");
  }

  function render() {
    const evaluationContract = chat?.evaluation_contract || null;
    root.dataset.evaluationContractHash = evaluationContract?.canonical_hash || "";
    root.dataset.canvasContractNodeCount = String(
      evaluationContract?.canvas?.nodes?.length || 0,
    );
    renderConversation();
    renderPreview();
    publishDraftToCanvas();
    updateSendState();
  }

  function publishDraftToCanvas() {
    const detail = {
      strategy: chat?.draft_strategy || null,
      translation: chat?.translation_sheet || {},
      lint: chat?.lint_warnings || [],
      ambiguity: chat?.ambiguities || [],
      setupMode: chat?.setup_mode || null,
      evaluationContract: chat?.evaluation_contract || null,
    };
    window.__traceEdgeChatDraft = detail;
    window.dispatchEvent(new CustomEvent("traceedge:chat-draft", {detail}));
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(150, Math.max(48, input.scrollHeight))}px`;
  }

  function updateSendState() {
    sendButton.disabled = loading || !input.value.trim();
  }

  async function loadOrCreate() {
    clearError();
    setLoading(true, "Restoring your setup conversation");
    try {
      chat = await request("/sessions/current");
      if (!chat) {
        chat = await request("/sessions", {method: "POST", body: "{}"});
      }
      render();
      input.focus();
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
      renderConversation();
    }
  }

  async function sendMessage(payload, displayContent = null) {
    if (loading || !chat) return;
    clearError();
    const requestPayload = {
      ...payload,
      client_message_id: payload.client_message_id || newClientMessageId(),
    };
    const content = displayContent || payload.option_label || payload.message || payload.option_value;
    const existing = optimisticMessages.get(requestPayload.client_message_id);
    optimisticMessages.set(requestPayload.client_message_id, {
      ...(existing || {}),
      role: "user",
      message_type: payload.option_key ? "option" : "text",
      content,
      payload: requestPayload,
      client_message_id: requestPayload.client_message_id,
      created_at: existing?.created_at || new Date().toISOString(),
      pending: true,
      failed: false,
    });
    lastAction = () => sendMessage(requestPayload, content);
    input.value = "";
    resizeInput();
    setLoading(true);
    try {
      chat = await request(`/sessions/${chat.id}/messages`, {
        method: "POST",
        body: JSON.stringify(requestPayload),
      });
      optimisticMessages.delete(requestPayload.client_message_id);
      render();
    } catch (error) {
      const failed = optimisticMessages.get(requestPayload.client_message_id);
      if (failed) optimisticMessages.set(requestPayload.client_message_id, {
        ...failed, pending: false, failed: true,
      });
      showError(error);
    } finally {
      setLoading(false);
      render();
      updateApprovalState();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (message) sendMessage({message});
  });
  input.addEventListener("input", () => { resizeInput(); updateSendState(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!sendButton.disabled) form.requestSubmit();
    }
  });
  root.querySelector("[data-ai-chat-retry]").addEventListener("click", () => lastAction?.());
  root.querySelector("[data-ai-chat-refine]").addEventListener("click", () => {
    input.focus();
    input.placeholder = "Tell me what you want to change in the translated rules...";
  });
  root.querySelector("[data-ai-chat-new]").addEventListener("click", async () => {
    if (chat?.messages?.length > 1 && !window.confirm("Start a new setup chat? This conversation will remain saved.")) return;
    clearError();
    setLoading(true, "Starting a new setup");
    try {
      chat = await request("/sessions", {method: "POST", body: "{}"});
      render();
      input.focus();
    } catch (error) { showError(error); }
    finally { setLoading(false); renderConversation(); }
  });
  approveButton.addEventListener("click", async () => {
    if (approveButton.disabled || !chat) return;
    clearError();
    const confirmed = [...previewContent.querySelectorAll("[data-low-confidence-rule]:checked")]
      .map((item) => item.dataset.lowConfidenceRule);
    lastAction = () => approveButton.click();
    setLoading(true, "Approving the exact rule set");
    try {
      chat = await request(`/sessions/${chat.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          approved: true,
          expected_schema_hash: chat.schema_hash,
          confirmed_low_confidence_rule_keys: confirmed,
        }),
      });
      render();
      if (chat.next_url) window.location.assign(chat.next_url);
    } catch (error) { showError(error); }
    finally { setLoading(false); renderConversation(); }
  });

  scanButton.addEventListener("click", async () => {
    if (scanButton.disabled || !chat) return;
    clearError();
    lastAction = () => scanButton.click();
    setLoading(true, "Scanning the configured market universe");
    try {
      chat = await request(`/sessions/${chat.id}/scan`, {method: "POST", body: "{}"});
      render();
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
      render();
    }
  });

  function openCanvas() {
    const builder = document.querySelector("[data-builder-shell]");
    if (!builder) return;
    root.classList.add("canvas-open");
    document.body.classList.add("ai-canvas-active");
    builder.hidden = false;
    builder.dataset.openCanvas = "true";
    const builderForm = builder.querySelector("#strategy-builder-form");
    if (builderForm) builderForm.hidden = false;
    builder.querySelector("[data-builder-intro]")?.setAttribute("hidden", "");
    builder.querySelectorAll("[data-builder-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.builderPanel !== "canvas";
    });
    document.querySelector('[data-builder-direction="canvas"]')?.click();
    if (openCanvasButton) openCanvasButton.hidden = true;
    if (returnChatButton) returnChatButton.hidden = false;
    if (minimizeChatButton) minimizeChatButton.hidden = false;
    publishDraftToCanvas();
  }

  function returnToChat() {
    const builder = document.querySelector("[data-builder-shell]");
    root.classList.remove("canvas-open");
    document.body.classList.remove("ai-canvas-active");
    document.body.classList.remove("ai-chat-minimized");
    root.classList.remove("assistant-minimized");
    if (builder && !builder.querySelector("[data-strategy-id]")?.dataset.strategyId) builder.hidden = true;
    if (openCanvasButton) openCanvasButton.hidden = false;
    if (returnChatButton) returnChatButton.hidden = true;
    if (minimizeChatButton) minimizeChatButton.hidden = true;
    input.focus();
  }

  openCanvasButton?.addEventListener("click", openCanvas);
  returnChatButton?.addEventListener("click", returnToChat);
  minimizeChatButton?.addEventListener("click", () => {
    const minimized = root.classList.toggle("assistant-minimized");
    document.body.classList.toggle("ai-chat-minimized", minimized);
    minimizeChatButton.setAttribute("aria-expanded", String(!minimized));
    minimizeChatButton.setAttribute("aria-label", minimized ? "Restore assistant" : "Minimize assistant");
    const symbol = minimizeChatButton.querySelector("[data-ai-minimize-symbol]");
    if (symbol) symbol.textContent = minimized ? "+" : "-";
  });

  async function initialize() {
    if (initialized) return;
    initialized = true;
    root.hidden = false;
    await loadOrCreate();
    const requestedMode = new URLSearchParams(window.location.search).get("mode");
    if (
      ["scanner", "monitor"].includes(requestedMode || "")
      && chat?.setup_mode
      && chat.setup_mode !== requestedMode
    ) {
      chat = await request("/sessions", {method: "POST", body: "{}"});
      render();
    }
    if (chat?.setup_mode !== requestedMode && ["scanner", "monitor"].includes(requestedMode || "")) {
      await sendMessage({
        message: "",
        option_key: "setup_mode",
        option_value: requestedMode,
        option_label: requestedMode === "scanner" ? "Scanner" : "Monitor",
      }, requestedMode === "scanner" ? "Scanner" : "Monitor");
    }
    window.setInterval(async () => {
      if (loading || !chat?.id || chat.status !== "building_mechanic") return;
      try {
        const refreshed = await request(`/sessions/${chat.id}`);
        const changed = refreshed.updated_at !== chat.updated_at
          || refreshed.messages?.length !== chat.messages?.length
          || refreshed.status !== chat.status;
        chat = refreshed;
        if (changed) render();
      } catch (_) {
        // Normal message actions surface errors; background status polling stays quiet.
      }
    }, 2500);
  }

  if (!root.hidden) initialize();
})();
