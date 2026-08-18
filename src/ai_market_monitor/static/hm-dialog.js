/* Opening and closing a popup, once, for every page on the redesigned dashboard pages.
 *
 * A real <dialog> opened with showModal() is what gives a popup its focus trap, its
 * Escape key and its inert background — from the browser, rather than from hand-written
 * key handlers that always miss a case. What the browser does *not* do is give the
 * keyboard back to whatever opened the popup, close on a backdrop click, or animate.
 *
 * That missing part was written three times: once for the Passport popups, once for the
 * Watchlists page and once for Opportunities. Three copies of the same twenty lines is
 * three places for the same bug to be fixed twice and missed once. It lives here now,
 * and every popup on the path uses it.
 */

import { dismiss, reveal } from "./hm-motion.js";

/** Draw the icons inside a piece of markup that has just been put on the page. */
export function paintIcons(scope) {
  if (!scope || typeof window.icon !== "function") return;
  scope.querySelectorAll("[data-icon]").forEach((element) => {
    element.innerHTML = window.icon(element.dataset.icon, element.dataset.iconClass || "icon");
  });
}

/**
 * Give one <dialog> everything a popup needs beyond what the browser provides.
 *
 * Returns `open(trigger)` and `close()`. Focus goes back to `trigger` however the
 * popup was dismissed — the button, Escape, or a click on the backdrop — because the
 * work is done in the dialog's own `close` event rather than in each closing path.
 */
export function manageDialog(dialog, { closers = [] } = {}) {
  if (!dialog) return { open() {}, close() {} };
  let opener = null;

  function open(trigger) {
    opener = trigger || document.activeElement;
    if (!dialog.open) dialog.showModal();
    reveal(dialog.querySelector(".t-dialog-shell") || dialog);
  }

  async function close() {
    if (!dialog.open) return;
    await dismiss(dialog.querySelector(".t-dialog-shell") || dialog);
    dialog.close();
  }

  dialog.addEventListener("close", () => {
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    opener = null;
  });

  /* Clicking the backdrop closes. The dialog element itself is the backdrop's hit
     area, so a click landing on it — and not on the shell — means "outside". */
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });

  /* A <dialog> is moved to the top layer and is not a descendant of the page wrapper,
     so its own buttons are looked for inside it rather than in the page. */
  for (const selector of closers) {
    for (const button of dialog.querySelectorAll(selector)) {
      button.addEventListener("click", () => close());
    }
  }

  return { open, close };
}
