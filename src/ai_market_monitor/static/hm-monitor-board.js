/* The board: cards, wires, and every way of moving them.
 *
 * Drawing rules:
 *   * A card is a title and one clause. Never a paragraph — the long form belongs in
 *     the settings panel, which is the disclosure a person chooses to open.
 *   * Every card is a `treeitem` with a real level and position, so the shape a
 *     sighted person sees on the board is the shape a screen reader is told about.
 *   * Every drag has a keyboard and single-click equal. Dragging a wire re-attaches a
 *     card; so does the "Sits inside" control in its settings. WCAG 2.2 SC 2.5.7.
 *
 * Motion rules (`brand guide.md` §15):
 *   * A new card arrives, a removed card leaves, a new wire draws itself along the
 *     direction the rule flows. Each one describes the change that just happened.
 *   * Nothing loops. Nothing glows. Positions move through a CSS transition, which
 *     `prefers-reduced-motion` already removes.
 */

import { attention, dismiss, drawPath, reveal } from "./hm-motion.js";
import { formatValue, missingOn, ruleClause, universeWords } from "./hm-monitor-plan.js";
import { categoryLook } from "./hm-monitor-catalog.js";

const COLUMN = 336;
const GAP = 22;
/* Wide enough that the shelf reads as a separate place rather than one more row of
   the monitor. Its caption sits in the gap. */
const SHELF_GAP = 96;
const SHELF_CAPTION = 40;
const MIN_SCALE = 0.35;
const MAX_SCALE = 1.6;
/* Fitting the board must never make it unreadable. A board with eight cards on it
   would otherwise shrink to half size on its own, and a person would arrive at a
   monitor they cannot read without doing anything wrong. Below this the board is
   pannable instead; a person can still zoom further out by hand if they want to. */
const READABLE_SCALE = 0.62;

const svgNS = "http://www.w3.org/2000/svg";
const icon = (name, cls = "icon") => (window.icon ? window.icon(name, cls) : "");
const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );

const GROUP_WORDS = {
  and: { title: "All of these", line: "Every card inside has to be true." },
  or: { title: "Any of these", line: "One card inside is enough." },
  not: { title: "None of these", line: "The card inside must not be true." },
};

export class Board {
  constructor(root, { store, catalog, handlers = {} }) {
    this.root = root;
    this.store = store;
    this.catalog = catalog;
    this.handlers = handlers;

    this.board = root.querySelector("[data-board]");
    this.world = root.querySelector("[data-world]");
    this.wires = root.querySelector("[data-wires]");
    this.nodesLayer = root.querySelector("[data-nodes]");
    /* The shelf is a sibling of the tree, not part of it. A `tree` may only contain
       `treeitem`s, and a card whose connection was cancelled is not an item of the
       monitor — telling a screen reader it was would misdescribe the very thing the
       person just changed. */
    this.asideLayer = root.querySelector("[data-aside]");
    this.shelf = root.querySelector("[data-shelf]");
    this.cutsLayer = root.querySelector("[data-cuts]");

    this.view = { x: 60, y: 40, scale: 1 };
    this.elements = new Map();
    this.geometry = new Map();
    this.wirePaths = new Map();
    this.wireHits = new Map();
    this.cutButtons = new Map();
    this.hoveredWire = null;
    this.selectedId = null;
    this.focusId = null;
    this.invite = null;
    this.pan = null;
    this.drag = null;
    this.link = null;
    this.pointers = new Map();
    this.pinch = null;

    this.applyView();
    this.bindBoard();
  }

  /* ── View ────────────────────────────────────────────────────────────────── */

  applyView() {
    const { x, y, scale } = this.view;
    this.world.style.setProperty("--m-x", `${x}px`);
    this.world.style.setProperty("--m-y", `${y}px`);
    this.world.style.setProperty("--m-scale", String(scale));
    this.board.style.setProperty("--m-grid", `${24 * scale}px`);
    this.board.style.setProperty("--m-grid-x", `${x}px`);
    this.board.style.setProperty("--m-grid-y", `${y}px`);
    if (this.handlers.onZoom) this.handlers.onZoom(scale);
  }

  zoomBy(factor, origin) {
    const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, this.view.scale * factor));
    if (next === this.view.scale) return;
    const rect = this.board.getBoundingClientRect();
    const point = origin || { x: rect.width / 2, y: rect.height / 2 };
    // Keep whatever is under the pointer under the pointer.
    this.view.x = point.x - ((point.x - this.view.x) * next) / this.view.scale;
    this.view.y = point.y - ((point.y - this.view.y) * next) / this.view.scale;
    this.view.scale = next;
    this.applyView();
  }

  /** Fit everything on the board, with room to breathe. */
  fit() {
    const boxes = [...this.geometry.values()];
    // The invitation is not a card in the monitor, but it is on the board and it is
    // the thing a first-time visitor came to read. Leaving it out of the measurement
    // fitted everything except the one panel that mattered.
    if (this.invite) {
      boxes.push({
        x: Number.parseFloat(this.invite.style.getPropertyValue("--m-nx")) || 0,
        y: Number.parseFloat(this.invite.style.getPropertyValue("--m-ny")) || 0,
        w: this.invite.offsetWidth,
        h: this.invite.offsetHeight,
      });
    }
    const rect = this.board.getBoundingClientRect();
    if (!boxes.length || !rect.width || !rect.height) return;
    const left = Math.min(...boxes.map((box) => box.x));
    const top = Math.min(...boxes.map((box) => box.y));
    const right = Math.max(...boxes.map((box) => box.x + box.w));
    const bottom = Math.max(...boxes.map((box) => box.y + box.h));
    const pad = 44;
    const width = Math.max(1, right - left);
    const height = Math.max(1, bottom - top);
    const scale = Math.min(
      MAX_SCALE,
      Math.max(READABLE_SCALE, Math.min((rect.width - pad * 2) / width, (rect.height - pad * 2) / height)),
    );
    this.view.scale = scale;
    this.view.x = (rect.width - width * scale) / 2 - left * scale;
    this.view.y = (rect.height - height * scale) / 2 - top * scale;
    this.applyView();
  }

  /**
   * Pan the least amount that brings one card fully into view.
   *
   * Used when a panel opens over the board. Re-fitting everything would throw away
   * the view a person had arranged; moving nothing would leave the card they just
   * selected hidden behind the panel they selected it with.
   */
  keepVisible(id) {
    const box = this.geometry.get(id);
    const rect = this.board.getBoundingClientRect();
    if (!box || !rect.width) return;
    const pad = 24;
    const left = this.view.x + box.x * this.view.scale;
    const top = this.view.y + box.y * this.view.scale;
    const right = left + box.w * this.view.scale;
    const bottom = top + box.h * this.view.scale;
    if (right > rect.width - pad) this.view.x -= right - (rect.width - pad);
    else if (left < pad) this.view.x += pad - left;
    if (bottom > rect.height - pad) this.view.y -= bottom - (rect.height - pad);
    else if (top < pad) this.view.y += pad - top;
    this.applyView();
  }

  toWorld(clientX, clientY) {
    const rect = this.board.getBoundingClientRect();
    return {
      x: (clientX - rect.left - this.view.x) / this.view.scale,
      y: (clientY - rect.top - this.view.y) / this.view.scale,
    };
  }

  /* ── Rendering ───────────────────────────────────────────────────────────── */

  render() {
    const active = document.activeElement;
    const inCards = active
      && (this.nodesLayer.contains(active) || this.asideLayer.contains(active));
    const held = inCards
      ? { node: active.closest("[data-node]"), act: active.dataset ? active.dataset.act : null }
      : null;

    this.syncNodes();
    this.layout();
    this.drawWires();
    this.updateRoving();

    // `syncNodes` rewrites the inside of every card, which throws away whatever had
    // the keyboard. Without putting it back, one press of Alt+Arrow would move a card
    // and then lose the card.
    if (held && held.node) {
      const again = this.elements.get(held.node.dataset.node);
      if (again) {
        const target = held.act ? again.querySelector(`[data-act="${held.act}"]`) : again;
        (target || again).focus({ preventScroll: true });
      }
    }
  }

  /** Every card in the monitor, in the order the tree reads. */
  order() {
    const list = [{ id: "universe", level: 1, position: 1, size: 3 }];
    const walk = (id, level, position, size) => {
      const node = this.store.node(id);
      if (!node) return;
      list.push({ id, level, position, size });
      if (node.kind !== "group") return;
      node.children.forEach((childId, index) =>
        walk(childId, level + 1, index + 1, node.children.length),
      );
    };
    walk(this.store.plan.rootId, 1, 2, 3);
    list.push({ id: "alert", level: 1, position: 3, size: 3 });
    return list;
  }

  /** The cards on the shelf, in the order they were set aside. */
  asideOrder() {
    const ids = this.store.asideIds;
    return ids.map((id, index) => ({
      id,
      aside: true,
      level: 1,
      position: index + 1,
      size: ids.length,
    }));
  }

  /** Everything on the board, monitor first and shelf after. */
  allEntries() {
    return [...this.order(), ...this.asideOrder()];
  }

  /** The ids the arrow keys walk through, in the order they are read. */
  navOrder() {
    return this.allEntries().map((entry) => entry.id);
  }

  syncNodes() {
    const wanted = this.allEntries();
    const wantedIds = new Set(wanted.map((entry) => entry.id));

    for (const [id, element] of [...this.elements]) {
      if (wantedIds.has(id)) continue;
      this.elements.delete(id);
      this.geometry.delete(id);
      dismiss(element).then(() => element.remove());
    }

    const previous = { tree: null, aside: null };
    for (const entry of wanted) {
      const home = entry.aside ? this.asideLayer : this.nodesLayer;
      const lane = entry.aside ? "aside" : "tree";
      let element = this.elements.get(entry.id);
      const isNew = !element;
      if (isNew) {
        element = document.createElement("article");
        element.className = "m-node";
        element.dataset.node = entry.id;
        element.tabIndex = -1;
        this.elements.set(entry.id, element);
      }
      // A card that has just been set aside, or joined back, changes which container
      // it belongs to. Moving it rather than rebuilding it keeps its identity, so the
      // keyboard stays on the card a person was working with.
      element.setAttribute("role", entry.aside ? "listitem" : "treeitem");
      // DOM order follows the tree, so the reading order is right even where
      // `aria-level` alone would leave it ambiguous.
      const after = previous[lane];
      if (after) { if (after.nextSibling !== element) after.after(element); }
      else if (home.firstChild !== element) home.prepend(element);
      previous[lane] = element;
      this.paint(element, entry);
      if (isNew) reveal(element, { from: 0.9 });
    }

    this.shelf.hidden = !this.store.asideIds.length;
    this.paintInvite();
  }

  paint(element, entry) {
    const parts = this.describe(entry);
    const isRoot = entry.id === this.store.plan.rootId;
    const isFixed = entry.id === "universe" || entry.id === "alert";
    const isAside = Boolean(entry.aside);

    if (isAside) {
      // Said in words as well as by position, because "which lane is it in" is not
      // something a person reading one card can see. WCAG 2.2 SC 1.4.1: never colour
      // or placement alone.
      parts.tags = [{ tone: "neutral", text: "Set aside", icon: "unlink" }, ...parts.tags];
      // An empty group or a missing value is only a problem for a card the monitor
      // actually reads. On the shelf it is a card waiting, not a card that is wrong.
      parts.incomplete = false;
    }

    element.dataset.kind = parts.kind;
    element.dataset.aside = isAside ? "true" : "false";
    if (isAside) element.removeAttribute("aria-level");
    else element.setAttribute("aria-level", String(entry.level));
    element.setAttribute("aria-posinset", String(entry.position));
    element.setAttribute("aria-setsize", String(entry.size));
    // `aria-selected` belongs to the tree pattern. A `listitem` that carries it claims
    // a selection model the shelf does not have.
    if (isAside) element.removeAttribute("aria-selected");
    else element.setAttribute("aria-selected", entry.id === this.selectedId ? "true" : "false");
    element.setAttribute(
      "aria-label",
      isAside
        ? `Set aside, not part of the monitor. ${parts.kindLabel}: ${parts.title}`
        : `${parts.kindLabel}: ${parts.title}`,
    );
    if (parts.kind === "group" && !isAside) element.setAttribute("aria-expanded", "true");
    else element.removeAttribute("aria-expanded");
    element.dataset.blocked = parts.blocked ? "true" : "false";
    element.dataset.incomplete = parts.incomplete ? "true" : "false";

    // A card can be re-attached only if there is somewhere to attach it to. The two
    // fixed cards and the main group have no other home, so they carry no wire handle
    // that would promise one.
    const canMove = !isFixed && !isRoot;
    const canAdd = parts.kind === "group" && !isAside;
    // Only a card that is actually joined to something has a connection to cancel.
    const canCut = canMove && !isAside;

    element.innerHTML = `
      ${canMove ? `<span class="m-port m-port-in" data-port="in" aria-hidden="true" title="${isAside ? "Drag onto a group to join this card back" : "Drag onto a group to move this card, or onto empty space to cancel the connection"}"></span>` : ""}
      ${canAdd ? `<span class="m-port m-port-out" data-port="out" aria-hidden="true" title="Drag onto a card to move it into this group"></span>` : ""}
      ${canMove ? `<span class="m-grip" data-grip aria-hidden="true">${icon("grip", "icon-sm")}</span>` : ""}
      <span class="m-chip" aria-hidden="true">${icon(parts.icon, "icon")}</span>
      <span class="m-node-copy">
        <span class="m-node-kind">${escapeHtml(parts.kindLabel)}</span>
        <strong class="m-node-title">${escapeHtml(parts.title)}</strong>
        ${parts.line ? `<p class="m-node-line">${escapeHtml(parts.line)}</p>` : ""}
      </span>
      <span class="m-node-foot">
        <span class="m-node-tags">${parts.tags
          .map((tag) => `<span class="t-pill" data-tone="${tag.tone}">${tag.icon ? icon(tag.icon, "icon-sm") : ""}${escapeHtml(tag.text)}</span>`)
          .join("")}</span>
        <span class="m-node-acts">
          ${canAdd ? act("add", "plus", `Add a condition inside ${parts.title}`) : ""}
          ${isAside ? act("join", "link", `Join ${parts.title} back into the monitor`) : ""}
          ${canCut ? act("cut", "unlink", `Cancel the connection to ${parts.title}`) : ""}
          ${act("edit", "sliders", `Settings for ${parts.title}`)}
          ${canMove ? act("remove", "trash", `Remove ${parts.title}`, true) : ""}
        </span>
      </span>
    `;
  }

  /** Everything a card shows, decided in one place so no two cards disagree. */
  describe(entry) {
    if (entry.id === "universe") {
      const universe = this.store.plan.universe;
      const choice = (this.catalog.universes || []).find(
        (option) => option.value === universe.mode,
      );
      const screened = this.root.dataset.screeningConfigured === "true";
      // Two of the three ways of choosing need a second answer. The card says which
      // one is still missing rather than reading as finished, because "One of my
      // Favorites lists" with no list chosen is not a choice a monitor can run on.
      const waiting =
        (universe.mode === "approved_watchlist" && !universe.watchlistId)
        || (universe.mode === "explicit_assets" && !(universe.symbols || []).length);
      const tags = [
        screened
          ? { tone: "eligible", text: "Shariah screened", icon: "shield_check" }
          : { tone: "review", text: "No standard active yet", icon: "alert" },
      ];
      if (universe.mode === "explicit_assets" && (universe.symbols || []).length) {
        tags.push({
          tone: "neutral",
          text: `${universe.symbols.length} coin${universe.symbols.length === 1 ? "" : "s"}`,
          icon: "coins",
        });
      }
      if (waiting) tags.push({ tone: "review", text: "Needs a choice", icon: "alert" });
      return {
        kind: "universe",
        kindLabel: "Coins to watch",
        icon: "coins",
        title: choice ? choice.label : "Every eligible coin",
        // The same words the readout uses, from the one place that writes them.
        line: waiting
          ? "Open this card and choose."
          : `Watching ${universeWords(this.store, choice ? choice.label.toLowerCase() : "")}.`,
        tags,
        blocked: false,
        incomplete: waiting,
      };
    }

    if (entry.id === "alert") {
      const labels = (this.handlers.channelLabels || (() => []))(this.store.plan.alert.channels);
      return {
        kind: "alert",
        kindLabel: "How you hear about it",
        icon: "bell",
        title: labels.length ? labels.join(" and ") : "Not chosen yet",
        line: labels.length
          ? `At most one message every ${this.store.plan.alert.cooldownMinutes} minutes.`
          : "Pick at least one way to be told.",
        tags: [],
        blocked: false,
        incomplete: !labels.length,
      };
    }

    const node = this.store.node(entry.id);
    if (node.kind === "group") {
      const words = GROUP_WORDS[node.op] || GROUP_WORDS.and;
      const size = node.children.length;
      return {
        kind: "group",
        kindLabel: "Group",
        icon: "branch",
        title: words.title,
        line: words.line,
        tags: [{ tone: "neutral", text: size === 1 ? "1 card" : `${size} cards`, icon: "layers" }],
        blocked: false,
        incomplete: size === 0,
      };
    }

    const mechanic = this.catalog.byKey.get(node.mechanic);
    if (!mechanic) {
      return {
        kind: "rule",
        kindLabel: "Condition",
        icon: "warning",
        title: "No longer offered",
        line: "This platform does not offer that condition any more. Remove it and pick another.",
        tags: [],
        blocked: true,
        incomplete: true,
      };
    }

    const look = categoryLook(mechanic.category);
    const missing = missingOn(mechanic, node.values);
    const timeframe = (mechanic.parameters || []).find((parameter) => parameter.name === "timeframe");
    const tags = [];
    if (timeframe && node.values.timeframe) {
      tags.push({ tone: "neutral", text: formatValue(timeframe, node.values.timeframe), icon: "clock" });
    }
    // Only the exception is labelled. Every card in an "all of these" group has to be
    // true, so a badge saying so on every one of them was three words of noise per
    // card and pushed the tags onto three lines. The wire to an optional card is drawn
    // dashed as well, so the difference is never carried by one label alone.
    if (!node.required) tags.push({ tone: "neutral", text: "Nice to have", icon: "info" });
    if (!mechanic.available) tags.push({ tone: "excluded", text: "Cannot run yet", icon: "alert" });
    else if (missing.length) tags.push({ tone: "review", text: "Needs a value", icon: "alert" });

    return {
      kind: "rule",
      kindLabel: look.name,
      icon: look.icon,
      title: mechanic.label,
      line: ruleClause(mechanic, node.values) || mechanic.explanation,
      tags,
      blocked: !mechanic.available,
      incomplete: missing.length > 0,
    };
  }

  /* The invitation lives beside the tree, not inside it: a `tree` may only contain
     `treeitem`s, and a prompt to add the first one is not a card in the monitor. */
  paintInvite() {
    if (this.store.ruleCount > 0) {
      if (this.invite) {
        const going = this.invite;
        this.invite = null;
        dismiss(going).then(() => going.remove());
      }
      return;
    }
    if (this.invite) return;
    const starters = (this.catalog.starters || [])
      .filter((starter) => starter.mode !== "scanner")
      .slice(0, 4);
    const element = document.createElement("div");
    element.className = "m-invite";
    element.innerHTML = `
      <h3>Add your first condition</h3>
      <p>Choose what the market has to do before you hear about it.</p>
      <button class="t-action is-primary" type="button" data-invite-open>${icon("plus", "icon-sm")}Open the list</button>
      ${starters.length ? `<p class="m-field-help">Or start from one of these:</p>
        <span class="m-starters">${starters
          .map((starter) => `<button class="m-starter" type="button" data-starter="${escapeHtml(starter.key)}">${escapeHtml(starter.label)}</button>`)
          .join("")}</span>` : ""}
    `;
    this.world.appendChild(element);
    this.invite = element;
    reveal(element, { from: 0.94 });
  }

  /* ── Layout ──────────────────────────────────────────────────────────────── */

  layout() {
    const heights = new Map();
    for (const [id, element] of this.elements) heights.set(id, element.offsetHeight || 112);

    const positions = new Map();
    let cursor = 0;
    let deepest = 0;

    const place = (id, depth) => {
      deepest = Math.max(deepest, depth);
      const node = this.store.node(id);
      const height = heights.get(id) || 112;
      if (node && node.kind === "group" && node.children.length) {
        const spans = node.children.map((childId) => place(childId, depth + 1));
        const top = spans[0].y;
        const bottom = spans[spans.length - 1].y + spans[spans.length - 1].h;
        const box = { x: (depth + 1) * COLUMN, y: top + (bottom - top - height) / 2, h: height };
        positions.set(id, box);
        return box;
      }
      const box = { x: (depth + 1) * COLUMN, y: cursor, h: height };
      cursor += height + GAP;
      positions.set(id, box);
      return box;
    };

    const rootBox = place(this.store.plan.rootId, 0);

    const universeHeight = heights.get("universe") || 112;
    positions.set("universe", {
      x: 0,
      y: rootBox.y + (rootBox.h - universeHeight) / 2,
      h: universeHeight,
    });

    const bottom = Math.max(...[...positions.values()].map((box) => box.y + box.h));
    positions.set("alert", {
      x: (deepest + 2) * COLUMN,
      y: bottom + GAP,
      h: heights.get("alert") || 112,
    });

    /* The shelf sits under the whole monitor, in its own row, with a caption above it.
       Below rather than beside: a lane to the right would put cards that are *not*
       being watched in the place a person reads last, which is where the alert card —
       the end of the rule — already is. */
    const asideIds = this.store.asideIds;
    const shelfTop =
      Math.max(...[...positions.values()].map((box) => box.y + box.h)) + SHELF_GAP;
    asideIds.forEach((id, index) => {
      positions.set(id, {
        x: index * COLUMN,
        y: shelfTop,
        h: heights.get(id) || 112,
      });
    });
    if (this.shelf) {
      this.shelf.style.setProperty("--m-nx", "0px");
      this.shelf.style.setProperty("--m-ny", `${shelfTop - SHELF_CAPTION}px`);
    }

    this.geometry.clear();
    for (const [id, box] of positions) {
      const element = this.elements.get(id);
      if (!element) continue;
      const offset = this.offsetOf(id);
      const x = box.x + offset.dx;
      const y = box.y + offset.dy;
      element.style.setProperty("--m-nx", `${x}px`);
      element.style.setProperty("--m-ny", `${y}px`);
      this.geometry.set(id, { x, y, w: element.offsetWidth || 264, h: box.h });
    }

    if (this.invite) {
      this.invite.style.setProperty("--m-nx", `${COLUMN * 2}px`);
      this.invite.style.setProperty("--m-ny", `${rootBox.y - 16}px`);
    }
  }

  offsetOf(id) {
    const target =
      id === "universe" ? this.store.plan.universe
      : id === "alert" ? this.store.plan.alert
      : this.store.node(id);
    return { dx: (target && target.dx) || 0, dy: (target && target.dy) || 0 };
  }

  /* ── Wires ───────────────────────────────────────────────────────────────── */

  wireList() {
    const links = [{ key: `universe>${this.store.plan.rootId}`, from: "universe", to: this.store.plan.rootId, optional: false }];
    for (const node of Object.values(this.store.plan.nodes)) {
      if (!node.parent) continue;
      links.push({
        key: `${node.parent}>${node.id}`,
        from: node.parent,
        to: node.id,
        optional: node.kind === "rule" && !node.required,
      });
    }
    links.push({ key: `${this.store.plan.rootId}>alert`, from: this.store.plan.rootId, to: "alert", optional: false });
    return links;
  }

  drawWires() {
    const wanted = new Map(this.wireList().map((link) => [link.key, link]));

    for (const [key, path] of [...this.wirePaths]) {
      if (wanted.has(key)) continue;
      this.wirePaths.delete(key);
      path.remove();
    }
    for (const [key, path] of [...this.wireHits]) {
      if (wanted.has(key)) continue;
      this.wireHits.delete(key);
      path.remove();
    }
    for (const [key, button] of [...this.cutButtons]) {
      if (wanted.has(key)) continue;
      this.cutButtons.delete(key);
      button.remove();
    }

    for (const [key, link] of wanted) {
      const from = this.geometry.get(link.from);
      const to = this.geometry.get(link.to);
      if (!from || !to) continue;
      let path = this.wirePaths.get(key);
      const isNew = !path;
      if (isNew) {
        path = document.createElementNS(svgNS, "path");
        path.setAttribute("class", "m-wire");
        path.dataset.wire = key;
        this.wires.appendChild(path);
        this.wirePaths.set(key, path);
      }
      const x1 = from.x + from.w;
      const y1 = from.y + from.h / 2;
      const x2 = to.x;
      const y2 = to.y + to.h / 2;
      path.dataset.optional = link.optional ? "true" : "false";
      path.setAttribute("d", curve(x1, y1, x2, y2));
      // The line a person sees is 2px wide. Two pixels is not something anybody can
      // reliably point at, so an invisible wide copy of the same curve takes the
      // pointer instead. Without it a wire could be looked at but never touched.
      let hit = this.wireHits.get(key);
      if (!hit) {
        hit = document.createElementNS(svgNS, "path");
        hit.setAttribute("class", "m-wire-hit");
        hit.dataset.wireHit = key;
        this.wires.appendChild(hit);
        this.wireHits.set(key, hit);
      }
      hit.setAttribute("d", path.getAttribute("d"));
      this.placeCut(key, link, (x1 + x2) / 2, (y1 + y2) / 2);
      if (isNew) drawPath(path);
    }
    this.updateCuts();
  }

  /**
   * The button that cancels one connection, sitting on the connection itself.
   *
   * A real button rather than a click target on the line: it is the same action the
   * card offers, so it needs the same name, the same focus ring and the same keyboard
   * reach. Only wires that can actually be cancelled get one — the coins and the alert
   * are fixed ends of the rule, and offering to cut them would promise something the
   * plan cannot represent.
   */
  placeCut(key, link, x, y) {
    const cuttable = link.to !== "alert" && link.from !== "universe";
    let button = this.cutButtons.get(key);
    if (!cuttable) {
      if (button) { button.remove(); this.cutButtons.delete(key); }
      return;
    }
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "m-cut";
      button.dataset.cut = link.to;
      button.dataset.cutWire = key;
      button.tabIndex = -1;
      button.innerHTML = `${icon("unlink", "icon-sm")}<span class="sr-only"></span>`;
      this.cutsLayer.appendChild(button);
      this.cutButtons.set(key, button);
    }
    // Position only. Naming the card costs a full `describe`, and this runs for every
    // wire on every frame of a drag — the two buttons that are actually on screen get
    // their name in `updateCuts` instead.
    button.style.setProperty("--m-nx", `${x}px`);
    button.style.setProperty("--m-ny", `${y}px`);
  }

  /**
   * Which cut buttons are offered right now.
   *
   * Only the wire being pointed at, or the wire into the selected card. Showing all of
   * them at once puts a circle in the middle of every line on the board, which turns a
   * readable picture into a row of buttons.
   */
  updateCuts() {
    const selectedWire = this.selectedId ? this.wireInto(this.selectedId) : null;
    for (const [key, button] of this.cutButtons) {
      const shown = key === this.hoveredWire || key === selectedWire;
      button.dataset.shown = shown ? "true" : "false";
      button.tabIndex = shown ? 0 : -1;
      button.setAttribute("aria-hidden", shown ? "false" : "true");
      if (!shown) continue;
      // Named here, where at most two buttons are ever visible, and only when the name
      // has changed — the label has to say *which* connection, or a screen reader hears
      // "cancel the connection" four times with nothing to tell them apart.
      const name = `Cancel the connection to ${this.describe({ id: button.dataset.cut }).title}`;
      if (button.getAttribute("aria-label") === name) continue;
      button.setAttribute("aria-label", name);
      button.querySelector(".sr-only").textContent = name;
    }
  }

  /** The key of the wire that joins this card to its group, if it has one. */
  wireInto(id) {
    const node = this.store.node(id);
    if (!node || !node.parent) return null;
    return `${node.parent}>${id}`;
  }

  /** Light the run of wires between one card and the coins it starts from. */
  lightPath(id) {
    const lit = new Set();
    let cursor = id === "alert" ? this.store.plan.rootId : id;
    if (id === "alert") lit.add(`${this.store.plan.rootId}>alert`);
    while (cursor) {
      const node = this.store.node(cursor);
      const parent = node ? node.parent : null;
      if (parent) lit.add(`${parent}>${cursor}`);
      else if (cursor === this.store.plan.rootId) lit.add(`universe>${cursor}`);
      cursor = parent;
    }
    for (const [key, path] of this.wirePaths) path.dataset.lit = lit.has(key) ? "true" : "false";
  }

  clearLight() {
    for (const path of this.wirePaths.values()) path.dataset.lit = "false";
  }

  /* ── Selection and focus ─────────────────────────────────────────────────── */

  select(id, { focus = false, tell = true } = {}) {
    this.selectedId = id;
    for (const [nodeId, element] of this.elements) {
      // Only the tree carries a selection state. The shelf is a list.
      if (element.dataset.aside === "true") continue;
      element.setAttribute("aria-selected", nodeId === id ? "true" : "false");
    }
    if (id) {
      this.focusId = id;
      this.lightPath(id);
      this.updateRoving();
      if (focus) {
        const element = this.elements.get(id);
        if (element) element.focus({ preventScroll: true });
      }
    } else {
      this.clearLight();
    }
    this.updateCuts();
    if (tell && this.handlers.onSelect) this.handlers.onSelect(id);
  }

  /* One card at a time is in the tab order, and its own buttons join the tab order
     with it. That is the roving pattern: Tab reaches the board once, then the card's
     actions, then leaves — rather than walking through seventy controls. */
  updateRoving() {
    const ids = this.navOrder();
    if (!ids.includes(this.focusId)) this.focusId = ids[0];
    for (const [id, element] of this.elements) {
      const here = id === this.focusId;
      element.tabIndex = here ? 0 : -1;
      for (const button of element.querySelectorAll("[data-act]")) button.tabIndex = here ? 0 : -1;
    }
  }

  /** Bring a card into view and say which one, once. */
  bringIntoView(id) {
    const box = this.geometry.get(id);
    const element = this.elements.get(id);
    if (!box || !element) return;
    const rect = this.board.getBoundingClientRect();
    this.view.x = rect.width / 2 - (box.x + box.w / 2) * this.view.scale;
    this.view.y = rect.height / 2 - (box.y + box.h / 2) * this.view.scale;
    this.applyView();
    this.select(id, { focus: true });
    attention(element);
  }

  /* ── Pointer and keyboard ────────────────────────────────────────────────── */

  bindBoard() {
    this.board.addEventListener("pointerdown", (event) => this.onPointerDown(event));
    this.board.addEventListener("pointermove", (event) => this.onPointerMove(event));
    this.board.addEventListener("pointerup", (event) => this.onPointerUp(event));
    this.board.addEventListener("pointercancel", (event) => this.onPointerUp(event));
    this.board.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
    this.board.addEventListener("keydown", (event) => this.onKeyDown(event));

    this.world.addEventListener("click", (event) => this.onClick(event));

    /* Escape has to be heard wherever the keyboard happens to be. A wire drag captures
       the pointer but does not move focus, so a listener on the board alone would miss
       it whenever the person had last clicked somewhere else. */
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !this.link) return;
      event.preventDefault();
      event.stopPropagation();
      this.cancelLink();
    }, true);

    /* Pointing at a wire offers the button that cancels it. */
    this.wires.addEventListener("pointerover", (event) => {
      const hit = event.target.closest ? event.target.closest("[data-wire-hit]") : null;
      if (!hit || this.link) return;
      this.hoveredWire = hit.dataset.wireHit;
      const path = this.wirePaths.get(this.hoveredWire);
      if (path) path.dataset.lit = "true";
      this.updateCuts();
    });
    this.wires.addEventListener("pointerout", (event) => {
      const to = event.relatedTarget;
      if (to && to.closest && to.closest("[data-wire-hit]")) return;
      this.hoveredWire = null;
      if (this.selectedId) this.lightPath(this.selectedId);
      else this.clearLight();
      this.updateCuts();
    });
    /* The cut button sits over the board, so the pointer can leave the wire on the way
       to it. Keeping it up while it is being pointed at is what makes it clickable. */
    this.cutsLayer.addEventListener("pointerover", (event) => {
      const button = event.target.closest("[data-cut-wire]");
      if (!button) return;
      this.hoveredWire = button.dataset.cutWire;
      this.updateCuts();
    });
    this.cutsLayer.addEventListener("pointerout", (event) => {
      const to = event.relatedTarget;
      if (to && to.closest && to.closest("[data-cut-wire]")) return;
      this.hoveredWire = null;
      this.updateCuts();
    });
    this.cutsLayer.addEventListener("focusin", (event) => {
      const button = event.target.closest("[data-cut-wire]");
      if (button) { this.hoveredWire = button.dataset.cutWire; this.updateCuts(); }
    });

    for (const layer of [this.nodesLayer, this.asideLayer]) {
      layer.addEventListener("pointerover", (event) => {
        const node = event.target.closest("[data-node]");
        if (node) this.lightPath(node.dataset.node);
      });
      layer.addEventListener("pointerout", (event) => {
        const to = event.relatedTarget;
        if (to && to.closest && to.closest("[data-node]")) return;
        if (this.selectedId) this.lightPath(this.selectedId);
        else this.clearLight();
      });
      layer.addEventListener("focusin", (event) => {
        const node = event.target.closest("[data-node]");
        if (!node) return;
        this.focusId = node.dataset.node;
        this.lightPath(this.focusId);
        this.updateCuts();
      });
    }
  }

  onClick(event) {
    const starter = event.target.closest("[data-starter]");
    if (starter) {
      if (this.handlers.onStarter) this.handlers.onStarter(starter.dataset.starter);
      return;
    }
    if (event.target.closest("[data-invite-open]")) {
      if (this.handlers.onAdd) this.handlers.onAdd(this.store.plan.rootId);
      return;
    }
    const cut = event.target.closest("[data-cut]");
    if (cut) { this.cut(cut.dataset.cut); return; }
    const wireHit = event.target.closest ? event.target.closest("[data-wire-hit]") : null;
    if (wireHit) {
      // Clicking a line selects the card it feeds. The connection and the card it ends
      // at are the same fact, and giving a line its own selection state would be a
      // second thing to keep in step with the first.
      this.select(wireHit.dataset.wireHit.split(">")[1], { focus: true });
      return;
    }
    const node = event.target.closest("[data-node]");
    if (!node) return;
    const id = node.dataset.node;
    const action = event.target.closest("[data-act]");
    if (!action) { this.select(id); return; }
    if (action.dataset.act === "add" && this.handlers.onAdd) this.handlers.onAdd(id);
    if (action.dataset.act === "edit" && this.handlers.onOpenSettings) this.handlers.onOpenSettings(id);
    if (action.dataset.act === "remove" && this.handlers.onRemove) this.handlers.onRemove(id);
    if (action.dataset.act === "cut") this.cut(id);
    if (action.dataset.act === "join") this.join(id);
  }

  /**
   * Cancel one connection without a drag. WCAG 2.2 SC 2.5.7.
   *
   * The keyboard and single-click equal of dropping a carried wire on empty space, and
   * the same call underneath, so the two routes cannot come to mean different things.
   */
  cut(id) {
    if (!this.store.detach(id)) return;
    // The shelf sits below the whole monitor, which on a full board is off the bottom
    // of the screen. Without following the card there, cancelling a connection looked
    // exactly like deleting the card: it left the view and never arrived anywhere a
    // person could see.
    window.requestAnimationFrame(() => this.bringIntoView(id));
  }

  /** Join a set-aside card back, without a drag. */
  join(id) {
    if (this.handlers.onJoin) this.handlers.onJoin(id);
  }

  onPointerDown(event) {
    if (event.button !== 0 && event.button !== 1) return;
    this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      this.pinch = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: this.view.scale };
      this.pan = null;
      this.drag = null;
      this.board.dataset.panning = "false";
      return;
    }

    const port = event.target.closest("[data-port]");
    if (port) {
      this.startLink(port.closest("[data-node]").dataset.node, port.dataset.port, event);
      return;
    }

    const node = event.target.closest("[data-node]");
    if (node) {
      if (!event.target.closest("button, a, input, select")) this.startDrag(node.dataset.node, event);
      return;
    }
    if (this.isBackground(event.target)) this.startPan(event);
  }

  /**
   * Whether a press landed on the empty board rather than on something.
   *
   * Asked positively, and it has to be. Panning captures the pointer, and a captured
   * pointer sends the click that follows to the element that captured it — so a pan
   * started under a button eats that button's click. Listing the things that are *not*
   * the board missed the message strip, and the "Undo" offered there stopped working
   * the moment anything else was added over the board.
   */
  isBackground(target) {
    // A wire is a thing, not the background behind it. Left in, a press on a line
    // started a pan, the pan captured the pointer, and the click that followed was
    // delivered to the board instead of the wire — so clicking a line did nothing.
    if (target.dataset && target.dataset.wireHit) return false;
    return target === this.board || target === this.world || target === this.nodesLayer
      || target === this.asideLayer
      || target === this.wires || (target.ownerSVGElement && target.ownerSVGElement === this.wires);
  }

  onPointerMove(event) {
    if (this.pointers.has(event.pointerId)) {
      this.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    if (this.pinch && this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (this.pinch.distance > 0) {
        const rect = this.board.getBoundingClientRect();
        const target = Math.min(MAX_SCALE, Math.max(MIN_SCALE, (this.pinch.scale * distance) / this.pinch.distance));
        this.zoomBy(target / this.view.scale, {
          x: (a.x + b.x) / 2 - rect.left,
          y: (a.y + b.y) / 2 - rect.top,
        });
      }
      return;
    }
    if (this.pan) this.movePan(event);
    else if (this.drag) this.moveDrag(event);
    else if (this.link) this.moveLink(event);
  }

  onPointerUp(event) {
    this.pointers.delete(event.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    if (this.pan) this.endPan(event);
    else if (this.drag) this.endDrag(event);
    else if (this.link) this.endLink(event);
  }

  startPan(event) {
    this.pan = { x: event.clientX, y: event.clientY, ox: this.view.x, oy: this.view.y, moved: false };
    this.board.dataset.panning = "true";
    this.board.setPointerCapture(event.pointerId);
  }

  movePan(event) {
    this.view.x = this.pan.ox + (event.clientX - this.pan.x);
    this.view.y = this.pan.oy + (event.clientY - this.pan.y);
    if (Math.abs(event.clientX - this.pan.x) + Math.abs(event.clientY - this.pan.y) > 3) this.pan.moved = true;
    this.applyView();
  }

  endPan(event) {
    const moved = this.pan.moved;
    this.pan = null;
    this.board.dataset.panning = "false";
    this.release(event);
    if (!moved) this.select(null);
  }

  startDrag(id, event) {
    const element = this.elements.get(id);
    const box = this.geometry.get(id);
    if (!element || !box) return;
    this.drag = {
      id,
      element,
      startX: event.clientX,
      startY: event.clientY,
      baseX: box.x,
      baseY: box.y,
      dx: 0,
      dy: 0,
      moved: false,
    };
    element.classList.add("is-dragging");
    this.board.setPointerCapture(event.pointerId);
  }

  moveDrag(event) {
    const drag = this.drag;
    drag.dx = (event.clientX - drag.startX) / this.view.scale;
    drag.dy = (event.clientY - drag.startY) / this.view.scale;
    if (Math.abs(drag.dx) + Math.abs(drag.dy) > 2) drag.moved = true;
    drag.element.style.setProperty("--m-nx", `${drag.baseX + drag.dx}px`);
    drag.element.style.setProperty("--m-ny", `${drag.baseY + drag.dy}px`);
    const box = this.geometry.get(drag.id);
    box.x = drag.baseX + drag.dx;
    box.y = drag.baseY + drag.dy;
    this.drawWires();
  }

  endDrag(event) {
    const drag = this.drag;
    this.drag = null;
    drag.element.classList.remove("is-dragging");
    this.release(event);
    if (!drag.moved) { this.select(drag.id, { focus: true }); return; }
    this.store.nudge(drag.id, drag.dx, drag.dy);
  }

  /**
   * Start dragging a wire.
   *
   * Taking hold of the inbound handle of a card that is already joined **picks the
   * existing connection up** rather than starting a second one — the idiom every node
   * editor uses, and the thing that was missing: a wire could be moved from one group
   * to another but never taken off, because letting go over nothing simply put it
   * back. Now the line is carried, and where it is dropped decides:
   *
   *   * on a group  — the card joins that group;
   *   * on empty space — the connection is cancelled and the card is set aside;
   *   * `Escape` — nothing changes at all.
   */
  startLink(id, side, event) {
    const anchor = this.geometry.get(id);
    if (!anchor) return;
    const node = this.store.node(id);
    const carrying = side === "in" && node && node.parent ? `${node.parent}>${id}` : null;
    const parentBox = carrying ? this.geometry.get(node.parent) : null;
    const from = parentBox
      ? { x: parentBox.x + parentBox.w, y: parentBox.y + parentBox.h / 2 }
      : {
        x: side === "out" ? anchor.x + anchor.w : anchor.x,
        y: anchor.y + anchor.h / 2,
      };
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("class", "m-wire-draft");
    path.dataset.mode = "aim";
    this.wires.appendChild(path);
    this.link = { id, side, path, target: null, carrying, overEmpty: false, from, pointerId: event.pointerId };
    // The real wire steps out of the way while its copy is being carried. Two lines
    // between the same two cards would read as a second connection being made.
    if (carrying) {
      const existing = this.wirePaths.get(carrying);
      if (existing) existing.dataset.carried = "true";
    }
    this.markDroppable(id, side);
    this.board.setPointerCapture(event.pointerId);
    this.board.dataset.linking = "true";
    if (this.handlers.onCoach) {
      this.handlers.onCoach(
        side === "out"
          ? "Drop the line on a card to move it into this group. Esc to leave it as it was."
          : carrying
            ? "Drop on a group to move this card there, or on empty space to cancel the connection. Esc to leave it as it was."
            : "Drop the line on a group to join this card to it. Esc to leave it as it was.",
      );
    }
  }

  moveLink(event) {
    const point = this.toWorld(event.clientX, event.clientY);
    const { from } = this.link;
    this.link.path.setAttribute("d", curve(from.x, from.y, point.x, point.y));
    const under = document.elementFromPoint(event.clientX, event.clientY);
    const node = under && under.closest ? under.closest("[data-node]") : null;
    this.link.target = node && node.dataset.droppable === "true" ? node.dataset.node : null;
    /* "Empty space" means inside the board and not on a card — decided from the board's
       own rectangle rather than from whatever element happens to be on top. Asking only
       what is under the pointer made the answer depend on things that are not part of
       the question: a message strip laid over the canvas, or a point past the bottom of
       the window, where there is no element at all. */
    const rect = this.board.getBoundingClientRect();
    const inside = event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom;
    this.link.overEmpty = inside && !node && Boolean(under) && this.isBackground(under);
    // The line says what letting go would do, before it is let go. Guessing and finding
    // out afterwards is the part of a drag that people get wrong.
    this.link.path.dataset.mode = this.link.target
      ? "join"
      : this.link.carrying && this.link.overEmpty
        ? "cut"
        : "aim";
  }

  endLink(event) {
    const link = this.closeLink();
    this.release(event);
    if (link.target) {
      if (link.side === "out") this.store.reparent(link.target, link.id);
      else this.store.reparent(link.id, link.target);
      return;
    }
    // Let go over the empty board while carrying a connection: that is the cancel.
    // Deliberately only while *carrying* one — a wire started from a card that has no
    // group has nothing to cancel, and dropping it on the board must do nothing.
    if (link.carrying && link.overEmpty && this.store.detach(link.id)) {
      window.requestAnimationFrame(() => this.bringIntoView(link.id));
    }
  }

  /**
   * Abandon a wire that is being dragged, changing nothing.
   *
   * The escape hatch every drag needs. Without it the only ways out of a wire drag
   * were to complete it or to drop it somewhere and undo.
   */
  cancelLink() {
    if (!this.link) return false;
    const link = this.closeLink();
    if (this.board.hasPointerCapture(link.pointerId)) {
      this.board.releasePointerCapture(link.pointerId);
    }
    if (this.handlers.onAnnounce) this.handlers.onAnnounce("The connection was left as it was.");
    return true;
  }

  /** Take down the draft line and every mark it put on the board. One place, so an
      escape and a completed drop can never clean up differently. */
  closeLink() {
    const link = this.link;
    this.link = null;
    link.path.remove();
    for (const element of this.elements.values()) delete element.dataset.droppable;
    if (link.carrying) {
      const existing = this.wirePaths.get(link.carrying);
      if (existing) delete existing.dataset.carried;
    }
    this.board.dataset.linking = "false";
    if (this.handlers.onCoach) this.handlers.onCoach("");
    return link;
  }

  /** Say which cards would accept this wire, before it is let go. */
  markDroppable(id, side) {
    const allowed = new Set();
    if (side === "out") {
      for (const node of Object.values(this.store.plan.nodes)) {
        if (node.id === id || node.parent === id) continue;
        if (this.store.groupsFor(node.id).some((group) => group.id === id)) allowed.add(node.id);
      }
    } else {
      const node = this.store.node(id);
      for (const group of this.store.groupsFor(id)) {
        if (node && group.id === node.parent) continue;
        allowed.add(group.id);
      }
    }
    for (const [nodeId, element] of this.elements) {
      if (nodeId === id) continue;
      element.dataset.droppable = allowed.has(nodeId) ? "true" : "false";
    }
  }

  release(event) {
    if (this.board.hasPointerCapture(event.pointerId)) this.board.releasePointerCapture(event.pointerId);
  }

  onWheel(event) {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      const rect = this.board.getBoundingClientRect();
      this.zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12, {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
      return;
    }
    this.view.x -= event.shiftKey ? event.deltaY : event.deltaX;
    this.view.y -= event.shiftKey ? 0 : event.deltaY;
    this.applyView();
  }

  onKeyDown(event) {
    const ids = this.navOrder();
    const current = this.focusId || ids[0];
    const index = ids.indexOf(current);
    const inField = Boolean(event.target.closest("input, textarea, select"));

    if (event.altKey && event.key.startsWith("Arrow")) {
      event.preventDefault();
      const step = event.shiftKey ? 32 : 8;
      this.store.nudge(
        current,
        event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0,
        event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0,
      );
      return;
    }

    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        const step = event.key === "ArrowDown" ? 1 : -1;
        this.select(ids[Math.min(ids.length - 1, Math.max(0, index + step))], { focus: true });
        break;
      }
      case "ArrowRight": {
        const node = this.store.node(current);
        if (node && node.kind === "group" && node.children.length) {
          event.preventDefault();
          this.select(node.children[0], { focus: true });
        }
        break;
      }
      case "ArrowLeft": {
        const node = this.store.node(current);
        if (node && node.parent) {
          event.preventDefault();
          this.select(node.parent, { focus: true });
        } else if (current === "alert" || current === "universe") {
          event.preventDefault();
          this.select(this.store.plan.rootId, { focus: true });
        }
        break;
      }
      case "Home":
        event.preventDefault();
        this.select(ids[0], { focus: true });
        break;
      case "End":
        event.preventDefault();
        this.select(ids[ids.length - 1], { focus: true });
        break;
      case "Enter":
        if (event.target.closest("button")) return;
        event.preventDefault();
        this.select(current);
        if (this.handlers.onOpenSettings) this.handlers.onOpenSettings(current);
        break;
      case "Delete":
      case "Backspace":
        if (inField || event.target.closest("button")) return;
        event.preventDefault();
        if (this.handlers.onRemove) this.handlers.onRemove(current);
        break;
      default:
        break;
    }
  }
}

function act(name, glyph, label, danger = false) {
  return `<button class="m-node-act" type="button" data-act="${name}" tabindex="-1"${danger ? ' data-danger="true"' : ""}>${icon(glyph, "icon-sm")}<span class="sr-only">${escapeHtml(label)}</span></button>`;
}

/** A smooth left-to-right curve, the way the rule reads. */
function curve(x1, y1, x2, y2) {
  const reach = Math.max(48, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + reach} ${y1}, ${x2 - reach} ${y2}, ${x2} ${y2}`;
}
