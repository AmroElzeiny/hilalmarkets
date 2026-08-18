/* Back to the top, on the server-rendered public pages.
 *
 * The React half of the site does this in `BackToTop` (components/SiteChrome.tsx). This
 * is the same behaviour for the pages React does not draw, and it deliberately matches
 * it line for line: appear after 700px of scrolling, scroll smoothly back, and put the
 * keyboard on the page's opening region rather than leaving it wherever it was.
 *
 * Nothing here decides where the button sits. That is `.hm-to-top` in
 * `hilalmarkets-public.css`, the one sheet both halves of the site load.
 */

(() => {
  "use strict";

  const button = document.querySelector("[data-hm-to-top]");
  if (!button) return;

  /* The same threshold the React pages use. Below it there is nothing to go back to, and
     a button that appears immediately is a button in the way. */
  const AFTER = 700;

  /* Shown only once scripting has run, so a reader without it never sees a control that
     could not work for them. */
  button.hidden = false;

  let shown = null;

  function settle() {
    const past = window.scrollY > AFTER;
    if (past === shown) return;
    shown = past;
    button.dataset.shown = String(past);
    button.setAttribute("aria-hidden", String(!past));
    button.tabIndex = past ? 0 : -1;
  }

  button.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
    // The keyboard follows the page. Without this the reader is back at the top visually
    // and still tabbing through the footer they just left.
    document.getElementById("main-content")?.focus?.();
  });

  window.addEventListener("scroll", settle, { passive: true });
  settle();
})();
