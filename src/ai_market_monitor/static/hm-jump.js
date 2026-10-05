/* In-page jump links that mark where a person actually is.
 *
 * A row of anchors is easy. The part worth writing once is the other half: keeping
 * `aria-current` on the link for whichever section is really on screen, rather than on
 * whichever link was last pressed. Those two answers differ the moment somebody
 * scrolls, and a marker that lies about position is worse than no marker.
 *
 * Written once because two pages on this path need it and a third will. It is the same
 * `IntersectionObserver` either way, and two copies is two places for the same
 * off-by-one margin to be tuned differently. That had happened: the Passport page kept
 * its own copy with an 88px margin while this one used 96px, and neither number was
 * the real height of anything once the bar stopped under the topbar.
 */

/**
 * Keep `aria-current="true"` on the link whose section is in view.
 *
 * `links` are anchors whose `href` is a same-page `#id`. A link pointing at nothing is
 * skipped rather than throwing, so a section removed from a template cannot break the
 * whole bar.
 *
 * `bar` is the sticky element the links sit in. When it is given, the band that counts
 * as "here" starts under the bar's real bottom edge — where it is held plus how tall it
 * is — and that same number is written to `--hm-jump-clear`, which the sections' own
 * `scroll-margin-top` reads, so a pressed link lands its section just under the bar
 * instead of underneath it. Both are measured again whenever the bar changes size or
 * the place it is held moves.
 *
 * Returns a function that stops watching.
 */
export function followSections(links, scope = document, { bar = null } = {}) {
  const rows = [...links]
    .map((link) => ({ link, section: scope.querySelector(link.getAttribute("href") || "") }))
    .filter((row) => row.section);
  if (!rows.length || !("IntersectionObserver" in window)) return () => {};
  const host = scope === document ? document.body : scope;

  /** Mark exactly one link, and no others. */
  function markOnly(section) {
    for (const row of rows) {
      if (row.section === section) row.link.setAttribute("aria-current", "true");
      else row.link.removeAttribute("aria-current");
    }
  }

  /* Marked before anything is observed.
   *
   * The watching band below starts under the sticky bar and ends part-way down the
   * window, so on a page whose first section begins below that band nothing intersects
   * until the person scrolls — and the bar sits there marking nothing at all. The first
   * section is where somebody is when the page opens, so that is what it says. A page
   * opened at an anchor says that section instead. */
  const landed = window.location.hash
    ? rows.find((row) => `#${row.section.id}` === window.location.hash)
    : null;
  markOnly((landed || rows[0]).section);

  /** How far down the window the bar's bottom edge sits once it is held. */
  function clearance() {
    if (!bar) return 96;
    const heldAt = Number.parseFloat(window.getComputedStyle(bar).top) || 0;
    return Math.round(heldAt + bar.offsetHeight + 12);
  }

  let watcher = null;
  function watch() {
    watcher?.disconnect();
    const clear = clearance();
    if (bar) host.style.setProperty("--hm-jump-clear", `${clear}px`);
    watcher = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) markOnly(entry.target);
        }
      },
      // The top margin clears the sticky bar itself, so a section is "here" once it is
      // below the bar rather than once it touches the top of the window.
      { rootMargin: `-${clear}px 0px -60% 0px`, threshold: 0 },
    );
    rows.forEach((row) => watcher.observe(row.section));
  }
  watch();

  let pending = 0;
  const again = () => {
    window.cancelAnimationFrame(pending);
    pending = window.requestAnimationFrame(watch);
  };
  const resized = bar && "ResizeObserver" in window ? new ResizeObserver(again) : null;
  resized?.observe(bar);
  window.addEventListener("resize", again);
  window.addEventListener(STICKY_TOP_MOVED, again);
  return () => {
    watcher?.disconnect();
    resized?.disconnect();
    window.removeEventListener("resize", again);
    window.removeEventListener(STICKY_TOP_MOVED, again);
  };
}

/** Sent when `holdBelowFixedHeader` moves the place sticky bars stop. */
const STICKY_TOP_MOVED = "hm:sticky-top";

/**
 * On a public page, stop sticky bars just under the website's fixed header.
 *
 * The header is drawn by the landing bundle, which runs after the page's own scripts,
 * and it changes height when the page scrolls (it tightens once you leave the top). So
 * it is waited for, then measured, and measured again every time it changes size. The
 * result goes to `--hm-sticky-top` on the body — the same value the dashboard's topbar
 * sets in `hm-shell.css` — so a bar written once stops in the right place on both.
 */
export function holdBelowFixedHeader(selector = ".hm-header") {
  const measure = (header) => {
    const bottom = Math.round(header.getBoundingClientRect().bottom);
    if (bottom <= 0) return;
    document.body.style.setProperty("--hm-sticky-top", `${bottom + 8}px`);
    window.dispatchEvent(new Event(STICKY_TOP_MOVED));
  };
  const follow = (header) => {
    measure(header);
    // The outer box: the header shrinks by its padding when the page scrolls, which
    // leaves its inner box exactly the same size.
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => measure(header)).observe(header, { box: "border-box" });
    }
  };
  const existing = document.querySelector(selector);
  if (existing) {
    follow(existing);
    return;
  }
  if (!("MutationObserver" in window)) return;
  const waiting = new MutationObserver(() => {
    const header = document.querySelector(selector);
    if (!header) return;
    waiting.disconnect();
    follow(header);
  });
  waiting.observe(document.body, { childList: true, subtree: true });
}
