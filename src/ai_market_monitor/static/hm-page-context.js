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
const ACCEPTED = new Set([
  "board",
  "screened_market",
  "opportunities",
  "watch_plans",
  "passport",
  "watchlist",
  "connections",
  "research",
  "settings",
  "support",
  "subscription",
  "report",
]);

/** Length caps for what is handed over. The server's own shapes in
 *  `schemas/hilal_chat.py` hold these same numbers, and the schema is the owner:
 *  `tests/unit/test_invariant_hilal_page_context.py` fails if the two drift apart.
 *  Over-long words are cut here, never refused — a description that grew is not a
 *  reason to lose the person's message. */
const PAGE_HEADING_MAX = 120;
const PAGE_SUMMARY_MAX = 500;
const PAGE_POINTS_MAX = 8;
const PAGE_POINT_MAX = 160;
const CARD_INPUTS_MAX = 6;
const CARD_INPUT_LABEL_MAX = 80;
const CARD_INPUT_VALUE_MAX = 120;

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
      if (!value) continue;
      view[name] = name === "board" ? truncateBoard(value) : truncatePageNote(value);
    } catch {
      // A page that cannot describe itself is not a reason to lose the message. The
      // assistant is simply told less, and says so rather than guessing.
    }
  }
  return view;
}

/** Cut one page's own words to the caps the server holds. */
function truncatePageNote(note) {
  if (typeof note === "string") {
    // A bare string is not an object the server accepts — sending it through
    // would fail the whole message — so it travels as the summary instead.
    const text = note.trim().slice(0, PAGE_SUMMARY_MAX);
    return text ? { summary: text } : null;
  }
  if (!note || typeof note !== "object") return null;
  const out = {};
  if (typeof note.heading === "string" && note.heading.trim()) {
    out.heading = note.heading.trim().slice(0, PAGE_HEADING_MAX);
  }
  if (typeof note.summary === "string" && note.summary.trim()) {
    out.summary = note.summary.trim().slice(0, PAGE_SUMMARY_MAX);
  }
  if (Array.isArray(note.points)) {
    const points = note.points
      .filter((point) => typeof point === "string" && point.trim())
      .map((point) => point.trim().slice(0, PAGE_POINT_MAX))
      .slice(0, PAGE_POINTS_MAX);
    if (points.length) out.points = points;
  }
  return Object.keys(out).length ? out : null;
}

/** Cut the canvas readout to the caps the server holds, so a long board can never
 *  fail the message it travels with. Reads the canvas's own words; changes none. */
function truncateBoard(board) {
  if (!board || typeof board !== "object") return null;
  const text = (value, limit) =>
    typeof value === "string" && value ? value.slice(0, limit) : value || null;
  const list = (values, count, each) =>
    Array.isArray(values)
      ? values
        .filter((item) => typeof item === "string" && item)
        .map((item) => item.slice(0, each))
        .slice(0, count)
      : [];
  return {
    sentence: text(board.sentence, 600),
    ready_percent: board.ready_percent || 0,
    cards: Array.isArray(board.cards)
      ? board.cards.slice(0, 32).map((card) => ({
        label: text(card.label, 80),
        reads: text(card.reads, 160),
        required: card.required !== false,
        inside: text(card.inside, 48),
        set_aside: Boolean(card.set_aside),
        needs: list(card.needs, 6, 80),
        inputs: Array.isArray(card.inputs)
          ? card.inputs.slice(0, CARD_INPUTS_MAX).map((input) => ({
            label: text(input.label, CARD_INPUT_LABEL_MAX),
            filled: Boolean(input.filled),
            value: input.value === null || input.value === undefined
              ? null
              : String(input.value).slice(0, CARD_INPUT_VALUE_MAX),
          }))
          : [],
      }))
      : [],
    checks: Array.isArray(board.checks)
      ? board.checks.slice(0, 32).map((check) => ({
        tone: check.tone,
        text: text(check.text, 240),
      }))
      : [],
    watching: text(board.watching, 120),
    ways_to_be_told: list(board.ways_to_be_told, 8, 60),
    controls: list(board.controls, 24, 60),
    how_to: list(board.how_to, 24, 160),
  };
}
