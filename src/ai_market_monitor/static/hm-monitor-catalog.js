/* What this platform can watch for, as the server describes it.
 *
 * One reader for the Builder contract, shared by the board, the condition library and
 * the settings panel. Nothing here decides what a mechanic *is*: the label, the
 * explanation, the options, the limits and whether it can run at all are all the
 * server's answers, fetched from the same endpoint the guided Builder reads.
 *
 * What this file owns is *presentation only*: a plain English name and an icon for
 * each category, and the search that ranks the list. An unknown category is never
 * dropped — it is tidied into words and shown, so a mechanic can never disappear
 * because this map had not heard of its group.
 */

/** Plain name and icon per category. Presentation for the raw category strings. */
const CATEGORY_LOOK = Object.freeze({
  "Price move": { name: "Price moves", icon: "trend_up" },
  "Price level": { name: "Price levels", icon: "scale" },
  "Price pattern": { name: "Price patterns", icon: "layers" },
  price: { name: "Price", icon: "chart" },
  price_action: { name: "Price action", icon: "chart" },
  candle_pattern: { name: "Candle shapes", icon: "rows" },
  trend: { name: "Trend", icon: "trend_up" },
  momentum: { name: "Momentum", icon: "activity" },
  volatility: { name: "Volatility", icon: "activity" },
  volatility_squeeze: { name: "Volatility", icon: "activity" },
  volume_flow: { name: "Volume", icon: "database" },
  volume_liquidity: { name: "Volume", icon: "database" },
  order_book_liquidity: { name: "Order book", icon: "list" },
  liquidity_smart_money: { name: "Liquidity levels", icon: "layers" },
  market_structure: { name: "Market structure", icon: "workflow" },
  market_context: { name: "Wider market", icon: "globe" },
  market_filter: { name: "Market filter", icon: "filter" },
  relative_strength: { name: "Strength against others", icon: "scale" },
  ranking_universe: { name: "Ranking coins", icon: "sort" },
  news_events: { name: "News and events", icon: "flag" },
  time_session: { name: "Time of day", icon: "clock" },
  session_time: { name: "Time of day", icon: "clock" },
  risk_trade_quality: { name: "Trade quality", icon: "shield" },
  risk: { name: "Trade quality", icon: "shield" },
  alert_behavior: { name: "How alerts behave", icon: "bell" },
  setup_lifecycle: { name: "Opportunity journey", icon: "history" },
  advanced: { name: "Advanced", icon: "sliders" },
  advanced_logic: { name: "Advanced", icon: "sliders" },
});

/** A category nobody mapped still gets readable words rather than a raw key. */
export function categoryLook(category) {
  const known = CATEGORY_LOOK[category];
  if (known) return known;
  const words = String(category || "other").replace(/[_-]+/g, " ").trim();
  return {
    name: words.charAt(0).toUpperCase() + words.slice(1),
    icon: "layers",
  };
}

/** Read the contract. Throws on anything that is not a usable answer. */
export async function loadCatalog(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`contract ${response.status}`);
  const payload = await response.json();
  if (!payload || !Array.isArray(payload.mechanics)) throw new Error("contract shape");
  return index(payload);
}

function index(payload) {
  const mechanics = payload.mechanics.map((item) => ({
    ...item,
    parameters: Array.isArray(item.parameters) ? item.parameters : [],
    operators: Array.isArray(item.operators) ? item.operators : [],
    directions: Array.isArray(item.directions) ? item.directions : [],
    timeframes: Array.isArray(item.timeframes) ? item.timeframes : [],
    examples: Array.isArray(item.examples) ? item.examples : [],
    haystack: [
      item.label,
      item.explanation,
      categoryLook(item.category).name,
      String(item.key || "").replace(/[:_]+/g, " "),
      ...(Array.isArray(item.examples) ? item.examples : []),
      // The trader's own words for this rule, sent by the server. They are the same
      // list the interpreter reads a written sentence against, so a person who would
      // have typed "bb squeeze" to the assistant finds the rule by typing it here.
      ...(Array.isArray(item.search_words) ? item.search_words : []),
    ]
      .join(" ")
      .toLowerCase(),
  }));

  const byKey = new Map(mechanics.map((item) => [item.key, item]));

  const categories = [];
  const seen = new Map();
  for (const item of mechanics) {
    let bucket = seen.get(item.category);
    if (!bucket) {
      const look = categoryLook(item.category);
      bucket = { key: item.category, name: look.name, icon: look.icon, count: 0 };
      seen.set(item.category, bucket);
      categories.push(bucket);
    }
    bucket.count += 1;
  }
  categories.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

  return {
    mechanics,
    byKey,
    categories,
    starters: Array.isArray(payload.starters) ? payload.starters : [],
    universes: Array.isArray(payload.universes) ? payload.universes : [],
    logic: Array.isArray(payload.logic) ? payload.logic : [],
    limits: payload.boolean_limits || null,
  };
}

/**
 * Rank the list against what a person typed.
 *
 * A whole-word hit in the name beats a hit anywhere else, so typing "volume" puts
 * the volume rules first instead of every rule whose explanation mentions volume.
 * Availability is a tie-break, never a filter: a rule the platform cannot run today
 * still appears, with its reason, because hiding it would leave a person hunting for
 * something that was there yesterday.
 */
export function searchMechanics(catalog, { query = "", category = "" } = {}) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const pool = category
    ? catalog.mechanics.filter((item) => item.category === category)
    : catalog.mechanics;

  if (!terms.length) {
    // With nothing typed, the Builder's own primitives come first. They are the
    // mechanics that carry no capability behind them — "Candle moves by a percentage",
    // "Price reaches a price you choose" — and the server writes their labels as
    // sentences a beginner can read. Alphabetical order alone opened the list on
    // "Ascending triangle breakout", which is a correct answer to a question a
    // beginner has not asked yet. Nothing is hidden; only the order changes.
    return [...pool].sort(
      (a, b) =>
        Number(!a.capability_key) - Number(!b.capability_key) ||
        Number(b.available) - Number(a.available) ||
        Number(b.beginner_friendly) - Number(a.beginner_friendly) ||
        a.label.localeCompare(b.label),
    );
  }

  const scored = [];
  for (const item of pool) {
    const name = item.label.toLowerCase();
    let score = 0;
    let matchedAll = true;
    for (const term of terms) {
      if (name.startsWith(term)) score += 12;
      else if (new RegExp(`\\b${escapeTerm(term)}`).test(name)) score += 9;
      else if (name.includes(term)) score += 6;
      else if (item.haystack.includes(term)) score += 2;
      else matchedAll = false;
    }
    if (!matchedAll) continue;
    if (item.available) score += 3;
    if (item.beginner_friendly) score += 1;
    scored.push({ item, score });
  }
  scored.sort((a, b) => b.score - a.score || a.item.label.localeCompare(b.item.label));
  return scored.map((entry) => entry.item);
}

function escapeTerm(term) {
  return term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
