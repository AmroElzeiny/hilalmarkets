/* The monitor canvas at /dashboard/monitor.
 *
 * Brings together the four parts that each own one thing: the contract reader, the
 * plan, the board, and the panels this file draws — the condition library, the
 * settings panel, the readout and the checks.
 *
 * There is no assistant here. Every rule this platform can run is reachable from the
 * list, with a form built from the server's own description of it, so authoring never
 * depends on a model being available or on it having understood a sentence.
 */

import { dismiss, reveal, settleIn } from "./hm-motion.js";
import { publish } from "./hm-page-context.js";
import { categoryLook, loadCatalog, searchMechanics } from "./hm-monitor-catalog.js";
import {
  PlanStore,
  checkPlan,
  groupWord,
  missingOn,
  planSentence,
  readiness,
  ruleClause,
} from "./hm-monitor-plan.js";
import { Board } from "./hm-monitor-board.js";

const root = document.querySelector("[data-monitor-root]");
if (root) start(root).catch(() => showContractError(root));

const icon = (name, cls = "icon") => (window.icon ? window.icon(name, cls) : "");
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );

const PAGE_SIZE = 40;

function showContractError(scope) {
  const loading = scope.querySelector("[data-loading]");
  const error = scope.querySelector("[data-contract-error]");
  if (loading) loading.hidden = true;
  if (error) error.hidden = false;
}

async function start(scope) {
  const find = (selector) => scope.querySelector(selector);
  const limits = readJson(scope.dataset.limits, {});
  const channels = readJson(scope.dataset.channels, []);

  const catalog = await loadCatalog(scope.dataset.contractUrl);
  find("[data-loading]").hidden = true;
  find("[data-contract-error]").hidden = true;

  // The compiler's own shape limits win over the ones rendered into the page: the
  // page was drawn once, the contract is read now.
  const store = new PlanStore({ limits: catalog.limits || limits });

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

  function paintUniversePanel() {
    inspectorKind.innerHTML = `${icon("coins", "icon-sm")}Coins to watch`;
    const options = catalog.universes || [];
    inspectorBody.innerHTML = `
      <div class="m-field">
        <span class="m-field-label"><span>Which coins</span></span>
        <div class="m-choices" role="group" aria-label="Which coins">
          ${options
            .map((option) => `<button class="m-choice" type="button" data-universe="${escapeHtml(option.value)}" aria-pressed="${store.plan.universe.mode === option.value}">${escapeHtml(option.label)}</button>`)
            .join("")}
        </div>
        <p class="m-field-help">${escapeHtml((options.find((option) => option.value === store.plan.universe.mode) || {}).explanation || "")}</p>
      </div>
      <p class="m-field-help">${icon("lock", "icon-sm")} Whichever you pick, only coins with a published Shariah review are watched.</p>
    `;
  }

  function paintAlertPanel() {
    inspectorKind.innerHTML = `${icon("bell", "icon-sm")}How you hear about it`;
    const chosen = new Set(store.plan.alert.channels);
    inspectorBody.innerHTML = `
      <div class="m-field">
        <span class="m-field-label"><span>Where to send it</span><span class="m-field-need">Needed</span></span>
        <div class="m-choices" role="group" aria-label="Where to send it">
          ${channels
            .map((channel) => `<button class="m-choice" type="button" data-channel="${escapeHtml(channel.value)}" aria-pressed="${chosen.has(channel.value)}">${escapeHtml(channel.label)}</button>`)
            .join("")}
        </div>
        ${chosen.size ? "" : `<p class="m-field-warn" data-first-missing tabindex="-1">${icon("alert", "icon-sm")}<span>Pick at least one.</span></p>`}
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
    if (universe) { store.setUniverse(universe.dataset.universe); return; }

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

  function paintReadout() {
    const universe = (catalog.universes || []).find((option) => option.value === store.plan.universe.mode);
    sentenceText.textContent = planSentence(store, catalog, {
      universeLabel: universe ? universe.label.toLowerCase() : "the screened coins",
      channelLabels: channelLabels(store.plan.alert.channels),
    });

    const checks = checkPlan(store, catalog);
    const score = readiness(checks);
    const open = checks.filter((check) => check.tone !== "pass");
    meter.dataset.tone = score === 100 ? "ready" : score >= 50 ? "near" : "empty";
    meterFill.style.setProperty("--m-ready", `${score}%`);
    meterText.textContent = score === 100 ? "Ready to take further" : `${score}% ready`;
    checksCount.textContent = open.length ? `${open.length} to fix` : "All checks pass";

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

  find("[data-contract-retry]").addEventListener("click", () => window.location.reload());

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
