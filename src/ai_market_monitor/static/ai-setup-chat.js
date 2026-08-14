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
  let pendingScanRequestId = null;
  const optimisticMessages = new Map();

  // The identity of the question currently on screen. Every message sent while a
  // question is open carries it: typed answers, option clicks, yes, no, a replacement
  // value, cancel, resume, a brand new request, a mode switch and a retry.
  //
  // Without it the server had to assume a message was written against whatever step is
  // current *now*. A reply typed just before the step advanced — or sent from a tab left
  // open, or from a retry of an older action — then landed on a field the trader was
  // never asked about, silently. The pair is read fresh from the server's own payload on
  // every render, so an old DOM button cannot answer a newer question: it re-reads this
  // at click time, not at render time.
  let activeQuestion = null;

  function readActiveQuestion() {
    const payload = latestAssistantPayload();
    const contract = payload.clarification
      || (Array.isArray(payload.clarifications) ? payload.clarifications[0] : null);
    const questionId = contract?.question_id || contract?.key || null;
    if (!questionId) {
      activeQuestion = null;
      return;
    }
    activeQuestion = {
      questionId,
      stepRevision: Number.isInteger(contract.step_revision) ? contract.step_revision : 0,
      workflowId: contract.workflow_id || null,
      canonicalValues: Array.isArray(contract.canonical_values) ? contract.canonical_values : [],
    };
  }

  // Attached at send time, never captured when a button was drawn. A chip rendered under
  // step one and clicked after step two arrived therefore carries step two's identity and
  // is refused by the server, instead of quietly answering the wrong field.
  function withQuestionIdentity(payload) {
    if (!activeQuestion) return payload;
    return {
      ...payload,
      question_id: activeQuestion.questionId,
      step_revision: activeQuestion.stepRevision,
    };
  }

  const normalizedMessageText = (value) => String(value ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();

  function dedupeVisibleMessages(items) {
    const deduped = [];
    const seenMessageIds = new Set();
    for (const item of items) {
      const messageId = item.id || item.message_id || null;
      if (messageId && seenMessageIds.has(messageId)) continue;

      const previous = deduped[deduped.length - 1];
      const assistantPair = previous?.role === "assistant" && item.role === "assistant";
      if (assistantPair) {
        const previousFingerprint = previous.payload?.response_fingerprint || "";
        const currentFingerprint = item.payload?.response_fingerprint || "";
        const sameFingerprint = Boolean(
          previousFingerprint && currentFingerprint
          && previousFingerprint === currentFingerprint,
        );
        const sameFallback = !previousFingerprint && !currentFingerprint
          && previous.message_type === item.message_type
          && normalizedMessageText(previous.content) === normalizedMessageText(item.content);
        if (sameFingerprint || sameFallback) continue;
      }

      if (messageId) seenMessageIds.add(messageId);
      deduped.push(item);
    }
    return deduped;
  }

  const newClientMessageId = () => globalThis.crypto?.randomUUID?.()
    || `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const clean = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  // Persisted assistant records remain auditable. The current product name is
  // adapted only when we render assistant content in the Hilal Markets UI.
  const brandText = (value) => String(value ?? "").replace(/\bTraceEdge\b/gi, "Hilal Markets");

  async function request(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    // Read the body once, then decide what it was. A reply that cannot be read is not
    // a success, whatever status came with it.
    //
    // This used to swallow the parse failure and return `{}` on any 2xx. A proxy error
    // page, a captive portal, or a truncated response therefore replaced the whole
    // conversation with an empty object: the message the person had just sent
    // disappeared from the screen with no error, the conversation id was gone, and
    // every later send addressed `/sessions/undefined/messages`. The turn had not
    // succeeded — it had not been read.
    //
    // An empty body is left alone. Some endpoints answer with nothing on purpose, and
    // treating "no content" as "unreadable content" would turn those into failures.
    const raw = await response.text();
    let payload = {};
    let unreadable = false;
    if (raw.trim()) {
      try { payload = JSON.parse(raw); } catch (_) { unreadable = true; payload = {}; }
    }
    if (response.ok && unreadable) {
      const error = new Error(
        "The reply could not be read, so nothing was changed. Please try again.",
      );
      error.status = response.status;
      error.payload = null;
      error.retryable = true;
      throw error;
    }
    if (!response.ok) {
      const detail = payload.detail;
      const message = typeof detail === "object"
        ? detail.message || detail.code
        : detail || payload.error?.message;
      const error = new Error(message || "The request could not be completed.");
      error.status = response.status;
      error.payload = payload?.id ? payload : null;
      // Whether trying again could possibly help. The server already knows — a busy
      // platform will clear, an exhausted daily allowance will not — and telling somebody
      // to "retry below" when the answer can only be the same is how a beginner ends up
      // clicking a dead button instead of using the Builder that still works.
      //
      // The server says so in two shapes: a turn envelope carries `error.retryable`, an
      // ordinary refusal carries it on the detail. Read both, and only fall back to the
      // status code when neither said anything.
      const stated = payload?.error?.retryable ?? (
        typeof detail === "object" && detail !== null ? detail.retryable : undefined
      );
      error.retryable = stated === undefined
        ? response.status >= 500 || response.status === 429
        : stated !== false;
      if (payload?.error?.message) error.message = payload.error.message;
      throw error;
    }
    return payload;
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
    sendButton.disabled = !canSend();
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
    const wrapper = window.HilalChatMessageRenderer.render(item, {
      variant: "setup",
      clean,
      transformAssistant: brandText,
    });
    if (!user) wrapper.dataset.testid = "ai-setup-assistant-message";
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
    const clarificationOptions = clarifications.flatMap((item) => {
      const serverOptions = Array.isArray(item.options) ? item.options : [];
      if (serverOptions.length) {
        return serverOptions.map((option) => ({
          ...option,
          key: option.key || item.key,
        }));
      }
      // One generic control for every clarification option there is. These used to post
      // the button's visible *label* as an ordinary chat message, so a reworded or
      // translated label silently stopped answering its own question. The canonical value
      // travels now; the label is only what the trader reads.
      const canonical = Array.isArray(item.canonical_values) ? item.canonical_values : [];
      const shown = Array.isArray(item.allowed_options) ? item.allowed_options : [];
      return shown.map((label, index) => ({
        key: "clarification_answer",
        value: canonical[index] ?? label,
        label,
      }));
    });
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
    // Read the identity of the question now on screen before anything is drawn, so every
    // control created below sends the current step rather than the one it was born under.
    readActiveQuestion();
    messagesTarget.innerHTML = "";
    const canonicalIds = new Set(
      (chat?.messages || []).map((item) => item.client_message_id).filter(Boolean),
    );
    canonicalIds.forEach((id) => optimisticMessages.delete(id));
    const visible = dedupeVisibleMessages([
      ...(chat?.messages || []),
      ...optimisticMessages.values(),
    ]);
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
    renderPendingChange();
    renderLastChange();
    renderHistoryActions();
    renderDegradedNotice();
    renderGuidedBuilder();
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

  // A turn the server says is still running. While this is set the composer is closed:
  // the server would refuse a second message anyway, and letting the user type into a
  // box that cannot send is how a double-click became two paid turns.
  function activeTurn() {
    const state = chat?.turn_state;
    return state && state.active ? state : null;
  }

  // The one rule for whether pressing Send does anything at all.
  //
  // It used to be written twice, in `setLoading` and in `updateSendState`, and
  // neither copy carried the condition `sendMessage` checks before anything else:
  // that the conversation exists. Until it has loaded, `chat` is null. The button
  // was enabled the moment the person typed a character, and pressing it returned
  // silently — their words left the box, nothing was sent, and no reason was given.
  // A beginner reads that as "the product ignored me".
  //
  // So the button is disabled exactly when a send would refuse, and both callers ask
  // this one function instead of each keeping a partial copy of the answer.
  function canSend() {
    if (!chat || loading) return false;
    if (activeTurn()) return false;
    if (chat?.ai_availability?.assistant_available === false) return false;
    return Boolean(input.value.trim());
  }

  function updateSendState() {
    const running = activeTurn();
    // The assistant being unavailable closes the composer and nothing else. The guided
    // fields stay live underneath, so the person carries on rather than being stuck.
    const assistantOff = chat?.ai_availability?.assistant_available === false;
    sendButton.disabled = !canSend();
    input.disabled = Boolean(running) || assistantOff;
    root.dataset.turnActive = running ? "true" : "false";
    root.dataset.turnStage = running?.stage || "";
    if (assistantOff && !running) {
      input.placeholder = "The assistant is unavailable — use the guided fields below.";
      return;
    }
    if (running) {
      // Said in plain words, and it explicitly tells the user not to send again. A
      // second send would be refused, but the refusal reads like a failure.
      input.placeholder = running.slow
        ? "This is taking longer than usual. Your message is safe — no need to send it again."
        : "Working on your last message...";
    } else if (input.placeholder.startsWith("Working on") || input.placeholder.startsWith("This is taking")) {
      input.placeholder = "";
    }
  }

  // Undo, Restore, Reset, and answering a pending change. Each is a server-owned
  // operation with its own idempotency key, so a double-click acts once.
  async function sendDraftAction(action, extra = {}) {
    if (loading || !chat) return;
    clearError();
    const body = {
      action,
      client_message_id: newClientMessageId(),
      ...extra,
    };
    lastAction = () => request(`/sessions/${chat.id}/draft-actions`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then((updated) => { chat = updated; render(); });
    setLoading(true, draftActionLabel(action));
    try {
      chat = await request(`/sessions/${chat.id}/draft-actions`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      render();
    } catch (error) {
      if (error.payload?.id) { chat = error.payload; render(); }
      showError(error);
    } finally {
      setLoading(false);
      render();
      updateApprovalState();
    }
  }

  function draftActionLabel(action) {
    return {
      undo_last_material_change: "Undoing your last change",
      restore_snapshot: "Putting back that version",
      reset_current_draft: "Clearing this setup",
      confirm_pending_change: "Applying the change",
      cancel_pending_change: "Cancelling the change",
    }[action] || "Working on it";
  }

  // Everything below renders the server's own before/after record. None of it reads the
  // assistant's sentence: a turn that replaced every rule would describe itself as an
  // update, and the user would only find out later.
  function diffLines(diff) {
    if (!diff) return [];
    const lines = [];
    for (const item of diff.removed_conditions || []) {
      lines.push(`Removes: ${item.summary || item.condition_id}`);
    }
    for (const item of diff.added_conditions || []) {
      lines.push(`Adds: ${item.summary || item.condition_id}`);
    }
    for (const item of diff.changed_fields || []) {
      const from = item.before ?? "not set";
      const to = item.after ?? "not set";
      lines.push(`${item.label || "Changes"}: ${from} → ${to}`);
    }
    if (diff.boolean_topology_changed) {
      lines.push(
        `Rules now combine with ${diff.boolean_topology_after} instead of ${diff.boolean_topology_before}`,
      );
    }
    for (const item of diff.methodology_changes || []) {
      lines.push(`Sharia screening method: ${item.before ?? "not set"} → ${item.after ?? "not set"}`);
    }
    for (const item of diff.universe_changes || []) {
      lines.push(`Coins watched: ${item.label} ${item.after ?? item.before ?? ""}`.trim());
    }
    for (const item of diff.market_scope_changes || []) {
      lines.push(`${item.label}: ${item.before ?? "not set"} → ${item.after ?? "not set"}`);
    }
    if (diff.approval_invalidated) lines.push("Your approval is cleared, so it needs approving again.");
    return lines;
  }

  function renderPendingChange() {
    const target = root.querySelector("[data-ai-pending-change]");
    if (!target) return;
    const pending = chat?.pending_change || null;
    target.hidden = !pending;
    target.innerHTML = "";
    if (!pending) return;
    target.dataset.testid = "ai-pending-change";
    target.dataset.proposalId = pending.proposal_id;

    const heading = document.createElement("p");
    heading.className = "ai-pending-change__title";
    heading.textContent = pending.stale
      ? "Your setup changed, so this change was not applied."
      : "This is a big change. Please check it before I make it.";
    target.append(heading);

    const list = document.createElement("ul");
    list.className = "ai-pending-change__list";
    for (const line of [...(pending.summary_lines || []), ...diffLines(pending.diff)]) {
      const entry = document.createElement("li");
      entry.textContent = line;
      list.append(entry);
    }
    for (const note of pending.governance_notes || []) {
      const entry = document.createElement("li");
      entry.className = "ai-pending-change__note";
      entry.textContent = note;
      list.append(entry);
    }
    target.append(list);

    const actions = document.createElement("div");
    actions.className = "ai-pending-change__actions";
    if (!pending.stale) {
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = "btn btn-primary";
      confirm.dataset.testid = "ai-pending-confirm";
      confirm.textContent = "Yes, make this change";
      confirm.addEventListener("click", () => sendDraftAction("confirm_pending_change", {
        proposal_id: pending.proposal_id,
        confirmed: true,
      }));
      actions.append(confirm);
    }
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn-secondary";
    cancel.dataset.testid = "ai-pending-cancel";
    cancel.textContent = pending.stale ? "Close" : "No, keep it as it is";
    cancel.addEventListener("click", () => sendDraftAction("cancel_pending_change", {
      proposal_id: pending.proposal_id,
    }));
    actions.append(cancel);
    target.append(actions);
  }

  function renderHistoryActions() {
    const target = root.querySelector("[data-ai-draft-history]");
    if (!target) return;
    target.innerHTML = "";
    const snapshots = Array.isArray(chat?.snapshots) ? chat.snapshots : [];
    const canUndo = Boolean(chat?.can_undo);
    target.hidden = !canUndo && snapshots.length < 2;
    if (target.hidden) return;

    if (canUndo) {
      const undo = document.createElement("button");
      undo.type = "button";
      undo.className = "btn btn-ghost";
      undo.dataset.testid = "ai-undo";
      undo.textContent = "Undo last change";
      undo.disabled = Boolean(activeTurn());
      undo.addEventListener("click", () => sendDraftAction("undo_last_material_change"));
      target.append(undo);
    }

    if (snapshots.length > 1) {
      const picker = document.createElement("select");
      picker.className = "ai-draft-history__picker";
      picker.dataset.testid = "ai-restore-picker";
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Go back to a saved version...";
      picker.append(blank);
      for (const item of snapshots) {
        if (item.is_current) continue;
        const option = document.createElement("option");
        option.value = item.snapshot_id;
        option.dataset.executableVersion = String(item.executable_version);
        option.textContent = `Version ${item.executable_version} — ${(item.summary_lines || []).join(", ")}`;
        picker.append(option);
      }
      picker.addEventListener("change", () => {
        const chosen = picker.selectedOptions[0];
        if (!chosen?.value) return;
        sendDraftAction("restore_snapshot", {
          snapshot_id: chosen.value,
          expected_executable_version: Number(chosen.dataset.executableVersion),
          confirmed: true,
        });
        picker.value = "";
      });
      target.append(picker);
    }

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn btn-ghost";
    reset.dataset.testid = "ai-reset-draft";
    reset.textContent = "Clear this setup";
    reset.disabled = Boolean(activeTurn());
    reset.addEventListener("click", () => {
      // Clearing loses work, so it asks first. Saved versions and any approved setup
      // are untouched, and the question says so rather than leaving the user to guess.
      const proceed = window.confirm(
        "Clear this setup and start again? Your saved versions and any approved setup are kept.",
      );
      if (proceed) sendDraftAction("reset_current_draft", {confirmed: true});
    });
    target.append(reset);
  }

  function renderLastChange() {
    const target = root.querySelector("[data-ai-last-change]");
    if (!target) return;
    const lines = diffLines(chat?.last_diff);
    target.hidden = lines.length === 0;
    target.innerHTML = "";
    if (!lines.length) return;
    target.dataset.testid = "ai-last-change";
    const list = document.createElement("ul");
    for (const line of lines) {
      const entry = document.createElement("li");
      entry.textContent = line;
      list.append(entry);
    }
    target.append(list);
  }

  // ---------------------------------------------------------------------------
  // The Guided Watch Plan Builder.
  //
  // This is the product path, not a fallback. Every field below is drawn from the
  // server's own contract — the timeframes, the comparisons, the ranges, which rules
  // exist at all. Nothing here decides what is valid; it only shows what the server
  // said and sends back what was chosen. That is what makes the Builder work with the
  // assistant switched off, and what stops the form offering something the compiler
  // would refuse.
  // ---------------------------------------------------------------------------

  let builderContract = null;
  let openRuleForm = null;

  async function loadBuilderContract() {
    if (builderContract) return builderContract;
    try {
      builderContract = await request("/builder-contract");
    } catch (error) {
      // The Builder cannot draw itself without the contract. It says so rather than
      // guessing at a form, because a guessed form builds a rule nobody chose.
      builderContract = null;
      showError(error);
    }
    return builderContract;
  }

  function mechanicByKey(key) {
    return (builderContract?.mechanics || []).find((item) => item.key === key) || null;
  }

  async function sendBuilderAction(action, extra = {}) {
    if (loading || !chat) return;
    clearError();
    const body = {action, client_message_id: newClientMessageId(), ...extra};
    const send = () => request(`/sessions/${chat.id}/builder-actions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    lastAction = () => send().then((updated) => { chat = updated; render(); });
    setLoading(true, builderActionLabel(action));
    try {
      chat = await send();
      openRuleForm = null;
      render();
      // The selected list and method live on the draft this action just changed, so the
      // options are re-read rather than assumed. Assuming would leave the previous
      // choice highlighted after a change, which is worse than not highlighting at all.
      if (UNIVERSE_ACTIONS.has(action)) {
        await loadUniverseOptions();
      }
    } catch (error) {
      if (error.payload?.id) { chat = error.payload; render(); }
      showError(error);
    } finally {
      setLoading(false);
      render();
      updateApprovalState();
    }
  }

  //: Actions that change what the "which coins" step should show.
  const UNIVERSE_ACTIONS = new Set([
    "select_universe",
    "select_watchlist",
    "select_methodology",
    "set_explicit_assets",
  ]);

  function builderActionLabel(action) {
    return {
      select_mode: "Saving what to build",
      rename_plan: "Saving the name",
      select_universe: "Saving which coins",
      select_watchlist: "Saving your list",
      set_explicit_assets: "Saving your coins",
      select_methodology: "Saving the screening method",
      add_condition: "Adding your rule",
      update_condition: "Saving your rule",
      remove_condition: "Removing that rule",
      arrange_conditions: "Rearranging your rules",
      group_conditions: "Grouping those rules",
      ungroup_conditions: "Removing that grouping",
      set_group_operator: "Changing how that group works",
      move_condition: "Moving that rule",
      apply_starter: "Setting up your starting point",
    }[action] || "Saving";
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function chipButton(label, explanation, selected, onClick) {
    const button = element("button", "gb-chip", label);
    button.type = "button";
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    if (selected) button.classList.add("is-selected");
    if (explanation) button.title = explanation;
    button.addEventListener("click", onClick);
    return button;
  }

  //: Below this width the AI Sheet beside the chat is not shown at all.
  const PHONE_WIDTH_PX = 700;

  /** Put the guided fields where this screen can actually show them.
   *
   * They belong beside the chat on a wide screen. On a phone that column is hidden,
   * and the guided fields were hidden with it — so the one route that still works
   * when the assistant is unavailable was missing on exactly the device most likely
   * to have a poor connection. On a phone they move under the conversation instead.
   *
   * Moving rather than duplicating. Two copies of the same fields would each hold
   * their own idea of the draft, and the person would edit the one that is not read.
   */
  function placeGuidedBuilder(target) {
    const phone = window.matchMedia(`(max-width: ${PHONE_WIDTH_PX}px)`).matches;
    const slot = root.querySelector(`[data-ai-guided-slot="${phone ? "phone" : "wide"}"]`);
    if (slot && target.parentElement !== slot) slot.append(target);
  }

  function renderGuidedBuilder() {
    const target = root.querySelector("[data-ai-guided-builder]");
    if (!target) return;
    placeGuidedBuilder(target);
    const state = chat?.builder;
    const availability = chat?.ai_availability || {};
    target.hidden = !state || availability.builder === false;
    if (target.hidden) return;
    target.innerHTML = "";
    target.dataset.testid = "guided-builder";
    target.dataset.lifecycle = chat?.lifecycle?.state || "";

    target.append(renderBuilderSteps(state));
    target.append(renderBuilderMode(state));
    target.append(renderBuilderAssets(state));
    target.append(renderBuilderRules(state));
    if ((state.conditions || []).length > 1) target.append(renderBuilderLogic(state));
    target.append(renderBuilderReview(state));
  }

  function renderBuilderSteps(state) {
    const box = element("nav", "gb-steps");
    box.setAttribute("aria-label", "Setup steps");
    box.dataset.testid = "guided-builder-steps";
    for (const step of state.steps || []) {
      const item = element("span", "gb-step", step.label);
      item.dataset.step = step.key;
      item.dataset.complete = step.complete ? "true" : "false";
      if (step.todo) item.title = step.todo;
      box.append(item);
    }
    return box;
  }

  function section(title, hint) {
    const box = element("section", "gb-section");
    box.append(element("h3", "gb-section-title", title));
    if (hint) box.append(element("p", "gb-section-hint", hint));
    return box;
  }

  function renderBuilderMode(state) {
    const box = section("What should this do?", null);
    box.dataset.testid = "guided-builder-mode";
    const row = element("div", "gb-chip-row");
    for (const choice of builderContract?.modes || []) {
      row.append(chipButton(
        choice.label,
        choice.explanation,
        state.mode === choice.value,
        () => sendBuilderAction("select_mode", {value: choice.value}),
      ));
    }
    box.append(row);
    return box;
  }

  //: The governed answers for "which coins" and "under which method", read from the
  //  server. Never a list written in this file: a methodology id hard-coded here would
  //  be a Sharia decision made in JavaScript.
  let universeOptions = null;

  async function loadUniverseOptions() {
    if (!chat?.id) return null;
    try {
      universeOptions = await request(`/sessions/${chat.id}/universe-options`);
    } catch (error) {
      // A failed read must not blank the step. The choices simply stay as they were,
      // and the person is told rather than shown an empty list they cannot act on.
      universeOptions = universeOptions || {load_failed: true};
    }
    return universeOptions;
  }

  function renderBuilderAssets(state) {
    const box = section(
      "Which coins should it watch?",
      state.universe_summary || "Pick the coins this should look at.",
    );
    box.dataset.testid = "guided-builder-assets";
    const row = element("div", "gb-chip-row");
    for (const choice of builderContract?.universes || []) {
      row.append(chipButton(
        choice.label,
        choice.explanation,
        state.universe_mode === choice.value,
        () => sendBuilderAction("select_universe", {value: choice.value}),
      ));
    }
    box.append(row);

    for (const notice of universeOptions?.notices || []) {
      box.append(element("p", "gb-section-hint", notice));
    }
    if (universeOptions?.load_failed) {
      box.append(element(
        "p",
        "gb-section-hint",
        "Your lists and screening methods could not be loaded just now. Reload to try again.",
      ));
    }

    if (state.universe_mode === "approved_watchlist") {
      box.append(renderWatchlistPicker());
    }
    box.append(renderMethodologyPicker(state));

    if (state.universe_mode === "explicit_assets") {
      const field = element("div", "gb-inline-field");
      const entry = document.createElement("input");
      entry.type = "text";
      entry.placeholder = "BTC, ETH";
      entry.setAttribute("aria-label", "Coins to watch");
      const save = element("button", "gb-button", "Save coins");
      save.type = "button";
      save.addEventListener("click", () => {
        if (entry.value.trim()) {
          sendBuilderAction("set_explicit_assets", {value: entry.value.trim()});
        }
      });
      field.append(entry, save);
      box.append(field);
      const chosen = universeOptions?.explicit_assets || [];
      if (chosen.length) {
        box.append(element(
          "p",
          "gb-section-hint",
          `Watching: ${chosen.join(", ")}. These are still screened before anything runs.`,
        ));
      }
    }
    return box;
  }

  function renderWatchlistPicker() {
    const box = element("div", "gb-subsection");
    box.dataset.testid = "guided-builder-watchlists";
    box.append(element("p", "gb-field-label", "Which of your lists?"));
    const lists = universeOptions?.watchlists || [];
    if (!lists.length) {
      box.append(element(
        "p",
        "gb-section-hint",
        "You have no Favorites lists yet. Make one from Halal Assets, "
        + "or choose every eligible coin instead.",
      ));
      return box;
    }
    const row = element("div", "gb-chip-row");
    for (const list of lists) {
      const count = list.asset_count === 1 ? "1 coin" : `${list.asset_count} coins`;
      row.append(chipButton(
        `${list.name} (${count})`,
        list.empty_reason || (list.is_default ? "Your default list." : null),
        Boolean(list.selected),
        () => sendBuilderAction("select_watchlist", {value: list.watchlist_id}),
      ));
    }
    box.append(row);
    return box;
  }

  function renderMethodologyPicker(state) {
    const box = element("div", "gb-subsection");
    box.dataset.testid = "guided-builder-methodology";
    box.append(element("p", "gb-field-label", "Which screening method?"));
    const methods = universeOptions?.methodologies || [];
    if (!methods.length) {
      box.append(element(
        "p",
        "gb-section-hint",
        state.methodology_summary
        || "No screening method is published yet, so nothing can be monitored until one is.",
      ));
      return box;
    }
    const row = element("div", "gb-chip-row");
    for (const method of methods) {
      row.append(chipButton(
        method.label,
        method.explanation,
        Boolean(method.selected),
        () => sendBuilderAction("select_methodology", {value: method.methodology_id}),
      ));
    }
    box.append(row);
    // Who stands behind the chosen method. Shown because a screening result means
    // nothing without the authority and the date attached to it.
    const chosen = methods.find((item) => item.selected);
    if (chosen) {
      const parts = [];
      if (chosen.governing_body) parts.push(chosen.governing_body);
      if (chosen.reviewer_group) parts.push(`reviewed by ${chosen.reviewer_group}`);
      if (chosen.effective_from) parts.push(`in effect from ${chosen.effective_from}`);
      if (parts.length) box.append(element("p", "gb-section-hint", `${parts.join(" · ")}.`));
    }
    return box;
  }

  function renderBuilderRules(state) {
    const box = section(
      "What should it watch for?",
      "Each rule is one thing to look for in the market.",
    );
    box.dataset.testid = "guided-builder-rules";
    const list = element("ul", "gb-rule-list");
    const conditions = state.conditions || [];
    conditions.forEach((condition, index) => {
      list.append(renderRuleCard(condition, index, conditions.length));
    });
    box.append(list);

    if (!conditions.length) {
      const starters = element("div", "gb-starters");
      starters.dataset.testid = "guided-builder-starters";
      starters.append(element("p", "gb-section-hint", "Not sure? Start from one of these."));
      for (const starter of builderContract?.starters || []) {
        const button = element("button", "gb-starter", starter.label);
        button.type = "button";
        button.title = starter.explanation;
        button.addEventListener(
          "click",
          () => sendBuilderAction("apply_starter", {value: starter.key}),
        );
        starters.append(button);
      }
      box.append(starters);
    }

    const add = element("button", "gb-button gb-button-primary", "Add a rule");
    add.type = "button";
    add.dataset.testid = "guided-builder-add-rule";
    add.addEventListener("click", () => {
      openRuleForm = {mode: "add", mechanicKey: null, values: {}};
      renderGuidedBuilder();
    });
    box.append(add);
    if (openRuleForm) box.append(renderRuleForm());
    return box;
  }

  function renderRuleCard(condition, index, total) {
    const item = element("li", "gb-rule");
    item.dataset.nodeId = condition.node_id;
    item.dataset.testid = "guided-builder-rule";
    item.append(element("p", "gb-rule-text", condition.sentence));
    const meta = element("p", "gb-rule-meta", condition.label);
    item.append(meta);

    const actions = element("div", "gb-rule-actions");
    if (condition.editable) {
      const edit = element("button", "gb-button", "Edit");
      edit.type = "button";
      edit.addEventListener("click", () => {
        openRuleForm = {
          mode: "edit",
          nodeId: condition.node_id,
          mechanicKey: condition.mechanic_key,
          values: {...condition.values},
        };
        renderGuidedBuilder();
      });
      actions.append(edit);

      // Duplicating opens the same form filled in. An exact copy of a rule always
      // fires with the original, so the platform refuses one — the useful thing is a
      // near copy the person then changes.
      const copy = element("button", "gb-button", "Duplicate");
      copy.type = "button";
      copy.addEventListener("click", () => {
        openRuleForm = {
          mode: "add",
          mechanicKey: condition.mechanic_key,
          values: {...condition.values},
        };
        renderGuidedBuilder();
      });
      actions.append(copy);
    } else if (condition.not_editable_reason) {
      actions.append(element("span", "gb-rule-note", condition.not_editable_reason));
    }

    const remove = element("button", "gb-button gb-button-quiet", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (window.confirm(`Remove this rule?\n\n${condition.sentence}`)) {
        sendBuilderAction("remove_condition", {node_id: condition.node_id});
      }
    });
    actions.append(remove);

    if (total > 1) {
      const up = element("button", "gb-button gb-button-quiet", "Move up");
      up.type = "button";
      up.disabled = index === 0;
      up.addEventListener("click", () => moveRule(index, index - 1));
      const down = element("button", "gb-button gb-button-quiet", "Move down");
      down.type = "button";
      down.disabled = index === total - 1;
      down.addEventListener("click", () => moveRule(index, index + 1));
      actions.append(up, down);
    }
    item.append(actions);
    return item;
  }

  function moveRule(from, to) {
    const order = (chat?.builder?.conditions || []).map((item) => item.node_id);
    if (to < 0 || to >= order.length) return;
    const [moved] = order.splice(from, 1);
    order.splice(to, 0, moved);
    sendBuilderAction("arrange_conditions", {
      order,
      join: chat?.builder?.join || "and",
    });
  }

  function renderRuleForm() {
    const box = element("form", "gb-rule-form");
    box.dataset.testid = "guided-builder-rule-form";
    box.addEventListener("submit", (event) => event.preventDefault());

    const picker = document.createElement("select");
    picker.setAttribute("aria-label", "Kind of rule");
    picker.dataset.testid = "guided-builder-mechanic";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose what to watch for…";
    picker.append(blank);
    for (const mechanic of builderContract?.mechanics || []) {
      const option = document.createElement("option");
      option.value = mechanic.key;
      option.textContent = mechanic.available
        ? mechanic.label
        : `${mechanic.label} — not available yet`;
      option.disabled = !mechanic.available;
      option.selected = mechanic.key === openRuleForm.mechanicKey;
      picker.append(option);
    }
    picker.addEventListener("change", () => {
      openRuleForm.mechanicKey = picker.value || null;
      openRuleForm.values = {};
      renderGuidedBuilder();
    });
    box.append(picker);

    const mechanic = mechanicByKey(openRuleForm.mechanicKey);
    if (mechanic) {
      box.append(element("p", "gb-section-hint", mechanic.explanation));
      if (!mechanic.available && mechanic.unavailable_reason) {
        box.append(element("p", "gb-rule-note", mechanic.unavailable_reason));
      }
      for (const parameter of mechanic.parameters) {
        box.append(renderParameterField(mechanic, parameter));
      }
      const save = element(
        "button",
        "gb-button gb-button-primary",
        openRuleForm.mode === "edit" ? "Save this rule" : "Add this rule",
      );
      save.type = "button";
      save.dataset.testid = "guided-builder-save-rule";
      save.addEventListener("click", () => {
        const payload = {
          mechanic_key: mechanic.key,
          values: {...openRuleForm.values},
        };
        if (openRuleForm.mode === "edit") {
          sendBuilderAction("update_condition", {...payload, node_id: openRuleForm.nodeId});
        } else {
          sendBuilderAction("add_condition", payload);
        }
      });
      box.append(save);
    }

    const cancel = element("button", "gb-button gb-button-quiet", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      openRuleForm = null;
      renderGuidedBuilder();
    });
    box.append(cancel);
    return box;
  }

  function renderParameterField(mechanic, parameter) {
    const wrap = element("label", "gb-field");
    wrap.append(element("span", "gb-field-label", parameter.label));
    if (parameter.help) wrap.append(element("span", "gb-field-help", parameter.help));
    const current = openRuleForm.values[parameter.name] ?? parameter.default ?? "";

    if (parameter.kind === "choice" || parameter.kind === "timeframe") {
      const select = document.createElement("select");
      select.dataset.parameter = parameter.name;
      if (!parameter.required) {
        const none = document.createElement("option");
        none.value = "";
        none.textContent = "Not set";
        select.append(none);
      }
      for (const choice of parameter.choices) {
        const option = document.createElement("option");
        option.value = choice.value;
        option.textContent = choice.label;
        if (choice.explanation) option.title = choice.explanation;
        option.selected = String(current) === choice.value;
        select.append(option);
      }
      select.addEventListener("change", () => {
        openRuleForm.values[parameter.name] = select.value;
      });
      openRuleForm.values[parameter.name] = select.value;
      wrap.append(select);
    } else if (parameter.kind === "boolean") {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.parameter = parameter.name;
      box.checked = Boolean(current);
      box.addEventListener("change", () => {
        openRuleForm.values[parameter.name] = box.checked;
      });
      wrap.append(box);
    } else {
      const field = document.createElement("input");
      field.type = parameter.kind === "text" ? "text" : "number";
      field.dataset.parameter = parameter.name;
      if (parameter.minimum !== null && parameter.minimum !== undefined) {
        field.min = String(parameter.minimum);
      }
      if (parameter.maximum !== null && parameter.maximum !== undefined) {
        field.max = String(parameter.maximum);
      }
      if (parameter.step) field.step = String(parameter.step);
      field.value = current === null ? "" : String(current);
      if (parameter.unit && parameter.unit !== "none") {
        field.setAttribute("aria-describedby", `unit-${parameter.name}`);
        wrap.append(element("span", "gb-field-unit", unitWord(parameter.unit)));
      }
      field.addEventListener("input", () => {
        openRuleForm.values[parameter.name] = field.value;
      });
      if (field.value !== "") openRuleForm.values[parameter.name] = field.value;
      wrap.append(field);
    }
    return wrap;
  }

  function unitWord(unit) {
    return {
      percent: "%",
      price: "price",
      count: "candles",
      multiple: "×",
      timeframe: "",
    }[unit] || "";
  }

  //: Which rules the person has ticked for grouping. Kept out of the draft on purpose:
  //  a selection is not a change to the strategy, and storing it would put a scratch
  //  value on the object every approval hash is taken over.
  let groupSelection = new Set();

  function logicLabel(operator) {
    const choice = (builderContract?.logic || []).find((item) => item.value === operator);
    return choice ? choice.label : operator;
  }

  function conditionLabel(state, nodeId) {
    const found = (state.conditions || []).find((item) => item.node_id === nodeId);
    return found?.summary || found?.rendered || "This rule";
  }

  function renderBuilderLogic(state) {
    const box = section(
      "How do these rules go together?",
      "Tick two or more rules to put them in a group. A group can sit inside another one.",
    );
    box.dataset.testid = "guided-builder-logic";

    const rows = state.structure || [];
    if (!rows.length) {
      // No stored shape yet: fall back to the flat join so the step is never empty.
      const row = element("div", "gb-chip-row");
      for (const choice of (builderContract?.logic || []).filter((i) => i.value !== "not")) {
        row.append(chipButton(
          choice.label,
          choice.explanation,
          (state.join || "and") === choice.value,
          () => sendBuilderAction("arrange_conditions", {
            order: (state.conditions || []).map((item) => item.node_id),
            join: choice.value,
          }),
        ));
      }
      box.append(row);
      return box;
    }

    groupSelection = new Set(
      [...groupSelection].filter((id) => rows.some((row) => row.node_id === id)),
    );

    box.append(renderLogicTree(state, rows));
    box.append(renderGroupControls(state, rows));
    return box;
  }

  function renderLogicTree(state, rows) {
    const tree = element("div", "gb-logic-tree");
    tree.dataset.testid = "guided-builder-logic-tree";
    for (const row of rows) {
      tree.append(renderLogicRow(state, row));
    }
    return tree;
  }

  function renderLogicRow(state, row) {
    const line = element("div", "gb-logic-row");
    line.dataset.nodeId = row.node_id;
    line.dataset.kind = row.kind;
    line.dataset.depth = String(row.depth);
    // Indentation is the whole point of the view: it is how a person sees that one rule
    // sits inside a group rather than beside it.
    line.style.marginInlineStart = `${(row.depth - 1) * 18}px`;

    if (row.kind === "group") {
      line.append(element("span", "gb-logic-badge", logicLabel(row.operator)));
      const controls = element("div", "gb-chip-row");
      for (const choice of builderContract?.logic || []) {
        const arity = builderContract?.boolean_limits?.arity?.[choice.value];
        const max = Array.isArray(arity) ? arity[1] : null;
        if (max !== null && max !== undefined && row.child_ids.length > max) continue;
        controls.append(chipButton(
          choice.label,
          choice.explanation,
          row.operator === choice.value,
          () => sendBuilderAction("set_group_operator", {
            group_id: row.node_id,
            operator: choice.value,
          }),
        ));
      }
      if (row.parent_id) {
        const remove = element("button", "gb-chip gb-chip-quiet", "Remove grouping");
        remove.type = "button";
        remove.addEventListener("click", () =>
          sendBuilderAction("ungroup_conditions", { group_id: row.node_id }));
        controls.append(remove);
      }
      line.append(controls);
      return line;
    }

    const label = element("label", "gb-logic-rule");
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = groupSelection.has(row.node_id);
    tick.addEventListener("change", () => {
      if (tick.checked) groupSelection.add(row.node_id);
      else groupSelection.delete(row.node_id);
      renderGuidedBuilder();
    });
    label.append(tick);
    label.append(element("span", null, conditionLabel(state, row.node_id)));
    line.append(label);

    // Somewhere to move this rule to. Only groups it is not already inside are offered,
    // so the list never contains a move that would do nothing or fail.
    const targets = (state.structure || []).filter(
      (item) => item.kind === "group" && item.node_id !== row.parent_id,
    );
    if (targets.length) {
      const move = element("div", "gb-chip-row");
      for (const target of targets) {
        move.append(chipButton(
          `Move into “${logicLabel(target.operator)}”`,
          null,
          false,
          () => sendBuilderAction("move_condition", {
            node_id: row.node_id,
            group_id: target.node_id,
          }),
        ));
      }
      line.append(move);
    }
    return line;
  }

  function renderGroupControls(state, rows) {
    const box = element("div", "gb-logic-actions");
    box.dataset.testid = "guided-builder-group-actions";
    const chosen = [...groupSelection];
    const limits = builderContract?.boolean_limits;

    const hint = element("p", "gb-section-hint");
    if (!chosen.length) {
      hint.textContent = "Tick rules above to group them together.";
      box.append(hint);
      return box;
    }

    const row = element("div", "gb-chip-row");
    for (const choice of builderContract?.logic || []) {
      const arity = limits?.arity?.[choice.value];
      const min = Array.isArray(arity) ? arity[0] : 2;
      const max = Array.isArray(arity) ? arity[1] : null;
      // The button is only offered when the selection is a size this grouping accepts.
      // "None of these" takes exactly one rule, so it appears only for a single tick.
      if (chosen.length < min) continue;
      if (max !== null && max !== undefined && chosen.length > max) continue;
      row.append(chipButton(
        `Group as “${choice.label}”`,
        choice.explanation,
        false,
        () => {
          groupSelection = new Set();
          sendBuilderAction("group_conditions", {
            node_ids: chosen,
            operator: choice.value,
          });
        },
      ));
    }
    if (!row.childElementCount) {
      hint.textContent = "Tick two or more rules that sit together to group them.";
      box.append(hint);
      return box;
    }
    box.append(row);
    if (limits) {
      box.append(element(
        "p",
        "gb-section-hint",
        `You can nest groups up to ${limits.max_depth} levels deep, `
        + `with ${limits.max_nodes} parts in total.`,
      ));
    }
    return box;
  }

  function renderBuilderReview(state) {
    const box = section("Where this setup is", null);
    box.dataset.testid = "guided-builder-review";
    const badge = element("p", "gb-lifecycle", chat?.lifecycle?.label || "");
    badge.dataset.state = chat?.lifecycle?.state || "";
    box.append(badge);
    if (chat?.lifecycle?.explanation) {
      box.append(element("p", "gb-section-hint", chat.lifecycle.explanation));
    }
    const todo = (state.steps || []).filter((item) => !item.complete && item.todo);
    if (todo.length) {
      const list = element("ul", "gb-todo");
      for (const step of todo) list.append(element("li", null, step.todo));
      box.append(list);
    }
    for (const question of state.open_questions || []) {
      box.append(element("p", "gb-open-question", question.question));
    }
    for (const item of state.unsupported || []) {
      box.append(element("p", "gb-unsupported", `Not supported yet: ${item.missing}`));
    }
    for (const item of state.provider_requirements || []) {
      if (item.status === "unavailable") {
        box.append(element("p", "gb-unsupported", "One rule needs market data you cannot use yet."));
      }
    }
    return box;
  }

  // The assistant being unavailable is said once, plainly, and never dressed up as a
  // problem with the setup. The Builder keeps working underneath it.
  function renderDegradedNotice() {
    const target = root.querySelector("[data-ai-degraded]");
    if (!target) return;
    const availability = chat?.ai_availability || {};
    const message = availability.assistant_available === false ? availability.message : null;
    target.hidden = !message;
    target.textContent = message || "";
    if (message) target.dataset.testid = "ai-degraded-notice";
    root.dataset.assistantAvailable = availability.assistant_available === false ? "false" : "true";
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
    const requestPayload = withQuestionIdentity({
      ...payload,
      client_message_id: payload.client_message_id || newClientMessageId(),
    });
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
      if (error.payload?.id) {
        chat = error.payload;
        optimisticMessages.delete(requestPayload.client_message_id);
        render();
      }
      const failed = optimisticMessages.get(requestPayload.client_message_id);
      if (failed) optimisticMessages.set(requestPayload.client_message_id, {
        ...failed,
        pending: false,
        failed: true,
        // Carried through so the bubble can say what actually happened instead of
        // promising a retry that cannot work.
        failure_reason: error.message || "",
        failure_retryable: error.retryable !== false,
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
  // Rotating a phone, or dragging a desktop window narrow, changes which column can
  // show the guided fields. Without this they would stay in the hidden one.
  window.addEventListener("resize", () => {
    const target = root.querySelector("[data-ai-guided-builder]");
    if (target) placeGuidedBuilder(target);
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
      pendingScanRequestId = null;
      lastAction = null;
      optimisticMessages.clear();
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
          expected_executable_version: chat.draft_v2?.executable_version || null,
          expected_executable_hash: chat.draft_v2?.executable_hash || null,
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
    pendingScanRequestId = pendingScanRequestId || newClientMessageId();
    setLoading(true, "Scanning the configured market universe");
    try {
      chat = await request(`/sessions/${chat.id}/scan`, {
        method: "POST",
        body: JSON.stringify({idempotency_key: pendingScanRequestId}),
      });
      pendingScanRequestId = null;
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
    // Fetched before the first render so the guided fields are there from the start,
    // whether or not the assistant is reachable.
    await loadBuilderContract();
    await loadOrCreate();
    // The person's own lists and the published screening methods. Loaded before the
    // first render so the "which coins" step is complete from the start, with no
    // assistant involved in filling it.
    await loadUniverseOptions();
    render();
    const requestedMode = new URLSearchParams(window.location.search).get("mode");
    if (
      ["scanner", "monitor"].includes(requestedMode || "")
      && chat?.setup_mode
      && chat.setup_mode !== requestedMode
    ) {
      chat = await request("/sessions", {method: "POST", body: "{}"});
      pendingScanRequestId = null;
      lastAction = null;
      optimisticMessages.clear();
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
