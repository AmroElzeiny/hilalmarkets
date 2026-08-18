/* Finding one card among many, once, for every page on the redesigned dashboard pages.
 *
 * Watchlists and Opportunities both answer the same two questions — "show me only the
 * ones like this" and "show me the one called that" — and both have to do the same
 * quiet things well: announce the result once rather than per keystroke, animate only
 * the cards that were not already there, and offer a way back when nothing matches.
 *
 * Written twice, those quiet parts drift: one page waits before speaking and the other
 * does not, and nothing on screen shows which. One owner, two callers.
 *
 * Nothing here decides what a card *is*. The page passes in `bucketsOf`, so the words a
 * card is grouped under stay a server decision on both pages.
 */

import { prefersReducedMotion, settleIn } from "./hm-motion.js";

/** How long typing settles before the result is spoken, in milliseconds. */
const SPEAK_AFTER = 400;

export function createCardFilter({
  cards,
  buttons = [],
  bucketsOf,
  bucketOfButton,
  nameOf = (card) => card.dataset.name || "",
  grid = null,
  nothing = null,
  said = null,
  search = null,
  reset = null,
  countWords = (shown) => `${shown} shown.`,
  emptyWords = "Nothing matches that.",
}) {
  const all = Array.from(cards || []);
  let bucket = "all";
  let query = "";

  function say(text) {
    if (!said) return;
    said.textContent = "";
    window.requestAnimationFrame(() => { said.textContent = text; });
  }

  function matches(card) {
    if (bucket !== "all" && !bucketsOf(card).includes(bucket)) return false;
    return !query || nameOf(card).includes(query);
  }

  function apply({ announce = true } = {}) {
    let shown = 0;
    const arriving = [];
    for (const card of all) {
      const wanted = matches(card);
      if (wanted && card.hidden) arriving.push(card);
      card.hidden = !wanted;
      if (wanted) shown += 1;
    }
    if (nothing) nothing.hidden = shown > 0;
    if (grid) grid.hidden = shown === 0;

    // Only the cards that were not already there move. Re-animating the whole grid on
    // every keystroke makes a page that is being typed into look like it is flickering.
    if (arriving.length) settleIn(arriving, { from: 8 });

    if (announce) say(shown === 0 ? emptyWords : countWords(shown));
    return shown;
  }

  function press(which) {
    bucket = which;
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(bucketOfButton(button) === which));
    }
  }

  for (const button of buttons) {
    button.addEventListener("click", () => {
      press(bucketOfButton(button));
      apply();
    });
  }

  if (search) {
    let typingTimer = null;
    search.addEventListener("input", () => {
      // Waited on, not answered per keystroke: a live region that speaks on every
      // letter is a live region nobody can listen to.
      window.clearTimeout(typingTimer);
      query = search.value.trim().toLowerCase();
      apply({ announce: false });
      typingTimer = window.setTimeout(() => apply(), SPEAK_AFTER);
    });
  }

  if (reset) {
    reset.addEventListener("click", () => {
      query = "";
      if (search) search.value = "";
      press("all");
      apply();
      if (search) search.focus();
    });
  }

  return {
    apply,
    /** Show one group, without a click — used by "show me" buttons. */
    show(which, { announce = true } = {}) {
      query = "";
      if (search) search.value = "";
      press(which);
      return apply({ announce });
    },
    say,
    first: () => all.find((card) => !card.hidden) || null,
    settle: () => settleIn(all, { from: 10, delayStep: 0.035 }),
    reducedMotion: prefersReducedMotion,
  };
}
