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

const assistantRoot = document.querySelector("[data-system-brain-assistant]");
if (assistantRoot) {
  const form = assistantRoot.querySelector("[data-brain-assistant-form]");
  const input = form?.querySelector("textarea");
  const submit = form?.querySelector("button[type='submit']");
  const messages = assistantRoot.querySelector("[data-brain-assistant-messages]");
  const history = [];

  const appendMessage = (role, text, details = null) => {
    const article = document.createElement("div");
    article.className = `brain-assistant-message ${role}`;
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    article.appendChild(paragraph);

    if (details?.findings?.length) {
      const list = document.createElement("ul");
      details.findings.forEach((finding) => {
        const item = document.createElement("li");
        item.textContent = `${finding.title}: ${finding.detail} [${finding.evidence_ref}]`;
        list.appendChild(item);
      });
      article.appendChild(list);
    }
    if (details?.suggested_actions?.length) {
      const actions = document.createElement("small");
      actions.textContent = `Suggested follow-up: ${details.suggested_actions
        .map((item) => `${item.label} - ${item.rationale}`)
        .join(" | ")}`;
      article.appendChild(actions);
    }
    if (details?.limitations?.length) {
      const limitations = document.createElement("small");
      limitations.textContent = `Limitations: ${details.limitations.join(" | ")}`;
      article.appendChild(limitations);
    }
    messages?.appendChild(article);
    if (messages) messages.scrollTop = messages.scrollHeight;
    return article;
  };

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input?.value.trim() || "";
    if (question.length < 2 || !submit || !input || !messages) return;

    appendMessage("user", question);
    const requestHistory = history.slice(-10);
    history.push({ role: "user", content: question });
    input.value = "";
    input.disabled = true;
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    const pending = appendMessage("assistant", "Searching retained evidence and current system state...");

    try {
      const response = await fetch("/dashboard/system-brain/assistant", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.body.dataset.systemBrainCsrf || "",
        },
        body: JSON.stringify({ message: question, history: requestHistory }),
      });
      const payload = await response.json().catch(() => ({}));
      pending.remove();
      if (!response.ok) {
        throw new Error(payload.detail || "The diagnostic assistant is unavailable.");
      }
      appendMessage("assistant", payload.answer, payload);
      history.push({ role: "assistant", content: payload.answer });
      if (history.length > 10) history.splice(0, history.length - 10);
    } catch (error) {
      pending.remove();
      appendMessage(
        "error",
        error instanceof Error
          ? error.message
          : "The diagnostic assistant is unavailable. No system action was taken.",
      );
    } finally {
      input.disabled = false;
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      input.focus();
    }
  });
}
