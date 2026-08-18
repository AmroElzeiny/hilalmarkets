/* The monitor being drawn, and everything that can be said about it.
 *
 * One owner for the plan: its shape, every change to it, the history behind those
 * changes, the words that describe it and the checks that say whether it is finished.
 * The board, the settings panel and the readout all read from here, so two parts of
 * the page can never disagree about what the person has drawn.
 *
 * Two rules from CLAUDE.md are load-bearing in this file:
 *
 *   *Fail closed.* A required value nobody has chosen stays empty and is reported as
 *   missing. It is never filled with the first option, the middle option, or the one
 *   the platform happens to prefer. A comparison nobody picked would silently monitor
 *   something other than what was asked for.
 *
 *   *Never invent.* Every default written into a new rule comes from the server's own
 *   contract. Where the contract has no default — and for a comparison it deliberately
 *   has none — this file leaves the value unset.
 */

const STORAGE_KEY = "hm.monitor.draft.v2";
const HISTORY_LIMIT = 60;

/** Parameters that belong on the card face rather than only in the settings panel. */
const HEADLINE_PARAMETERS = new Set(["threshold", "direction"]);

/** Comparisons that read as an event rather than a measurement. */
const EVENT_COMPARATORS = new Set(["is_true", "is_false"]);

let counter = 0;
const nextId = (prefix) => `${prefix}${++counter}-${Math.random().toString(36).slice(2, 7)}`;

export function emptyPlan() {
  const root = { id: nextId("g"), kind: "group", op: "and", parent: null, children: [], dx: 0, dy: 0 };
  return {
    version: 2,
    rootId: root.id,
    universe: { mode: "eligible_market", dx: 0, dy: 0 },
    alert: { channels: [], cooldownMinutes: 15, dx: 0, dy: 0 },
    nodes: { [root.id]: root },
  };
}

/* ── The store ─────────────────────────────────────────────────────────────── */

export class PlanStore {
  constructor({ limits } = {}) {
    this.limits = limits || { max_depth: 6, max_nodes: 24, arity: {} };
    this.plan = restore() || emptyPlan();
    this.past = [];
    this.future = [];
    this.listeners = new Set();
    this.savedAt = null;
    this.canStore = undefined;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Make one change, as one undoable step.
   *
   * ``structural`` says the change added, removed or re-parented a card, so the board
   * knows to rebuild rather than only redraw. Moving a card is not structural: it
   * changes where a card sits and nothing about what it means.
   */
  commit(fn, { structural = false, announce = "" } = {}) {
    const before = JSON.stringify(this.plan);
    const result = fn(this.plan);
    if (result === false) return false;
    const after = JSON.stringify(this.plan);
    if (before === after) return false;
    this.past.push(before);
    if (this.past.length > HISTORY_LIMIT) this.past.shift();
    this.future.length = 0;
    this.persist();
    this.emit({ structural, announce });
    return true;
  }

  undo() {
    if (!this.past.length) return false;
    this.future.push(JSON.stringify(this.plan));
    this.plan = JSON.parse(this.past.pop());
    this.persist();
    this.emit({ structural: true, announce: "Undone." });
    return true;
  }

  redo() {
    if (!this.future.length) return false;
    this.past.push(JSON.stringify(this.plan));
    this.plan = JSON.parse(this.future.pop());
    this.persist();
    this.emit({ structural: true, announce: "Redone." });
    return true;
  }

  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }

  clear() {
    this.past.push(JSON.stringify(this.plan));
    this.future.length = 0;
    this.plan = emptyPlan();
    this.persist();
    this.emit({ structural: true, announce: "The board was cleared." });
  }

  emit(detail) {
    for (const listener of this.listeners) listener(this.plan, detail);
  }

  persist() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this.plan));
      this.savedAt = new Date();
      this.canStore = true;
    } catch {
      // A browser with storage turned off still gets a working board; it simply
      // forgets the draft when the tab closes. Saying nothing would be worse, so the
      // page reports it separately through `storageWorks`.
      this.savedAt = null;
      this.canStore = false;
    }
  }

  /**
   * Whether this browser will keep the draft.
   *
   * Asked of storage itself rather than of "have we saved yet". Reading it from the
   * last save meant a board nobody had touched reported that the browser was refusing
   * to keep drafts, which was untrue and alarming on the very first visit.
   */
  get storageWorks() {
    if (this.canStore !== undefined) return this.canStore;
    try {
      const probe = `${STORAGE_KEY}.probe`;
      window.localStorage.setItem(probe, "1");
      window.localStorage.removeItem(probe);
      this.canStore = true;
    } catch {
      this.canStore = false;
    }
    return this.canStore;
  }

  /* ── Reading ─────────────────────────────────────────────────────────────── */

  node(id) { return this.plan.nodes[id] || null; }
  get root() { return this.plan.nodes[this.plan.rootId]; }

  children(id) {
    const node = this.node(id);
    if (!node || node.kind !== "group") return [];
    return node.children.map((childId) => this.node(childId)).filter(Boolean);
  }

  /** Every group a card could be moved into, excluding itself and its own subtree. */
  groupsFor(id) {
    const banned = new Set();
    const walk = (nodeId) => {
      banned.add(nodeId);
      for (const child of this.children(nodeId)) walk(child.id);
    };
    if (id) walk(id);
    return Object.values(this.plan.nodes).filter(
      (node) => node.kind === "group" && !banned.has(node.id),
    );
  }

  depthOf(id) {
    let depth = 0;
    let node = this.node(id);
    while (node && node.parent) {
      depth += 1;
      node = this.node(node.parent);
    }
    return depth;
  }

  get maxDepth() {
    return Object.keys(this.plan.nodes).reduce(
      (deepest, id) => Math.max(deepest, this.depthOf(id)),
      0,
    );
  }

  get ruleCount() {
    return Object.values(this.plan.nodes).filter((node) => node.kind === "rule").length;
  }

  /**
   * The cards that are on the board but not joined to anything.
   *
   * A card gets here by having its wire cancelled. It keeps every setting it had; it
   * simply is not part of the monitor while it sits here. The main group is the one
   * card with no parent that is *not* set aside, so it is excluded by name rather than
   * by "has no parent" — that test alone would have hidden the root.
   */
  get asideIds() {
    return Object.values(this.plan.nodes)
      .filter((node) => node.id !== this.plan.rootId && !node.parent)
      .map((node) => node.id);
  }

  isAside(id) {
    const node = this.node(id);
    return Boolean(node) && id !== this.plan.rootId && !node.parent;
  }

  /* ── Changing ────────────────────────────────────────────────────────────── */

  addRule(mechanic, { parentId, values } = {}) {
    const parent = this.node(parentId) || this.root;
    const id = nextId("r");
    this.commit(
      (plan) => {
        plan.nodes[id] = {
          id,
          kind: "rule",
          mechanic: mechanic.key,
          values: values ? { ...values } : startingValues(mechanic),
          required: true,
          parent: parent.id,
          dx: 0,
          dy: 0,
        };
        plan.nodes[parent.id].children.push(id);
      },
      { structural: true, announce: `${mechanic.label} added.` },
    );
    return id;
  }

  /**
   * Several rules as one step, for a ready-made starting point.
   *
   * One commit, so one press of undo takes the whole starter back off the board
   * rather than peeling it away a card at a time.
   */
  addRules(entries, { op } = {}) {
    const added = [];
    this.commit(
      (plan) => {
        const root = plan.nodes[plan.rootId];
        if (op) root.op = op;
        for (const entry of entries) {
          const id = nextId("r");
          added.push(id);
          plan.nodes[id] = {
            id,
            kind: "rule",
            mechanic: entry.mechanic.key,
            values: { ...startingValues(entry.mechanic), ...(entry.values || {}) },
            required: entry.required !== false,
            parent: root.id,
            dx: 0,
            dy: 0,
          };
          root.children.push(id);
        }
      },
      { structural: true, announce: `${entries.length} card${entries.length === 1 ? "" : "s"} added.` },
    );
    return added;
  }

  addGroup({ parentId, op = "or" } = {}) {
    const parent = this.node(parentId) || this.root;
    if (parent.kind !== "group") return null;
    const id = nextId("g");
    this.commit(
      (plan) => {
        plan.nodes[id] = { id, kind: "group", op, parent: parent.id, children: [], dx: 0, dy: 0 };
        plan.nodes[parent.id].children.push(id);
      },
      { structural: true, announce: "A group was added." },
    );
    return id;
  }

  remove(id) {
    const node = this.node(id);
    if (!node || id === this.plan.rootId) return false;
    return this.commit(
      (plan) => {
        const doomed = [];
        const walk = (nodeId) => {
          doomed.push(nodeId);
          const item = plan.nodes[nodeId];
          if (item && item.kind === "group") item.children.forEach(walk);
        };
        walk(id);
        const parent = plan.nodes[node.parent];
        if (parent) parent.children = parent.children.filter((child) => child !== id);
        for (const doomedId of doomed) delete plan.nodes[doomedId];
      },
      { structural: true, announce: "Card removed." },
    );
  }

  /**
   * Cancel the wire between a card and the group it sits in.
   *
   * The card stays on the board with everything it had, set aside from the monitor,
   * and can be joined again at any time. It is deliberately *not* the same as removing
   * it: cancelling a connection is a change to the shape of the rule, and answering it
   * by deleting the person's work would be a different action wearing the same word.
   *
   * The main group is refused. It has no wire to a parent, only one to the coins, and
   * a monitor with no main group has nowhere to put anything.
   */
  detach(id) {
    const node = this.node(id);
    if (!node || id === this.plan.rootId || !node.parent) return false;
    return this.commit(
      (plan) => {
        const parent = plan.nodes[plan.nodes[id].parent];
        if (parent) parent.children = parent.children.filter((child) => child !== id);
        plan.nodes[id].parent = null;
        // The card is about to be laid out on the shelf rather than beside its old
        // group, so a nudge that made sense in the old place would move it off the
        // shelf by that much.
        plan.nodes[id].dx = 0;
        plan.nodes[id].dy = 0;
      },
      { structural: true, announce: "The connection was cancelled. The card is set aside." },
    );
  }

  reparent(id, newParentId, { index = null } = {}) {
    const node = this.node(id);
    const parent = this.node(newParentId);
    if (!node || !parent || parent.kind !== "group" || id === this.plan.rootId) return false;
    if (this.groupsFor(id).every((group) => group.id !== newParentId)) return false;
    return this.commit(
      (plan) => {
        const old = plan.nodes[plan.nodes[id].parent];
        if (old) old.children = old.children.filter((child) => child !== id);
        plan.nodes[id].parent = newParentId;
        plan.nodes[id].dx = 0;
        plan.nodes[id].dy = 0;
        const target = plan.nodes[newParentId].children;
        if (index === null || index < 0 || index > target.length) target.push(id);
        else target.splice(index, 0, id);
      },
      {
        structural: true,
        // Read before the change runs, so it still describes where the card came from.
        // "Moved to another group" is untrue of a card that was not in one.
        announce: node.parent
          ? "Card moved to another group."
          : "The card was joined back into the monitor.",
      },
    );
  }

  setValue(id, name, value) {
    return this.commit((plan) => {
      const node = plan.nodes[id];
      if (!node || node.kind !== "rule") return false;
      if (value === null || value === undefined || value === "") delete node.values[name];
      else node.values[name] = value;
      return true;
    });
  }

  setRequired(id, required) {
    return this.commit((plan) => {
      const node = plan.nodes[id];
      if (!node || node.kind !== "rule") return false;
      node.required = Boolean(required);
      return true;
    });
  }

  setOperator(id, op) {
    return this.commit(
      (plan) => {
        const node = plan.nodes[id];
        if (!node || node.kind !== "group") return false;
        node.op = op;
        return true;
      },
      { structural: true },
    );
  }

  setUniverse(mode) {
    return this.commit((plan) => { plan.universe.mode = mode; });
  }

  setChannels(channels) {
    return this.commit((plan) => { plan.alert.channels = [...channels]; });
  }

  setCooldown(minutes) {
    return this.commit((plan) => { plan.alert.cooldownMinutes = minutes; });
  }

  nudge(id, dx, dy) {
    return this.commit((plan) => {
      const target = id === "universe" ? plan.universe : id === "alert" ? plan.alert : plan.nodes[id];
      if (!target) return false;
      target.dx = Math.round((target.dx || 0) + dx);
      target.dy = Math.round((target.dy || 0) + dy);
      return true;
    });
  }

  tidy() {
    return this.commit(
      (plan) => {
        plan.universe.dx = 0; plan.universe.dy = 0;
        plan.alert.dx = 0; plan.alert.dy = 0;
        for (const node of Object.values(plan.nodes)) { node.dx = 0; node.dy = 0; }
      },
      { announce: "The board was tidied." },
    );
  }
}

function restore() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const plan = JSON.parse(raw);
    if (!plan || plan.version !== 2 || !plan.nodes || !plan.nodes[plan.rootId]) return null;
    return plan;
  } catch {
    return null;
  }
}

/**
 * The values a brand-new rule starts with.
 *
 * Only what the server itself declared a default for. A required field the contract
 * left without one — the comparison is the important case — stays empty, and the
 * settings panel opens on it. Guessing here would compile a rule nobody agreed to.
 */
export function startingValues(mechanic) {
  const values = {};
  for (const parameter of mechanic.parameters || []) {
    if (parameter.default === null || parameter.default === undefined) continue;
    values[parameter.name] = parameter.default;
  }
  return values;
}

/* ── Words ─────────────────────────────────────────────────────────────────── */

const numbers = new Intl.NumberFormat(undefined, { maximumFractionDigits: 8 });

export function formatValue(parameter, value) {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(parameter.choices) && parameter.choices.length) {
    const choice = parameter.choices.find((option) => option.value === String(value));
    return choice ? choice.label : String(value);
  }
  if (typeof value === "number" || (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value)))) {
    const text = numbers.format(Number(value));
    return parameter.unit === "percent" ? `${text}%` : text;
  }
  return String(value);
}

/**
 * One rule as a short clause: "goes up at least 5%".
 *
 * Built only from the words the contract already ships — the choice labels are
 * written as sentence fragments, so joining them in a fixed order produces a
 * readable phrase without this file writing any English of its own.
 *
 * Only the parameters that carry the meaning appear. A lookback period with a
 * server default is real, but it is not what a person is checking at a glance, so it
 * lives in the settings panel instead of crowding the card.
 */
export function ruleClause(mechanic, values) {
  const parameters = mechanic.parameters || [];
  const find = (name) => parameters.find((parameter) => parameter.name === name);
  const shown = parameters.filter(
    (parameter) =>
      parameter.name !== "timeframe" &&
      (parameter.required || HEADLINE_PARAMETERS.has(parameter.name)),
  );

  const comparator = find("comparator");
  const comparatorValue = comparator ? values.comparator : undefined;
  const comparatorIsEvent = EVENT_COMPARATORS.has(String(comparatorValue));

  const head = [];
  const tail = [];
  for (const name of ["direction", "comparator", "threshold"]) {
    const parameter = shown.find((item) => item.name === name);
    if (!parameter) continue;
    const text = formatValue(parameter, values[parameter.name]);
    if (text) head.push({ name, text });
  }
  for (const parameter of shown) {
    if (["direction", "comparator", "threshold"].includes(parameter.name)) continue;
    const text = formatValue(parameter, values[parameter.name]);
    if (text) tail.push({ name: parameter.name, text });
  }

  // "the last candle's low happens" reads; "happens the last candle's low" does not.
  // An event comparison names the thing first and says what it does at the end.
  const ordered = comparatorIsEvent
    ? [...tail, ...head.filter((part) => part.name === "comparator")]
    : [...head, ...tail];

  return ordered.map((part) => part.text).join(" ").trim();
}

/** What is still empty on one rule, in the words of its own form. */
export function missingOn(mechanic, values) {
  return (mechanic.parameters || [])
    .filter((parameter) => parameter.required)
    .filter((parameter) => {
      const value = values[parameter.name];
      return value === null || value === undefined || value === "";
    });
}

/** How a group reads on a card, and how the same group reads inside a sentence. */
const JOIN_WORD = { and: "all of these", or: "any of these", not: "none of these" };

/** What a group of cards is called, in the words the card itself uses.
 *
 *  Exported so anything that has to *describe* a group — the checks, the readout, the
 *  summary the assistant is given — says the same three words the person can read on
 *  the card. A second table of group words would be a second answer to "what does this
 *  group mean", and the first person to see them disagree would be a customer. */
export function groupWord(op) {
  return JOIN_WORD[op] || JOIN_WORD.and;
}
const JOIN_CLAUSE = {
  and: "all of these are true",
  or: "any one of these is true",
  not: "none of these are true",
};

/**
 * The whole monitor as one sentence.
 *
 * Deliberately short. Three rules are listed; beyond that it says how many more,
 * because a readout that grows without limit is the wall of text this page exists to
 * avoid.
 */
export function planSentence(store, catalog, { universeLabel, channelLabels }) {
  const parts = [];
  parts.push(`Watch ${universeLabel || "the screened coins"}.`);

  const phrase = groupPhrase(store, catalog, store.plan.rootId);
  parts.push(phrase.text ? `Tell me when ${phrase.text}.` : "No condition has been added yet.");

  if (channelLabels && channelLabels.length) {
    parts.push(`Send it to ${listWords(channelLabels)}.`);
  }
  return parts.join(" ");
}

/**
 * One group as words, and whether those words are a joined clause.
 *
 * `joined` matters to the caller: a clause that itself contains "all of these are
 * true" needs brackets when it sits inside another one, and a single named condition
 * does not. Brackets around every nested group made a two-card monitor read like an
 * equation.
 */
function groupPhrase(store, catalog, groupId) {
  const children = store.children(groupId);
  if (!children.length) return { text: "", joined: false };
  const group = store.node(groupId);
  const named = [];
  for (const child of children.slice(0, 3)) {
    if (child.kind === "group") {
      const inner = groupPhrase(store, catalog, child.id);
      if (inner.text) named.push(inner.joined ? `(${inner.text})` : inner.text);
      continue;
    }
    const mechanic = catalog.byKey.get(child.mechanic);
    named.push(mechanic ? mechanic.label.toLowerCase() : "a condition this platform no longer offers");
  }
  const extra = children.length - 3;
  if (extra > 0) named.push(`${extra} more`);
  if (!named.length) return { text: "", joined: false };
  if (named.length === 1 && group.op !== "not") return { text: named[0], joined: false };
  return {
    text: `${JOIN_CLAUSE[group.op] || JOIN_CLAUSE.and}: ${listWords(named)}`,
    joined: true,
  };
}

function listWords(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/* ── Checks ────────────────────────────────────────────────────────────────── */

/**
 * Everything standing between this draft and a monitor that could run.
 *
 * Each entry names the card it is about, so the readout can offer to point at it.
 * A "stop" is something the compiler would refuse; a "warn" is something it would
 * accept but a person probably did not mean.
 */
export function checkPlan(store, catalog) {
  const checks = [];
  const limits = store.limits || {};
  // Only the cards that are actually joined to the monitor. A card whose connection
  // was cancelled is on the board but outside the rule, so holding the draft back
  // because *it* is unfinished would refuse a monitor that never mentions it.
  const rules = Object.values(store.plan.nodes).filter(
    (node) => node.kind === "rule" && !store.isAside(node.id),
  );
  const aside = store.asideIds;

  if (aside.length) {
    checks.push({
      id: "aside",
      tone: "warn",
      nodeId: aside[0],
      text: aside.length === 1
        ? "One card is set aside. It is not part of this monitor until you join it back."
        : `${aside.length} cards are set aside. They are not part of this monitor until you join them back.`,
    });
  }

  if (!rules.length) {
    checks.push({ id: "empty", tone: "stop", text: "Add at least one condition." });
  } else {
    checks.push({ id: "empty", tone: "pass", text: `${rules.length} condition${rules.length === 1 ? "" : "s"} on the board.` });
  }

  for (const rule of rules) {
    const mechanic = catalog.byKey.get(rule.mechanic);
    if (!mechanic) {
      checks.push({
        id: `gone-${rule.id}`,
        tone: "stop",
        nodeId: rule.id,
        text: "This platform no longer offers that condition. Remove it and pick another.",
      });
      continue;
    }
    if (!mechanic.available) {
      checks.push({
        id: `blocked-${rule.id}`,
        tone: "stop",
        nodeId: rule.id,
        text: `"${mechanic.label}" cannot run yet. ${mechanic.unavailable_reason || "The data it needs is not connected."}`,
      });
    }
    const missing = missingOn(mechanic, rule.values);
    if (missing.length) {
      // The field names are quoted rather than lowercased into the sentence: "still
      // needs compare how" reads as broken English, while "still needs "Compare how""
      // reads as the label a person can go and look for.
      checks.push({
        id: `missing-${rule.id}`,
        tone: "stop",
        nodeId: rule.id,
        text: `"${mechanic.label}" still needs ${listWords(missing.map((parameter) => `"${parameter.label}"`))}.`,
      });
    }
  }

  for (const group of Object.values(store.plan.nodes)) {
    if (group.kind !== "group") continue;
    // A set-aside group is a shelf, not a hole in the rule. "This group has nothing in
    // it" would be an error about something the monitor does not use.
    if (store.isAside(group.id)) continue;
    const size = group.children.length;
    const arity = (limits.arity || {})[group.op] || [1, null];
    const least = Number(arity[0]) || 1;
    if (size === 0) {
      checks.push({
        id: `bare-${group.id}`,
        tone: "stop",
        nodeId: group.id,
        text: group.id === store.plan.rootId
          ? "The main group has nothing in it."
          : "This group has nothing in it. Add a condition or remove the group.",
      });
    } else if (size < least) {
      checks.push({
        id: `thin-${group.id}`,
        tone: "warn",
        nodeId: group.id,
        text: `"${groupWord(group.op)}" needs at least ${least} cards to mean anything.`,
      });
    }
  }

  // The limit is on the monitor, so it counts what the monitor holds. Counting the
  // shelf as well would refuse a draft over a card the rule never reaches.
  const nodeCount = Object.keys(store.plan.nodes).filter(
    (id) => !store.isAside(id),
  ).length;
  const maxNodes = Number(limits.max_nodes) || 24;
  if (nodeCount > maxNodes) {
    checks.push({ id: "too-many", tone: "stop", text: `A monitor can hold ${maxNodes} cards. This one has ${nodeCount}.` });
  }

  const maxDepth = Number(limits.max_depth) || 6;
  if (store.maxDepth >= maxDepth) {
    checks.push({ id: "too-deep", tone: "stop", text: `Groups can sit ${maxDepth} deep. Flatten one to go further.` });
  }

  if (!store.plan.alert.channels.length) {
    checks.push({ id: "no-channel", tone: "stop", nodeId: "alert", text: "Choose at least one way to be told." });
  } else {
    checks.push({ id: "no-channel", tone: "pass", text: "A way to be told is chosen." });
  }

  return checks;
}

export function readiness(checks) {
  const counted = checks.filter((check) => check.tone !== "warn");
  if (!counted.length) return 0;
  const passed = counted.filter((check) => check.tone === "pass").length;
  return Math.round((passed / counted.length) * 100);
}
