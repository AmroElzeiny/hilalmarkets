/* The side menu on a phone. It is a drawer there and a fixed column on a computer, so
   the button only exists at small widths and the drawer closes on Escape, on a tap
   outside it, and on following a link. */
(() => {
  const toggle = document.querySelector("[data-brain-menu-toggle]");
  const nav = document.getElementById("brain-nav");
  const scrim = document.querySelector("[data-brain-scrim]");
  if (!toggle || !nav) return;
  const setOpen = (open) => {
    document.body.classList.toggle("brain-nav-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    if (scrim) scrim.hidden = !open;
  };
  toggle.addEventListener("click", () => {
    setOpen(!document.body.classList.contains("brain-nav-open"));
  });
  scrim?.addEventListener("click", () => setOpen(false));
  nav.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => setOpen(false)));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
  setOpen(false);
})();

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

/* "Mark all as passed", on one review case.
 *
 * It selects "pass" on every condition that allows it and "covered" on every use rule
 * that allows it, then carries the reviewer's own written reason into each box.
 *
 * Three things it deliberately does not do:
 *   - it never invents wording. Every explanation is the sentence the reviewer typed,
 *     mirrored, and it stops mirroring into any box the reviewer edits by hand;
 *   - it never selects an outcome a condition does not offer. Such a condition is left
 *     untouched and stays visible in the "x of y" count as still open;
 *   - it never submits. The reviewer still presses Approve.
 */
(() => {
  const form = document.querySelector("[data-review-decision-form]");
  if (!form) return;
  const trigger = form.querySelector("[data-approve-all]");
  const progress = form.querySelector("[data-criteria-progress]");
  const reason = form.querySelector("#decision-reason");
  const outcomes = [...form.querySelectorAll("select[name='criterion_outcome']")];
  const explanations = [...form.querySelectorAll("textarea[name='criterion_reason']")];
  const useDecisions = [...form.querySelectorAll("select[name='use_decision']")];
  const useReasons = [...form.querySelectorAll("textarea[name='use_reason']")];

  const refreshProgress = () => {
    if (!progress) return;
    const decided = outcomes.filter((item) => item.value).length;
    progress.textContent = `${decided} of ${outcomes.length}`;
    progress.dataset.complete = String(decided === outcomes.length && outcomes.length > 0);
  };

  const mirror = () => {
    const text = reason ? reason.value.trim() : "";
    [...explanations, ...useReasons].forEach((box) => {
      if (box.dataset.mirrored !== "true") return;
      box.value = text;
    });
  };

  const select = (element, wanted) => {
    const option = [...element.options].find((item) => item.value === wanted);
    if (!option) return false;
    element.value = wanted;
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  [...explanations, ...useReasons].forEach((box) => {
    // A box the reviewer touches stops following the shared reason from then on.
    box.addEventListener("input", () => { box.dataset.mirrored = "false"; });
  });
  outcomes.forEach((item) => item.addEventListener("change", refreshProgress));
  reason?.addEventListener("input", mirror);

  trigger?.addEventListener("click", () => {
    let refused = 0;
    outcomes.forEach((item, index) => {
      if (select(item, "pass")) {
        const box = explanations[index];
        if (box && !box.value.trim()) box.dataset.mirrored = "true";
      } else {
        refused += 1;
      }
    });
    useDecisions.forEach((item, index) => {
      if (select(item, "covered")) {
        const box = useReasons[index];
        if (box && !box.value.trim()) box.dataset.mirrored = "true";
      } else {
        refused += 1;
      }
    });
    mirror();
    refreshProgress();
    trigger.dataset.refused = String(refused);
    trigger.setAttribute(
      "aria-label",
      refused
        ? `${refused} condition(s) cannot be passed and were left for you to decide`
        : "Every condition marked as passed",
    );
    if (reason && !reason.value.trim()) reason.focus();
  });

  refreshProgress();
})();

/* Ticking several cases and deciding them at once.
 *
 * The bar only appears once something is selected, and it counts what is selected so a
 * decision can never be recorded on a different number of cases than the page shows.
 *
 * The ceiling is read from `data-bulk-max`, which the page renders from the same
 * constant the endpoint enforces. It is never written here. Select all stops at that
 * number and says so, because the failure this replaces was silent: the reviewer ticked
 * every row, wrote a reason, pressed Approve, and the whole batch came back refused
 * with nothing decided and no hint anywhere that a limit existed. */
(() => {
  const table = document.querySelector("[data-case-table]");
  const bar = document.querySelector("[data-bulk-form]");
  if (!table || !bar) return;
  const boxes = [...table.querySelectorAll("[data-case-select]")];
  const all = table.querySelector("[data-select-all]");
  const count = bar.querySelector("[data-bulk-count]");
  const limitNote = bar.querySelector("[data-bulk-limit-note]");

  /* A missing or unreadable attribute must not become "no limit". Falling back to
     Infinity would rebuild the exact bug this replaces, so an unreadable value falls
     back to selecting nothing extra rather than to selecting everything. */
  const parsed = Number.parseInt(bar.dataset.bulkMax || "", 10);
  const maximum = Number.isFinite(parsed) && parsed > 0 ? parsed : 1;

  const refresh = () => {
    const selected = boxes.filter((box) => box.checked);
    bar.hidden = selected.length === 0;
    if (count) count.textContent = String(selected.length);
    boxes.forEach((box) => {
      box.closest("[data-case-row]")?.classList.toggle("is-selected", box.checked);
    });
    if (limitNote) {
      const capped = selected.length >= maximum && boxes.length > maximum;
      limitNote.hidden = !capped;
      limitNote.textContent = capped
        ? `${maximum} is the most one decision can cover. ${boxes.length - maximum} of the ${boxes.length} shown are not selected.`
        : "";
    }
    if (all) {
      /* "Every row is ticked" and "the ceiling is reached" are different states and the
         box has to show the right one. With more rows on the page than the ceiling
         allows, a full tick is impossible, so the box stays indeterminate rather than
         claiming everything is selected. */
      const reachable = Math.min(boxes.length, maximum);
      all.checked = selected.length > 0 && selected.length >= reachable;
      all.indeterminate = selected.length > 0 && selected.length < boxes.length;
    }
  };

  boxes.forEach((box) => box.addEventListener("change", refresh));
  all?.addEventListener("change", () => {
    /* Ticking down the list and stopping at the ceiling, rather than ticking everything
       and letting the server throw the whole selection away. */
    let taken = 0;
    boxes.forEach((box) => {
      box.checked = all.checked && taken < maximum;
      if (box.checked) taken += 1;
    });
    refresh();
  });
  bar.querySelector("[data-bulk-clear]")?.addEventListener("click", () => {
    boxes.forEach((box) => { box.checked = false; });
    refresh();
  });

  /* Last stop before the request. Nothing on this page can build a selection over the
     ceiling any more, but a decision the endpoint will refuse must never leave the
     browser, however it came to be ticked.

     Bound on the document in the capture phase so it runs *before* the shared
     confirm-before-submit handler on the form. Bound on the form it would run after it,
     and the reviewer would answer "are you sure?" about a decision that was never going
     to be sent. */
  document.addEventListener(
    "submit",
    (event) => {
      if (event.target !== bar) return;
      const selected = boxes.filter((box) => box.checked).length;
      if (selected <= maximum) return;
      event.preventDefault();
      event.stopPropagation();
      window.alert(
        `${selected} cases are selected and ${maximum} is the most one decision can cover. ` +
          "Untick some before deciding.",
      );
    },
    true,
  );

  refresh();
})();

/* Stat tiles count up to their measured value, then show the exact text again — so a
   rounded animation frame is never the number somebody reads. */
if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  document.querySelectorAll("[data-count-to]").forEach((node) => {
    const target = Number.parseFloat(node.dataset.countTo || "");
    const display = node.dataset.countDisplay || node.textContent;
    if (!Number.isFinite(target) || target <= 0) return;
    const started = performance.now();
    const duration = 620;
    const tick = (now) => {
      const progress = Math.min((now - started) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = progress < 1
        ? Math.round(target * eased).toLocaleString()
        : display;
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

/* The customer chat log.
 *
 * Reading what a customer actually said to the Support AI and to Hilal. This used to
 * share a block with the System Brain assistant, which is a different thing entirely —
 * one reads conversations other people had, the other is a conversation you are having.
 * The assistant now lives in its own file and in a window that opens on every page; see
 * `system-brain-agent.js`.
 */
const workspace = document.querySelector("[data-system-brain-workspace]");
if (workspace) {
  const csrf = document.body.dataset.systemBrainCsrf || "";
  const customerList = workspace.querySelector("[data-brain-customer-conversations]");
  const customerDetail = workspace.querySelector("[data-brain-conversation-detail]");
  const filters = workspace.querySelector("[data-brain-explorer-filters]");
  const liveState = workspace.querySelector("[data-brain-live-state]");
  const loadMore = workspace.querySelector("[data-brain-load-more]");
  let currentCustomerCursor = null;
  let lastEventId = Number(sessionStorage.getItem("systemBrainConversationEventId") || 0);
  let eventStream = null;
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

  function renderMessage(item) {
    return window.HilalChatMessageRenderer.render(item, { variant: "admin" });
  }

  /* The two assistants a customer talks to, named the way the filter menu names them.
     The raw source value is a database word; nobody reading a support log should have to
     translate "public_site_chat" in their head. */
  const SOURCE_LABELS = {
    public_site_chat: "Support AI",
    dashboard_hilal_agent: "Hilal Agent",
  };

  function sourceLabel(value) {
    return SOURCE_LABELS[value] || String(value || "").replaceAll("_", " ");
  }

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
      button.querySelector("span").textContent = `${sourceLabel(item.source_type)} · ${item.lifecycle_or_stage}`;
      button.querySelector("small").textContent = item.last_message_excerpt || "No message kept";
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
    heading.querySelector("p").textContent = `${sourceLabel(data.summary.source_type)} · ${
      data.summary.user_email || (data.summary.anonymous ? "Visitor, not signed in" : "No email kept")
    }`;
    const timeline = document.createElement("div");
    timeline.className = "brain-customer-timeline";
    data.messages.forEach((item) => timeline.append(renderMessage(item)));
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

  loadCustomerConversations(false)
    .then(() => connectConversationEvents())
    .catch((error) => {
      if (liveState) liveState.textContent = error.message;
    });
}
