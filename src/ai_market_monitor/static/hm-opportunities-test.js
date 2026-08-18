/* The Opportunities page at /dashboard/opportunities.
 *
 * Nothing here decides what anything means. Every word a person reads was written by
 * the server — including the words inside the popups, which are rendered into a
 * <template> on the card and copied out when asked for. That is deliberate: a popup
 * that built its own sentences from numbers would be a second opinion about the same
 * evidence, free to disagree with the card behind it.
 *
 * The one thing this file fetches is the price picture, and only when somebody asks
 * for it.
 */

import { createCardFilter } from "./hm-card-filter.js";
import { manageDialog, paintIcons } from "./hm-dialog.js";
import { prefersReducedMotion, whenSeen } from "./hm-motion.js";

const root = document.querySelector("[data-opportunities-root]");
if (root) start(root);

/** Where the drawing tool lives. Vendored, so nothing is fetched from another site. */
const CHART_LIBRARY = "/static/vendor/lightweight-charts.standalone.production.js";

/** Where this application keeps the prices behind one opportunity. */
const CHART_DATA = "/api/v1/dashboard/lifecycles";

function start(scope) {
  const find = (selector) => scope.querySelector(selector);

  /* ── Showing the right cards ─────────────────────────────────────────────── */

  const filter = createCardFilter({
    cards: scope.querySelectorAll("[data-o-card]"),
    buttons: [...scope.querySelectorAll("[data-o-filter]")],
    bucketOfButton: (button) => button.dataset.oFilter,
    bucketsOf: (card) => [card.dataset.kind],
    grid: find("[data-o-grid]"),
    nothing: find("[data-o-nothing]"),
    said: find("[data-o-said]"),
    search: find("[data-o-search]"),
    reset: find("[data-o-reset]"),
    countWords: (shown) => `${shown} ${shown === 1 ? "coin" : "coins"} shown.`,
    emptyWords: "Nothing here matches that.",
  });

  /* Choosing which list to look at is a plain form with a plain button, and there is
     deliberately nothing here for it. Sending the page away the moment a name is
     highlighted would move somebody who was only reading the choices with the arrow
     keys — WCAG 2.2 SC 3.2.2, and a real surprise for anybody using a keyboard. */

  /* ── How much of the list is true ────────────────────────────────────────── */

  /* Each bar fills from nothing to its real count, once, as it comes into view. The
     movement *is* the number, so a person sees "nearly all of it" before reading it —
     and the same count is written in words directly above, so the bar is never the
     only place it appears. */
  for (const fill of scope.querySelectorAll("[data-o-fill]")) {
    const percent = Math.max(0, Math.min(100, Number(fill.dataset.percent) || 0));
    if (prefersReducedMotion()) {
      fill.style.width = `${percent}%`;
      continue;
    }
    whenSeen(fill.closest("[data-o-card]"), () => {
      window.requestAnimationFrame(() => { fill.style.width = `${percent}%`; });
    });
  }

  /* ── What did we see? ────────────────────────────────────────────────────── */

  /** Put a card's own server-rendered markup inside a popup, and draw its icons. */
  function showInside(body, template) {
    body.replaceChildren();
    if (!template) return false;
    body.append(template.content.cloneNode(true));
    paintIcons(body);
    return true;
  }

  const sawDialog = document.querySelector("[data-o-saw-dialog]");
  const saw = manageDialog(sawDialog, {
    closers: ["[data-o-saw-close]", "[data-o-saw-cancel]"],
  });
  const sawBody = sawDialog.querySelector("[data-o-saw-body]");
  const sawTitle = sawDialog.querySelector("[data-o-saw-title]");

  for (const button of scope.querySelectorAll("[data-o-saw]")) {
    button.addEventListener("click", () => {
      const card = button.closest("[data-o-card]");
      sawTitle.textContent = `What did we see for ${button.dataset.symbol}?`;
      showInside(sawBody, card.querySelector("[data-o-evidence]"));
      saw.open(button);
    });
  }

  /* ── Why was I not told? ─────────────────────────────────────────────────── */

  const whyDialog = document.querySelector("[data-o-why-dialog]");
  const why = manageDialog(whyDialog, {
    closers: ["[data-o-why-close]", "[data-o-why-cancel]"],
  });
  const whyBody = whyDialog.querySelector("[data-o-why-body]");
  const whyTitle = whyDialog.querySelector("[data-o-why-title]");

  for (const button of scope.querySelectorAll("[data-o-why]")) {
    button.addEventListener("click", () => {
      const card = button.closest("[data-o-card]");
      whyTitle.textContent = `Why was I not told about ${button.dataset.symbol}?`;
      showInside(whyBody, card.querySelector("[data-o-why-answer]"));
      why.open(button);
    });
  }

  /* ── Is this coin halal? ─────────────────────────────────────────────────── */

  /* The path's own Passport popup, which every page here shares. It is asked to open
     rather than linked to: it says "there is no published record for this one" in
     words, where a link would have landed on a "not found" page. The link to the full
     Passport lives inside it, once there is a Passport to link to. */
  for (const button of scope.querySelectorAll("[data-o-passport]")) {
    button.addEventListener("click", () => {
      window.HilalPassportQuickView?.open(
        {
          asset: button.dataset.coin,
          methodologyId: button.dataset.standard || null,
          basePath: "/dashboard/market",
        },
        button,
      );
    });
  }

  /* ── The price picture ───────────────────────────────────────────────────── */

  const chartDialog = document.querySelector("[data-o-chart-dialog]");
  const chart = manageDialog(chartDialog, {
    closers: ["[data-o-chart-close]", "[data-o-chart-cancel]"],
  });
  const chartStage = chartDialog.querySelector("[data-o-chart-stage]");
  const chartNote = chartDialog.querySelector("[data-o-chart-note]");
  const chartTitle = chartDialog.querySelector("[data-o-chart-title]");
  let drawing = null;

  /** Fetch the drawing tool once, the first time somebody asks for a picture. */
  function drawingTool() {
    if (window.LightweightCharts?.createChart) return Promise.resolve(true);
    if (drawing) return drawing;
    drawing = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = CHART_LIBRARY;
      script.addEventListener(
        "load",
        () => resolve(Boolean(window.LightweightCharts?.createChart)),
        { once: true },
      );
      script.addEventListener("error", () => resolve(false), { once: true });
      document.head.appendChild(script);
    });
    return drawing;
  }

  /* The drawing tool paints to a canvas, so it needs real colour values rather than
     CSS names. They are read off the page's own tokens instead of being typed in here,
     which would be a second copy of the palette free to drift from the first. */
  function palette() {
    const styles = window.getComputedStyle(chartStage);
    const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    return {
      surface: token("--t-surface", "#ffffff"),
      copy: token("--t-copy", "#50555e"),
      line: token("--t-line", "#e6e8ec"),
      rise: token("--t-rise", "#55712a"),
      fall: token("--t-fall", "#8d3029"),
    };
  }

  async function drawPicture(setupId, timeframe) {
    chartStage.replaceChildren();
    chartNote.textContent = "Getting the picture ready…";

    let candles = [];
    try {
      const where = new URL(
        `${CHART_DATA}/${encodeURIComponent(setupId)}/chart`,
        window.location.origin,
      );
      // The same size of candle the list itself looks at. A one-minute picture beside a
      // fifteen-minute rule would be a picture of something else.
      if (timeframe) where.searchParams.set("timeframe", timeframe);
      const response = await fetch(where, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("no picture");
      const payload = await response.json();
      candles = Array.isArray(payload.candles) ? payload.candles : [];
    } catch {
      // A picture we cannot draw is said in words, never left as an empty grey box.
      chartNote.textContent =
        "We could not get a price picture for this one. Nothing about your list changed — what we checked is in “What did we see?”.";
      return;
    }
    if (!candles.length) {
      chartNote.textContent =
        "There is no price picture for this one yet. What we checked is in “What did we see?”.";
      return;
    }
    if (!(await drawingTool())) {
      chartNote.textContent =
        "The drawing tool did not load. What we checked is in “What did we see?”.";
      return;
    }

    /* Only the prices are drawn. Nothing marks where a check passed, on purpose: this
       popup says it is a picture of the market and not our evidence, and drawing our
       findings onto it would blur the line it exists to draw. What we checked has its
       own popup, in words. */
    const colors = palette();
    const board = window.LightweightCharts.createChart(chartStage, {
      autoSize: true,
      layout: {
        background: { color: colors.surface },
        textColor: colors.copy,
        fontFamily: "Onest, Arial, sans-serif",
      },
      grid: { vertLines: { color: colors.line }, horzLines: { color: colors.line } },
      rightPriceScale: { borderColor: colors.line },
      timeScale: { borderColor: colors.line },
    });
    const series = board.addCandlestickSeries({
      upColor: colors.rise,
      downColor: colors.fall,
      borderUpColor: colors.rise,
      borderDownColor: colors.fall,
      wickUpColor: colors.rise,
      wickDownColor: colors.fall,
    });
    series.setData(
      candles.map((candle) => ({
        time: Math.floor(new Date(candle.timestamp).getTime() / 1000),
        open: Number(candle.open),
        high: Number(candle.high),
        low: Number(candle.low),
        close: Number(candle.close),
      })),
    );
    board.timeScale().fitContent();
    chartNote.textContent =
      "Drawn by an outside company's tool from the market's own prices. It is not our evidence.";
  }

  for (const button of scope.querySelectorAll("[data-o-chart]")) {
    button.addEventListener("click", () => {
      chartTitle.textContent = `${button.dataset.symbol} price picture`;
      chart.open(button);
      drawPicture(button.dataset.oChart, button.dataset.timeframe);
    });
  }

  /* Whatever is on screen is thrown away when the popup closes, so a second coin can
     never be drawn on top of the first one's picture. */
  chartDialog.addEventListener("close", () => {
    chartStage.replaceChildren();
  });

  /* ── Arriving ────────────────────────────────────────────────────────────── */

  filter.settle();
}
