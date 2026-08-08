(() => {
  "use strict";

  const launcher = document.querySelector("[data-public-chat-launcher]");
  const panel = document.querySelector("[data-public-chat-panel]");
  const backdrop = document.querySelector("[data-public-chat-backdrop]");
  if (!launcher || !panel || !backdrop) return;

  // Whether the product is invite-only, decided by the server and stamped on the panel.
  const waitlistMode = panel.dataset.publicChatWaitlistMode === "true";

  const view = {
    close: panel.querySelector("[data-public-chat-close]"),
    newConversation: panel.querySelector("[data-public-chat-new]"),
    loading: panel.querySelector("[data-public-chat-loading]"),
    profileForm: panel.querySelector("[data-public-chat-profile]"),
    profileError: panel.querySelector("[data-public-chat-profile-error]"),
    conversation: panel.querySelector("[data-public-chat-conversation]"),
    messages: panel.querySelector("[data-public-chat-messages]"),
    starters: panel.querySelector("[data-public-chat-starters]"),
    composer: panel.querySelector("[data-public-chat-composer]"),
    input: panel.querySelector("[data-public-chat-input]"),
    send: panel.querySelector("[data-public-chat-send]"),
    answerFeedback: panel.querySelector("[data-public-chat-answer-feedback]"),
    feedbackYes: panel.querySelector("[data-public-chat-feedback-yes]"),
    feedbackNo: panel.querySelector("[data-public-chat-feedback-no]"),
    feedbackStatus: panel.querySelector("[data-public-chat-feedback-status]"),
    inquiry: panel.querySelector("[data-public-chat-inquiry]"),
    inquiryName: panel.querySelector("[data-public-chat-inquiry-name]"),
    inquiryEmail: panel.querySelector("[data-public-chat-inquiry-email]"),
    inquiryError: panel.querySelector("[data-public-chat-inquiry-error]"),
    inquiryCancel: panel.querySelector("[data-public-chat-inquiry-cancel]"),
    success: panel.querySelector("[data-public-chat-success]"),
    successCopy: panel.querySelector("[data-public-chat-success-copy]"),
    rating: panel.querySelector("[data-public-chat-rating]"),
    ratingFeedback: panel.querySelector("[data-public-chat-rating-feedback]"),
    ratingThanks: panel.querySelector("[data-public-chat-rating-thanks]"),
    another: panel.querySelector("[data-public-chat-another]"),
    successClose: panel.querySelector("[data-public-chat-success-close]"),
    forget: panel.querySelector("[data-public-chat-forget]"),
    connectivity: panel.querySelector("[data-public-chat-connectivity]"),
  };
  const state = {
    bootstrap: null,
    profile: null,
    sessionId: sessionIdentifier(),
    sending: false,
    started: false,
    previousFocus: null,
    lastQuestion: "",
    lastAnswerEventId: null,
    feedbackRecorded: false,
    feedbackSending: false,
    inquiryKey: null,
    inquiryQuestion: null,
    inquiryAnswerEventId: null,
    inquiryResult: null,
  };

  function sessionIdentifier() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID().replaceAll("-", "");
    const bytes = new Uint8Array(20);
    window.crypto?.getRandomValues?.(bytes);
    return `session_${Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("") || Date.now()}`;
  }

  function functionalConsent() {
    return document.documentElement.dataset.consentFunctional === "granted";
  }

  function analyticsConsent() {
    return document.documentElement.dataset.consentAnalytics === "granted";
  }

  function setHidden(element, hidden) {
    if (element) element.hidden = hidden;
  }

  function errorText(payload, fallback) {
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
    return fallback;
  }

  async function request(path, options = {}) {
    if (navigator.onLine === false) {
      throw new Error("You appear to be offline. Reconnect, then retry.");
    }
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (state.bootstrap?.csrf_token) headers["X-CSRF-Token"] = state.bootstrap.csrf_token;
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (!response.ok) throw new Error(errorText(payload, "The request could not be completed."));
    return payload;
  }

  async function bootstrap() {
    if (state.bootstrap) return;
    const response = await fetch("/api/v1/public-chat/bootstrap", { credentials: "same-origin" });
    if (!response.ok) throw new Error("Product help is temporarily unavailable.");
    state.bootstrap = await response.json();
    view.input.maxLength = Number(state.bootstrap.max_message_length || 800);
    const remembered = readPersistedProfile();
    if (remembered) {
      try {
        state.profile = await request("/api/v1/public-chat/profile", {
          method: "POST",
          body: JSON.stringify(remembered),
        });
      } catch {
        clearRememberedProfile();
      }
    }
  }

  function storedProfile(storage, rememberOnDevice) {
    if (!state.bootstrap) return null;
    try {
      const parsed = JSON.parse(storage.getItem(state.bootstrap.profile_storage_key));
      if (parsed?.version !== state.bootstrap.profile_version) return null;
      if (parsed?.consent_version !== state.bootstrap.consent_version) return null;
      if (!parsed.name || !parsed.email) return null;
      return {
        name: parsed.name,
        email: parsed.email,
        remember_on_device: rememberOnDevice,
      };
    } catch { return null; }
  }

  function readPersistedProfile() {
    if (!state.bootstrap) return null;
    const remembered = functionalConsent()
      ? storedProfile(localStorage, true)
      : null;
    return remembered || storedProfile(sessionStorage, false);
  }

  function clearRememberedProfile() {
    if (!state.bootstrap) return;
    try { localStorage.removeItem(state.bootstrap.profile_storage_key); } catch { /* no-op */ }
    try { sessionStorage.removeItem(state.bootstrap.profile_storage_key); } catch { /* no-op */ }
  }

  function rememberProfile() {
    if (!state.bootstrap || !state.profile) return;
    const payload = JSON.stringify({
      version: state.bootstrap.profile_version,
      consent_version: state.bootstrap.consent_version,
      saved_at: new Date().toISOString(),
      name: state.profile.name,
      email: state.profile.email,
    });
    try { sessionStorage.setItem(state.bootstrap.profile_storage_key, payload); } catch { /* no-op */ }
    try {
      if (state.profile.remember_on_device && functionalConsent()) {
        localStorage.setItem(state.bootstrap.profile_storage_key, payload);
      } else {
        localStorage.removeItem(state.bootstrap.profile_storage_key);
      }
    } catch { /* Session state still works without device storage. */ }
  }

  function updateConnectivity() {
    const offline = navigator.onLine === false;
    setHidden(view.connectivity, !offline);
    if (view.send) view.send.disabled = offline || state.sending;
  }

  function showError(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  function resetAnswerFeedback() {
    state.lastAnswerEventId = null;
    state.feedbackRecorded = false;
    state.feedbackSending = false;
    setHidden(view.answerFeedback, true);
    setHidden(view.feedbackStatus, true);
    view.feedbackStatus.textContent = "";
    view.feedbackYes.disabled = false;
    view.feedbackNo.disabled = false;
    view.feedbackYes.hidden = false;
    view.feedbackNo.hidden = false;
  }

  function showAnswerFeedback(answerEventId) {
    state.lastAnswerEventId = answerEventId || null;
    state.feedbackRecorded = false;
    view.feedbackYes.disabled = !state.lastAnswerEventId;
    view.feedbackNo.disabled = !state.lastAnswerEventId;
    view.feedbackYes.hidden = false;
    view.feedbackNo.hidden = false;
    view.feedbackStatus.textContent = "";
    setHidden(view.feedbackStatus, true);
    setHidden(view.answerFeedback, false);
  }

  async function recordAnswerFeedback(helpful, supportFormRequested) {
    if (!state.lastAnswerEventId || state.feedbackRecorded || state.feedbackSending) {
      return false;
    }
    state.feedbackSending = true;
    view.feedbackYes.disabled = true;
    view.feedbackNo.disabled = true;
    try {
      const result = await request(
        `/api/v1/public-chat/answers/${state.lastAnswerEventId}/feedback`,
        {
          method: "POST",
          body: JSON.stringify({
            session_id: state.sessionId,
            helpful,
            support_form_requested: supportFormRequested,
          }),
        },
      );
      state.feedbackRecorded = true;
      view.feedbackYes.hidden = true;
      view.feedbackNo.hidden = true;
      view.feedbackStatus.textContent = result.message;
      setHidden(view.feedbackStatus, false);
      return true;
    } catch (error) {
      view.feedbackStatus.textContent = error.message;
      setHidden(view.feedbackStatus, false);
      view.feedbackYes.disabled = false;
      view.feedbackNo.disabled = false;
      return false;
    } finally {
      state.feedbackSending = false;
    }
  }

  function showProfile() {
    setHidden(view.loading, true);
    setHidden(view.profileForm, false);
    setHidden(view.conversation, true);
    setHidden(view.composer, true);
    setHidden(view.answerFeedback, true);
    view.profileForm.querySelector("input[name='name']")?.focus();
  }

  function startConversation() {
    setHidden(view.loading, true);
    setHidden(view.profileForm, true);
    setHidden(view.conversation, false);
    setHidden(view.composer, false);
    if (!state.started) {
      state.started = true;
      // Pre-launch there is no published price, so naming pricing as a topic invites a
      // question the assistant is not allowed to answer. The server stamps the state on
      // the panel; the greeting follows it rather than keeping its own idea of it.
      const topics = waitlistMode
        ? "screening evidence, private-beta access, the waitlist, and product boundaries"
        : "screening evidence, private-beta access, pricing, and product boundaries";
      appendMessage("assistant", `Hi ${firstName(state.profile?.name)}. I can explain Hilal Markets, ${topics}. What would you like to know?`);
    }
    window.setTimeout(() => view.input?.focus(), 30);
  }

  function firstName(name) {
    return String(name || "there").trim().split(/\s+/)[0] || "there";
  }

  async function openChat() {
    if (!panel.hidden) {
      view.input?.focus();
      return;
    }
    launcher.classList.add("was-opened");
    state.previousFocus = document.activeElement;
    panel.hidden = false;
    backdrop.hidden = false;
    panel.setAttribute("aria-hidden", "false");
    backdrop.setAttribute("aria-hidden", "false");
    launcher.setAttribute("aria-expanded", "true");
    document.body.classList.add("public-chat-open");
    updateConnectivity();
    requestAnimationFrame(() => {
      panel.classList.add("is-open");
      backdrop.classList.add("is-open");
    });
    try {
      await bootstrap();
      if (state.profile) startConversation();
      else showProfile();
    } catch (error) {
      view.loading.innerHTML = "";
      const message = document.createElement("p");
      message.setAttribute("role", "alert");
      message.textContent = error.message;
      view.loading.append(message);
    }
  }

  function closeChat() {
    panel.classList.remove("is-open");
    backdrop.classList.remove("is-open");
    launcher.setAttribute("aria-expanded", "false");
    panel.setAttribute("aria-hidden", "true");
    backdrop.setAttribute("aria-hidden", "true");
    document.body.classList.remove("public-chat-open");
    window.setTimeout(() => {
      panel.hidden = true;
      backdrop.hidden = true;
      if (state.previousFocus instanceof HTMLElement) state.previousFocus.focus();
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 210);
  }

  function appendSafeBoldText(container, text) {
    const value = String(text || "");
    const boldPattern = /\*\*([\s\S]+?)\*\*/g;
    let cursor = 0;
    let match;
    while ((match = boldPattern.exec(value)) !== null) {
      container.append(document.createTextNode(value.slice(cursor, match.index)));
      if (match[1].trim()) {
        const strong = document.createElement("strong");
        strong.textContent = match[1];
        container.append(strong);
      } else {
        container.append(document.createTextNode(match[0]));
      }
      cursor = boldPattern.lastIndex;
    }
    container.append(document.createTextNode(value.slice(cursor)));
  }

  function appendMessage(role, text, options = {}) {
    const wrapper = document.createElement("article");
    wrapper.className = `public-chat-message is-${role}${options.error ? " is-error" : ""}`;
    const bubble = document.createElement("div");
    bubble.className = "public-chat-bubble";
    if (role === "assistant") appendSafeBoldText(bubble, text);
    else bubble.textContent = text;
    wrapper.append(bubble);
    if (options.clarification) {
      const clarification = document.createElement("p");
      clarification.className = "public-chat-clarification";
      clarification.textContent = options.clarification;
      wrapper.append(clarification);
    }
    if (options.followUps?.length) {
      const actions = document.createElement("div");
      actions.className = "public-chat-follow-ups";
      actions.setAttribute("aria-label", "Suggested follow-up questions");
      options.followUps.forEach((label) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chip";
        button.textContent = label;
        button.addEventListener("click", () => {
          if (state.sending) return;
          actions.querySelectorAll("button").forEach((item) => { item.disabled = true; });
          sendQuestion(label);
        });
        actions.append(button);
      });
      wrapper.append(actions);
    }
    if (options.retry) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "public-chat-retry";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => {
        if (state.sending) return;
        retry.disabled = true;
        sendQuestion(options.retry, {
          appendUser: false,
          retryNode: wrapper,
          clientMessageId: options.clientMessageId,
        });
      });
      wrapper.append(retry);
    }
    const meta = document.createElement("span");
    meta.className = "public-chat-message-meta";
    meta.textContent = role === "user" ? "You" : "Hilal Markets product help";
    wrapper.append(meta);
    view.messages.append(wrapper);
    view.messages.scrollTop = view.messages.scrollHeight;
    return wrapper;
  }

  function appendTyping() {
    const wrapper = document.createElement("article");
    wrapper.className = "public-chat-message is-assistant";
    wrapper.setAttribute("aria-label", "Hilal Markets is checking verified product information");
    const bubble = document.createElement("div");
    bubble.className = "public-chat-bubble public-chat-typing";
    bubble.innerHTML = "<i></i><i></i><i></i>";
    wrapper.append(bubble);
    view.messages.append(wrapper);
    view.messages.scrollTop = view.messages.scrollHeight;
    return wrapper;
  }

  async function sendQuestion(rawQuestion, options = {}) {
    const question = String(rawQuestion || "").trim();
    if (!question || state.sending) return;
    resetAnswerFeedback();
    state.sending = true;
    state.lastQuestion = question;
    view.send.disabled = true;
    view.input.value = "";
    resizeInput();
    if (options.appendUser !== false) appendMessage("user", question);
    setHidden(view.starters, true);
    setHidden(view.inquiry, true);
    const typing = appendTyping();
    const clientMessageId = options.clientMessageId || `public-chat:${state.sessionId}:${Date.now()}`;
    try {
      const result = await request("/api/v1/public-chat/answers", {
        method: "POST",
        body: JSON.stringify({
          question,
          session_id: state.sessionId,
          client_message_id: clientMessageId,
          source_page: `${location.pathname}${location.search}`.slice(0, 240),
          profile: state.profile,
        }),
      });
      typing.remove();
      options.retryNode?.remove();
      appendMessage("assistant", result.message, {
        clarification: result.clarification_question,
        followUps: result.suggested_follow_ups,
      });
      showAnswerFeedback(result.answer_event_id);
      if (result.support_handoff_explicitly_requested) {
        const recorded = await recordAnswerFeedback(false, true);
        if (recorded) showInquiry(question, result.answer_event_id);
      }
    } catch (error) {
      typing.remove();
      if (options.retryNode) {
        options.retryNode.querySelector(".public-chat-bubble").textContent = error.message;
        options.retryNode.querySelector(".public-chat-retry").disabled = false;
      } else {
        appendMessage("assistant", error.message, {
          error: true,
          retry: question,
          clientMessageId,
        });
      }
    } finally {
      state.sending = false;
      updateConnectivity();
      view.input.focus();
    }
  }

  function showInquiry(question, answerEventId = state.lastAnswerEventId) {
    if (state.inquiryQuestion !== question || state.inquiryAnswerEventId !== answerEventId) {
      state.inquiryKey = `public-inquiry:${state.sessionId}:${Date.now()}`;
      state.inquiryQuestion = question;
      state.inquiryAnswerEventId = answerEventId;
    }
    view.inquiryName.value = state.profile?.name || "";
    view.inquiryEmail.value = state.profile?.email || "";
    view.inquiry.querySelector("textarea[name='details']").value = question;
    setHidden(view.answerFeedback, true);
    setHidden(view.inquiry, false);
    view.inquiry.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.setTimeout(() => view.inquiryName.focus(), 30);
  }

  function attribution() {
    if (!analyticsConsent()) {
      return {
        attribution_consent: false,
        utm_source: null,
        utm_medium: null,
        utm_campaign: null,
      };
    }
    const query = new URLSearchParams(location.search);
    return {
      attribution_consent: true,
      utm_source: query.get("utm_source"),
      utm_medium: query.get("utm_medium"),
      utm_campaign: query.get("utm_campaign"),
    };
  }

  async function submitInquiry(event) {
    event.preventDefault();
    if (state.sending) return;
    state.sending = true;
    showError(view.inquiryError, "");
    const button = view.inquiry.querySelector("button[type='submit']");
    button.disabled = true;
    const data = new FormData(view.inquiry);
    try {
      const editedProfile = {
        name: String(data.get("name") || "").trim(),
        email: String(data.get("email") || "").trim(),
        remember_on_device: Boolean(state.profile?.remember_on_device),
      };
      const result = await request("/api/v1/public-chat/inquiries", {
        method: "POST",
        body: JSON.stringify({
          profile: editedProfile,
          session_id: state.sessionId,
          answer_event_id: state.inquiryAnswerEventId,
          details: data.get("details"),
          category: data.get("category"),
          source_page: `${location.pathname}${location.search}`.slice(0, 240),
          referrer: analyticsConsent() ? (document.referrer || null) : null,
          ...attribution(),
          idempotency_key: state.inquiryKey,
          company_website: data.get("company_website") || "",
        }),
      });
      state.profile = editedProfile;
      rememberProfile();
      state.inquiryResult = result;
      resetRating();
      setHidden(view.inquiry, true);
      setHidden(view.composer, true);
      const emailCopy = result.email_delivery_status === "sent"
        ? `We also sent a confirmation to ${result.masked_email}.`
        : `A confirmation email is queued for ${result.masked_email}.`;
      view.successCopy.textContent = `${result.message} Reference ${result.reference}. ${emailCopy}`;
      setHidden(view.success, false);
      view.success.focus();
    } catch (error) {
      showError(view.inquiryError, error.message);
    } finally {
      state.sending = false;
      button.disabled = false;
    }
  }

  async function submitRating(helpful) {
    if (!state.inquiryResult) return;
    const buttons = [...view.rating.querySelectorAll("[data-public-chat-helpful]")];
    buttons.forEach((item) => { item.disabled = true; });
    try {
      await request("/api/v1/public-chat/ratings", {
        method: "POST",
        body: JSON.stringify({
          reference: state.inquiryResult.reference,
          feedback_token: state.inquiryResult.feedback_token,
          helpful,
          feedback: view.ratingFeedback.value.trim() || null,
        }),
      });
      view.rating.classList.add("is-recorded");
      setHidden(view.ratingThanks, false);
    } catch {
      buttons.forEach((item) => { item.disabled = false; });
    }
  }

  function resetRating() {
    view.rating.classList.remove("is-recorded");
    setHidden(view.ratingThanks, true);
    view.ratingFeedback.value = "";
    view.rating.querySelectorAll("[data-public-chat-helpful]").forEach((item) => {
      item.disabled = false;
    });
  }

  function resetForAnotherQuestion() {
    state.inquiryKey = null;
    state.inquiryQuestion = null;
    state.inquiryAnswerEventId = null;
    state.inquiryResult = null;
    resetAnswerFeedback();
    setHidden(view.success, true);
    resetRating();
    setHidden(view.inquiry, true);
    setHidden(view.composer, false);
    appendMessage("assistant", "What else would you like to understand about Hilal Markets?");
    view.input.focus();
  }

  function resetConversation() {
    state.sessionId = sessionIdentifier();
    state.started = false;
    state.lastQuestion = "";
    state.inquiryKey = null;
    state.inquiryQuestion = null;
    state.inquiryAnswerEventId = null;
    state.inquiryResult = null;
    state.sending = false;
    resetAnswerFeedback();
    view.messages.replaceChildren();
    view.inquiry.reset();
    setHidden(view.success, true);
    resetRating();
    setHidden(view.inquiry, true);
    setHidden(view.starters, false);
    if (state.profile) startConversation();
    else showProfile();
  }

  function forgetSavedDetails() {
    clearRememberedProfile();
    state.profile = null;
    state.started = false;
    view.profileForm.reset();
    resetConversation();
  }

  function resizeInput() {
    view.input.style.height = "auto";
    view.input.style.height = `${Math.min(view.input.scrollHeight, 108)}px`;
  }

  launcher.addEventListener("click", openChat);
  view.close.addEventListener("click", closeChat);
  view.newConversation.addEventListener("click", resetConversation);
  backdrop.addEventListener("click", closeChat);
  view.profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    showError(view.profileError, "");
    const data = new FormData(view.profileForm);
    const button = view.profileForm.querySelector("button[type='submit']");
    button.disabled = true;
    try {
      state.profile = await request("/api/v1/public-chat/profile", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          email: data.get("email"),
          remember_on_device: data.get("remember_on_device") === "on",
        }),
      });
      rememberProfile();
      startConversation();
    } catch (error) {
      showError(view.profileError, error.message);
    } finally { button.disabled = false; }
  });
  view.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendQuestion(view.input.value);
  });
  view.input.addEventListener("input", resizeInput);
  view.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      view.composer.requestSubmit();
    }
  });
  view.feedbackYes.addEventListener("click", () => {
    recordAnswerFeedback(true, false);
  });
  view.feedbackNo.addEventListener("click", async () => {
    const question = state.lastQuestion;
    const answerEventId = state.lastAnswerEventId;
    const recorded = await recordAnswerFeedback(false, true);
    if (recorded) showInquiry(question, answerEventId);
  });
  panel.querySelectorAll("[data-public-chat-question]").forEach((button) => {
    button.addEventListener("click", () => sendQuestion(button.dataset.publicChatQuestion));
  });
  view.inquiry.addEventListener("submit", submitInquiry);
  view.inquiryCancel.addEventListener("click", () => {
    setHidden(view.inquiry, true);
    view.feedbackStatus.textContent = "Support form canceled. You can keep chatting.";
    setHidden(view.feedbackStatus, false);
    setHidden(view.answerFeedback, false);
    view.input.focus();
  });
  view.another.addEventListener("click", resetForAnotherQuestion);
  view.successClose.addEventListener("click", closeChat);
  view.forget.addEventListener("click", forgetSavedDetails);
  panel.querySelectorAll("[data-public-chat-helpful]").forEach((button) => {
    button.addEventListener("click", () => submitRating(button.dataset.publicChatHelpful === "true"));
  });
  window.addEventListener("hm:consent-updated", (event) => {
    if (!event.detail?.functional) {
      try { localStorage.removeItem(state.bootstrap?.profile_storage_key); } catch { /* no-op */ }
    }
    rememberProfile();
  });
  window.addEventListener("online", updateConnectivity);
  window.addEventListener("offline", updateConnectivity);
  document.addEventListener("keydown", (event) => {
    if (panel.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeChat();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...panel.querySelectorAll("button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled])")]
      .filter((item) => !item.closest("[hidden]") && item.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
