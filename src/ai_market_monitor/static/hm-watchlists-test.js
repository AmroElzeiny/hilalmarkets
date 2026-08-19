/* The Monitors page at /dashboard/monitors.
 *
 * Everything here is about finding one monitor among several and being sure before
 * changing it. Nothing on this page computes a fact: the server decided what each
 * monitor is doing and what is holding it back, and this file only filters, asks and
 * animates.
 *
 * Finding a card is `hm-card-filter.js` and opening a popup is `hm-dialog.js`, both
 * shared with the Opportunities page. Motion comes from `hm-motion.js`, where
 * `prefers-reduced-motion` is decided once for the whole path.
 */

import { createCardFilter } from "./hm-card-filter.js";
import { manageDialog } from "./hm-dialog.js";
import { animate, attention, prefersReducedMotion } from "./hm-motion.js";

const root = document.querySelector("[data-watchlists-root]");
if (root) start(root);

/** What each question asks, and what pressing the button really does. */
const QUESTIONS = {
  pause: {
    title: "Pause this monitor?",
    body: "It stops checking the market. Everything it has already found is kept, and you can start it again whenever you like.",
    go: "Pause it",
  },
  resume: {
    title: "Start this monitor again?",
    body: "It goes back to checking the market for you, with exactly the conditions it had before.",
    go: "Start it again",
  },
  archive: {
    title: "Put this monitor away?",
    body: "It stops checking and leaves this page. Everything it found stays readable, and nothing is deleted.",
    go: "Put it away",
  },
  // A corrected copy of a monitor is waiting. Both answers change something, so both
  // are asked the same way every other change on this page is asked.
  repair: {
    title: "Look at the fix?",
    body: "We prepared a corrected copy of this monitor. Opening it switches nothing on — you read it first, and it only starts working if you approve it.",
    go: "Open the fix",
  },
  repair_discard: {
    title: "Throw the fix away?",
    body: "The corrected copy is thrown away, and this monitor keeps working exactly as it does now.",
    go: "Throw it away",
  },
};

function start(scope) {
  const find = (selector) => scope.querySelector(selector);

  /* ── Showing the right cards ─────────────────────────────────────────────── */

  const filter = createCardFilter({
    cards: scope.querySelectorAll("[data-w-card]"),
    buttons: [...scope.querySelectorAll("[data-w-filter]")],
    bucketOfButton: (button) => button.dataset.wFilter,
    // "Needs a look" cuts across the other three: a paused monitor and a watching one
    // can both need it. A card carries every group it belongs to rather than only one.
    bucketsOf: (card) =>
      card.dataset.attention === "true"
        ? [card.dataset.bucket, "attention"]
        : [card.dataset.bucket],
    grid: find("[data-w-grid]"),
    nothing: find("[data-w-nothing]"),
    said: find("[data-w-said]"),
    search: find("[data-w-search]"),
    reset: find("[data-w-reset]"),
    countWords: (shown) => `${shown} ${shown === 1 ? "monitor" : "monitors"} shown.`,
    emptyWords: "No monitor matches that.",
  });

  /* ── "Show me" on the banner ─────────────────────────────────────────────── */

  const jump = find("[data-w-jump]");
  if (jump) {
    jump.addEventListener("click", () => {
      const shown = filter.show("attention", { announce: false });
      const first = filter.first();
      if (first) {
        first.scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "center",
        });
        // One pulse on the card being pointed at, so "show me" actually shows.
        attention(first);
      }
      filter.say(`${shown} ${shown === 1 ? "monitor needs" : "monitors need"} a look.`);
    });
  }

  /* ── Asking before changing anything ─────────────────────────────────────── */

  const dialog = document.querySelector("[data-w-dialog]");
  const ask = manageDialog(dialog, {
    closers: ["[data-w-ask-close]", "[data-w-ask-cancel]"],
  });
  const form = dialog.querySelector("[data-w-ask-form]");

  for (const button of scope.querySelectorAll("[data-w-ask]")) {
    button.addEventListener("click", () => {
      const question = QUESTIONS[button.dataset.wAsk] || QUESTIONS.pause;
      dialog.querySelector("[data-w-ask-title]").textContent = question.title;
      dialog.querySelector("[data-w-ask-body]").textContent = question.body;
      dialog.querySelector("[data-w-ask-name]").textContent = button.dataset.name;
      dialog.querySelector("[data-w-ask-go-label]").textContent = question.go;
      form.action = button.dataset.action;
      ask.open(button);
    });
  }

  /* ── What is holding a monitor back ──────────────────────────────────────── */

  const explain = document.querySelector("[data-w-explain]");
  const why = manageDialog(explain, {
    closers: ["[data-w-explain-close]", "[data-w-explain-cancel]"],
  });

  for (const button of scope.querySelectorAll("[data-w-holding]")) {
    button.addEventListener("click", () => {
      const share = Math.max(0, Math.min(100, Number(button.dataset.share) || 0));
      explain.querySelector("[data-w-explain-title]").textContent = button.dataset.name;
      explain.querySelector("[data-w-explain-share]").textContent = `${share}%`;
      const card = button.closest("[data-w-card]");
      const editable = card && card.querySelector('a[href*="/builder"]');
      if (editable) {
        explain.querySelector("[data-w-explain-edit]").href = editable.getAttribute("href");
      }
      why.open(button);
      // The bar fills to the real share, from nothing, once — the movement is the
      // number, so a person sees "most of the time" rather than reading it.
      const fill = explain.querySelector("[data-w-explain-fill]");
      fill.style.width = "0%";
      window.requestAnimationFrame(() => { fill.style.width = `${share}%`; });
    });
  }

  /* ── Arriving ────────────────────────────────────────────────────────────── */

  filter.settle();
  if (!prefersReducedMotion()) {
    const banner = find(".w-banner");
    if (banner) animate(banner, { opacity: [0, 1] }, { duration: 0.24 });
  }
}
