/* What the person is looking at, in one place.
 *
 * Hilal needs to answer "why is this not ready?" about the thing actually in front of
 * somebody. That means the assistant has to know the page, the part of it in view, and
 * — on the canvas — the monitor being drawn.
 *
 * The temptation is for the assistant to go and read the page itself: query the board,
 * walk the cards, work out what each one means. That is the mistake this codebase keeps
 * paying for. The canvas already owns the words for a card, the sentence for a monitor
 * and the checklist for a draft; a second reader would produce a second opinion, and
 * the two would drift the first time either changed.
 *
 * So the direction is reversed. A page **publishes** what it is showing, in its own
 * words, through `publish()`. This module only stores the last thing published and
 * hands it over. It never interprets, and it never reads a page it was not given.
 *
 * The one thing it works out for itself is which section is on screen, because that is
 * a property of scrolling rather than of any page's meaning.
 */

/** The parts of a screen the server will accept a description of.
 *
 *  Closed on purpose. Anything else a page published would be sent to a server that
 *  refuses fields it does not know, and the whole message would fail — so a new page
 *  describing itself in a new way would break the chat everywhere, quietly. A name
 *  that is not here is dropped instead. */
const ACCEPTED = new Set(["board"]);

/** Pages that will describe themselves when asked, by name. */
const askers = new Map();

/**
 * Say what this page is showing, by giving a function that describes it.
 *
 * A function rather than a value, deliberately. Anything worth telling the assistant
 * about changes while the person works — the canvas changes on every keystroke — and a
 * value handed over once would be stale by the time anybody asked a question about it.
 */
export function publish(name, describe) {
  if (typeof describe === "function") askers.set(name, describe);
}

/** Which named section is in front of the person right now.
 *
 *  A page says what its parts are called with `data-hm-part`. Where it has not, the
 *  direct children of the page wrapper are used and named by their own heading — worse,
 *  because the name is then whatever happens to be written there, but never nothing. */
function sectionInView() {
  const marked = [...document.querySelectorAll("[data-hm-part]")];
  const candidates = marked.length
    ? marked
    : [...document.querySelectorAll(".hm-t > *, main > section")];
  if (!candidates.length) return null;

  const middle = window.innerHeight / 2;
  let best = null;
  let bestDistance = Infinity;
  for (const element of candidates) {
    const box = element.getBoundingClientRect();
    if (box.height === 0 || box.bottom < 0 || box.top > window.innerHeight) continue;
    // The one whose middle is nearest the middle of the screen is the one being read.
    const distance = Math.abs((box.top + box.bottom) / 2 - middle);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = element;
    }
  }
  if (!best) return null;
  const named =
    best.dataset.hmPart
    || (best.querySelector("h1, h2, h3") || {}).textContent
    || best.getAttribute("aria-label")
    || "";
  return String(named).trim().replace(/\s+/g, " ").slice(0, 120) || null;
}

/** The coin or Passport open on this page, if it has one. */
function subjectOnScreen() {
  const holder = document.querySelector("[data-asset-symbol], [data-canonical-asset]");
  if (!holder) return null;
  const value = holder.dataset.assetSymbol || holder.dataset.canonicalAsset || "";
  return value.trim().slice(0, 64) || null;
}

/**
 * Everything the assistant may be told about this screen.
 *
 * Shaped exactly like the server's own view of a screen, so nothing has to be
 * translated on the way and nothing can be added on one side only.
 */
export function snapshot() {
  const view = {
    page: document.body.dataset.dashboardPage || null,
    section: sectionInView(),
    subject: subjectOnScreen(),
    board: null,
  };
  for (const [name, describe] of askers) {
    if (!ACCEPTED.has(name)) continue;
    try {
      const value = describe();
      if (value) view[name] = value;
    } catch {
      // A page that cannot describe itself is not a reason to lose the message. The
      // assistant is simply told less, and says so rather than guessing.
    }
  }
  return view;
}
