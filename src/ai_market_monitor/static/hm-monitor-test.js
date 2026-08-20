/* The monitor canvas at /dashboard/create-monitor.
 *
 * Brings together the four parts that each own one thing: the contract reader, the
 * plan, the board, and the panels this file draws — the condition library, the
 * settings panel, the readout and the checks.
 *
 * There is no assistant here. Every rule this platform can run is reachable from the
 * list, with a form built from the server's own description of it, so authoring never
 * depends on a model being available or on it having understood a sentence.
 */

import { attention, dismiss, prefersReducedMotion, reveal, settleIn } from "./hm-motion.js";
import { publish } from "./hm-page-context.js";
import { categoryLook, loadCatalog, searchMechanics } from "./hm-monitor-catalog.js";
import {
  PlanStore,
  checkPlan,
  groupWord,
  missingOn,
  planFromServer,
  planIsReady,
  planReadback,
  planSentence,
  readiness,
  ruleClause,
} from "./hm-monitor-plan.js";
import { Board } from "./hm-monitor-board.js";

const root = document.querySelector("[data-monitor-root]");
if (root) {
  // "Try again" is wired before the contract is read, not after it has been read.
  //
  // It used to be registered inside `start`, past the `await` that reads the contract.
  // That is the one path where the button is never reached: the read fails, `start`
  // stops at the `await`, the error banner appears — carrying a button with no handler
  // on it. The only way out of a failed read had nothing behind it.
  const retry = root.querySelector("[data-contract-retry]");
  if (retry) retry.addEventListener("click", () => window.location.reload());
  start(root).catch((error) => showContractError(root, error));
}

const icon = (name, cls = "icon") => (window.icon ? window.icon(name, cls) : "");
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );

const PAGE_SIZE = 40;

/* Why the board is empty, in the words that match what actually happened.
 *
 * "It could not be read" and "it did not answer in time" are different things to a
 * person: the first sounds like something is broken, the second like something worth
 * trying again in a moment. Both used to read the same. */
function showContractError(scope, failure) {
  const loading = scope.querySelector("[data-loading]");
  const error = scope.querySelector("[data-contract-error]");
  const reason = scope.querySelector("[data-contract-reason]");
  if (loading) loading.hidden = true;
  if (error) error.hidden = false;
  if (reason && window.hmWaitedTooLong && window.hmWaitedTooLong(failure)) {
    reason.textContent =
      "The server did not answer in time, so the board stays empty. Nothing was guessed"
      + " in its place, and your saved draft is untouched.";
  }
}

/* The board one monitor was drawn from, or nothing.
 *
 * "Nothing" covers three different situations and they are deliberately not told apart
 * here: the monitor was never drawn on this canvas, the monitor is not on this account,
 * or the server could not answer. Every one of them ends the same way — this canvas will
 * not show a board for that monitor — and the message the server sends says which. What
 * must never happen is an empty board appearing under that monitor's name.
 */
async function readSavedBoard(boardUrl, monitorId) {
  if (!boardUrl) return { plan: null, reason: "" };
  try {
    const response = await fetch(`${boardUrl}/${encodeURIComponent(monitorId)}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body && body.detail;
      return {
        plan: null,
        reason:
          (detail && detail.message)
          || "That monitor could not be read just now. Nothing about it has changed.",
        monitor: null,
      };
    }
    return {
      plan: body.plan || null,
      reason: body.reason || "",
      monitor: body.monitor || null,
    };
  } catch {
    return {
      plan: null,
      reason: "That monitor could not be read just now. Nothing about it has changed.",
      monitor: null,
    };
  }
}

async function start(scope) {
  const find = (selector) => scope.querySelector(selector);
  const limits = readJson(scope.dataset.limits, {});
  const channels = readJson(scope.dataset.channels, []);

  const catalog = await loadCatalog(scope.dataset.contractUrl);
  find("[data-loading]").hidden = true;
  find("[data-contract-error]").hidden = true;

  /* Which monitor is being changed, and the board it was drawn from.
   *
   * Read before the board exists, because the board a person sees on a page that says
   * "change this monitor" must be that monitor's own — never an empty one they could
   * switch on by accident, replacing their rules with nothing. When the server has no
   * board to give, `opening` stays null and the notice below says why. */
  const asked = (scope.dataset.monitorId || "").trim();
  const opened = asked ? await readSavedBoard(scope.dataset.boardUrl, asked) : null;
  const opening = opened && opened.plan ? planFromServer(opened.plan) : null;

  /* Which monitor this board would really change.
   *
   * Only a monitor whose board actually opened. A monitor that could not be drawn is not
   * being changed by whatever gets drawn here instead: the notice offers a *new* monitor,
   * and switching this board on with that id still attached would replace the person's
   * rules with a board that was never theirs. So the page stops being about it. */
  const monitorId = opening ? asked : "";

  // The compiler's own shape limits win over the ones rendered into the page: the
  // page was drawn once, the contract is read now.
  const store = new PlanStore({
    limits: catalog.limits || limits,
    // Which ways of being told can really reach this person. Decided by the server,
    // handed over with the channel list, and never worked out here.
    reachableChannels: new Set(
      channels.filter((channel) => channel.ready !== "false").map((channel) => channel.value),
    ),
    monitorId: monitorId || null,
    opening,
  });

  const announce = find("[data-announce]");
  const coach = find("[data-coach]");
  const stage = find("[data-stage]");

  const channelLabels = (values) =>
    values
      .map((value) => (channels.find((channel) => channel.value === value) || {}).label || value)
      .filter(Boolean);

  const board = new Board(scope, {
    store,
    catalog,
    handlers: {
      channelLabels,
      onSelect: (id) => showInspector(id),
      onOpenSettings: (id) => showInspector(id, { focus: true }),
      onAdd: (parentId) => openLibrary(parentId),
      onRemove: (id) => removeWithUndo(id),
      onStarter: (key) => applyStarter(key),
      onZoom: (scale) => { find("[data-zoom-label]").textContent = `${Math.round(scale * 100)}%`; },
      onCoach: (text) => setCoach(text),
      onAnnounce: (text) => say(text),
      onJoin: (id) => joinBack(id),
    },
  });

  /* ── Announcements and coaching ────────────────────────────────────────── */

  function say(text) {
    if (!text) return;
    announce.textContent = "";
    window.requestAnimationFrame(() => { announce.textContent = text; });
  }

  let coachTimer = null;
  function setCoach(text, { seconds = 0 } = {}) {
    window.clearTimeout(coachTimer);
    if (!text) {
      coach.hidden = true;
      coach.dataset.kind = "";
      coach.innerHTML = "";
      return;
    }
    coach.innerHTML = `${icon("info", "icon-sm")}<span>${escapeHtml(text)}</span>`;
    coach.dataset.kind = "hint";
    coach.hidden = false;
    reveal(coach, { from: 0.96 });
    if (seconds) coachTimer = window.setTimeout(() => setCoach(""), seconds * 1000);
  }

  /* ── Removing, with a way back ─────────────────────────────────────────── */

  function removeWithUndo(id) {
    if (id === "universe" || id === "alert" || id === store.plan.rootId) {
      setCoach("This card is part of every monitor, so it cannot be removed.", { seconds: 4 });
      return;
    }
    const element = board.elements.get(id);
    const done = () => {
      if (!store.remove(id)) return;
      if (currentInspector === id) showInspector(null);
      setCoach("");
      coach.innerHTML = `${icon("undo", "icon-sm")}<span>Card removed.</span><button class="t-action" type="button" data-coach-undo>Undo</button>`;
      // Marked as an offer rather than a hint, so the next change does not clear the
      // one thing on the page that gives the change back.
      coach.dataset.kind = "offer";
      coach.hidden = false;
      window.clearTimeout(coachTimer);
      coachTimer = window.setTimeout(() => setCoach(""), 7000);
    };
    if (element) dismiss(element).then(done);
    else done();
  }

  coach.addEventListener("click", (event) => {
    if (!event.target.closest("[data-coach-undo]")) return;
    store.undo();
    setCoach("");
  });

  /* ── Joining a set-aside card back ─────────────────────────────────────── */

  /**
   * Put a card that was taken off its wire back into the monitor.
   *
   * Where there is one possible home the card goes there, because there is nothing to
   * decide. Where there are several the panel opens on the picker and the person says
   * which — the board never picks a group on their behalf. Choosing the first, or the
   * nearest, would quietly change what the monitor watches for.
   */
  function joinBack(id) {
    const groups = store.groupsFor(id).filter((group) => group.id !== id);
    if (!groups.length) {
      setCoach("There is no group to put this card in yet. Add a group first.", { seconds: 5 });
      return;
    }
    if (groups.length === 1) {
      store.reparent(id, groups[0].id);
      // The card leaves the shelf and rejoins the monitor, which is somewhere else
      // entirely on the board. Following it there is how a person sees where it went.
      window.requestAnimationFrame(() => board.bringIntoView(id));
      return;
    }
    showInspector(id);
    setCoach("Choose which group this card should join, under “Sits inside”.", { seconds: 6 });
    window.requestAnimationFrame(() => {
      const picker = inspectorBody.querySelector("[data-parent-select]");
      if (picker) picker.focus();
    });
  }

  /* ── Starters ──────────────────────────────────────────────────────────── */

  function applyStarter(key) {
    const starter = (catalog.starters || []).find((item) => item.key === key);
    if (!starter) return;
    const entries = [];
    const skipped = [];
    for (const rule of starter.rules || []) {
      const mechanic = catalog.byKey.get(rule.mechanic_key);
      if (!mechanic) { skipped.push(rule.mechanic_key); continue; }
      entries.push({ mechanic, values: rule.values, required: rule.required });
    }
    if (!entries.length) {
      setCoach("That starting point uses something this platform cannot run right now.", { seconds: 5 });
      return;
    }
    store.addRules(entries, { op: starter.join });
    if (skipped.length) {
      setCoach(`${skipped.length} part of that starting point could not be added, so it was left out rather than replaced.`, { seconds: 6 });
    }
    window.requestAnimationFrame(() => board.fit());
  }

  /* ── The condition library ─────────────────────────────────────────────── */

  // Both dialogs sit outside the page wrapper, because a <dialog> is moved to the
  // browser's top layer and is not a descendant of anything on the page.
  const library = document.querySelector("[data-library]");
  const libSearch = library.querySelector("[data-library-search]");
  const libClear = library.querySelector("[data-library-clear]");
  const libRail = library.querySelector("[data-library-rail]");
  const libList = library.querySelector("[data-library-list]");
  const libCount = library.querySelector("[data-library-count]");
  const libDetail = library.querySelector("[data-library-detail]");
  const libEmpty = library.querySelector("[data-library-empty]");
  const libMore = library.querySelector("[data-library-more]");
  const libMoreText = library.querySelector("[data-library-more-text]");
  const libAdd = library.querySelector("[data-library-add]");

  const libraryState = { parentId: null, category: "", query: "", shown: PAGE_SIZE, results: [], chosen: null, opener: null };

  /**
   * The kinds of condition, each with how many match what is typed.
   *
   * The counts follow the search rather than standing still. A list headed
   * "Momentum 21" while showing five results invites a person to click it expecting
   * twenty-one, and a kind with nothing in it for this search says so instead of
   * looking like a place worth trying.
   */
  function paintRail() {
    const matched = searchMechanics(catalog, { query: libraryState.query });
    const counts = new Map();
    for (const item of matched) counts.set(item.category, (counts.get(item.category) || 0) + 1);

    libRail.innerHTML = [
      { key: "", name: "Everything", icon: "layers", count: matched.length },
      ...catalog.categories.map((entry) => ({ ...entry, count: counts.get(entry.key) || 0 })),
    ]
      .filter((entry) => entry.count > 0 || entry.key === libraryState.category)
      .map(
        (entry) => `<button class="m-lib-cat" type="button" data-category="${escapeHtml(entry.key)}" aria-pressed="${entry.key === libraryState.category}">
          ${icon(entry.icon, "icon-sm")}
          <span class="m-lib-cat-name">${escapeHtml(entry.name)}</span>
          <span class="m-lib-cat-count">${entry.count}</span>
        </button>`,
      )
      .join("");
  }

  function paintResults({ keepChosen = false } = {}) {
    libraryState.results = searchMechanics(catalog, {
      query: libraryState.query,
      category: libraryState.category,
    });
    const total = libraryState.results.length;
    const visible = libraryState.results.slice(0, libraryState.shown);

    libCount.textContent = total
      ? `${total} condition${total === 1 ? "" : "s"} to choose from`
      : "";
    libEmpty.hidden = total > 0;
    libList.hidden = total === 0;
    libMore.hidden = total <= visible.length;
    if (libMoreText) libMoreText.textContent = `Show ${Math.min(PAGE_SIZE, total - visible.length)} more`;

    libList.innerHTML = visible
      .map((item) => {
        const look = categoryLook(item.category);
        return `<li>
          <button class="m-lib-item" type="button" data-key="${escapeHtml(item.key)}" aria-selected="${libraryState.chosen === item.key}">
            <span class="m-chip" aria-hidden="true">${icon(look.icon, "icon")}</span>
            <span class="m-lib-name">${escapeHtml(item.label)}</span>
            <span class="t-pill" data-tone="${item.available ? "neutral" : "excluded"}">${item.available ? escapeHtml(look.name) : "Not yet"}</span>
            <span class="m-lib-why">${escapeHtml(item.explanation || "")}</span>
          </button>
        </li>`;
      })
      .join("");

    settleIn(libList.querySelectorAll(".m-lib-item"), { from: 6 });
    if (!keepChosen) chooseMechanic(visible.length ? visible[0].key : null);
  }

  function chooseMechanic(key) {
    libraryState.chosen = key;
    for (const button of libList.querySelectorAll("[data-key]")) {
      button.setAttribute("aria-selected", button.dataset.key === key ? "true" : "false");
    }
    const item = key ? catalog.byKey.get(key) : null;
    libAdd.disabled = !item;
    if (!item) {
      libDetail.innerHTML = `<p class="m-lib-detail-empty">Pick one on the left to read what it does.</p>`;
      return;
    }
    const look = categoryLook(item.category);
    libDetail.innerHTML = `
      <span class="m-chip" aria-hidden="true">${icon(look.icon, "icon")}</span>
      <h3>${escapeHtml(item.label)}</h3>
      <p>${escapeHtml(item.explanation || "")}</p>
      ${item.examples && item.examples.length ? `<p class="m-lib-example">${icon("info", "icon-sm")} ${escapeHtml(item.examples[0])}</p>` : ""}
      ${item.available
        ? `<p class="m-field-help">${item.parameters.filter((parameter) => parameter.required).length} thing${item.parameters.filter((parameter) => parameter.required).length === 1 ? "" : "s"} to set after you add it.</p>`
        : `<p class="m-field-warn">${icon("alert", "icon-sm")}<span>${escapeHtml(item.unavailable_reason || "This one cannot run yet.")}</span></p>`}
    `;
  }

  function openLibrary(parentId) {
    libraryState.parentId = parentId || store.plan.rootId;
    libraryState.opener = document.activeElement;
    libraryState.shown = PAGE_SIZE;
    paintRail();
    paintResults();
    library.showModal();
    window.requestAnimationFrame(() => libSearch.focus());
  }

  function addChosen() {
    const mechanic = catalog.byKey.get(libraryState.chosen);
    if (!mechanic) return;
    const id = store.addRule(mechanic, { parentId: libraryState.parentId });
    library.close();
    window.requestAnimationFrame(() => {
      board.select(id);
      showInspector(id, { focus: true });
      const missing = missingOn(mechanic, store.node(id).values);
      if (missing.length === 1) {
        setCoach(`Set "${missing[0].label}" to finish this card.`, { seconds: 6 });
      } else if (missing.length) {
        setCoach(`${missing.length} things to set on the right to finish this card.`, { seconds: 6 });
      }
    });
  }

  libSearch.addEventListener("input", () => {
    libraryState.query = libSearch.value.trim();
    libraryState.shown = PAGE_SIZE;
    libClear.hidden = !libraryState.query;
    paintRail();
    paintResults();
  });

  libSearch.addEventListener("keydown", (event) => {
    // A search box with something typed in it swallows Escape to clear itself, so the
    // dialog never closed — the one key every dialog has to answer to. Closing is
    // handled here first; the box has its own visible button for clearing.
    if (event.key === "Escape") {
      event.preventDefault();
      library.close();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "Enter") return;
    event.preventDefault();
    if (event.key === "Enter") { addChosen(); return; }
    const first = libList.querySelector("[data-key]");
    if (first) first.focus();
  });

  libClear.addEventListener("click", () => {
    libSearch.value = "";
    libraryState.query = "";
    libClear.hidden = true;
    paintRail();
    paintResults();
    libSearch.focus();
  });

  libRail.addEventListener("click", (event) => {
    const button = event.target.closest("[data-category]");
    if (!button) return;
    libraryState.category = button.dataset.category;
    libraryState.shown = PAGE_SIZE;
    paintRail();
    paintResults();
  });

  // One click reads it, two clicks add it. The first result is chosen for reading as
  // soon as the list appears, so "clicking the chosen one adds it" would have made
  // the very first click do something different from every click after it.
  libList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-key]");
    if (button) chooseMechanic(button.dataset.key);
  });

  libList.addEventListener("dblclick", (event) => {
    const button = event.target.closest("[data-key]");
    if (!button) return;
    chooseMechanic(button.dataset.key);
    addChosen();
  });

  libList.addEventListener("keydown", (event) => {
    const buttons = [...libList.querySelectorAll("[data-key]")];
    const index = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown" && index > -1) {
      event.preventDefault();
      const next = buttons[Math.min(buttons.length - 1, index + 1)];
      next.focus();
      chooseMechanic(next.dataset.key);
    }
    if (event.key === "ArrowUp" && index > -1) {
      event.preventDefault();
      if (index === 0) { libSearch.focus(); return; }
      const previous = buttons[index - 1];
      previous.focus();
      chooseMechanic(previous.dataset.key);
    }
  });

  libList.addEventListener("focusin", (event) => {
    const button = event.target.closest("[data-key]");
    if (button) chooseMechanic(button.dataset.key);
  });

  libMore.addEventListener("click", () => {
    libraryState.shown += PAGE_SIZE;
    paintResults({ keepChosen: true });
  });

  libAdd.addEventListener("click", addChosen);
  library.querySelector("[data-library-close]").addEventListener("click", () => library.close());
  library.querySelector("[data-library-cancel]").addEventListener("click", () => library.close());
  library.querySelector("[data-library-reset]").addEventListener("click", () => {
    libSearch.value = "";
    libraryState.query = "";
    libraryState.category = "";
    libClear.hidden = true;
    paintRail();
    paintResults();
    libSearch.focus();
  });
  library.addEventListener("close", () => {
    if (libraryState.opener && libraryState.opener.isConnected) libraryState.opener.focus();
  });

  /* ── The settings panel ────────────────────────────────────────────────── */

  const inspector = find("[data-inspector]");
  const inspectorKind = find("[data-inspector-kind]");
  const inspectorBody = find("[data-inspector-body]");
  let currentInspector = null;

  function showInspector(id, { focus = false } = {}) {
    currentInspector = id;
    if (!id) {
      inspector.hidden = true;
      inspectorBody.innerHTML = "";
      return;
    }
    const wasHidden = inspector.hidden;
    inspector.hidden = false;
    paintInspector(id);
    if (wasHidden) reveal(inspector, { from: 0.98 });
    // The panel takes a third of the board on a wide screen, so the card being
    // settled can sit behind it. Asked on every selection, not only the first: the
    // panel stays open while a person moves from card to card.
    window.requestAnimationFrame(() => board.keepVisible(id));
    if (focus) {
      const target = inspectorBody.querySelector("[data-first-missing]") || inspectorBody.querySelector("button, select, input");
      if (target) target.focus();
    }
  }

  function paintInspector(id) {
    if (id === "universe") return paintUniversePanel();
    if (id === "alert") return paintAlertPanel();
    const node = store.node(id);
    if (!node) { showInspector(null); return; }
    if (node.kind === "group") return paintGroupPanel(node);
    return paintRulePanel(node);
  }

  /* Where a card sits, including "nowhere".
   *
   * "Set aside" is an option in the same list rather than a separate button, because it
   * is the same question: which group does this card belong to? Offering it here is
   * also what makes cancelling a connection reachable without a drag at all — WCAG 2.2
   * SC 2.5.7 asks for exactly that, and a person using a switch or voice control has no
   * other way to take a wire off. */
  const ASIDE_VALUE = "__aside__";

  function parentPicker(node) {
    const groups = store.groupsFor(node.id).filter((group) => group.id !== node.id);
    if (!groups.length) return "";
    const label = (group) =>
      `${(({ and: "All of these", or: "Any of these", not: "None of these" })[group.op])}${group.id === store.plan.rootId ? " (the main group)" : ""}`;
    const aside = store.isAside(node.id);
    return `<div class="m-field">
      <span class="m-field-label"><span>Sits inside</span></span>
      <select class="m-select" data-parent-select>
        ${groups.map((group) => `<option value="${group.id}" ${group.id === node.parent ? "selected" : ""}>${escapeHtml(label(group))}</option>`).join("")}
        <option value="${ASIDE_VALUE}" ${aside ? "selected" : ""}>Nothing — set this card aside</option>
      </select>
      <p class="m-field-help">${aside
        ? "This card is on the board but outside the monitor. Nothing about it is being watched until you put it in a group."
        : "Moving a card to another group changes when it counts. Choosing &ldquo;nothing&rdquo; cancels its connection and keeps every setting. You can also drag the circle on its left edge onto empty space."}</p>
    </div>`;
  }

  /* ── Which coins ─────────────────────────────────────────────────────────
   *
   * Three ways of answering, and two of them need a real second step. "Coins I name
   * myself" used to store a mode and nothing else, so the board said a person had named
   * coins when they had named none; "One of my Favorites lists" was the same. Both ask
   * the question properly now, against the server's own screened list — never a coin
   * typed free-hand, because a ticker nobody screened is a coin the monitor could never
   * watch. */

  const coinPicker = {
    query: "",
    results: [],
    searching: false,
    notice: "",
    favorites: null,
    loadingFavorites: false,
    failed: "",
  };
  let coinSearchTimer = null;

  /** Which of the two pickers this panel is showing. Said once. */
  const usingFavorites = () =>
    store.plan.universe.mode === "approved_watchlist"
    || Boolean(store.plan.universe.watchlistId);

  const chosenCoins = () => store.plan.universe.symbols || [];

  /**
   * Redraw the "Coins to watch" panel without taking the search box away.
   *
   * The panel is rebuilt from `innerHTML`, so a redraw while somebody is typing would
   * destroy the input they are typing into and drop the caret. The value survives on
   * its own — it is read from `coinPicker.query` — but focus does not, so it is put
   * back on the same control it left.
   */
  function repaintUniverse() {
    if (currentInspector !== "universe") return;
    const active = document.activeElement;
    const wasSearching = Boolean(active && active.matches && active.matches("[data-coin-search]"));
    paintInspector("universe");
    if (!wasSearching) return;
    const again = inspectorBody.querySelector("[data-coin-search]");
    if (!again) return;
    again.focus();
    const end = again.value.length;
    try {
      again.setSelectionRange(end, end);
    } catch {
      // A `search` input refuses selection in some browsers. Focus is the part that
      // matters; losing the caret position is not worth failing the redraw over.
    }
  }

  /* The name of the Favorites list a reopened board points at.
   *
   * A board the server kept holds the list's id and not its name, because a list can be
   * renamed and a name copied at save time would be wrong the moment it was. So the name
   * is filled in here, from the person's own lists, the first time they are read. Until
   * then the readout says "one of your Favorites lists", which is true rather than
   * blank. */
  function nameTheChosenList() {
    const universe = store.plan.universe || {};
    if (universe.mode !== "approved_watchlist" || !universe.watchlistId) return;
    if (universe.watchlistName) return;
    const chosen = (coinPicker.favorites || []).find((item) => item.id === universe.watchlistId);
    if (chosen) store.labelWatchlist(universe.watchlistId, chosen.name);
  }

  async function loadFavorites() {
    if (coinPicker.favorites || coinPicker.loadingFavorites) return;
    coinPicker.loadingFavorites = true;
    try {
      const response = await fetch("/api/v1/dashboard/monitor-canvas/favorites", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("favorites");
      const payload = await response.json();
      coinPicker.favorites = Array.isArray(payload.items) ? payload.items : [];
      coinPicker.failed = "";
      nameTheChosenList();
    } catch {
      coinPicker.favorites = null;
      coinPicker.failed = "Your Favorites lists could not be read just now.";
    } finally {
      coinPicker.loadingFavorites = false;
      repaintUniverse();
    }
  }

  async function searchCoins(query) {
    coinPicker.searching = true;
    repaintUniverse();
    try {
      const url = `/api/v1/dashboard/monitor-canvas/coins?q=${encodeURIComponent(query)}&limit=12`;
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("coins");
      const payload = await response.json();
      // A late answer to an earlier keystroke must not replace the newer one.
      if (coinPicker.query !== query) return;
      coinPicker.results = Array.isArray(payload.items) ? payload.items : [];
      coinPicker.notice = payload.notice || "";
      coinPicker.failed = "";
    } catch {
      coinPicker.results = [];
      coinPicker.failed = "The coin list could not be read just now. Nothing was guessed in its place.";
    } finally {
      coinPicker.searching = false;
      repaintUniverse();
    }
  }

  function coinChips(symbols) {
    if (!symbols.length) return "";
    return `<ul class="m-coins" aria-label="Coins this monitor will watch">
      ${symbols
        .map(
          (symbol) => `<li><span class="m-coin">
            <span class="m-coin-name">${escapeHtml(symbol)}</span>
            <button class="m-coin-drop" type="button" data-coin-remove="${escapeHtml(symbol)}"
                    aria-label="Stop watching ${escapeHtml(symbol)}">${icon("close", "icon-sm")}</button>
          </span></li>`,
        )
        .join("")}
    </ul>`;
  }

  function typedCoinPicker() {
    const chosen = chosenCoins();
    const rows = coinPicker.results.filter((item) => !chosen.includes(item.symbol));
    return `
      <div class="m-field">
        <span class="m-field-label"><span>Search for a coin</span><span class="m-field-need">Needed</span></span>
        <span class="m-search">
          ${icon("search", "icon-sm")}
          <label class="sr-only" for="m-coin-search">Search for a coin by name or ticker</label>
          <input id="m-coin-search" type="search" autocomplete="off" spellcheck="false"
                 placeholder="Try: bitcoin, ETH, sol"
                 value="${escapeHtml(coinPicker.query)}"
                 role="combobox" aria-expanded="${rows.length > 0}"
                 aria-controls="m-coin-results" aria-autocomplete="list"
                 data-coin-search>
        </span>
        ${coinPicker.searching ? `<p class="m-field-help" role="status">Looking…</p>` : ""}
        ${coinPicker.failed ? `<p class="m-field-warn">${icon("alert", "icon-sm")}<span>${escapeHtml(coinPicker.failed)}</span></p>` : ""}
        <ul class="m-suggest" id="m-coin-results" role="listbox" aria-label="Coins you can add"
            ${rows.length ? "" : "hidden"}>
          ${rows
            .map(
              (item) => `<li role="presentation">
                <button class="m-suggest-row" type="button" role="option" aria-selected="false"
                        data-coin-add="${escapeHtml(item.symbol)}">
                  <span class="m-suggest-mark" aria-hidden="true">${
                    item.logo_url
                      ? `<img src="${escapeHtml(item.logo_url)}" alt="" loading="lazy">`
                      : escapeHtml(item.symbol.slice(0, 2))
                  }</span>
                  <span class="m-suggest-name">${escapeHtml(item.name)}</span>
                  <span class="m-suggest-ticker t-figure">${escapeHtml(item.symbol)}</span>
                  <span class="t-pill" data-tone="${item.status === "eligible" ? "eligible" : "neutral"}">${escapeHtml(item.status_label)}</span>
                </button>
              </li>`,
            )
            .join("")}
        </ul>
        ${
          !coinPicker.searching && coinPicker.query && !rows.length && !coinPicker.failed
            ? `<p class="m-field-help">Nothing screened matches “${escapeHtml(coinPicker.query)}”. Only coins with a published Shariah review can be watched.</p>`
            : ""
        }
        ${coinPicker.notice ? `<p class="m-field-warn">${icon("info", "icon-sm")}<span>${escapeHtml(coinPicker.notice)}</span></p>` : ""}
      </div>
      <div class="m-field">
        <span class="m-field-label"><span>Chosen so far</span></span>
        ${
          chosen.length
            ? coinChips(chosen)
            : `<p class="m-field-warn" data-first-missing tabindex="-1">${icon("alert", "icon-sm")}<span>No coin is chosen yet, and none was chosen for you.</span></p>`
        }
        <p class="m-field-help">Keep searching to add more. You can take one out at any time.</p>
      </div>`;
  }

  function favoritesPicker() {
    if (coinPicker.favorites === null) {
      loadFavorites();
      return `<div class="m-field">
        <p class="m-field-help" role="status">${coinPicker.failed ? escapeHtml(coinPicker.failed) : "Reading your Favorites lists…"}</p>
      </div>`;
    }
    if (!coinPicker.favorites.length) {
      return `<div class="m-field">
        <p class="m-field-warn" data-first-missing tabindex="-1">${icon("alert", "icon-sm")}<span>You have no Favorites lists yet. Save one from Halal Assets, or choose every eligible coin instead.</span></p>
        <a class="t-action" href="${escapeHtml(scope.dataset.marketPath || "/dashboard/market")}">${icon("coins", "icon-sm")}Open Halal Assets</a>
      </div>`;
    }

    const universe = store.plan.universe;
    const active = coinPicker.favorites.find((item) => item.id === universe.watchlistId) || null;
    const chosen = new Set(chosenCoins());
    const whole = Boolean(active) && universe.mode === "approved_watchlist";

    return `
      <div class="m-field">
        <span class="m-field-label"><span>Which list</span><span class="m-field-need">Needed</span></span>
        <div class="m-lists" role="radiogroup" aria-label="Your Favorites lists">
          ${coinPicker.favorites
            .map(
              (item) => `<button class="m-list" type="button" role="radio"
                      aria-checked="${item.id === universe.watchlistId}"
                      data-favorite="${escapeHtml(item.id)}"
                      data-name="${escapeHtml(item.name)}">
                <span class="m-list-name">${escapeHtml(item.name)}${item.is_default ? " · your default" : ""}</span>
                <span class="m-list-count">${item.coins.length} coin${item.coins.length === 1 ? "" : "s"}</span>
              </button>`,
            )
            .join("")}
        </div>
        ${
          active
            ? ""
            : `<p class="m-field-warn" data-first-missing tabindex="-1">${icon("alert", "icon-sm")}<span>Pick one list.</span></p>`
        }
      </div>
      ${
        active
          ? `<div class="m-field">
              <span class="m-field-label"><span>Coins on “${escapeHtml(active.name)}”</span></span>
              ${
                active.coins.length
                  ? `<div class="m-pick-bar">
                      <button class="t-action" type="button" data-pick-all aria-pressed="${whole}">${icon("check", "icon-sm")}Watch the whole list</button>
                      <button class="t-action is-quiet" type="button" data-pick-none>${icon("close", "icon-sm")}Clear</button>
                    </div>
                    <ul class="m-picks" aria-label="Coins on this list">
                      ${active.coins
                        .map(
                          (symbol) => `<li>
                            <button class="m-pick" type="button" role="switch"
                                    aria-checked="${whole || chosen.has(symbol)}"
                                    data-pick="${escapeHtml(symbol)}">
                              <span class="m-pick-tick" aria-hidden="true">${icon("check", "icon-sm")}</span>
                              <span class="t-figure">${escapeHtml(symbol)}</span>
                            </button>
                          </li>`,
                        )
                        .join("")}
                    </ul>
                    <p class="m-field-help">${
                      whole
                        ? "The whole list is being watched, so a coin you add to it later is watched too."
                        : chosen.size
                          ? `Only the ${chosen.size} coin${chosen.size === 1 ? "" : "s"} you ticked. A coin added to the list later is not watched unless you tick it.`
                          : "Tick the coins you want, or watch the whole list."
                    }</p>`
                  : `<p class="m-field-warn">${icon("alert", "icon-sm")}<span>${escapeHtml(active.empty_reason || "This list has no coins in it yet.")}</span></p>`
              }
            </div>`
          : ""
      }`;
  }

  function paintUniversePanel() {
    inspectorKind.innerHTML = `${icon("coins", "icon-sm")}Coins to watch`;
    const options = catalog.universes || [];
    const universe = store.plan.universe;
    const pressed = (value) => {
      if (value === "approved_watchlist") return usingFavorites();
      if (value === "explicit_assets") {
        return universe.mode === "explicit_assets" && !universe.watchlistId;
      }
      return universe.mode === value;
    };
    inspectorBody.innerHTML = `
      <div class="m-field">
        <span class="m-field-label"><span>Which coins</span></span>
        <div class="m-choices" role="group" aria-label="Which coins">
          ${options
            .map((option) => `<button class="m-choice" type="button" data-universe="${escapeHtml(option.value)}" aria-pressed="${pressed(option.value)}">${escapeHtml(option.label)}</button>`)
            .join("")}
        </div>
        <p class="m-field-help">${escapeHtml((options.find((option) => pressed(option.value)) || {}).explanation || "")}</p>
      </div>
      ${usingFavorites() ? favoritesPicker() : ""}
      ${universe.mode === "explicit_assets" && !universe.watchlistId ? typedCoinPicker() : ""}
      <p class="m-field-help">${icon("lock", "icon-sm")} Whichever you pick, only coins with a published Shariah review are watched.</p>
    `;
  }

  function paintAlertPanel() {
    inspectorKind.innerHTML = `${icon("bell", "icon-sm")}How you hear about it`;
    const chosen = new Set(store.plan.alert.channels);
    // A way of being told that cannot reach this person yet says so here, while they
    // are choosing. Saying it only at the last step would refuse a board somebody had
    // already finished, for a reason that was known before they started.
    const waiting = channels.filter(
      (channel) => chosen.has(channel.value) && channel.ready === "false",
    );
    inspectorBody.innerHTML = `
      <div class="m-field">
        <span class="m-field-label"><span>Where to send it</span><span class="m-field-need">Needed</span></span>
        <div class="m-choices" role="group" aria-label="Where to send it">
          ${channels
            .map((channel) => `<button class="m-choice" type="button" data-channel="${escapeHtml(channel.value)}" aria-pressed="${chosen.has(channel.value)}">${escapeHtml(channel.label)}${channel.ready === "false" ? ` <span class="m-choice-note">not set up</span>` : ""}</button>`)
            .join("")}
        </div>
        ${chosen.size ? "" : `<p class="m-field-warn" data-first-missing tabindex="-1">${icon("alert", "icon-sm")}<span>Pick at least one.</span></p>`}
        ${waiting
          .map((channel) => `<p class="m-field-warn">${icon("alert", "icon-sm")}<span>${escapeHtml(channel.not_ready_reason || "This one is not set up on your account yet.")}</span></p>`)
          .join("")}
      </div>
      <div class="m-field">
        <span class="m-field-label"><span>Quiet time between messages</span></span>
        <span class="m-number">
          <label class="sr-only" for="m-cooldown">Quiet time between messages, in minutes</label>
          <input id="m-cooldown" type="number" min="0" max="1440" step="1" value="${store.plan.alert.cooldownMinutes}" data-cooldown>
          <span class="m-number-unit">minutes</span>
        </span>
        <p class="m-field-help">The same coin will not message you again inside this time.</p>
      </div>
    `;
  }

  function paintGroupPanel(node) {
    inspectorKind.innerHTML = `${icon("branch", "icon-sm")}Group`;
    const options = catalog.logic || [];
    inspectorBody.innerHTML = `
      <div class="m-field">
        <span class="m-field-label"><span>How the cards inside count</span></span>
        <div class="m-choices" role="group" aria-label="How the cards inside count">
          ${options
            .map((option) => `<button class="m-choice" type="button" data-op="${escapeHtml(option.value)}" aria-pressed="${node.op === option.value}">${escapeHtml(option.label)}</button>`)
            .join("")}
        </div>
        <p class="m-field-help">${escapeHtml((options.find((option) => option.value === node.op) || {}).explanation || "")}</p>
      </div>
      ${node.id === store.plan.rootId ? "" : parentPicker(node)}
      <div class="m-inspector-foot">
        <button class="t-action" type="button" data-inspector-add>${icon("plus", "icon-sm")}Add a condition inside</button>
        ${node.id === store.plan.rootId ? "" : `<button class="t-action" type="button" data-inspector-remove>${icon("trash", "icon-sm")}Remove this group</button>`}
      </div>
    `;
  }

  function paintRulePanel(node) {
    const mechanic = catalog.byKey.get(node.mechanic);
    if (!mechanic) {
      inspectorKind.innerHTML = `${icon("warning", "icon-sm")}Condition`;
      inspectorBody.innerHTML = `
        <p class="m-field-warn">${icon("alert", "icon-sm")}<span>This platform no longer offers that condition. Nothing was put in its place.</span></p>
        <div class="m-inspector-foot"><button class="t-action" type="button" data-inspector-remove>${icon("trash", "icon-sm")}Remove this card</button></div>`;
      return;
    }

    const look = categoryLook(mechanic.category);
    inspectorKind.innerHTML = `${icon(look.icon, "icon-sm")}${escapeHtml(look.name)}`;
    const missing = missingOn(mechanic, node.values);
    const firstMissing = missing.length ? missing[0].name : null;
    const clause = ruleClause(mechanic, node.values);

    inspectorBody.innerHTML = `
      <p class="m-echo"><small>This card says</small>${escapeHtml(mechanic.label)}${clause ? ` — ${escapeHtml(clause)}` : ""}</p>
      ${mechanic.available ? "" : `<p class="m-field-warn">${icon("alert", "icon-sm")}<span>${escapeHtml(mechanic.unavailable_reason || "This one cannot run yet.")}</span></p>`}
      ${(mechanic.parameters || []).map((parameter) => field(parameter, node, firstMissing)).join("")}
      <div class="m-field">
        <span class="m-field-label"><span>Does it have to be true?</span></span>
        <div class="m-choices" role="group" aria-label="Does it have to be true?">
          <button class="m-choice" type="button" data-required="true" aria-pressed="${node.required}">Must be true</button>
          <button class="m-choice" type="button" data-required="false" aria-pressed="${!node.required}">Nice to have</button>
        </div>
        <p class="m-field-help">A nice-to-have card adds confidence but never blocks the alert on its own.</p>
      </div>
      ${parentPicker(node)}
      <div class="m-inspector-foot">
        <button class="t-action" type="button" data-inspector-remove>${icon("trash", "icon-sm")}Remove this card</button>
      </div>
    `;
  }

  /** One control, drawn from the server's description of the field. */
  function field(parameter, node, firstMissing) {
    const value = node.values[parameter.name];
    const isMissing = parameter.required && (value === null || value === undefined || value === "");
    const mark = parameter.name === firstMissing ? " data-first-missing tabindex=\"-1\"" : "";
    const head = `<span class="m-field-label"><span>${escapeHtml(parameter.label)}</span><span class="m-field-need">${parameter.required ? "Needed" : "Optional"}</span></span>`;
    const help = parameter.help ? `<p class="m-field-help">${escapeHtml(parameter.help)}</p>` : "";
    const warn = isMissing
      ? `<p class="m-field-warn"${mark}>${icon("alert", "icon-sm")}<span>Nothing is chosen yet, and nothing was chosen for you.</span></p>`
      : "";

    const choices = Array.isArray(parameter.choices) ? parameter.choices : [];
    if (choices.length) {
      const control = choices.length <= 12
        ? `<div class="m-choices" role="group" aria-label="${escapeHtml(parameter.label)}">
            ${choices.map((choice) => `<button class="m-choice" type="button" data-set="${escapeHtml(parameter.name)}" data-value="${escapeHtml(choice.value)}" aria-pressed="${String(value) === String(choice.value)}">${escapeHtml(choice.label)}</button>`).join("")}
          </div>`
        : `<select class="m-select" data-set="${escapeHtml(parameter.name)}" aria-label="${escapeHtml(parameter.label)}">
            <option value="">Not chosen</option>
            ${choices.map((choice) => `<option value="${escapeHtml(choice.value)}" ${String(value) === String(choice.value) ? "selected" : ""}>${escapeHtml(choice.label)}</option>`).join("")}
          </select>`;
      const chosen = choices.find((choice) => String(choice.value) === String(value));
      const why = chosen && chosen.explanation ? `<p class="m-field-help">${escapeHtml(chosen.explanation)}</p>` : help;
      return `<div class="m-field">${head}${control}${why}${warn}</div>`;
    }

    if (parameter.kind === "number" || parameter.kind === "integer") {
      const unit = { percent: "%", price: "price", count: "candles" }[parameter.unit] || "";
      return `<div class="m-field">${head}
        <span class="m-number">
          <label class="sr-only" for="m-p-${escapeHtml(parameter.name)}">${escapeHtml(parameter.label)}</label>
          <input id="m-p-${escapeHtml(parameter.name)}" type="number" inputmode="decimal"
            ${parameter.minimum === null || parameter.minimum === undefined ? "" : `min="${parameter.minimum}"`}
            ${parameter.maximum === null || parameter.maximum === undefined ? "" : `max="${parameter.maximum}"`}
            step="${parameter.step || (parameter.kind === "integer" ? 1 : "any")}"
            value="${value === null || value === undefined ? "" : escapeHtml(value)}"
            data-set="${escapeHtml(parameter.name)}">
          ${unit ? `<span class="m-number-unit">${escapeHtml(unit)}</span>` : ""}
        </span>
        ${help}${warn}</div>`;
    }

    return `<div class="m-field">${head}
      <input class="m-text" type="text" value="${value === null || value === undefined ? "" : escapeHtml(value)}"
        aria-label="${escapeHtml(parameter.label)}" data-set="${escapeHtml(parameter.name)}">
      ${help}${warn}</div>`;
  }

  inspectorBody.addEventListener("click", (event) => {
    const id = currentInspector;
    if (!id) return;

    const universe = event.target.closest("[data-universe]");
    if (universe) {
      const mode = universe.dataset.universe;
      store.setUniverse(mode);
      // Leaving the Favorites picker means leaving the list behind. Keeping the id
      // would draw the list picker under a mode that does not use it, and would send
      // a list the person had stopped choosing.
      if (mode !== "approved_watchlist") store.setUniverseWatchlist(null);
      if (mode === "eligible_market") store.setUniverseSymbols([]);
      if (mode === "approved_watchlist") {
        coinPicker.favorites = null;
        loadFavorites();
      }
      if (mode === "explicit_assets") coinPicker.query = "";
      return;
    }

    const addCoin = event.target.closest("[data-coin-add]");
    if (addCoin) {
      store.setUniverseSymbols([...chosenCoins(), addCoin.dataset.coinAdd]);
      say(`${addCoin.dataset.coinAdd} added.`);
      window.requestAnimationFrame(() => {
        const box = inspectorBody.querySelector("[data-coin-search]");
        if (box) box.focus();
      });
      return;
    }

    const dropCoin = event.target.closest("[data-coin-remove]");
    if (dropCoin) {
      const symbol = dropCoin.dataset.coinRemove;
      store.setUniverseSymbols(chosenCoins().filter((item) => item !== symbol));
      say(`${symbol} removed.`);
      return;
    }

    const favorite = event.target.closest("[data-favorite]");
    if (favorite) {
      store.setUniverseWatchlist(favorite.dataset.favorite, favorite.dataset.name);
      // Choosing a list means the whole list until somebody says otherwise. It is the
      // answer that keeps following the list, so it is the one a person gets by simply
      // picking the list they already keep.
      store.setUniverse("approved_watchlist");
      store.setUniverseSymbols([]);
      return;
    }

    if (event.target.closest("[data-pick-all]")) {
      store.setUniverse("approved_watchlist");
      store.setUniverseSymbols([]);
      say("Watching the whole list.");
      return;
    }

    if (event.target.closest("[data-pick-none]")) {
      store.setUniverse("explicit_assets");
      store.setUniverseSymbols([]);
      say("No coin from this list is chosen.");
      return;
    }

    const pick = event.target.closest("[data-pick]");
    if (pick) {
      const list = (coinPicker.favorites || []).find(
        (item) => item.id === store.plan.universe.watchlistId,
      );
      const all = list ? list.coins : [];
      const symbol = pick.dataset.pick;
      const whole = store.plan.universe.mode === "approved_watchlist";
      // Unticking one coin out of a whole list turns "the list" into "these coins", so
      // the ticked set starts from every coin on it rather than from nothing.
      const current = whole ? all : chosenCoins();
      const next = current.includes(symbol)
        ? current.filter((item) => item !== symbol)
        : [...current, symbol];
      if (next.length === all.length && all.length) {
        store.setUniverse("approved_watchlist");
        store.setUniverseSymbols([]);
      } else {
        store.setUniverse("explicit_assets");
        store.setUniverseSymbols(next);
      }
      return;
    }

    const channel = event.target.closest("[data-channel]");
    if (channel) {
      const next = new Set(store.plan.alert.channels);
      if (next.has(channel.dataset.channel)) next.delete(channel.dataset.channel);
      else next.add(channel.dataset.channel);
      store.setChannels([...next]);
      return;
    }

    const op = event.target.closest("[data-op]");
    if (op) { store.setOperator(id, op.dataset.op); return; }

    const required = event.target.closest("[data-required]");
    if (required) { store.setRequired(id, required.dataset.required === "true"); return; }

    const set = event.target.closest("button[data-set]");
    if (set) {
      const current = store.node(id).values[set.dataset.set];
      // Pressing the chosen option again clears it rather than doing nothing, so a
      // value can always be taken back to "not chosen".
      store.setValue(id, set.dataset.set, String(current) === set.dataset.value ? "" : set.dataset.value);
      return;
    }

    if (event.target.closest("[data-inspector-add]")) { openLibrary(id); return; }
    if (event.target.closest("[data-inspector-remove]")) removeWithUndo(id);
  });

  inspectorBody.addEventListener("change", (event) => {
    const id = currentInspector;
    if (!id) return;
    const parentSelect = event.target.closest("[data-parent-select]");
    if (parentSelect) {
      if (parentSelect.value === ASIDE_VALUE) store.detach(id);
      else store.reparent(id, parentSelect.value);
      return;
    }
    const select = event.target.closest("select[data-set]");
    if (select) { store.setValue(id, select.dataset.set, select.value); return; }
    const cooldown = event.target.closest("[data-cooldown]");
    if (cooldown) {
      const minutes = Math.max(0, Math.min(1440, Math.round(Number(cooldown.value) || 0)));
      cooldown.value = String(minutes);
      store.setCooldown(minutes);
    }
  });

  let typingTimer = null;
  inspectorBody.addEventListener("input", (event) => {
    const coinSearch = event.target.closest("[data-coin-search]");
    if (coinSearch) {
      window.clearTimeout(coinSearchTimer);
      coinPicker.query = coinSearch.value.trim();
      if (!coinPicker.query) {
        coinPicker.results = [];
        coinPicker.searching = false;
        repaintUniverse();
        return;
      }
      // A pause rather than a keystroke. Searching on every letter would ask the server
      // once per letter for an answer nobody has finished asking for.
      coinSearchTimer = window.setTimeout(() => searchCoins(coinPicker.query), 240);
      return;
    }
    const input = event.target.closest("input[data-set]");
    if (!input || !currentInspector) return;
    window.clearTimeout(typingTimer);
    const id = currentInspector;
    const name = input.dataset.set;
    const raw = input.value;
    typingTimer = window.setTimeout(() => {
      store.setValue(id, name, input.type === "number" && raw !== "" ? Number(raw) : raw);
    }, 260);
  });

  /* Reaching the suggestions from the search box, and back.
   *
   * A list of options that only a mouse can reach is not a list of options. Down enters
   * it, Up walks back out to the box, Enter takes the one under the cursor. */
  inspectorBody.addEventListener("keydown", (event) => {
    const rows = [...inspectorBody.querySelectorAll("[data-coin-add]")];
    const box = event.target.closest("[data-coin-search]");
    if (box) {
      if (event.key === "ArrowDown" && rows.length) {
        event.preventDefault();
        rows[0].focus();
      }
      if (event.key === "Enter" && rows.length) {
        event.preventDefault();
        rows[0].click();
      }
      return;
    }
    const row = event.target.closest("[data-coin-add]");
    if (!row) return;
    const index = rows.indexOf(row);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      rows[Math.min(rows.length - 1, index + 1)].focus();
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (index === 0) {
        const again = inspectorBody.querySelector("[data-coin-search]");
        if (again) again.focus();
        return;
      }
      rows[index - 1].focus();
    }
  });

  find("[data-inspector-close]").addEventListener("click", () => {
    board.select(null);
    showInspector(null);
  });

  /* ── Readout and checks ────────────────────────────────────────────────── */

  const sentenceText = find("[data-sentence-text]");
  const meter = find("[data-meter]");
  const meterFill = find("[data-meter-fill]");
  const meterText = find("[data-meter-text]");
  const checksPanel = find("[data-checks]");
  const checkList = find("[data-check-list]");
  const checksToggle = find("[data-checks-toggle]");
  const checksCount = find("[data-checks-count]");
  const nextStep = find("[data-next-step]");

  function paintReadout() {
    const universe = (catalog.universes || []).find((option) => option.value === store.plan.universe.mode);
    sentenceText.textContent = planSentence(store, catalog, {
      universeLabel: universe ? universe.label.toLowerCase() : "the screened coins",
      channelLabels: channelLabels(store.plan.alert.channels),
    });

    const checks = checkPlan(store, catalog);
    const score = readiness(checks);
    const open = checks.filter((check) => check.tone !== "pass");
    const ready = planIsReady(checks);
    meter.dataset.tone = ready ? "ready" : score >= 50 ? "near" : "empty";
    meterFill.style.setProperty("--m-ready", `${score}%`);
    meterText.textContent = ready ? "Ready to switch on" : `${score}% ready`;
    checksCount.textContent = open.length ? `${open.length} to fix` : "All checks pass";

    // The next step appears the moment there is nothing blocking, and disappears again
    // if something is taken back out. It arrives with a small movement so it is noticed
    // without asking for attention — `brand guide.md` §15.
    const wasHidden = nextStep.hidden;
    nextStep.hidden = !ready;
    if (ready && wasHidden) reveal(nextStep, { from: 0.9 });

    checkList.innerHTML = checks
      .map((check) => {
        const glyph = check.tone === "pass" ? "check" : check.tone === "warn" ? "info" : "alert";
        return `<li class="m-check" data-tone="${check.tone}">
          ${icon(glyph, "icon-sm")}
          <span class="m-check-text">${escapeHtml(check.text)}</span>
          ${check.nodeId ? `<button class="t-action" type="button" data-show="${escapeHtml(check.nodeId)}">${icon("eye", "icon-sm")}Show me</button>` : ""}
        </li>`;
      })
      .join("");
  }

  checkList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-show]");
    if (!button) return;
    board.bringIntoView(button.dataset.show);
    showInspector(button.dataset.show);
  });

  /* ── The last step: read it back, test it, switch it on ──────────────────
   *
   * One popup and three states, because it is one action. A person reads what they
   * built, presses once, watches the message actually go out, and is told what each
   * way of being told did.
   *
   * Two honesty rules run through all of it:
   *
   *   * the monitor is created first and the test is sent after. A test that went out
   *     first would say "it works" about something that had not been saved;
   *   * a channel that fails does not undo the monitor, and the popup says so in those
   *     words. Losing a real, running monitor because Telegram was disconnected would
   *     be a worse answer than the truth.
   */

  const launch = document.querySelector("[data-launch]");
  const launchState = { opener: null, busy: false, monitor: null };
  const launchStep = (name) => launch.querySelector(`[data-launch-step="${name}"]`);
  const settingsPath = scope.dataset.settingsPath || "/dashboard/settings";

  const CHANNEL_ICON = {
    web: "dashboard",
    email: "mail",
    telegram: "telegram",
    whatsapp: "whatsapp",
  };

  function channelLabel(value) {
    const found = channels.find((channel) => channel.value === value);
    return found ? found.label : value;
  }

  /** The setup, written out the way the board writes it. Never a second wording. */
  function paintReadback() {
    const universe = (catalog.universes || []).find(
      (option) => option.value === store.plan.universe.mode,
    );
    const rows = planReadback(store, catalog, {
      universeLabel: universe ? universe.label.toLowerCase() : "the screened coins",
      channelLabels: channelLabels(store.plan.alert.channels),
    });
    launch.querySelector("[data-launch-readback]").innerHTML = rows
      .map((row) => {
        const cards = (row.cards || [])
          .map(
            (card) => `<li class="m-readback-card" data-depth="${card.depth}" data-group="${Boolean(card.group)}">
              ${card.group ? "" : `<span class="m-readback-dot" aria-hidden="true"></span>`}
              <span>${escapeHtml(card.text)}${card.optional ? ` <em>(nice to have)</em>` : ""}</span>
            </li>`,
          )
          .join("");
        return `<div class="m-readback-row">
          <dt>${escapeHtml(row.label)}</dt>
          <dd>${escapeHtml(row.value)}${cards ? `<ul class="m-readback-cards">${cards}</ul>` : ""}</dd>
        </div>`;
      })
      .join("");
  }

  function showLaunchStep(name) {
    for (const step of launch.querySelectorAll("[data-launch-step]")) {
      step.hidden = step.dataset.launchStep !== name;
    }
    const visible = launchStep(name);
    if (visible) settleIn(visible.children, { from: 8 });
  }

  function openLaunch() {
    launchState.opener = document.activeElement;
    launchState.busy = false;
    launchState.monitor = null;
    const nameBox = launch.querySelector("[data-launch-name]");
    // Whatever they called it last time this popup was open. Never a name we invented:
    // an empty box is answered by the server's own "My monitor", said once.
    nameBox.value = launchState.name || "";
    launch.querySelector("[data-launch-eyebrow]").textContent = "Last step";
    // A person changing a monitor is not switching one on: it is already watching. Two
    // different things said in two different ways, from the one fact that decides it.
    launch.querySelector("[data-launch-title]").textContent = monitorId
      ? "Check it over, then save the change"
      : "Check it over, then switch it on";
    launch.querySelector("[data-launch-lede]").textContent = monitorId
      ? "This is your monitor in plain words, with your changes. Nothing has changed yet."
      : "This is your monitor in plain words. Nothing is watching yet.";
    launch.querySelector("[data-launch-note]").textContent = monitorId
      ? "Nothing changes until you press the button."
      : "Nothing starts until you press the button.";
    const cancel = launch.querySelector("[data-launch-cancel]");
    cancel.hidden = false;
    // Reset, because the failure wording renames it. Re-opening the popup after a
    // refusal would otherwise still offer "Back to the board" on a fresh review step.
    cancel.textContent = "Go back to the board";
    launch.querySelector("[data-launch-go]").hidden = false;
    launch.querySelector("[data-launch-finish]").hidden = true;
    launch.querySelector("[data-launch-retry]").hidden = true;
    // The risk note is only on the page for somebody who has not answered it. Where it
    // is, the button waits for the answer rather than the answer being assumed.
    const accept = launch.querySelector("[data-launch-accept]");
    if (accept) accept.checked = false;
    launch.querySelector("[data-launch-go]").disabled = Boolean(accept);
    if (accept) {
      // A button that cannot be pressed must say what would let it be pressed.
      launch.querySelector("[data-launch-note]").textContent =
        "Tick the box above, then press the button.";
    }
    launch.querySelector("[data-launch-bar]").hidden = true;
    paintReadback();
    showLaunchStep("review");
    launch.showModal();
    window.requestAnimationFrame(() => nameBox.focus());
  }

  /** The board as the server reads it: only the cards that are joined to the monitor. */
  function planForServer() {
    const build = (id) => {
      const node = store.node(id);
      if (!node) return null;
      if (node.kind === "rule") {
        return { kind: "rule", mechanic: node.mechanic, values: node.values, required: node.required !== false };
      }
      const children = store
        .children(id)
        .map((child) => build(child.id))
        .filter(Boolean);
      return { kind: "group", op: node.op, children };
    };
    const universe = store.plan.universe;
    return {
      name: (launch.querySelector("[data-launch-name]").value || "").trim(),
      root: build(store.plan.rootId),
      universe: {
        mode: universe.mode,
        watchlist_id: universe.mode === "approved_watchlist" ? universe.watchlistId : null,
        symbols: universe.mode === "explicit_assets" ? universe.symbols || [] : [],
      },
      alert: {
        channels: [...store.plan.alert.channels],
        cooldown_minutes: store.plan.alert.cooldownMinutes,
      },
    };
  }

  /** One row per way of being told, drawn before anything is sent. */
  function paintSendRows(chosen) {
    launch.querySelector("[data-launch-sends]").innerHTML = chosen
      .map(
        (value) => `<li class="m-send" data-send="${escapeHtml(value)}" data-state="waiting">
          <span class="m-send-mark" aria-hidden="true">${icon(CHANNEL_ICON[value] || "bell", "icon-sm")}</span>
          <span class="m-send-name">${escapeHtml(channelLabel(value))}</span>
          <span class="m-send-track" aria-hidden="true"><span class="m-send-dot"></span></span>
          <span class="m-send-state">Waiting</span>
        </li>`,
      )
      .join("");
  }

  /**
   * Let each row land, one at a time, before the popup moves on.
   *
   * The point is not decoration: a person watching four rows all flip at once learns
   * nothing about which one is which. One at a time, with a tick or a cross and a word
   * beside it, is the difference between "it worked" and "email worked, Telegram did
   * not". Reduced motion gets the same information with no wait at all.
   */
  async function settleSendRows(results) {
    const step = prefersReducedMotion() ? 0 : 200;
    for (const item of results) {
      const row = launch.querySelector(`[data-send="${CSS.escape(item.channel)}"]`);
      if (row) {
        row.dataset.state = item.sent ? "sent" : "failed";
        row.querySelector(".m-send-state").innerHTML =
          `${icon(item.sent ? "check" : "alert", "icon-sm")}${item.sent ? "Sent" : "Did not send"}`;
        if (step) attention(row);
      }
      if (step) await new Promise((done) => window.setTimeout(done, step));
    }
  }

  function paintResultRows(results) {
    launch.querySelector("[data-launch-results]").innerHTML = results
      .map(
        (item) => `<li class="m-send" data-state="${item.sent ? "sent" : "failed"}">
          <span class="m-send-mark" aria-hidden="true">${icon(CHANNEL_ICON[item.channel] || "bell", "icon-sm")}</span>
          <span class="m-send-name">${escapeHtml(channelLabel(item.channel))}</span>
          <span class="m-send-state">${icon(item.sent ? "check" : "alert", "icon-sm")}${item.sent ? "Sent" : "Did not send"}</span>
          <span class="m-send-why">${escapeHtml(item.detail)}</span>
        </li>`,
      )
      .join("");
  }

  async function runLaunch() {
    if (launchState.busy) return;
    launchState.busy = true;
    launchState.name = (launch.querySelector("[data-launch-name]").value || "").trim();
    const chosen = [...store.plan.alert.channels];

    launch.querySelector("[data-launch-eyebrow]").textContent = monitorId
      ? "Saving the change"
      : "Switching it on";
    launch.querySelector("[data-launch-title]").textContent = "Sending one test message";
    launch.querySelector("[data-launch-lede]").textContent = monitorId
      ? "Your change is being saved, then one message goes to every way you chose."
      : "It is being switched on, then one message goes to every way you chose.";
    launch.querySelector("[data-launch-cancel]").hidden = true;
    launch.querySelector("[data-launch-go]").hidden = true;
    launch.querySelector("[data-launch-note]").textContent = "This takes a few seconds.";
    paintSendRows(chosen);
    showLaunchStep("sending");

    const bar = launch.querySelector("[data-launch-bar]");
    const fill = launch.querySelector("[data-launch-bar-fill]");
    bar.hidden = false;
    bar.dataset.state = "working";
    fill.style.width = "12%";
    // The bar moves because something is happening, not to guess how far along it is.
    // It stops short of the end until a real answer arrives, and only then finishes.
    const creep = window.setInterval(() => {
      const now = Number.parseFloat(fill.style.width) || 12;
      fill.style.width = `${Math.min(88, now + 6)}%`;
    }, 420);
    for (const row of launch.querySelectorAll("[data-send]")) {
      row.dataset.state = "sending";
      row.querySelector(".m-send-state").textContent = "Sending…";
    }
    launch.querySelector("[data-launch-status]").textContent = monitorId
      ? "Saving the change…"
      : "Switching it on…";

    let payload = null;
    let problem = "";
    try {
      const response = await fetch("/api/v1/dashboard/monitor-canvas/activate", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          // Which monitor this board belongs to. Sent so the server writes a new version
          // of the monitor the person opened, rather than a second monitor beside it.
          monitor_id: monitorId,
          plan: planForServer(),
          // The board's own sentence, so the message that arrives says the same thing
          // the person just read.
          in_plain_words: sentenceText.textContent || "",
          accepted_risk_note: Boolean(
            launch.querySelector("[data-launch-accept]")
            && launch.querySelector("[data-launch-accept]").checked,
          ),
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        problem =
          (body && body.detail && body.detail.message)
          || "The monitor could not be switched on, so nothing was saved.";
      } else {
        payload = body;
      }
    } catch {
      problem = "We could not reach the server. Nothing was saved, and your board is untouched.";
    } finally {
      window.clearInterval(creep);
      launchState.busy = false;
    }

    fill.style.width = "100%";
    if (problem) {
      bar.dataset.state = "failed";
      finishLaunch({ ok: false, message: problem, results: [] });
      return;
    }
    const results = payload.results || [];
    // Which of the two things happened is the server's answer, not this page's guess.
    const changed = Boolean((payload.monitor || {}).changed);
    launch.querySelector("[data-launch-status]").textContent = changed
      ? "Your change is saved. Sending one test message to each way you chose…"
      : "It is on. Sending one test message to each way you chose…";
    await settleSendRows(results);
    paintResultRows(results);
    bar.dataset.state = payload.all_sent ? "ok" : "partial";
    launchState.monitor = payload.monitor || null;
    // The change is saved, so the board kept in this browser is no longer an unfinished
    // edit — it is what the monitor runs. Keeping it would make the next visit warn
    // about changes that were saved.
    if (changed) store.forgetDraft();
    finishLaunch({
      ok: true,
      allSent: Boolean(payload.all_sent),
      changed,
      results,
      name: (payload.monitor || {}).name || launchState.name || "Your monitor",
    });
  }

  function finishLaunch({ ok, allSent, changed, message, results, name }) {
    const outcome = launch.querySelector("[data-launch-outcome]");
    const title = launch.querySelector("[data-launch-outcome-title]");
    const line = launch.querySelector("[data-launch-outcome-line]");
    const mark = launch.querySelector("[data-launch-outcome-mark]");
    const note = launch.querySelector("[data-launch-settings-note]");
    launch.querySelector("[data-launch-eyebrow]").textContent = ok ? "Done" : "Not done";
    launch.querySelector("[data-launch-lede]").textContent = "";

    if (!ok) {
      outcome.dataset.tone = "bad";
      mark.innerHTML = icon("alert", "icon");
      title.textContent = monitorId ? "The change was not saved" : "It was not switched on";
      line.textContent = message;
      note.hidden = true;
      launch.querySelector("[data-launch-title]").textContent = monitorId
        ? "Nothing was changed"
        : "Nothing was saved";
      launch.querySelector("[data-launch-results]").innerHTML = "";
      launch.querySelector("[data-launch-note]").textContent = monitorId
        ? "Your monitor is still watching exactly as it was, and your board is as you left it."
        : "Your board is exactly as you left it.";
      launch.querySelector("[data-launch-retry]").hidden = false;
      launch.querySelector("[data-launch-cancel]").hidden = false;
      launch.querySelector("[data-launch-cancel]").textContent = "Back to the board";
      launch.querySelector("[data-launch-finish]").hidden = true;
      showLaunchStep("done");
      say(
        monitorId
          ? "The change was not saved. Your monitor is unchanged."
          : "The monitor was not switched on. Nothing was saved.",
      );
      return;
    }

    const failed = (results || []).filter((item) => !item.sent);
    outcome.dataset.tone = allSent ? "ok" : "mixed";
    mark.innerHTML = icon(allSent ? "check" : "alert", "icon");
    title.textContent = changed ? `${name} is changed and watching` : `${name} is watching now`;
    line.textContent = allSent
      ? "The test message went to every way you chose. A real alert will arrive the same way."
      : `The monitor is on and watching. ${failed.length} way${failed.length === 1 ? "" : "s"} of being told did not work, so it is worth fixing before something happens.`;
    note.hidden = false;
    launch.querySelector("[data-launch-title]").textContent = allSent
      ? (changed ? "Your change is saved" : "It is watching now")
      : "It is watching, with something to fix";
    launch.querySelector("[data-launch-note]").textContent = changed
      ? "It is watching by your new rules from now on. What it already found is kept."
      : "Your board stays here, so you can build another one.";
    launch.querySelector("[data-launch-retry]").hidden = true;
    launch.querySelector("[data-launch-cancel]").hidden = true;
    launch.querySelector("[data-launch-finish]").hidden = false;
    showLaunchStep("done");
    window.requestAnimationFrame(() => launch.querySelector("[data-launch-finish]").focus());
    say(
      allSent
        ? (changed ? `${name} is changed and watching.` : `${name} is watching now.`)
        : `${name} is watching. Some ways of being told did not work.`,
    );
  }

  find("[data-next-step]").addEventListener("click", openLaunch);
  launch.querySelector("[data-launch-go]").addEventListener("click", runLaunch);
  launch.addEventListener("change", (event) => {
    if (!event.target.closest("[data-launch-accept]")) return;
    launch.querySelector("[data-launch-go]").disabled = !event.target.checked;
    launch.querySelector("[data-launch-note]").textContent = event.target.checked
      ? "Nothing starts until you press the button."
      : "Tick the box above, then press the button.";
  });
  launch.querySelector("[data-launch-retry]").addEventListener("click", openLaunch);
  launch.querySelector("[data-launch-close]").addEventListener("click", () => {
    if (!launchState.busy) launch.close();
  });
  launch.querySelector("[data-launch-cancel]").addEventListener("click", () => launch.close());
  launch.querySelector("[data-launch-finish]").addEventListener("click", () => {
    // Somebody who just changed a monitor came from the list of monitors and belongs
    // back on it, looking at the one they changed. Somebody who just made their first
    // one is sent to Settings, where the ways of being told are.
    window.location.href = (launchState.monitor || {}).changed
      ? scope.dataset.watchlistsPath || "/dashboard/monitors"
      : settingsPath;
  });
  // A popup that is doing something must not vanish under a stray Escape: the monitor
  // is mid-creation and the answer is still coming.
  launch.addEventListener("cancel", (event) => {
    if (launchState.busy) event.preventDefault();
  });
  launch.addEventListener("close", () => {
    if (launchState.opener && launchState.opener.isConnected) launchState.opener.focus();
  });

  checksToggle.addEventListener("click", () => {
    const open = checksPanel.hidden;
    checksPanel.hidden = !open;
    checksToggle.setAttribute("aria-expanded", String(open));
    if (open) settleIn(checkList.querySelectorAll(".m-check"), { from: 6 });
  });

  /* ── Toolbar ───────────────────────────────────────────────────────────── */

  const undoButton = find("[data-undo]");
  const redoButton = find("[data-redo]");
  const savedPill = find("[data-saved-pill]");
  const savedText = find("[data-saved-text]");

  find("[data-open-library]").addEventListener("click", () => openLibrary(board.selectedId && store.node(board.selectedId) && store.node(board.selectedId).kind === "group" ? board.selectedId : store.plan.rootId));
  find("[data-add-group]").addEventListener("click", () => {
    const parent = board.selectedId && store.node(board.selectedId) && store.node(board.selectedId).kind === "group"
      ? board.selectedId
      : store.plan.rootId;
    const id = store.addGroup({ parentId: parent });
    if (id) window.requestAnimationFrame(() => { board.select(id); showInspector(id); });
  });
  undoButton.addEventListener("click", () => store.undo());
  redoButton.addEventListener("click", () => store.redo());
  find("[data-tidy]").addEventListener("click", () => { store.tidy(); board.fit(); });

  for (const button of scope.querySelectorAll("[data-zoom]")) {
    button.addEventListener("click", () => {
      if (button.dataset.zoom === "in") board.zoomBy(1.15);
      else if (button.dataset.zoom === "out") board.zoomBy(1 / 1.15);
      else board.fit();
    });
  }

  /* ── The two canvas sizes ──────────────────────────────────────────────── */

  function setMode(mode) {
    stage.dataset.mode = mode;
    document.body.classList.toggle("m-locked", mode === "full");
    for (const button of scope.querySelectorAll("[data-canvas-mode]")) {
      button.setAttribute("aria-checked", String(button.dataset.canvasMode === mode));
    }
    window.requestAnimationFrame(() => {
      board.fit();
      say(mode === "full" ? "The canvas fills the screen. Press Escape to go back." : "The canvas is back inside the page.");
    });
  }

  for (const button of scope.querySelectorAll("[data-canvas-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.canvasMode));
  }

  const shortcuts = document.querySelector("[data-shortcuts]");
  let shortcutsOpener = null;
  find("[data-open-shortcuts]").addEventListener("click", (event) => {
    shortcutsOpener = event.currentTarget;
    shortcuts.showModal();
  });
  shortcuts.querySelector("[data-shortcuts-close]").addEventListener("click", () => shortcuts.close());
  shortcuts.addEventListener("close", () => {
    if (shortcutsOpener && shortcutsOpener.isConnected) shortcutsOpener.focus();
  });

  /* ── Keys that work anywhere on the page ───────────────────────────────── */

  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest("input, textarea, select");
    if (event.key === "Escape" && stage.dataset.mode === "full" && !document.querySelector("dialog[open]")) {
      setMode("page");
      return;
    }
    if (!(event.ctrlKey || event.metaKey)) return;
    if (event.key.toLowerCase() === "z") {
      if (typing) return;
      event.preventDefault();
      if (event.shiftKey) store.redo();
      else store.undo();
    }
    if (event.key === "Enter" && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      openLibrary(store.plan.rootId);
    }
    if (event.key === "0") {
      event.preventDefault();
      board.fit();
    }
  });

  /* ── Keeping every part in step ────────────────────────────────────────── */

  function refresh(_plan, detail = {}) {
    // A hint asks for something. Once the board has changed, it has either been done
    // or been overtaken, and either way it should not still be on screen telling a
    // person to do what they just did.
    if (coach.dataset.kind === "hint") setCoach("");
    board.render();
    paintReadout();
    if (currentInspector) {
      if (currentInspector !== "universe" && currentInspector !== "alert" && !store.node(currentInspector)) {
        showInspector(null);
      } else {
        const active = document.activeElement;
        const keep = active && inspectorBody.contains(active) ? active.dataset.set : null;
        paintInspector(currentInspector);
        if (keep) {
          const again = inspectorBody.querySelector(`input[data-set="${CSS.escape(keep)}"]`);
          if (again) {
            const end = again.value.length;
            again.focus();
            if (again.type !== "number") again.setSelectionRange(end, end);
          }
        }
      }
    }
    undoButton.disabled = !store.canUndo;
    redoButton.disabled = !store.canRedo;
    paintSaved();
    /* A change that moves cards can move the one a person is working on right off the
     * screen. Cancelling a connection sends a card to the shelf below the whole board;
     * undoing it sends the same card back to the top. Following it is the smallest pan
     * that keeps it visible — never a re-fit, which would throw away the view they had
     * arranged. */
    if (detail.structural && board.selectedId && store.node(board.selectedId)) {
      window.requestAnimationFrame(() => board.keepVisible(board.selectedId));
    }
    if (detail.announce) say(detail.announce);
  }

  function paintSaved() {
    if (!store.storageWorks) {
      savedPill.dataset.state = "empty";
      savedText.textContent = "This browser is not keeping the draft";
      return;
    }
    if (!store.ruleCount) {
      savedPill.dataset.state = "empty";
      savedText.textContent = "Nothing drawn yet";
      return;
    }
    savedPill.dataset.state = "saved";
    savedText.textContent = `Draft saved · ${store.ruleCount} condition${store.ruleCount === 1 ? "" : "s"}`;
  }

  /* ── Telling Hilal what is on the board ────────────────────────────────── */

  /**
   * The monitor being drawn, in the words this page already uses for it.
   *
   * Every line comes from a part of the page a person can see: the readout sentence,
   * the checklist, the card labels, the clause printed on each card. Nothing is worked
   * out a second time here, so the assistant cannot describe a card differently from
   * the card.
   *
   * Built when asked rather than on every change. The board changes on each keystroke
   * and almost none of those changes are followed by a question.
   */
  function boardSummary() {
    const universe = (catalog.universes || []).find(
      (option) => option.value === store.plan.universe.mode,
    );
    const checks = checkPlan(store, catalog);
    const cards = [];
    for (const node of Object.values(store.plan.nodes)) {
      if (node.kind !== "rule") continue;
      const mechanic = catalog.byKey.get(node.mechanic);
      const parent = node.parent ? store.node(node.parent) : null;
      cards.push({
        label: mechanic ? mechanic.label : "A condition this platform no longer offers",
        reads: mechanic ? ruleClause(mechanic, node.values) || null : null,
        required: node.required !== false,
        inside: parent ? groupWord(parent.op) : null,
        set_aside: store.isAside(node.id),
        needs: mechanic
          ? missingOn(mechanic, node.values).map((parameter) => parameter.label).slice(0, 6)
          : [],
      });
      if (cards.length >= 32) break;
    }
    return {
      sentence: planSentence(store, catalog, {
        universeLabel: universe ? universe.label.toLowerCase() : "the screened coins",
        channelLabels: channelLabels(store.plan.alert.channels),
      }),
      ready_percent: readiness(checks),
      cards,
      checks: checks
        .slice(0, 32)
        .map((check) => ({ tone: check.tone, text: check.text.slice(0, 240) })),
      watching: universe ? universe.label : null,
      ways_to_be_told: channelLabels(store.plan.alert.channels).slice(0, 8),
      controls: controlNames(),
      how_to: howTo(),
    };
  }

  /**
   * How this board is worked, in the page's own written words.
   *
   * Read from the "Keys and gestures" dialog rather than written out here. Hilal is
   * allowed to tell somebody to drag a wire onto empty space *because the page says
   * so*; if that sentence ever changes, its answer changes with it, and if the page
   * stops saying it, Hilal stops saying it too.
   */
  function howTo() {
    const rows = [];
    for (const row of document.querySelectorAll("[data-shortcuts] .m-keys > div")) {
      const term = (row.querySelector("dt") || {}).textContent || "";
      const means = (row.querySelector("dd") || {}).textContent || "";
      const line = `${term.trim().replace(/\s+/g, " ")}: ${means.trim().replace(/\s+/g, " ")}`;
      if (line.length > 3) rows.push(line.slice(0, 160));
      if (rows.length >= 24) break;
    }
    return rows;
  }

  /**
   * The names of the controls actually on this screen.
   *
   * Read off the buttons themselves, never typed out here. Hilal is told it may only
   * name a control from this list, so a button that gets renamed renames itself in the
   * assistant's answers too — instead of the assistant sending somebody to look for a
   * button that stopped existing two releases ago.
   */
  function controlNames() {
    const names = new Set();
    const wanted = [
      "[data-open-library]",
      "[data-add-group]",
      "[data-undo]",
      "[data-redo]",
      "[data-tidy]",
      "[data-checks-toggle]",
      "[data-canvas-mode]",
      "[data-open-shortcuts]",
      // The buttons on a card itself: add, settings, cut, join, remove.
      "[data-act]",
    ];
    for (const selector of wanted) {
      for (const button of scope.querySelectorAll(selector)) {
        const name = (
          button.getAttribute("aria-label")
          || button.title
          || button.textContent
          || ""
        ).trim().replace(/\s+/g, " ");
        if (name) names.add(name.slice(0, 60));
        if (names.size >= 24) return [...names];
      }
    }
    return [...names];
  }

  publish("board", boardSummary);

  store.subscribe(refresh);

  board.render();
  paintReadout();
  paintSaved();
  window.requestAnimationFrame(() => board.fit());

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      board.layout();
      board.drawWires();
    }, 160);
  });

  /* ── Opening a monitor somebody already has ──────────────────────────────── */

  if (monitorId) {
    const named = ((opened && opened.monitor) || {}).name;
    // The name of the monitor in front of them, so "Change it" on one card can never
    // land on a page that could be about any of them.
    if (named) find("[data-page-title]").textContent = named;
    if (named && !launchState.name) launchState.name = named;

    // A reopened board holds the Favorites list's id and not its name, so the readout
    // would say "one of your Favorites lists" until somebody happened to open the coin
    // picker. The lists are read once, here, and the name appears with the board.
    if (
      store.plan.universe.mode === "approved_watchlist"
      && store.plan.universe.watchlistId
      && !store.plan.universe.watchlistName
    ) {
      loadFavorites();
    }

    if (store.holdsUnsavedEdit) {
      const draftNotice = find("[data-draft-notice]");
      draftNotice.hidden = false;
      find("[data-draft-notice-restore]").addEventListener("click", () => {
        store.putBackTheSavedMonitor();
        draftNotice.hidden = true;
        board.fit();
      });
    }
  } else if (asked) {
    const notice = find("[data-open-notice]");
    notice.hidden = false;
    find("[data-open-notice-text]").textContent =
      (opened && opened.reason)
      || "That monitor could not be read just now. Nothing about it has changed.";
    find("[data-open-notice-fresh]").addEventListener("click", () => {
      /* The plain address, and nothing else.
       *
       * Deliberately *not* clearing the board first. A monitor that cannot be drawn is
       * opened under the same key as a brand-new monitor, so what is on the board here
       * is whatever new monitor this person had already started drawing — clearing it
       * would throw away work that has nothing to do with the monitor they came to
       * change, and there is no copy of it anywhere else. Somebody who wants an empty
       * board can empty this one; nobody can get a cleared one back. */
      window.location.href = window.location.pathname;
    });
    say("This monitor cannot be drawn on the canvas. Nothing about it has changed.");
  }

  // A first-time visitor is told the one thing that is not obvious from looking.
  if (!store.ruleCount) {
    setCoach("Drag the board to move around it. Every card is one plain sentence.", { seconds: 8 });
  }
}

function readJson(raw, fallback) {
  try {
    const parsed = JSON.parse(raw || "");
    return parsed === null || parsed === undefined ? fallback : parsed;
  } catch {
    return fallback;
  }
}
