/* One reader for what a dashboard page says about itself, shared by every page.
 *
 * Each page publishes its own words through `publish(name, describe)` in
 * `hm-page-context.js`. Without this module every page would write its own way of
 * reading a heading and a list of parts — the duplicate-parser failure this codebase
 * keeps paying for, ending in ten pages disagreeing about what a heading is.
 *
 * What it reads is generic and visible: the first heading, the names the page gave
 * its own parts with `data-hm-part`, and whatever page-specific lines the calling
 * page module passes in from data already on its screen. It invents no words: every
 * string here comes from the page. Length caps are applied by `snapshot()`, never
 * here.
 */

function clean(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

/** The page's own heading, as printed. */
export function pageHeading() {
  const heading = document.querySelector(".hm-t h1, main h1, h1");
  return clean(heading && heading.textContent) || null;
}

/** The names the page gave its own parts. */
export function pageParts() {
  const names = [];
  for (const part of document.querySelectorAll("[data-hm-part]")) {
    const name = clean(part.dataset.hmPart);
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

/** One page's own words: its heading, what it shows, and the names of its parts.
 *
 *  `summary` and `points` are the calling page's own lines, read from data already
 *  on its screen. Anything missing is simply left out — a page that cannot describe
 *  itself is told less about, never guessed about.
 */
export function pageNote({ summary = null, points = [] } = {}) {
  const note = {};
  const heading = pageHeading();
  if (heading) note.heading = heading;
  const line = clean(summary);
  if (line) note.summary = line;
  const extra = (Array.isArray(points) ? points : []).map(clean).filter(Boolean);
  const parts = [...extra];
  for (const name of pageParts()) {
    if (!parts.includes(name)) parts.push(name);
  }
  if (parts.length) note.points = parts;
  return Object.keys(note).length ? note : null;
}

/** How many rows a list on the page holds, in the page's own words. */
export function countLine(selector, singular, plural) {
  const found = document.querySelectorAll(selector).length;
  return `${found} ${found === 1 ? singular : plural}`;
}
