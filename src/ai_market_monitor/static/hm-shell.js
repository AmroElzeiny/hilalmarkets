/* The dashboard shell's behaviour: the side menu and the topbar.
 *
 * **This file is the only owner of the side menu's state.** There were three before it:
 * one in `hilalmarkets.js` keyed on `hilalmarkets-sidebar-collapsed`, one in
 * `dashboard.js` keyed on `amm-sidebar-collapsed`, and a third set of rules in
 * `hilalmarkets.css`. All three wrote the same `sidebar-collapsed` class on `<body>`
 * from different stored values, so whichever ran last decided what a person saw, and
 * pressing the button changed one of the two keys while the other kept its old answer
 * for the next page load. That is why the menu appeared to forget its state.
 *
 * Every animation goes through `hm-motion.js`, which owns the durations, the easing and
 * the reduced-motion decision. Nothing here writes a duration of its own, and nothing
 * here checks `prefers-reduced-motion` for itself — `animate()` already places the
 * element at its final keyframe when a person has asked for less movement.
 */

import { DURATION, animate, prefersReducedMotion, settleIn } from "./hm-motion.js";

const STORAGE_KEY = "hilalmarkets-sidebar-collapsed";
const DESKTOP = window.matchMedia("(min-width: 901px)");

const body = document.body;
const nav = document.querySelector("[data-hm-shell-nav]");
const topbar = document.querySelector("[data-hm-shell-top]");

/** Run once the document has been parsed, whenever this module happens to execute. */
function ready(run) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run, { once: true });
    return;
  }
  run();
}

/* ── Minimize and expand ───────────────────────────────────────────────────── */

/**
 * Fold the menu down to its icons, or open it again.
 *
 * The stored answer is what a person last chose on a wide screen. It is deliberately not
 * applied below 901px, where the menu is a drawer instead and "minimized" has no meaning
 * — but it is also not overwritten there, so making the window narrow and wide again
 * gives back the menu they had.
 */
function wireMinimize() {
  const toggle = nav?.querySelector("[data-sidebar-collapse]");
  if (!toggle) return;

  const minimizeIcon = toggle.querySelector('[data-sidebar-collapse-icon="minimize"]');
  const expandIcon = toggle.querySelector('[data-sidebar-collapse-icon="expand"]');
  const label = toggle.querySelector(".hm-nav-toggle-label");
  const tip = toggle.querySelector(".hm-nav-tip");

  const apply = (collapsed) => {
    const showsCollapsed = collapsed && DESKTOP.matches;
    body.classList.toggle("sidebar-collapsed", showsCollapsed);
    toggle.setAttribute("aria-expanded", String(!showsCollapsed));
    const words = showsCollapsed ? "Open the menu" : "Minimize the menu";
    toggle.setAttribute("aria-label", words);
    toggle.setAttribute("title", words);
    if (label) label.textContent = showsCollapsed ? "Open menu" : "Minimize menu";
    if (tip) tip.textContent = words;
    if (minimizeIcon) minimizeIcon.hidden = showsCollapsed;
    if (expandIcon) expandIcon.hidden = !showsCollapsed;
  };

  const stored = () => window.localStorage.getItem(STORAGE_KEY) === "true";
  apply(stored());

  toggle.addEventListener("click", () => {
    const collapsed = !body.classList.contains("sidebar-collapsed");
    window.localStorage.setItem(STORAGE_KEY, String(collapsed));
    apply(collapsed);
    // The rows change width, so wherever the marker was parked is no longer that row.
    window.requestAnimationFrame(() => parkMarker());
  });

  DESKTOP.addEventListener?.("change", () => {
    apply(stored());
    window.requestAnimationFrame(() => parkMarker());
  });
}

/* ── The drawer, on a small screen ─────────────────────────────────────────── */

function wireDrawer() {
  const backdrop = document.querySelector("[data-sidebar-backdrop]");
  if (!nav) return;

  const close = () => {
    nav.classList.remove("is-open");
    backdrop?.classList.remove("is-open");
    body.classList.remove("no-scroll");
    document.querySelector("[data-open-sidebar]")?.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    nav.classList.add("is-open");
    backdrop?.classList.add("is-open");
    body.classList.add("no-scroll");
    document.querySelector("[data-open-sidebar]")?.setAttribute("aria-expanded", "true");
    nav.querySelector("[data-close-sidebar]")?.focus({ preventScroll: true });
  };

  document.querySelectorAll("[data-open-sidebar]").forEach((button) => {
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", "hm-side-menu");
    button.addEventListener("click", open);
  });
  document.querySelectorAll("[data-close-sidebar]").forEach((button) => {
    button.addEventListener("click", close);
  });
  backdrop?.addEventListener("click", close);
  nav.id = nav.id || "hm-side-menu";

  // Following a link on a small screen means the drawer has done its job.
  nav.addEventListener("click", (event) => {
    if (!DESKTOP.matches && event.target.closest("[data-hm-nav-link]")) close();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && nav.classList.contains("is-open")) {
      close();
      document.querySelector("[data-open-sidebar]")?.focus({ preventScroll: true });
    }
  });
}

/* ── The travelling marker ─────────────────────────────────────────────────── */

/* One surface for the whole menu. It follows the pointer or the keyboard from row to
   row, so the movement says "that row, now this one" instead of one rectangle blinking
   out while another blinks in somewhere else. When nothing is being pointed at it parks
   itself, invisible, on the current page — so the next movement starts from where a
   person's attention already is.

   `brand guide.md` §15: motion explains a relationship. This one explains "here". */

const navBody = nav?.querySelector("[data-hm-nav-body]");
const marker = nav?.querySelector("[data-hm-nav-marker]");

function markerTargets() {
  return navBody ? [...navBody.querySelectorAll("[data-hm-nav-link]")] : [];
}

function moveMarker(link, { show }) {
  if (!marker || !navBody || !link) return;
  const row = link.getBoundingClientRect();
  const host = navBody.getBoundingClientRect();
  const top = row.top - host.top + navBody.scrollTop;
  marker.style.height = `${Math.round(row.height)}px`;
  animate(
    marker,
    { transform: `translateY(${Math.round(top)}px)`, opacity: show ? 1 : 0 },
    { duration: show ? DURATION.quick : DURATION.instant },
  );
}

function parkMarker() {
  const active = navBody?.querySelector("[data-hm-nav-link].is-active") || markerTargets()[0];
  moveMarker(active, { show: false });
}

function wireMarker() {
  if (!marker || !navBody) return;
  const links = markerTargets();
  if (!links.length) return;

  // The rows paint their own hover surface without this. Saying so in a class keeps the
  // two from both being on at once.
  navBody.classList.add("has-marker");
  window.requestAnimationFrame(() => parkMarker());

  links.forEach((link) => {
    link.addEventListener("pointerenter", () => moveMarker(link, { show: !link.classList.contains("is-active") }));
    link.addEventListener("focus", () => moveMarker(link, { show: !link.classList.contains("is-active") }));
    link.addEventListener("blur", () => parkMarker());
  });
  navBody.addEventListener("pointerleave", () => parkMarker());
  window.addEventListener("resize", () => parkMarker());
}

/* ── Flyout names, for the minimized menu ──────────────────────────────────── */

/* Placed against the window rather than inside the list, because the list scrolls and
   anything positioned inside a scrolling box is clipped by it.
   The flyout is `aria-hidden`: the same words are already the accessible name of the
   control it belongs to, and a tooltip is never the only place a name exists. */

let openTip = null;

function tipFor(control) {
  const cell = control.closest(".hm-nav-cell");
  return cell ? cell.querySelector(".hm-nav-tip") : control.querySelector(".hm-nav-tip");
}

function hideTip() {
  if (!openTip) return;
  const tip = openTip;
  openTip = null;
  tip.style.opacity = "0";
  tip.classList.remove("is-shown");
}

/* Keep the flyout inside the window.
 *
 * It is `position: fixed`, so a number written into `left` is a number on the screen:
 * nothing clips it and nothing scrolls it back. The gap is measured from the edge of the
 * window rather than assumed, because the menu is 84px wide on one machine and a drawer
 * on another. */
const TIP_GAP = 10;
const TIP_EDGE = 8;

function placeTip(tip, anchor, size, rtl) {
  const top = Math.min(
    Math.max(TIP_EDGE, anchor.top + anchor.height / 2 - size.height / 2),
    Math.max(TIP_EDGE, window.innerHeight - size.height - TIP_EDGE),
  );
  tip.style.top = `${Math.round(top)}px`;
  if (rtl) {
    const right = Math.min(
      Math.max(TIP_EDGE, window.innerWidth - anchor.left + TIP_GAP),
      Math.max(TIP_EDGE, window.innerWidth - size.width - TIP_EDGE),
    );
    tip.style.right = `${Math.round(right)}px`;
    tip.style.left = "auto";
    return;
  }
  const left = Math.min(
    Math.max(TIP_EDGE, anchor.right + TIP_GAP),
    Math.max(TIP_EDGE, window.innerWidth - size.width - TIP_EDGE),
  );
  tip.style.left = `${Math.round(left)}px`;
  tip.style.right = "auto";
}

function showTip(control) {
  if (!DESKTOP.matches || !body.classList.contains("sidebar-collapsed")) return;
  const tip = tipFor(control);
  if (!tip) return;
  hideTip();
  openTip = tip;
  tip.classList.add("is-shown");
  const rtl = document.documentElement.dir === "rtl";
  placeTip(tip, control.getBoundingClientRect(), tip.getBoundingClientRect(), rtl);
  /* A fade and a short slide out of the menu, and nothing else.
   *
   * It used to turn as well — `rotateY` with `transformPerspective: 700`. Both were
   * written as *keyframes*, and a keyframe is a value to travel to. The element had no
   * perspective to start from, so the vendored bundle travelled from none, and for the
   * first frames of every flyout the perspective was a handful of pixels instead of
   * seven hundred. A perspective that small magnifies whatever leans towards the viewer
   * enormously: the name shot out sideways across the page and then shrank back into
   * place as the number climbed. Measured in Chromium at `perspective(134px)` a fifth of
   * the way in, and smaller still before that.
   *
   * The turn is gone rather than corrected. `brand guide.md` §15 asks that movement
   * explain a relationship; the slide already says "this name belongs to that row", and
   * the turn only said that somebody knew how to write one. Nothing else in the product
   * animates in three dimensions, and `test_no_animation_in_this_product_is_three
   * _dimensional` keeps it that way. */
  animate(tip, { opacity: [0, 1], x: [rtl ? 8 : -8, 0] }, { duration: DURATION.quick });
}

function wireTips() {
  if (!nav) return;
  const controls = [
    ...nav.querySelectorAll("[data-hm-nav-link]"),
    ...nav.querySelectorAll("[data-sidebar-collapse]"),
    ...nav.querySelectorAll("[data-hm-nav-user]"),
  ];
  controls.forEach((control) => {
    control.addEventListener("pointerenter", () => showTip(control));
    control.addEventListener("pointerleave", hideTip);
    control.addEventListener("focus", () => showTip(control));
    control.addEventListener("blur", hideTip);
  });
  window.addEventListener("scroll", hideTip, { passive: true });
  window.addEventListener("resize", hideTip);
}

/* ── The account menu ──────────────────────────────────────────────────────── */

/* It exists because the minimized menu had no way out of the product: the sign-out form
   was one of the things the old `display: none` removed, so a person who minimized the
   menu could not sign out until they opened it again. */

function wireAccount() {
  const trigger = nav?.querySelector("[data-hm-nav-user]");
  const menu = nav?.querySelector("[data-hm-nav-account]");
  if (!trigger || !menu) return;

  const close = ({ restore = false } = {}) => {
    if (menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restore) trigger.focus({ preventScroll: true });
  };
  const open = () => {
    hideTip();
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    animate(menu, { opacity: [0, 1], y: [6, 0] }, { duration: DURATION.quick });
    menu.querySelector("[role='menuitem']")?.focus({ preventScroll: true });
  };

  trigger.addEventListener("click", () => (menu.hidden ? open() : close()));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close({ restore: true });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !trigger.contains(event.target)) close();
  });
  menu.addEventListener("focusout", (event) => {
    if (!menu.contains(event.relatedTarget) && event.relatedTarget !== trigger) close();
  });
}

/* ── The topbar ────────────────────────────────────────────────────────────── */

/* The shortcut is written into the bar rather than left in a help page, and it is
   written in the words of the machine a person is actually using. */
function wireSearch() {
  const field = topbar?.querySelector("[data-hm-top-search-input]");
  const key = topbar?.querySelector("[data-hm-top-search-key]");
  if (!field) return;

  const apple = /mac|iphone|ipad|ipod/i.test(navigator.userAgent);
  if (key) key.textContent = apple ? "⌘ K" : "Ctrl K";

  document.addEventListener("keydown", (event) => {
    const wanted = apple ? event.metaKey : event.ctrlKey;
    if (wanted && (event.key === "k" || event.key === "K")) {
      event.preventDefault();
      field.focus({ preventScroll: true });
      field.select();
      return;
    }
    if (event.key === "Escape" && document.activeElement === field) field.blur();
  });
}

/* ── Arrival ───────────────────────────────────────────────────────────────── */

/* The rows settle in one after the next, once, on arrival. It says the menu has
   finished loading. It is not a loop and it never runs again. */
function wireArrival() {
  if (!navBody || prefersReducedMotion()) return;
  settleIn(navBody.querySelectorAll("[data-hm-nav-link]"), { delayStep: 0.022, from: 6 });
}

ready(() => {
  wireMinimize();
  wireDrawer();
  wireMarker();
  wireTips();
  wireAccount();
  wireSearch();
  wireArrival();
  body.classList.add("hm-shell-ready");
});
