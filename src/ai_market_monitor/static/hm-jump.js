/* In-page jump links that mark where a person actually is.
 *
 * A row of anchors is easy. The part worth writing once is the other half: keeping
 * `aria-current` on the link for whichever section is really on screen, rather than on
 * whichever link was last pressed. Those two answers differ the moment somebody
 * scrolls, and a marker that lies about position is worse than no marker.
 *
 * Written once because two pages on this path need it and a third will. It is the same
 * `IntersectionObserver` either way, and two copies is two places for the same
 * off-by-one margin to be tuned differently.
 */

/**
 * Keep `aria-current="true"` on the link whose section is in view.
 *
 * `links` are anchors whose `href` is a same-page `#id`. A link pointing at nothing is
 * skipped rather than throwing, so a section removed from a template cannot break the
 * whole bar.
 *
 * Returns a function that stops watching.
 */
export function followSections(links, scope = document) {
  const rows = [...links]
    .map((link) => ({ link, section: scope.querySelector(link.getAttribute("href") || "") }))
    .filter((row) => row.section);
  if (!rows.length || !("IntersectionObserver" in window)) return () => {};

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

  const watcher = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) markOnly(entry.target);
      }
    },
    // The top margin clears the sticky bar itself, so a section is "here" once it is
    // below the bar rather than once it touches the top of the window.
    { rootMargin: "-96px 0px -60% 0px", threshold: 0 },
  );
  rows.forEach((row) => watcher.observe(row.section));
  return () => watcher.disconnect();
}
