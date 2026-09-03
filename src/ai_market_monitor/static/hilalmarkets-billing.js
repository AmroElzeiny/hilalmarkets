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
  // What a discount code changed the price to, or null while none is applied. Held here
  // rather than read back out of the page: `refresh()` rewrites the price line on every
  // change, and without this it would put the full price back under an applied code.
  let discounted = null;

  /** Money as this product writes it: `$15`, and cents only when there are cents. */
  function money(amount) {
    const value = Number(amount);
    if (!Number.isFinite(value)) return String(amount);
    const rounded = Math.round(value * 100) / 100;
    return Number.isInteger(rounded) ? `$${rounded}` : `$${rounded.toFixed(2)}`;
  }

  function planData() {
    return (catalog.plans || {})[selectedPlan] || null;
  }

  function selectedPeriod() {
    const selected = periodRadios.find((radio) => radio.checked);
    return selected ? selected.value : "monthly";
  }

  function setMethodState(input, wrapper, decision) {
    if (!input || !wrapper) return;
    const available = Boolean(decision && decision.available);
    input.disabled = !available;
    wrapper.classList.toggle("is-unavailable", !available);
    wrapper.setAttribute("aria-disabled", String(!available));
    if (!available) input.checked = false;
    // Why this way of paying cannot be used, in the server's words. A method that is
    // switched off and one this plan simply is not sold through are different facts, and
    // "unavailable" answers neither of them.
    const note = wrapper.querySelector("[data-billing-method-note]");
    if (note && decision && typeof decision.note === "string") {
      note.textContent = decision.note;
    }
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
      // both disappear together on any other interval. Marked, not hidden: the offer
      // script owns whether the offer is still running, and two scripts setting the same
      // `hidden` flag would take turns undoing each other every second.
      const promoted = available && interval === "monthly" && Boolean(originalPrice);
      if (original) original.toggleAttribute("data-offer-inactive", !promoted);
      if (countdown) countdown.toggleAttribute("data-offer-inactive", !promoted);
      // The code is a monthly offer. Left under a yearly price it would name a code that
      // does nothing there, which is worse than saying nothing.
      const codeNote = card ? card.querySelector("[data-dashboard-plan-code]") : null;
      if (codeNote) codeNote.hidden = !promoted;
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
    // Which ways of paying work for this plan and this period was decided on the server.
    // This file used to rebuild that answer out of separate flags, which is the same rule
    // written twice — and the copy in the browser is the one nothing tests.
    const decided = (plan.methods || {})[cycle] || {};
    const cardDecision = decided.card || { available: false, note: "" };
    const cryptoDecision = decided.crypto || { available: false, note: "" };
    const purchasable = Boolean(availability.purchasable);
    const cardAvailable = purchasable && Boolean(cardDecision.available);
    const cryptoAvailable = purchasable && Boolean(cryptoDecision.available);

    setMethodState(cardInput, cardOption, {
      available: cardAvailable,
      note: cardDecision.note,
    });
    setMethodState(cryptoInput, cryptoOption, {
      available: cryptoAvailable,
      note: cryptoDecision.note,
    });
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
      // `plan.monthly` is what a checkout charges with no code. A code replaces it, and
      // the price it replaced stays on screen so the discount is visible rather than
      // remembered.
      priceLabel.textContent = trialSelected
        ? `$0 today, then ${money(plan.monthly)}/month unless cancelled`
        : period === "annual"
          ? `${money(plan.annual)} per year`
          : discounted
            ? `${money(discounted.amount)} per month, was ${money(discounted.was)}`
            : `${money(plan.monthly)} per month`;
    }
    // The box prices against the plan being bought, so it is told which one that is and
    // what it costs before anybody presses Apply.
    const box = dialog.querySelector("[data-discount]");
    if (box) box.dataset.discountFull = String(plan.monthly);
    if (summary) {
      // The company that will really take the money, sent with the plan. Its name used to
      // be written into this file, so the sentence said "Creem" on a server set to any
      // other company — and named a company the buyer would never reach.
      const cardCompany = cardDecision.company || "The payment company";
      summary.textContent = trialSelected
        ? `${cardCompany} will show the seven-day trial and the first charge date before you confirm.`
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
        // Where this button really leads. Both company names used to be written here, so
        // the button promised Creem or NOWPayments whatever the server was set to use.
        const company =
          selectedMethod === "card"
            ? cardDecision.company
            : selectedMethod === "crypto"
              ? cryptoDecision.company
              : "";
        submit.textContent = selectedMethod
          ? company
            ? `Continue to ${company}`
            : "Continue to secure payment"
          : "Payment method unavailable";
      }
    }
  }

  function open(planCode, cycle, trigger) {
    if (!(catalog.plans || {})[planCode]) return;
    selectedPlan = planCode;
    // A code priced for one plan is not priced for another, so opening the popup on a
    // different plan starts with no code rather than with the last one.
    discounted = null;
    trialSelected = cycle === "trial_7_day";
    activeTrigger = trigger || null;
    // A period the server switched off cannot be chosen, not even by a link that asks
    // for it. Fall back to monthly rather than opening checkout on something unbuyable.
    const annualRadio = periodRadios.find((radio) => radio.value === "annual");
    const wanted = cycle === "annual" && annualRadio && !annualRadio.disabled
      ? "annual"
      : "monthly";
    periodRadios.forEach((radio) => {
      radio.checked = radio.value === wanted;
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
  // A code was applied or cleared beside the crypto choice. The box owns the code; this
  // file owns the price line in the popup, so it listens rather than being written into.
  dialog.addEventListener("hm:discount", (event) => {
    const detail = event.detail || {};
    discounted = detail.code && detail.was ? { amount: detail.amount, was: detail.was } : null;
    refresh();
  });
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
