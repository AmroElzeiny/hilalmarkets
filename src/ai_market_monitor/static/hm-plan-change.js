/**
 * The form that cancels a plan somebody is already paying for.
 *
 * Nothing here decides anything: whether a plan card carries a Cancel button was decided
 * on the server, and every sentence in the form — the reasons and the consent line — is
 * rendered into the page from `services/plan_changes.py`. This file opens the dialog and
 * keeps the "another reason" box honest.
 *
 * It used to drive a second form too, for moving up or down a plan. That form re-priced
 * the card already held, which the owner ruled out on 2026-09-10: a different paid plan
 * is bought at its full price on the normal payment page. The form, its buttons and this
 * half of the script were removed together, because its lead sentence — "There is no new
 * payment page" — had become untrue while nothing could show it any more.
 *
 * Deliberately its own file, not part of `hilalmarkets-billing.js`. That script returns
 * early when the checkout dialog is absent, and the checkout dialog is exactly what is
 * absent on the page of somebody who already has a plan — which is the only page where
 * this form exists at all.
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
  if (!cancelDialog) return;
  wireOtherReason(cancelDialog);
  document.querySelectorAll("[data-cancel-plan-trigger]").forEach((trigger) => {
    trigger.addEventListener("click", () => open(cancelDialog));
  });
  cancelDialog.querySelectorAll("[data-cancel-plan-close]").forEach((button) => {
    button.addEventListener("click", () => close(cancelDialog));
  });
})();
