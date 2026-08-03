document.querySelectorAll("[data-filter-target]").forEach((input) => {
  input.addEventListener("input", () => {
    const table = document.getElementById(input.dataset.filterTarget);
    const query = input.value.trim().toLowerCase();
    table?.querySelectorAll("tbody tr").forEach((row) => {
      row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
    });
  });
});

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!reduceMotion) {
  document.querySelectorAll("[data-count]").forEach((node) => {
    const raw = String(node.dataset.count || "");
    const target = Number.parseFloat(raw);
    if (!Number.isFinite(target) || target <= 0) return;
    const suffix = raw.endsWith("h") ? "h" : "";
    const decimals = raw.includes(".") ? 1 : 0;
    const started = performance.now();
    const duration = 520;
    const tick = (now) => {
      const progress = Math.min((now - started) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = `${(target * eased).toFixed(decimals)}${suffix}`;
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

document.querySelectorAll("[data-decision-form], [data-guarded-form]").forEach((form) => {
  form.querySelectorAll("button[name='action']").forEach((button) => {
    button.addEventListener("click", () => {
      if (["approve_with_qualification", "request_more_evidence"].includes(button.value)) {
        const extras = form.querySelector("[data-decision-extras]");
        if (extras) extras.open = true;
      }
    });
  });
  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    const prompt = submitter?.dataset.confirm;
    if (prompt && !window.confirm(prompt)) {
      event.preventDefault();
      return;
    }
    if (!form.checkValidity()) return;
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    if (submitter) {
      submitter.disabled = false;
      submitter.setAttribute("aria-busy", "true");
      const original = submitter.textContent.trim();
      submitter.textContent = `Recording: ${original}`;
    }
  });
});

document.querySelectorAll("[data-access-form]").forEach((form) => {
  const options = form.querySelectorAll("input[name='tier']");
  const monthsField = form.querySelector("[data-months-field]");
  const monthsInput = monthsField?.querySelector("input[name='months']");
  const applyButton = form.querySelector("[data-plan-apply]");
  const refreshAccessForm = () => {
    const selected = form.querySelector("input[name='tier']:checked");
    const requiresMonths = selected?.value === "full_access";
    if (monthsField) monthsField.hidden = !requiresMonths;
    if (monthsInput) {
      monthsInput.disabled = !requiresMonths;
      monthsInput.required = requiresMonths;
      if (!requiresMonths) monthsInput.value = "";
    }
    if (applyButton) {
      applyButton.hidden = !selected;
      applyButton.disabled = !selected;
    }
  };
  options.forEach((option) => option.addEventListener("change", refreshAccessForm));
  refreshAccessForm();
});

const userActionDialog = document.querySelector("[data-user-action-dialog]");
if (userActionDialog) {
  const dialogTitle = userActionDialog.querySelector("[data-user-dialog-title]");
  const dialogBody = userActionDialog.querySelector("[data-user-dialog-body]");
  const dialogTarget = userActionDialog.querySelector("[data-user-dialog-target]");
  const dialogReason = userActionDialog.querySelector("[data-user-dialog-reason]");
  const dialogError = userActionDialog.querySelector("[data-user-dialog-error]");
  const cancelButton = userActionDialog.querySelector("[data-user-dialog-cancel]");
  const confirmButton = userActionDialog.querySelector("[data-user-dialog-confirm]");
  let pendingForm = null;
  let pendingSubmitter = null;

  document.querySelectorAll("[data-user-admin-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.userActionConfirmed === "true") {
        delete form.dataset.userActionConfirmed;
        form.querySelectorAll("button").forEach((button) => {
          button.disabled = true;
        });
        return;
      }
      event.preventDefault();
      if (!form.checkValidity()) {
        form.reportValidity();
        return;
      }
      pendingForm = form;
      pendingSubmitter = event.submitter;
      const selectedPlan = form.querySelector("input[name='tier']:checked");
      const selectedLabel = selectedPlan
        ?.closest(".brain-plan-option")
        ?.querySelector("strong")
        ?.textContent.trim();
      const months = form.querySelector("input[name='months']")?.value;
      const planDetail = selectedLabel
        ? ` Selected access: ${selectedLabel}${months ? ` for ${months} month${months === "1" ? "" : "s"}` : ""}.`
        : "";
      if (dialogTitle) dialogTitle.textContent = form.dataset.confirmTitle || "Confirm account action";
      if (dialogBody) {
        dialogBody.textContent = `${form.dataset.confirmBody || "Review this account action before continuing."}${planDetail}`;
      }
      if (dialogTarget) dialogTarget.textContent = form.dataset.userLabel || "Selected user";
      if (dialogReason) dialogReason.value = "";
      if (dialogError) dialogError.textContent = "";
      if (confirmButton) confirmButton.textContent = form.dataset.confirmAction || "Confirm";
      userActionDialog.classList.toggle(
        "is-danger",
        form.dataset.confirmTone === "danger",
      );
      userActionDialog.showModal();
      window.setTimeout(() => dialogReason?.focus(), 0);
    });
  });

  const closeUserActionDialog = () => {
    userActionDialog.close();
    pendingSubmitter?.focus();
    pendingForm = null;
    pendingSubmitter = null;
  };

  cancelButton?.addEventListener("click", closeUserActionDialog);
  userActionDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeUserActionDialog();
  });
  userActionDialog.addEventListener("click", (event) => {
    if (event.target === userActionDialog) closeUserActionDialog();
  });
  dialogReason?.addEventListener("input", () => {
    if (dialogError) dialogError.textContent = "";
  });
  confirmButton?.addEventListener("click", () => {
    const reason = dialogReason?.value.trim() || "";
    if (reason.length < 3) {
      if (dialogError) dialogError.textContent = "Enter a short reason before confirming.";
      dialogReason?.focus();
      return;
    }
    if (!pendingForm) return;
    const reasonInput = pendingForm.querySelector("input[name='reason']");
    if (reasonInput) reasonInput.value = reason;
    const form = pendingForm;
    const submitter = pendingSubmitter;
    form.dataset.userActionConfirmed = "true";
    userActionDialog.close();
    pendingForm = null;
    pendingSubmitter = null;
    form.requestSubmit(submitter || undefined);
  });
}

const publicationDialog = document.querySelector("[data-publication-dialog]");
const publicationLauncher = document.querySelector("[data-open-publication]");
if (publicationDialog && publicationLauncher) {
  publicationLauncher.addEventListener("click", () => {
    publicationDialog.showModal();
  });
  publicationDialog.querySelectorAll("[data-close-publication]").forEach((button) => {
    button.addEventListener("click", () => publicationDialog.close());
  });
  publicationDialog.addEventListener("click", (event) => {
    if (event.target === publicationDialog) publicationDialog.close();
  });
  publicationDialog.addEventListener("close", () => publicationLauncher.focus());
}

document.querySelectorAll("[data-accept-ai-rationale]").forEach((button) => {
  button.addEventListener("click", () => {
    const source = document.querySelector("[data-ai-rationale-source]");
    const target = document.getElementById("decision-reason");
    if (!source || !target || button.disabled) return;
    target.value = source.textContent.trim();
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.focus();
  });
});

const workspace = document.querySelector("[data-system-brain-workspace]");
if (workspace) {
  const csrf = document.body.dataset.systemBrainCsrf || "";
  const assistantRoot = workspace.querySelector("[data-system-brain-assistant]");
  const form = assistantRoot?.querySelector("[data-brain-assistant-form]");
  const input = form?.querySelector("textarea");
  const submit = form?.querySelector("button[type='submit']");
  const cancel = form?.querySelector("[data-brain-cancel-turn]");
  const messages = assistantRoot?.querySelector("[data-brain-assistant-messages]");
  const progress = workspace.querySelector("[data-brain-agent-progress]");
  const title = workspace.querySelector("[data-brain-agent-title]");
  const conversationList = workspace.querySelector("[data-brain-agent-conversations]");
  const evidence = workspace.querySelector("[data-brain-evidence]");
  const proposalPanel = workspace.querySelector("[data-brain-action-proposals]");
  const artifactsPanel = workspace.querySelector("[data-brain-artifacts]");
  const customerList = workspace.querySelector("[data-brain-customer-conversations]");
  const customerDetail = workspace.querySelector("[data-brain-conversation-detail]");
  const filters = workspace.querySelector("[data-brain-explorer-filters]");
  const liveState = workspace.querySelector("[data-brain-live-state]");
  const loadMore = workspace.querySelector("[data-brain-load-more]");
  let activeConversationId = null;
  let activeConversationTitle = "New analysis";
  let currentCustomerCursor = null;
  let lastEventId = Number(sessionStorage.getItem("systemBrainConversationEventId") || 0);
  let activeController = null;
  let eventStream = null;
  let progressTimer = null;
  let eventFallbackTimer = null;
  let eventFailureCount = 0;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        "X-CSRF-Token": csrf,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof payload.detail === "object"
        ? payload.detail.message || payload.detail.code
        : payload.detail;
      throw new Error(detail || "The request could not be completed.");
    }
    return payload;
  }

  function renderAgentMessage(item) {
    return window.HilalChatMessageRenderer.render(item, { variant: "admin" });
  }

  function showAgentConversation(data) {
    activeConversationId = data.conversation_id;
    activeConversationTitle = data.title;
    if (title) title.textContent = data.title;
    if (messages) {
      messages.innerHTML = "";
      (data.messages || []).forEach((item) => messages.append(renderAgentMessage(item)));
      if (!data.messages?.length) {
        const empty = document.createElement("p");
        empty.className = "brain-muted";
        empty.textContent = "Ask a question to begin this persisted analysis.";
        messages.append(empty);
      }
      messages.scrollTop = messages.scrollHeight;
    }
    renderEvidence((data.messages || []).at(-1)?.metadata || {});
    loadProposals();
    loadArtifacts();
    recoverRunProgress();
  }

  function renderEvidence(metadata = {}) {
    if (!evidence) return;
    evidence.innerHTML = "";
    const calls = metadata.tool_calls || [];
    const refs = new Set(calls.flatMap((item) => item.evidence_refs || []));
    if (!calls.length && !refs.size) {
      evidence.innerHTML = '<p class="brain-muted">No evidence has been inspected in this turn.</p>';
      return;
    }
    calls.forEach((call) => {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${call.tool_name} · ${call.status} · ${call.duration_ms} ms`;
      const list = document.createElement("ul");
      (call.evidence_refs || []).forEach((ref) => {
        const item = document.createElement("li");
        item.textContent = ref;
        list.append(item);
      });
      details.append(summary, list);
      evidence.append(details);
    });
  }

  async function loadAgentConversations(selectFirst = false) {
    if (!conversationList) return;
    const query = workspace.querySelector("[data-brain-agent-search]")?.value.trim() || "";
    const data = await api(`/api/v1/system-brain/conversations?q=${encodeURIComponent(query)}`);
    conversationList.innerHTML = "";
    data.items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "brain-conversation-button";
      button.dataset.active = String(item.conversation_id === activeConversationId);
      button.innerHTML = `<strong></strong><small></small>`;
      button.querySelector("strong").textContent = item.title;
      button.querySelector("small").textContent = item.last_message_at
        ? new Date(item.last_message_at).toLocaleString() : "No messages yet";
      button.addEventListener("click", () => openAgentConversation(item.conversation_id));
      conversationList.append(button);
    });
    if (selectFirst && !activeConversationId && data.items[0]) {
      await openAgentConversation(data.items[0].conversation_id);
    }
  }

  async function openAgentConversation(conversationId) {
    const data = await api(`/api/v1/system-brain/conversations/${conversationId}`);
    showAgentConversation(data);
    await loadAgentConversations(false);
  }

  async function createAgentConversation() {
    const data = await api("/api/v1/system-brain/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "New analysis" }),
    });
    showAgentConversation(data);
    await loadAgentConversations(false);
    input?.focus();
  }

  async function loadProposals() {
    if (!proposalPanel || !activeConversationId) return;
    const data = await api(`/api/v1/system-brain/action-proposals?conversation_id=${activeConversationId}&status=pending`);
    proposalPanel.innerHTML = "";
    data.items.forEach((proposal) => {
      const card = document.createElement("article");
      card.className = "brain-action-proposal";
      const heading = document.createElement("strong");
      heading.textContent = `${proposal.action} · ${proposal.target}`;
      const changes = document.createElement("pre");
      changes.textContent = JSON.stringify(proposal.exact_changes, null, 2);
      const reason = document.createElement("p");
      reason.textContent = proposal.reason;
      const risks = document.createElement("small");
      risks.textContent = `Risks: ${proposal.risks.join("; ") || "No model-supplied risks"}. Rollback: ${proposal.rollback_path}`;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "brain-action-button";
      button.textContent = "Review and confirm";
      button.addEventListener("click", () => showProposalConfirmation(proposal));
      card.append(heading, reason, changes, risks, button);
      proposalPanel.append(card);
    });
  }

  function showProposalConfirmation(proposal) {
    const dialog = document.createElement("dialog");
    dialog.className = "brain-confirmation-dialog";
    const formNode = document.createElement("form");
    formNode.method = "dialog";
    const heading = document.createElement("h3");
    heading.textContent = `Confirm ${proposal.action}`;
    const target = document.createElement("p");
    target.textContent = `Target: ${proposal.target}`;
    const changes = document.createElement("pre");
    changes.textContent = JSON.stringify(proposal.exact_changes, null, 2);
    const effect = document.createElement("p");
    effect.textContent = `Expected effect: ${proposal.expected_effect}`;
    const risk = document.createElement("p");
    risk.textContent = `Risks: ${proposal.risks.join("; ") || "None stated"}. Rollback: ${proposal.rollback_path}`;
    const label = document.createElement("label");
    label.textContent = "Human confirmation reason";
    const reason = document.createElement("textarea");
    reason.required = true;
    reason.minLength = 3;
    reason.maxLength = 1000;
    const controls = document.createElement("div");
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "brain-secondary-button";
    dismiss.textContent = "Cancel";
    const confirm = document.createElement("button");
    confirm.type = "submit";
    confirm.className = "brain-action-button";
    confirm.textContent = "Confirm exact proposal";
    label.append(reason);
    controls.append(dismiss, confirm);
    formNode.append(heading, target, changes, effect, risk, label, controls);
    dialog.append(formNode);
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dismiss.addEventListener("click", close);
    dialog.addEventListener("cancel", close);
    formNode.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!formNode.reportValidity()) return;
      confirm.disabled = true;
      try {
        await api(`/api/v1/system-brain/action-proposals/${proposal.proposal_id}/confirm`, {
          method: "POST",
          body: JSON.stringify({ confirmation_token: proposal.confirmation_token, reason: reason.value.trim() }),
        });
        close();
        await loadProposals();
      } catch (error) {
        confirm.disabled = false;
        risk.textContent = error.message;
      }
    });
    dialog.showModal();
    reason.focus();
  }

  async function loadArtifacts() {
    if (!artifactsPanel || !activeConversationId) return;
    const data = await api(`/api/v1/system-brain/artifacts?conversation_id=${activeConversationId}`);
    artifactsPanel.innerHTML = "";
    if (!data.items.length) {
      artifactsPanel.innerHTML = '<p class="brain-muted">No saved artifacts in this conversation.</p>';
      return;
    }
    data.items.forEach((artifact) => {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${artifact.title} · ${artifact.artifact_kind.replaceAll("_", " ")}`;
      const content = document.createElement("pre");
      content.textContent = artifact.content;
      const refs = document.createElement("small");
      refs.textContent = `Evidence: ${artifact.evidence_refs.join(", ")}`;
      const status = document.createElement("small");
      status.textContent = artifact.model_authored_draft
        ? "Internal AI-authored draft; verify against the cited evidence before use."
        : "Server-generated artifact";
      details.append(summary, status, content, refs);
      artifactsPanel.append(details);
    });
  }

  function stopProgressPoll() {
    if (progressTimer) window.clearInterval(progressTimer);
    progressTimer = null;
  }

  async function refreshRunProgress() {
    if (!activeConversationId) return;
    const state = await api(`/api/v1/system-brain/conversations/${activeConversationId}/run-progress`);
    if (["running", "cancel_requested"].includes(state.status)) {
      if (progress) {
        progress.hidden = false;
        progress.textContent = `${state.status.replaceAll("_", " ")} · ${state.step_count} model step(s) · ${state.tool_call_count} audited tool call(s)`;
      }
      renderEvidence({ tool_calls: state.tool_calls });
      return;
    }
    stopProgressPoll();
    if (progress) progress.hidden = true;
    if (state.assistant_message_id) await openAgentConversation(activeConversationId);
  }

  function startProgressPoll() {
    stopProgressPoll();
    progressTimer = window.setInterval(() => refreshRunProgress().catch(() => {}), 700);
  }

  async function recoverRunProgress() {
    try {
      const state = await api(`/api/v1/system-brain/conversations/${activeConversationId}/run-progress`);
      if (["running", "cancel_requested"].includes(state.status)) startProgressPoll();
    } catch (_error) {
      // The persisted conversation remains authoritative; a later refresh can recover it.
    }
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input?.value.trim() || "";
    if (question.length < 2 || !submit || !input || !messages) return;
    if (!activeConversationId) await createAgentConversation();
    const clientMessageId = `brain-${crypto.randomUUID()}`;
    messages.append(renderAgentMessage({ role: "user", content: question, created_at: new Date().toISOString() }));
    input.value = "";
    input.disabled = true;
    submit.disabled = true;
    cancel.hidden = false;
    progress.hidden = false;
    activeController = new AbortController();
    startProgressPoll();
    try {
      const data = await api(`/api/v1/system-brain/conversations/${activeConversationId}/turns`, {
        method: "POST",
        signal: activeController.signal,
        body: JSON.stringify({ message: question, client_message_id: clientMessageId }),
      });
      messages.append(renderAgentMessage({ role: "assistant", content: data.answer, created_at: new Date().toISOString() }));
      renderEvidence({ tool_calls: data.tool_calls });
      await Promise.all([loadAgentConversations(false), loadProposals()]);
    } catch (error) {
      const text = error?.name === "AbortError"
        ? "The browser stopped waiting. The persisted turn may still complete; reopen this conversation to recover it."
        : `${error.message} No domain state was changed.`;
      messages.append(renderAgentMessage({ role: "assistant", content: text }));
    } finally {
      activeController = null;
      stopProgressPoll();
      input.disabled = false;
      submit.disabled = false;
      cancel.hidden = true;
      progress.hidden = true;
      input.focus();
    }
  });
  cancel?.addEventListener("click", async () => {
    if (activeConversationId) {
      await api(`/api/v1/system-brain/conversations/${activeConversationId}/cancel-active-run`, { method: "POST" }).catch(() => {});
    }
    activeController?.abort();
  });
  workspace.querySelector("[data-brain-new-conversation]")?.addEventListener("click", createAgentConversation);
  workspace.querySelector("[data-brain-agent-search]")?.addEventListener("input", () => loadAgentConversations(false));
  workspace.querySelector("[data-brain-rename-conversation]")?.addEventListener("click", async () => {
    if (!activeConversationId) return;
    const nextTitle = window.prompt("Rename this persisted analysis", activeConversationTitle);
    if (!nextTitle?.trim()) return;
    const data = await api(`/api/v1/system-brain/conversations/${activeConversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ title: nextTitle.trim() }),
    });
    showAgentConversation(data);
    await loadAgentConversations(false);
  });
  workspace.querySelector("[data-brain-archive-conversation]")?.addEventListener("click", async () => {
    if (!activeConversationId || !window.confirm("Archive this analysis? Its audit history remains retained.")) return;
    await api(`/api/v1/system-brain/conversations/${activeConversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ archived: true }),
    });
    activeConversationId = null;
    await loadAgentConversations(true);
  });

  function customerQuery(append = false) {
    const parameters = new URLSearchParams();
    new FormData(filters).forEach((value, key) => {
      if (value === "on") parameters.set(key, "true");
      else if (String(value).trim()) parameters.set(key, String(value).trim());
    });
    parameters.set("seen_event_id", String(lastEventId));
    parameters.set("limit", "30");
    if (append && currentCustomerCursor) parameters.set("cursor", currentCustomerCursor);
    return parameters;
  }

  async function loadCustomerConversations(append = false) {
    if (!customerList || !filters) return;
    const data = await api(`/api/v1/system-brain/customer-conversations?${customerQuery(append)}`);
    if (!append) customerList.innerHTML = "";
    data.items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "brain-customer-conversation";
      if (item.unread) button.dataset.unread = "true";
      button.innerHTML = "<strong></strong><span></span><small></small>";
      button.querySelector("strong").textContent = item.display_name;
      button.querySelector("span").textContent = `${item.source_type.replaceAll("_", " ")} · ${item.lifecycle_or_stage}`;
      button.querySelector("small").textContent = item.last_message_excerpt || "No retained message excerpt";
      button.addEventListener("click", () => openCustomerConversation(item));
      customerList.append(button);
    });
    currentCustomerCursor = data.next_cursor;
    loadMore.hidden = !currentCustomerCursor;
    if (data.latest_event_id > lastEventId) {
      lastEventId = data.latest_event_id;
      sessionStorage.setItem("systemBrainConversationEventId", String(lastEventId));
    }
  }

  async function openCustomerConversation(summary) {
    if (!customerDetail) return;
    const reason = "Customer experience and quality review";
    const data = await api(`/api/v1/system-brain/customer-conversations/${summary.source_type}/${summary.conversation_id}?access_reason=${encodeURIComponent(reason)}`);
    customerDetail.innerHTML = "";
    const heading = document.createElement("header");
    heading.innerHTML = "<h3></h3><p></p>";
    heading.querySelector("h3").textContent = data.summary.display_name;
    heading.querySelector("p").textContent = data.summary.user_email || (data.summary.anonymous ? "Anonymous visitor" : "No retained email");
    const timeline = document.createElement("div");
    timeline.className = "brain-customer-timeline";
    data.messages.forEach((item) => timeline.append(renderAgentMessage(item)));
    const telemetry = document.createElement("details");
    telemetry.innerHTML = "<summary>Admin telemetry</summary>";
    const telemetryText = document.createElement("pre");
    telemetryText.textContent = JSON.stringify(data.messages.map((item) => ({
      message_id: item.message_id, model: item.model, latency_ms: item.latency_ms,
      input_tokens: item.input_tokens, output_tokens: item.output_tokens,
      reasoning_tokens: item.reasoning_tokens, estimated_cost_usd: item.estimated_cost_usd,
      error_code: item.error_code,
    })), null, 2);
    telemetry.append(telemetryText);
    const links = document.createElement("nav");
    links.className = "brain-related-links";
    (data.related_links || []).forEach((item) => {
      const link = document.createElement("a");
      link.href = item.href;
      link.textContent = item.label;
      links.append(link);
    });
    const lifecycle = document.createElement("details");
    lifecycle.innerHTML = "<summary>Lifecycle history</summary>";
    const lifecycleText = document.createElement("pre");
    lifecycleText.textContent = JSON.stringify(data.lifecycle_events || [], null, 2);
    lifecycle.append(lifecycleText);
    if (!data.transcript_complete) {
      const warning = document.createElement("p");
      warning.className = "brain-flash warning";
      warning.textContent = data.transcript_limitation;
      customerDetail.append(warning);
    }
    customerDetail.append(heading, links, timeline, lifecycle, telemetry);
  }

  filters?.addEventListener("submit", (event) => {
    event.preventDefault();
    currentCustomerCursor = null;
    loadCustomerConversations(false);
  });
  loadMore?.addEventListener("click", () => loadCustomerConversations(true));

  function applyConversationEvent(event) {
    const id = Number(event.event_id || event.lastEventId || 0);
    if (id <= lastEventId) return;
    lastEventId = id;
    sessionStorage.setItem("systemBrainConversationEventId", String(lastEventId));
    loadCustomerConversations(false);
  }

  function startEventPollingFallback() {
    eventStream?.close();
    if (eventFallbackTimer) window.clearInterval(eventFallbackTimer);
    if (liveState) liveState.textContent = "Live polling fallback";
    eventFallbackTimer = window.setInterval(async () => {
      try {
        const data = await api(`/api/v1/system-brain/customer-conversation-events?after_id=${lastEventId}&limit=100`);
        data.items.forEach(applyConversationEvent);
      } catch (_error) {
        if (liveState) liveState.textContent = "Live updates unavailable";
      }
    }, 1500);
  }

  function connectConversationEvents() {
    if (!("EventSource" in window)) {
      startEventPollingFallback();
      return;
    }
    eventStream?.close();
    eventStream = new EventSource(`/api/v1/system-brain/customer-conversation-stream?after_id=${lastEventId}`);
    eventStream.onopen = () => {
      eventFailureCount = 0;
      if (liveState) liveState.textContent = "Live";
    };
    eventStream.onerror = () => {
      eventFailureCount += 1;
      if (liveState) liveState.textContent = "Reconnecting…";
      if (eventFailureCount >= 3) startEventPollingFallback();
    };
    ["conversation_created", "message_persisted", "turn_completed", "turn_failed", "lifecycle_changed", "approval_occurred"].forEach((name) => {
      eventStream.addEventListener(name, (event) => {
        applyConversationEvent({ lastEventId: event.lastEventId });
      });
    });
  }

  Promise.all([loadAgentConversations(true), loadCustomerConversations(false)])
    .then(() => connectConversationEvents())
    .catch((error) => {
      if (liveState) liveState.textContent = error.message;
    });
}
