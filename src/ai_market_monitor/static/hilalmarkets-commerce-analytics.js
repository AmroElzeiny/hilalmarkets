(() => {
  "use strict";

  const result = document.querySelector("[data-checkout-result]");
  if (result) window.requestAnimationFrame(() => result.focus({ preventScroll: true }));

  const marker = document.querySelector("[data-commerce-event]");
  if (!marker) return;

  const allowedEvents = new Set([
    "checkout_started",
    "checkout_completed",
    "checkout_cancelled",
    "checkout_failed",
  ]);
  const allowedPlans = new Set(["demo", "trader", "pro"]);
  const allowedIntervals = new Set(["monthly", "annual"]);
  const eventName = String(marker.dataset.commerceEvent || "");
  const planCode = String(marker.dataset.planCode || "");
  const billingInterval = String(marker.dataset.billingInterval || "");
  const eventScope = String(marker.dataset.eventScope || "").slice(0, 128);

  if (
    !allowedEvents.has(eventName)
    || (planCode && !allowedPlans.has(planCode))
    || (billingInterval && !allowedIntervals.has(billingInterval))
  ) return;

  const storageKey = `hm-commerce-event:${eventName}:${eventScope}`;
  let sent = false;

  function wasSent() {
    try {
      return window.sessionStorage.getItem(storageKey) === "sent";
    } catch {
      return sent;
    }
  }

  function rememberSent() {
    sent = true;
    try {
      window.sessionStorage.setItem(storageKey, "sent");
    } catch {
      // Session storage is optional; the in-memory guard still prevents repeats.
    }
  }

  function emit() {
    if (sent || wasSent()) return;
    if (document.documentElement.dataset.consentAnalytics !== "granted") return;
    window.dataLayer = window.dataLayer || [];
    const payload = {
      event: eventName,
      page_path: window.location.pathname,
    };
    if (planCode) payload.plan_code = planCode;
    if (billingInterval) payload.billing_interval = billingInterval;
    window.dataLayer.push(payload);
    rememberSent();
  }

  emit();
  window.addEventListener("hm:consent-updated", emit);
})();
