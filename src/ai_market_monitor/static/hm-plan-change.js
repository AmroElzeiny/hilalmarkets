/**
 * The three forms that change a plan somebody is already paying for.
 *
 * Cancelling, moving up and moving down. Nothing here decides anything: which button a
 * plan card carries was decided on the server, and every sentence in these forms — the
 * reasons, and the two consent lines — is rendered into the page from
 * `services/plan_changes.py`. This file opens the right dialog and keeps the consent
 * sentence matching the timing that is actually selected.
 *
 * That last part is the whole reason this is a script rather than plain HTML. The upgrade
 * form offers two timings with two different money promises, and a tick box that keeps
 * showing the other one's sentence would take somebody's agreement to a thing that is not
 * going to happen.
 *
 * Deliberately its own file, not part of `hilalmarkets-billing.js`. That script returns
 * early when the checkout dialog is absent, and the checkout dialog is exactly what is
 * absent on the page of somebody who already has a plan — which is the only page where
 * these forms exist at all.
 */
(function () {
  "use strict";

  /** Show a dialog, or fall back to plain visibility where `showModal` is missing. */
  function open(dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function close(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  /**
   * Wire the "another reason" box inside one form.
   *
   * The box is hidden until it is needed and only *required* while it is showing. A
   * hidden field that is still required stops a form from being sent with nothing on
   * screen explaining why, which is the browser's least helpful behaviour.
   */
  function wireOtherReason(scope) {
    const wrap = scope.querySelector("[data-plan-reason-text]");
    const box = scope.querySelector("[data-plan-reason-textarea]");
    if (!wrap || !box) return;
    const radios = Array.from(scope.querySelectorAll("[data-plan-reason]"));
    function sync() {
      const chosen = radios.find((radio) => radio.checked);
      const wantsText =
        Boolean(chosen) && chosen.getAttribute("data-plan-reason-other") === "true";
      wrap.hidden = !wantsText;
      box.required = wantsText;
      if (!wantsText) box.value = "";
    }
    radios.forEach((radio) => radio.addEventListener("change", sync));
    sync();
  }

  // ── Cancelling ────────────────────────────────────────────────────────────
  const cancelDialog = document.querySelector("[data-cancel-plan-dialog]");
  if (cancelDialog) {
    wireOtherReason(cancelDialog);
    document.querySelectorAll("[data-cancel-plan-trigger]").forEach((trigger) => {
      trigger.addEventListener("click", () => open(cancelDialog));
    });
    cancelDialog.querySelectorAll("[data-cancel-plan-close]").forEach((button) => {
      button.addEventListener("click", () => close(cancelDialog));
    });
  }

  // ── Moving up or down ─────────────────────────────────────────────────────
  const switchDialog = document.querySelector("[data-plan-switch-dialog]");
  if (!switchDialog) return;

  let consents = {};
  const consentNode = document.getElementById("plan-switch-consents");
  if (consentNode) {
    try {
      consents = JSON.parse(consentNode.textContent || "{}");
    } catch (_error) {
      consents = {};
    }
  }

  const planInput = switchDialog.querySelector("[data-plan-switch-code]");
  const kicker = switchDialog.querySelector("[data-plan-switch-kicker]");
  const title = switchDialog.querySelector("[data-plan-switch-title]");
  const lead = switchDialog.querySelector("[data-plan-switch-lead]");
  const timingBox = switchDialog.querySelector("[data-plan-switch-timing]");
  const timingNow = switchDialog.querySelector("[data-plan-switch-timing-now]");
  const timingLater = switchDialog.querySelector("[data-plan-switch-timing-later]");
  const fixedTiming = switchDialog.querySelector("[data-plan-switch-fixed-timing]");
  const reasonBox = switchDialog.querySelector("[data-plan-switch-reasons]");
  const consentLine = switchDialog.querySelector("[data-plan-switch-consent]");
  const submit = switchDialog.querySelector("[data-plan-switch-submit]");
  const timingRadios = Array.from(switchDialog.querySelectorAll("[data-plan-timing]"));
  const reasonRadios = Array.from(reasonBox ? reasonBox.querySelectorAll("[data-plan-reason]") : []);

  wireOtherReason(switchDialog);

  /** The sentence that matches what is selected right now. */
  function consentFor(direction) {
    if (direction === "downgrade") return consents.downgrade || "";
    const chosen = timingRadios.find((radio) => radio.checked);
    const timing = chosen ? chosen.getAttribute("data-plan-timing") : "period_end";
    return timing === "immediate"
      ? consents.upgradeNow || ""
      : consents.upgradePeriodEnd || "";
  }

  let direction = "upgrade";

  function syncConsent() {
    if (consentLine) consentLine.textContent = consentFor(direction);
  }

  timingRadios.forEach((radio) => radio.addEventListener("change", syncConsent));

  document.querySelectorAll("[data-plan-switch-trigger]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      direction = trigger.getAttribute("data-plan-switch") || "upgrade";
      const planCode = trigger.getAttribute("data-plan-code") || "";
      const planName = trigger.getAttribute("data-plan-name") || "this plan";
      if (planInput) planInput.value = planCode;

      const movingUp = direction === "upgrade";
      if (kicker) kicker.textContent = movingUp ? "Upgrade" : "Downgrade";
      if (title) {
        title.textContent = movingUp
          ? `Move up to ${planName}`
          : `Move down to ${planName}`;
      }
      if (lead) {
        lead.textContent = movingUp
          ? "There is no new payment page. The payment company already has your card."
          : "You keep what you have until the end of the period you have already paid " +
            "for. The smaller price starts on your renewal day.";
      }

      // The two timing choices name the plan as well. Every other sentence in this form
      // is already rebuilt from the button that opened it; these two carried the plan
      // name written into the page, so they kept saying "Pro" whichever plan was being
      // moved to — a person would have agreed to a timing for the wrong plan.
      if (timingNow) timingNow.textContent = `I want ${planName} now`;
      if (timingLater) {
        timingLater.textContent = `I want ${planName} at the end of this month`;
      }

      // Only an upgrade offers a choice of when. A downgrade always waits for the end of
      // the paid period, so its timing travels in a hidden field that is enabled only
      // while the visible choice is put away — two fields of the same name, both live,
      // would send two values and the browser would pick one of them for us.
      if (timingBox) timingBox.hidden = !movingUp;
      timingRadios.forEach((radio) => {
        radio.disabled = !movingUp;
      });
      if (fixedTiming) fixedTiming.disabled = movingUp;

      // A downgrade asks why. Nobody has to explain buying more.
      if (reasonBox) reasonBox.hidden = movingUp;
      reasonRadios.forEach((radio) => {
        radio.required = !movingUp;
        radio.disabled = movingUp;
        if (movingUp) radio.checked = false;
      });
      const otherWrap = switchDialog.querySelector("[data-plan-reason-text]");
      const otherBox = switchDialog.querySelector("[data-plan-reason-textarea]");
      if (movingUp && otherWrap && otherBox) {
        otherWrap.hidden = true;
        otherBox.required = false;
        otherBox.value = "";
      }

      if (submit) submit.textContent = movingUp ? `Move up to ${planName}` : `Move down to ${planName}`;
      const consentBox = switchDialog.querySelector('input[name="switch_consent"]');
      if (consentBox) consentBox.checked = false;
      syncConsent();
      open(switchDialog);
    });
  });

  switchDialog.querySelectorAll("[data-plan-switch-close]").forEach((button) => {
    button.addEventListener("click", () => close(switchDialog));
  });
})();
