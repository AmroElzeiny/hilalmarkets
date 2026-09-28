/* A dashboard page brought back by the browser's Back button is loaded fresh.
 *
 * Browsers can keep a page in memory when somebody leaves it, and put that exact page back
 * — scripts, buttons and all — when they press Back. Chrome started doing this even for
 * pages marked "do not store", which is every dashboard page.
 *
 * Every dashboard script that sends somebody away first locks what they pressed, so a
 * second click cannot start a second payment or a second publish. A page restored from
 * memory comes back still locked. On the subscription page that meant: pay by crypto, go
 * to the crypto payment page, press Back, choose Card, press "Go to the card payment page"
 * — and nothing at all happened. No message, and no request ever left the browser,
 * because the popup still believed it was sending the first one. The sign-in form had
 * already met the same fault and fixed it for itself (`hilalmarkets-auth.js`); the payment
 * popups never had that fix.
 *
 * Fixing it inside each script would be one fix per script, and the next script written
 * would start without it. Loading the page again fixes all of them at once: every script
 * starts from nothing, and the page shows today's facts — a payment that was just
 * started is listed, and a new checkout gets a new request number. The dashboard marks
 * every page "do not store" precisely because it wants fresh facts, so this only asks
 * the browser to do what the server already asked for.
 *
 * Deliberately a classic script, loaded from `base_dashboard.html` right after
 * `hm-request.js`, so it is listening before any page script runs.
 */

(() => {
  "use strict";

  window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.reload();
  });
})();
