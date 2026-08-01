(function () {
  "use strict";

  const dialog = document.getElementById("billing-checkout-dialog");
  const dataNode = document.getElementById("billing-plan-data");
  if (!(dialog instanceof HTMLDialogElement) || !dataNode) return;

  let catalog = {};
  try {
    catalog = JSON.parse(dataNode.textContent || "{}");
  } catch (_error) {
    return;
  }

  const form = dialog.querySelector("[data-billing-checkout-form]");
  const planInput = dialog.querySelector("[data-billing-plan-code]");
  const cycleInput = dialog.querySelector("[data-billing-cycle]");
  const planLabel = dialog.querySelector("[data-billing-plan-label]");
  const priceLabel = dialog.querySelector("[data-billing-price-label]");
  const summary = dialog.querySelector("#billing-dialog-summary");
  const periodOptions = dialog.querySelector("[data-billing-period-options]");
  const periodRadios = Array.from(
    dialog.querySelectorAll('input[name="billing_period_choice"]')
  );
  const cardInput = dialog.querySelector('input[name="payment_method"][value="card"]');
  const cryptoInput = dialog.querySelector(
    'input[name="payment_method"][value="crypto"]'
  );
  const cardOption = dialog.querySelector("[data-billing-method-card]");
  const cryptoOption = dialog.querySelector("[data-billing-method-crypto]");
  const submit = dialog.querySelector("[data-billing-submit]");
  const status = dialog.querySelector("[data-billing-dialog-status]");
  const pageIntervalRadios = Array.from(
    document.querySelectorAll('input[name="dashboard_billing_interval"]')
  );
  const pageIntervalStatus = document.querySelector("[data-billing-page-interval-status]");
  let activeTrigger = null;
  let selectedPlan = "";
  let trialSelected = false;
  let submitting = false;

  function planData() {
    return (catalog.plans || {})[selectedPlan] || null;
  }

  function selectedPeriod() {
    const selected = periodRadios.find((radio) => radio.checked);
    return selected ? selected.value : "monthly";
  }

  function setMethodState(input, wrapper, available) {
    if (!input || !wrapper) return;
    input.disabled = !available;
    wrapper.classList.toggle("is-unavailable", !available);
    wrapper.setAttribute("aria-disabled", String(!available));
    if (!available) input.checked = false;
  }

  function refreshPagePricing() {
    const interval =
      pageIntervalRadios.find((radio) => radio.checked)?.value === "annual"
        ? "annual"
        : "monthly";
    document.querySelectorAll("[data-dashboard-plan-price]").forEach((price) => {
      const amount = price.querySelector("[data-dashboard-plan-amount]");
      const period = price.querySelector("[data-dashboard-plan-period]");
      const original = price.querySelector("[data-dashboard-plan-original]");
      const card = price.closest("[data-offer-card]");
      const countdown = card
        ? card.querySelector("[data-dashboard-offer-countdown]")
        : null;
      const free = price.getAttribute("data-free") === "true";
      const soonLabel = price.getAttribute("data-coming-soon-label") || "Soon";
      const available =
        price.getAttribute(
          interval === "annual" ? "data-annual-available" : "data-monthly-available"
        ) !== "false";
      const originalPrice = price.getAttribute("data-original-monthly-price") || "";
      // An interval that is not open yet shows no number. A price beside "Soon" reads
      // as a charge the user is about to face.
      price.classList.toggle("is-coming-soon", !available);
      if (amount) {
        amount.textContent = available
          ? `$${price.getAttribute(
              interval === "annual" ? "data-annual-price" : "data-monthly-price"
            ) || "0"}`
          : soonLabel;
      }
      if (period) {
        period.textContent = !available
          ? "Not available yet"
          : free
            ? "Free forever"
            : interval === "annual"
              ? "per year"
              : "per month";
      }
      // The crossed-out price and the countdown belong to the monthly launch offer, so
      // both disappear together on any other interval.
      const promoted = available && interval === "monthly" && Boolean(originalPrice);
      if (original) original.hidden = !promoted;
      if (countdown) countdown.hidden = !promoted;
    });
    document.querySelectorAll("[data-dashboard-purchase-button]").forEach((button) => {
      const planCode = button.getAttribute("data-plan-code") || "";
      const plan = (catalog.plans || {})[planCode];
      if (!plan) return;
      const cycle =
        button.getAttribute(interval === "annual" ? "data-annual-cycle" : "data-monthly-cycle") ||
        interval;
      const card = button.closest("[data-offer-card]");
      const priceNode = card ? card.querySelector("[data-dashboard-plan-price]") : null;
      // A plan or an interval that is not on sale yet cannot be bought whatever the
      // payment provider reports about it.
      const offered =
        !priceNode ||
        priceNode.getAttribute(
          interval === "annual" ? "data-annual-available" : "data-monthly-available"
        ) !== "false";
      const availability = plan.availability || {};
      const available =
        offered &&
        Boolean(availability.purchasable) &&
        (cycle === "trial_7_day"
          ? Boolean(availability.trial)
          : cycle === "annual"
            ? Boolean(availability.card_annual)
            : Boolean(availability.card_monthly || availability.crypto_monthly));
      button.setAttribute("data-billing-cycle", cycle);
      button.textContent =
        button.getAttribute(interval === "annual" ? "data-annual-label" : "data-monthly-label") ||
        button.textContent;
      button.disabled = !available;
      button.setAttribute("aria-disabled", String(!available));
    });
    document.querySelectorAll("[data-dashboard-trial-note]").forEach((note) => {
      note.hidden = interval !== "monthly";
    });
    if (pageIntervalStatus) {
      pageIntervalStatus.textContent = `Prices are shown for ${interval} billing.`;
    }
  }

  function refresh() {
    const plan = planData();
    if (!plan) return;
    const period = selectedPeriod();
    const availability = plan.availability || {};
    const cycle = trialSelected ? "trial_7_day" : period;
    const cardAvailable = Boolean(availability.purchasable) && (trialSelected
      ? Boolean(availability.trial)
      : Boolean(availability[`card_${period}`]));
    const cryptoAvailable =
      Boolean(availability.purchasable) &&
      !trialSelected &&
      period === "monthly" &&
      Boolean(availability.crypto_monthly);

    setMethodState(cardInput, cardOption, cardAvailable);
    setMethodState(cryptoInput, cryptoOption, cryptoAvailable);
    if (!cardInput?.checked && !cryptoInput?.checked) {
      if (cardAvailable && cardInput) cardInput.checked = true;
      else if (cryptoAvailable && cryptoInput) cryptoInput.checked = true;
    }

    if (planInput) planInput.value = selectedPlan;
    if (cycleInput) cycleInput.value = cycle;
    if (planLabel) {
      planLabel.textContent = trialSelected
        ? `${plan.name} - 7-day trial`
        : plan.name;
    }
    if (priceLabel) {
      priceLabel.textContent = trialSelected
        ? `$0 today, then $${plan.monthly}/month unless cancelled`
        : period === "annual"
          ? `$${plan.annual} per year`
          : `$${plan.monthly} per month`;
    }
    if (summary) {
      summary.textContent = trialSelected
        ? "Creem will show the seven-day trial and the first charge date before you confirm."
        : "Review the plan and choose how you want to pay.";
    }
    if (periodOptions) periodOptions.hidden = trialSelected;
    if (status) {
      status.textContent =
        cardAvailable || cryptoAvailable
          ? "You will enter card or wallet details on the selected provider's secure page."
          : "This plan and billing period are not configured for checkout yet.";
    }
    if (submit) {
      const selectedMethod = cardInput?.checked
        ? "card"
        : cryptoInput?.checked
          ? "crypto"
          : "";
      submit.disabled = submitting || !selectedMethod;
      if (!submitting) {
        submit.textContent =
          selectedMethod === "card"
            ? "Continue to Creem"
            : selectedMethod === "crypto"
              ? "Continue to NOWPayments"
              : "Payment method unavailable";
      }
    }
  }

  function open(planCode, cycle, trigger) {
    if (!(catalog.plans || {})[planCode]) return;
    selectedPlan = planCode;
    trialSelected = cycle === "trial_7_day";
    activeTrigger = trigger || null;
    periodRadios.forEach((radio) => {
      radio.checked = radio.value === (cycle === "annual" ? "annual" : "monthly");
    });
    refresh();
    dialog.showModal();
    const firstField = dialog.querySelector('input[name="first_name"]');
    if (firstField) window.setTimeout(() => firstField.focus(), 0);
  }

  document.querySelectorAll("[data-billing-dialog-trigger]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      open(
        trigger.getAttribute("data-plan-code") || "",
        trigger.getAttribute("data-billing-cycle") || "monthly",
        trigger
      );
    });
  });

  dialog.querySelectorAll("[data-billing-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    if (activeTrigger instanceof HTMLElement) activeTrigger.focus();
  });
  periodRadios.forEach((radio) => radio.addEventListener("change", refresh));
  pageIntervalRadios.forEach((radio) =>
    radio.addEventListener("change", refreshPagePricing)
  );
  [cardInput, cryptoInput].forEach((input) => {
    if (input) input.addEventListener("change", refresh);
  });
  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting || !form.reportValidity()) return;
      submitting = true;
      if (submit) {
        submit.disabled = true;
        submit.textContent = "Opening secure checkout...";
      }
      if (status) {
        status.classList.remove("is-error");
        status.textContent = "Creating one secure checkout for this order.";
      }
      try {
        const response = await window.fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
        });
        const payload = await response.json().catch(() => ({}));
        const checkoutUrl =
          typeof payload.checkout_url === "string" ? payload.checkout_url : "";
        if (!response.ok || !checkoutUrl) {
          const message =
            payload?.error?.message ||
            "We could not open secure checkout. Please review your details and try again.";
          throw new Error(message);
        }
        window.location.assign(checkoutUrl);
      } catch (error) {
        submitting = false;
        if (status) {
          status.classList.add("is-error");
          status.textContent =
            error instanceof Error
              ? error.message
              : "We could not open secure checkout. Please try again.";
        }
        refresh();
        if (status instanceof HTMLElement) status.focus({ preventScroll: true });
      }
    });
  }

  if (dialog.dataset.autoOpen === "true") {
    const initialPlan = dialog.dataset.selectedPlan || "";
    const initialCycle = dialog.dataset.selectedCycle || "monthly";
    window.setTimeout(() => open(initialPlan, initialCycle, null), 0);
  }
  refreshPagePricing();
})();
