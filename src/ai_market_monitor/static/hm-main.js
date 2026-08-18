/* Behaviour for `/main`.
 *
 * Everything sequenced or measured is here; anything a CSS transition can do is in
 * `hm-main.css`. Both go through the same shared layer, so the whole page moves at one
 * speed and one change retunes it.
 *
 * Built on `hm-motion.js` (Motion One, vendored at /static/vendor/motion.min.js) and
 * `hm-dialog.js`. Neither is written again here: the focus trap, the Escape key, the
 * backdrop click and the return of the keyboard to whatever opened a popup are the
 * dialog layer's job, and they were written three times before it existed.
 *
 * The reduced-motion promise is kept by `hm-motion.js` itself: when somebody has asked
 * for less movement, `animate()` places the element at its final keyframe instead of
 * travelling to it. Nothing here needs to check, and nothing here can forget.
 */

import { manageDialog, paintIcons } from "./hm-dialog.js";
import { animate, countTo, prefersReducedMotion, settleIn, whenSeen } from "./hm-motion.js";

const root = document.querySelector("[data-main-root]");
if (root) {
  settleBand();
  countEveryFigure();
  drawTheRing();
  openTheExplainer();
  openThePassport();
  settleTiles();
}

/* ── The band ───────────────────────────────────────────────────────────────────
   It arrives as one surface, and the live mark pulses exactly once behind it. Marking
   the band settled is what starts that pulse, so the ring expands when the page is
   ready rather than while it is still assembling. */
function settleBand() {
  const band = document.querySelector("[data-main-now]");
  if (!band) return;
  const arriving = animate(
    band,
    { opacity: [0, 1], transform: ["translateY(10px)", "translateY(0px)"] },
  );
  const settled = () => { band.dataset.settled = "true"; };
  if (arriving?.finished) arriving.finished.then(settled, settled);
  else settled();
}

/* ── Counting up ────────────────────────────────────────────────────────────────
   Only counts of things a person can go and look at. Never a price: a price that
   visibly travels through values it never had is invented market data.

   Each figure counts when it is actually scrolled into view, so a tile below the fold
   has still counted by the time somebody reaches it. */
function countEveryFigure() {
  for (const node of document.querySelectorAll("[data-main-count]")) {
    const target = Number.parseInt(node.dataset.mainCount, 10);
    if (!Number.isFinite(target)) continue;
    if (target === 0 || prefersReducedMotion()) {
      node.textContent = String(target);
      continue;
    }
    node.dataset.countValue = "0";
    node.textContent = "0";
    whenSeen(node, () => countTo(node, target));
  }
}

/* ── The ring ───────────────────────────────────────────────────────────────────
   How much of a person's own list is true, drawn from empty to its real value once.
   The movement is the meaning: the arc grows to exactly the share that passed.

   The value is read from the element the server rendered, never recomputed here — a
   second place that worked out a percentage is a second place that could disagree. */
function drawTheRing() {
  const ring = document.querySelector("[data-main-ring]");
  const path = ring?.querySelector("[data-main-ring-path]");
  if (!ring || !path) return;

  const share = Math.max(0, Math.min(100, Number.parseInt(ring.dataset.value, 10) || 0));
  let length = 0;
  try {
    length = path.getTotalLength();
  } catch {
    return;
  }
  if (!length) return;

  const offset = length * (1 - share / 100);
  path.style.strokeDasharray = String(length);
  path.style.strokeDashoffset = String(length);

  whenSeen(ring, () => {
    const drawn = animate(path, { strokeDashoffset: [length, offset] }, { duration: 0.6 });
    /* Commit the value the arc settled on. A finished Web Animations animation stops
       holding its own end state, and the inline "empty" offset set above would win the
       moment it did — the ring would fill and then silently empty itself again. */
    const settle = () => { path.style.strokeDashoffset = String(offset); };
    if (drawn?.finished) drawn.finished.then(settle, settle);
    else settle();
  });
}

/* ── "What does this mean" ──────────────────────────────────────────────────────
   One popup, filled by whichever tile opened it. Four copies of the same markup would
   be four places for the same fix to be missed, and four dialogs in one document all
   answering to the same hooks.

   The points arrive as text from the server and are written with `textContent`, so a
   value can never be read as markup. */
function openTheExplainer() {
  const dialog = document.querySelector("[data-main-explain-dialog]");
  if (!dialog) return;
  /* Escape, the backdrop and the return of the keyboard are the dialog layer's job. */
  const { open } = manageDialog(dialog, { closers: ["[data-main-explain-close]"] });
  const title = dialog.querySelector("[data-main-explain-title]");
  const body = dialog.querySelector("[data-main-explain-body]");

  for (const button of document.querySelectorAll("[data-main-explain]")) {
    button.addEventListener("click", () => {
      title.textContent = button.dataset.explainTitle || "What this means";
      body.replaceChildren(
        ...String(button.dataset.explainBody || "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => {
            const item = document.createElement("li");
            item.textContent = line;
            return item;
          }),
      );
      open(button);
      paintIcons(dialog);
    });
  }
}

/* ── The Passport popup ─────────────────────────────────────────────────────────
   Opened, not linked to. A link straight to the full Passport is a visible button that
   leads to a "not found" page whenever the coin has no published record; the popup
   answers that case in words, with the link to the full Passport inside it once there
   is one.

   The popup itself belongs to the shared dialogs module. This only says which coin,
   under which standard, and from which page — so the "Open the full Passport" link
   inside it stays on the path that opened it. */
function openThePassport() {
  for (const button of document.querySelectorAll("[data-main-passport]")) {
    button.addEventListener("click", () => {
      window.HilalPassportQuickView?.open(
        {
          asset: button.dataset.mainPassport,
          methodologyId: button.dataset.standard || "",
          basePath: "/dashboard/market",
        },
        button,
      );
    });
  }
}

/* Cards arrive one just after the one before it, so a person's eye follows the order of
   the page rather than being handed all of it at once. */
function settleTiles() {
  whenSeen(document.querySelector("[data-main-tiles]"), () => {
    settleIn(document.querySelectorAll("[data-main-tile]"));
  });
}
