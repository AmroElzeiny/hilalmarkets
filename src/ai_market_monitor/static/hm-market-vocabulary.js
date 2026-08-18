/* The shared vocabulary of the redesigned dashboard pages.
 *
 * The market grid, the Favorites popup, the Passport quick view and the Passport page
 * all have to answer the same questions: what tone is this status, what icon says so,
 * how is this number written. This module owns those answers.
 *
 * That is deliberate. The recurring fault in this codebase is two modules each
 * deciding what a word means and each understanding a different subset — the live
 * market page and the quick view already carry two separate status-to-badge tables
 * that agree only by luck. One table here, imported by every caller.
 */

/** Statuses that mean "this passed the review", by their stored value. */
export const ELIGIBLE_STATUSES = Object.freeze(["eligible", "eligible_with_qualifications"]);
/** Statuses that mean "a human still has to finish looking at this". */
export const REVIEW_STATUSES = Object.freeze(["under_review", "disputed", "pending_review"]);
/** Statuses that mean "this did not pass". */
export const EXCLUDED_STATUSES = Object.freeze(["excluded", "not_eligible"]);

export function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * The tone for an asset-level Shariah status.
 *
 * Anything unrecognised is "neutral" on purpose. An unknown status must never be
 * shown in the colour that means "passed" — a wrong guess here would present an
 * unreviewed coin as approved.
 */
export function assetTone(status) {
  const value = String(status || "").toLowerCase();
  if (ELIGIBLE_STATUSES.includes(value)) return "eligible";
  if (REVIEW_STATUSES.includes(value)) return "review";
  if (EXCLUDED_STATUSES.includes(value)) return "excluded";
  return "neutral";
}

/**
 * Does this status carry a reviewer condition the trader has to read?
 *
 * A separate question from the tone. "Passed" and "passed, with a condition attached"
 * are both eligible and are both green, but only one of them asks something of the
 * reader, so the market page can count and filter on it.
 */
export function carriesCondition(status) {
  return String(status || "").toLowerCase() === "eligible_with_qualifications";
}

/** The tone for one use-case coverage decision inside a Passport. */
export function coverageTone(status) {
  const value = String(status || "").toLowerCase();
  if (value === "covered") return "eligible";
  if (["qualified", "covered_with_qualification"].includes(value)) return "review";
  if (["excluded", "not_covered", "not_covered_by_this_decision"].includes(value)) return "excluded";
  return "neutral";
}

/** The tone for one methodology criterion outcome. */
export function criterionTone(outcome) {
  const value = String(outcome || "").toLowerCase();
  if (value === "pass") return "eligible";
  if (value === "fail") return "excluded";
  if (value === "qualification") return "review";
  return "neutral";
}

/** Each tone's icon, so colour is never the only signal. */
export function toneIcon(tone) {
  return {
    eligible: "shield_check",
    review: "clock",
    excluded: "close",
    neutral: "info",
  }[tone] || "info";
}

/**
 * The words shown on a status chip.
 *
 * The stored label is authoritative. "Eligible" is rewritten to "Halal" only because
 * that is the word this product uses with its own customers for the same stored
 * status — the status itself is never reinterpreted here.
 */
export function statusText(item) {
  const stored = String(item?.status_label || "").trim();
  if (stored) return stored.replace(/^eligible\b/i, "Halal");
  const tone = assetTone(item?.status);
  return {
    eligible: "Halal",
    review: "Being reviewed",
    excluded: "Not eligible",
    neutral: "Not enough information",
  }[tone];
}

/** A short plain sentence saying what the status means for the reader. */
export function statusMeaning(item) {
  return {
    eligible: "This coin passed the review under the standard shown, on the date shown.",
    review: "A reviewer is still checking this coin. No result has been published yet.",
    excluded: "This coin did not pass the review under the standard shown.",
    neutral: "There is not enough checked evidence to publish a result for this coin.",
  }[assetTone(item?.status)];
}

/**
 * A price, written with as many decimals as the size of the number needs.
 *
 * Missing stays missing: an em dash, never a zero. A zero would read as a real price.
 */
export function formatPrice(value, quote) {
  if (!isFiniteNumber(value)) return "--";
  const size = Math.abs(value);
  const maximumFractionDigits = size >= 1000 ? 2 : size >= 1 ? 4 : size >= 0.01 ? 6 : 10;
  const text = new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
  return quote ? `${text} ${String(quote).toUpperCase()}` : text;
}

export function formatCompact(value) {
  if (!isFiniteNumber(value)) return "--";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

export function formatChange(value) {
  if (!isFiniteNumber(value)) return "--";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** A date a person can read, in their own locale, or an honest "not recorded". */
export function formatDate(value, { withTime = false } = {}) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return date.toLocaleDateString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

/**
 * Put a coin's logo into a container.
 *
 * Which pictures exist for a coin is answered by `core/asset_logos.py` and arrives on
 * the record. This used to build the catalog address here instead whenever the record
 * carried no `logo_module_url` — a fourth hand-typed copy of the catalog version, and a
 * second opinion about which coins have a picture. A record that names no address now
 * simply keeps its letters, which is what the server said it should have.
 *
 * The letters go in first and stay until a real picture has loaded, so a coin never
 * flickers through an empty container on its way to a fallback.
 */
export function loadCoinLogo(container, symbol, identity = {}) {
  if (!container) return;
  const ticker = String(symbol || "").toUpperCase();
  container.textContent = ticker.slice(0, 3);
  delete container.dataset.assetLogoLoaded;
  window.HilalAssetLogos?.load(container, identity.logo_module_url, ticker, identity.logo_url);
}

/** Turn a stored key such as `token_utility` into "Token utility". */
export function humanize(value) {
  const text = String(value || "").replace(/[_-]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1).toLowerCase() : "";
}
