/* The System Brain assistant, in a window that opens on every page.
 *
 * It used to be a box on the Inbox page only. That is the wrong place for it twice over:
 * the questions people actually have — "what does this case want?", "why did that one
 * refuse?" — happen while looking at a *different* page, and walking away from the page
 * to go and ask is how the question stops being asked at all.
 *
 * So it is a window in the corner, like Hilal on the dashboard and the Support assistant
 * on the public site. Same shape, same manners, deliberately a different mark: Hilal is a
 * spark, this one is a robot head, because they are not the same assistant and must never
 * be mistaken for each other.
 *
 * What is different underneath is the context. Hilal reads what a customer can see; this
 * reads the product's own records through governed tools, and — new here — it is told
 * which page the reader is standing on and which cases that page is showing. That is what
 * makes "why is this one stuck?" a question with an answer.
 *
 * Everything is persisted server-side. The transcript, the conversation list, the running
 * turn: reloading the page or opening another one loses nothing.
 */
(() => {
  const root = document.querySelector("[data-brain-agent-dock]");
  if (!root) return;

  const csrf = document.body.dataset.systemBrainCsrf || "";
  const launcher = root.querySelector("[data-brain-agent-open]");
  const windowNode = root.querySelector("[data-brain-agent-window]");
  const closeButton = root.querySelector("[data-brain-agent-close]");
  const form = root.querySelector("[data-brain-agent-form]");
  const input = form?.querySelector("textarea");
  const submit = form?.querySelector("button[type='submit']");
  const cancel = root.querySelector("[data-brain-agent-cancel]");
  const messages = root.querySelector("[data-brain-agent-messages]");
  const progress = root.querySelector("[data-brain-agent-progress]");
  const title = root.querySelector("[data-brain-agent-title]");
  const conversationList = root.querySelector("[data-brain-agent-conversations]");
  const conversationSearch = root.querySelector("[data-brain-agent-search]");
  const evidence = root.querySelector("[data-brain-agent-evidence]");
  const proposalPanel = root.querySelector("[data-brain-agent-proposals]");
  const artifactsPanel = root.querySelector("[data-brain-agent-artifacts]");
  const contextNote = root.querySelector("[data-brain-agent-context]");

  let activeConversationId = null;
  let activeConversationTitle = "New analysis";
  let activeController = null;
  let progressTimer = null;
  let loaded = false;

  /* ---- what the reader is looking at ------------------------------------- */

  /* Read off the page rather than sent down from the server, because the reader may have
     filtered, paged or opened a case since it was rendered — and the whole point is to
     describe the screen as it is now.

     Deliberately narrow. It carries *which* records are on screen, never a copy of the
     page's text: an unbounded blob of the page would ship customer data to a provider on
     every single turn. */
  function pageContext() {
    const references = [...document.querySelectorAll("[data-case-reference]")]
      .map((node) => (node.dataset.caseReference || "").trim())
      .filter(Boolean);
    const unique = [...new Set(references)].slice(0, 40);
    return {
      path: window.location.pathname.slice(0, 300),
      section: (document.body.dataset.brainSection || "").slice(0, 80),
      heading: (document.querySelector(".brain-topbar h1")?.textContent || "").trim().slice(0, 200),
      case_references: unique,
      focus: (root.dataset.brainAgentFocus || "").slice(0, 160),
    };
  }

  function describeContext(context) {
    const where = context.heading || context.section || "this page";
    if (!context.case_references.length) return `It can see: ${where}.`;
    const count = context.case_references.length;
    return `It can see: ${where}, and the ${count} case${count === 1 ? "" : "s"} listed on it.`;
  }

  /* ---- talking to the server --------------------------------------------- */

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
        empty.textContent = "Ask anything about the product, the cases or the code.";
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
      evidence.innerHTML = '<p class="brain-muted">What it looked at appears here.</p>';
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
    const query = conversationSearch?.value.trim() || "";
    const data = await api(`/api/v1/system-brain/conversations?q=${encodeURIComponent(query)}`);
    conversationList.innerHTML = "";
    data.items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "brain-conversation-button";
      button.dataset.active = String(item.conversation_id === activeConversationId);
      button.innerHTML = "<strong></strong><small></small>";
      button.querySelector("strong").textContent = item.title;
      button.querySelector("small").textContent = new Date(
        item.last_message_at || item.updated_at,
      ).toLocaleString();
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

  /* The assistant never acts. It may only ever *propose*, and a proposal becomes an
     action through this dialog, which is a separate authenticated confirmation. Moving
     the assistant into a floating window changed nothing about that, and this comment is
     here so it never quietly does. */
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
      artifactsPanel.innerHTML = '<p class="brain-muted">Nothing saved in this chat.</p>';
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

  /* ---- the window -------------------------------------------------------- */

  /* Loaded the first time the window is opened, not on every page load. The assistant is
     on every System Brain page now, and fetching a conversation list on all of them for a
     window nobody opened is work for nothing. */
  async function ensureLoaded() {
    if (loaded) return;
    loaded = true;
    try {
      await loadAgentConversations(true);
    } catch (_error) {
      loaded = false;
    }
  }

  function setOpen(open) {
    if (!windowNode || !launcher) return;
    windowNode.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    root.classList.toggle("is-open", open);
    if (!open) return;
    if (contextNote) contextNote.textContent = describeContext(pageContext());
    ensureLoaded().then(() => input?.focus());
  }

  launcher?.addEventListener("click", () => setOpen(windowNode?.hidden !== false));
  closeButton?.addEventListener("click", () => {
    setOpen(false);
    launcher?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && windowNode && !windowNode.hidden) {
      setOpen(false);
      launcher?.focus();
    }
  });

  /* Enter sends, Shift+Enter makes a new line — the arrangement people try first, and
     the one both other assistants already use. */
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form?.requestSubmit();
    }
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input?.value.trim() || "";
    if (question.length < 2 || !submit || !input || !messages) return;
    if (!activeConversationId) await createAgentConversation();
    const clientMessageId = `brain-${crypto.randomUUID()}`;
    messages.append(renderAgentMessage({ role: "user", content: question, created_at: new Date().toISOString() }));
    messages.scrollTop = messages.scrollHeight;
    input.value = "";
    input.disabled = true;
    submit.disabled = true;
    if (cancel) cancel.hidden = false;
    if (progress) progress.hidden = false;
    activeController = new AbortController();
    startProgressPoll();
    try {
      const data = await api(`/api/v1/system-brain/conversations/${activeConversationId}/turns`, {
        method: "POST",
        signal: activeController.signal,
        body: JSON.stringify({
          message: question,
          client_message_id: clientMessageId,
          page_context: pageContext(),
        }),
      });
      messages.append(renderAgentMessage({ role: "assistant", content: data.answer, created_at: new Date().toISOString() }));
      messages.scrollTop = messages.scrollHeight;
      renderEvidence({ tool_calls: data.tool_calls });
      await Promise.all([loadAgentConversations(false), loadProposals()]);
    } catch (error) {
      const text = error?.name === "AbortError"
        ? "The browser stopped waiting. The persisted turn may still complete; reopen this chat to recover it."
        : `${error.message} No domain state was changed.`;
      messages.append(renderAgentMessage({ role: "assistant", content: text }));
    } finally {
      activeController = null;
      stopProgressPoll();
      input.disabled = false;
      submit.disabled = false;
      if (cancel) cancel.hidden = true;
      if (progress) progress.hidden = true;
      input.focus();
    }
  });

  cancel?.addEventListener("click", async () => {
    if (activeConversationId) {
      await api(`/api/v1/system-brain/conversations/${activeConversationId}/cancel-active-run`, { method: "POST" }).catch(() => {});
    }
    activeController?.abort();
  });

  root.querySelector("[data-brain-agent-new]")?.addEventListener("click", createAgentConversation);
  conversationSearch?.addEventListener("input", () => loadAgentConversations(false).catch(() => {}));
  root.querySelector("[data-brain-agent-rename]")?.addEventListener("click", async () => {
    if (!activeConversationId) return;
    const nextTitle = window.prompt("Rename this chat", activeConversationTitle);
    if (!nextTitle?.trim()) return;
    const data = await api(`/api/v1/system-brain/conversations/${activeConversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ title: nextTitle.trim() }),
    });
    showAgentConversation(data);
    await loadAgentConversations(false);
  });
  root.querySelector("[data-brain-agent-archive]")?.addEventListener("click", async () => {
    if (!activeConversationId || !window.confirm("Archive this chat? Its audit history is kept.")) return;
    await api(`/api/v1/system-brain/conversations/${activeConversationId}`, {
      method: "PATCH",
      body: JSON.stringify({ archived: true }),
    });
    activeConversationId = null;
    await loadAgentConversations(true);
  });
})();
