/* The the redesigned dashboard screened market.
 *
 * Reads the same endpoint the live page reads. Every value on screen comes from that
 * response or from the server-rendered page: nothing here estimates, rounds a missing
 * number into existence, or lets a price change a Shariah status.
 */

import { animate, countTo, reveal, settleIn } from "./hm-motion.js";
import {
  assetTone,
  carriesCondition,
  formatChange,
  formatCompact,
  formatPrice,
  isFiniteNumber,
  loadCoinLogo,
  statusText,
  toneIcon,
} from "./hm-market-vocabulary.js";

const root = document.querySelector("[data-market-root]");
if (root) start(root);

function start(root) {
  const endpoint = root.dataset.endpoint;
  const basePath = root.dataset.basePath || "/dashboard/market";
  const methodologyId = root.dataset.methodologyId || "";

  const find = (selector) => root.querySelector(selector);
  const cards = find("[data-cards]");
  const tableWrap = find("[data-table-wrap]");
  const tableBody = find("[data-table-body]");
  const skeletons = find("[data-skeletons]");
  const searchInput = find("[data-search]");
  const resultNote = find("[data-result-note]");
  const emptyState = find("[data-empty]");
  const emptyMessage = find("[data-empty-message]");
  const errorBox = find("[data-quote-error]");
  const errorMessage = find("[data-quote-error-message]");
  const livePill = find("[data-live-pill]");
  const liveLabel = find("[data-live-label]");
  const liveDetail = find("[data-live-detail]");
  const liveToggle = find("[data-live-toggle]");
  const liveToggleLabel = find("[data-live-toggle-label]");
  const standardSelect = find("[data-standard]");
  const standardForm = find("[data-standard-form]");

  const favorites = new Set(
    JSON.parse(root.dataset.favoriteAssets || "[]").map((value) => String(value).toUpperCase()),
  );
  let favoriteWatchlistId = root.dataset.favoriteWatchlistId || "";

  /** The last snapshot, keyed by exchange symbol. The only source for what is drawn. */
  const items = new Map();
  /** Live DOM per symbol so a refresh updates in place instead of rebuilding. */
  const cardNodes = new Map();
  const rowNodes = new Map();

  const state = {
    exchange: root.dataset.exchange || "binance",
    quote: root.dataset.quote || "USDT",
    filter: "all",
    view: "cards",
    search: searchInput ? searchInput.value : "",
    sort: { key: "volume", direction: "desc" },
    paused: false,
    firstPaint: true,
  };

  let controller = null;
  let timer = null;
  let refreshAfter = 4000;

  /* ── Rendering ─────────────────────────────────────────────────────────── */

  function logoInto(container, item) {
    loadCoinLogo(container, item.canonical_asset, {
      logo_module_url: item.logo_module_url,
      logo_url: item.logo_url,
    });
  }

  function passportHref(item) {
    const methodology = item.methodology_id || methodologyId;
    const query = methodology ? `?methodology_id=${encodeURIComponent(methodology)}` : "";
    return `${basePath}/${encodeURIComponent(String(item.canonical_asset).toLowerCase())}${query}`;
  }

  function buildCard(item) {
    const node = document.createElement("article");
    node.className = "t-asset";
    node.dataset.symbol = item.symbol;
    node.innerHTML = `
      <div class="t-asset-top">
        <span class="t-logo" data-logo></span>
        <span class="t-asset-name">
          <span class="t-asset-symbol" data-symbol></span>
          <span class="t-asset-full" data-name></span>
        </span>
        <button class="t-fav" type="button" data-favorite aria-pressed="false">
          <span data-icon="heart" data-icon-class="icon"></span>
          <span class="sr-only" data-favorite-label></span>
        </button>
      </div>
      <div class="t-price-row">
        <span class="t-price t-figure" data-price>--</span>
        <span class="t-change" data-change data-direction="flat"><span data-icon="trend_flat" data-icon-class="icon-sm"></span><span data-change-value>--</span></span>
      </div>
      <div class="t-range" data-range hidden>
        <div class="t-range-track"><span class="t-range-marker" data-range-marker></span></div>
        <div class="t-range-ends"><span data-range-low></span><span data-range-high></span></div>
      </div>
      <span class="t-status" data-status data-tone="neutral"><span data-status-icon></span><span data-status-text></span></span>
      <div class="t-asset-meta">
        <span data-meta-volume></span>
        <span class="t-nodata" data-nodata hidden><span data-icon="alert" data-icon-class="icon-sm"></span><span data-nodata-text></span></span>
      </div>
      <div class="t-asset-actions">
        <button class="t-action is-primary" type="button" data-quick-view><span data-icon="passport" data-icon-class="icon-sm"></span>See the evidence</button>
        <a class="t-action" data-full-passport><span data-icon="external" data-icon-class="icon-sm"></span>Full Passport</a>
      </div>`;
    paintIcons(node);
    logoInto(node.querySelector("[data-logo]"), item);
    cards.append(node);
    cardNodes.set(item.symbol, node);
    return node;
  }

  function buildRow(item) {
    const node = document.createElement("tr");
    node.dataset.symbol = item.symbol;
    node.innerHTML = `
      <td>
        <span class="t-cell-coin">
          <span class="t-logo" data-logo></span>
          <span class="t-asset-name">
            <span class="t-asset-symbol" data-symbol></span>
            <span class="t-asset-full" data-name></span>
          </span>
        </span>
      </td>
      <td><span class="t-pill" data-status data-tone="neutral"><span data-status-icon></span><span data-status-text></span></span></td>
      <td class="t-num" data-price>--</td>
      <td class="t-num"><span class="t-change" data-change data-direction="flat"><span data-icon="trend_flat" data-icon-class="icon-sm"></span><span data-change-value>--</span></span></td>
      <td class="t-num" data-volume>--</td>
      <td>
        <span class="t-row-actions">
          <button class="t-icon-btn" type="button" data-favorite aria-pressed="false"><span data-icon="heart" data-icon-class="icon-sm"></span><span class="sr-only" data-favorite-label></span></button>
          <button class="t-icon-btn" type="button" data-quick-view><span data-icon="passport" data-icon-class="icon-sm"></span><span class="sr-only" data-quick-label></span></button>
          <a class="t-icon-btn" data-full-passport><span data-icon="external" data-icon-class="icon-sm"></span><span class="sr-only" data-full-label></span></a>
        </span>
      </td>`;
    paintIcons(node);
    logoInto(node.querySelector("[data-logo]"), item);
    tableBody.append(node);
    rowNodes.set(item.symbol, node);
    return node;
  }

  function paintIcons(scope) {
    scope.querySelectorAll("[data-icon]").forEach((element) => {
      element.innerHTML = window.icon(element.dataset.icon, element.dataset.iconClass || "icon");
    });
  }

  /** Write one item's values into whichever node shape it was given. */
  function paint(node, item, { isCard }) {
    const asset = String(item.canonical_asset || "").toUpperCase();
    node.querySelector("[data-symbol]").textContent = asset;
    node.querySelector("[data-name]").textContent = item.asset_name && item.asset_name !== asset
      ? item.asset_name
      : `${String(item.exchange || "").toUpperCase()} spot`;

    const priceNode = node.querySelector("[data-price]");
    const previous = Number.parseFloat(priceNode.dataset.value || "");
    const next = isFiniteNumber(item.last) ? item.last : item.bid;
    priceNode.textContent = formatPrice(next, item.quote_asset);
    priceNode.dataset.value = isFiniteNumber(next) ? String(next) : "";
    priceNode.dataset.empty = isFiniteNumber(next) ? "false" : "true";
    if (isFiniteNumber(previous) && isFiniteNumber(next) && previous !== next) {
      const className = next > previous ? "is-up" : "is-down";
      priceNode.classList.remove("is-up", "is-down");
      void priceNode.offsetWidth;
      priceNode.classList.add(className);
      window.setTimeout(() => priceNode.classList.remove(className), 700);
    }

    const change = node.querySelector("[data-change]");
    const direction = !isFiniteNumber(item.percentage_24h)
      ? "flat"
      : item.percentage_24h > 0
        ? "up"
        : item.percentage_24h < 0
          ? "down"
          : "flat";
    change.dataset.direction = direction;
    change.querySelector("[data-change-value]").textContent = formatChange(item.percentage_24h);
    const arrow = change.querySelector("[data-icon]");
    /* No arrow when there is no move to point at. An arrow beside "--" would read as
       a direction nobody measured. */
    arrow.hidden = !isFiniteNumber(item.percentage_24h);
    const arrowName = direction === "up" ? "trend_up" : direction === "down" ? "trend_down" : "trend_flat";
    if (arrow.dataset.icon !== arrowName) {
      arrow.dataset.icon = arrowName;
      arrow.innerHTML = window.icon(arrowName, "icon-sm");
    }

    const tone = assetTone(item.status);
    const status = node.querySelector("[data-status]");
    status.dataset.tone = tone;
    status.querySelector("[data-status-icon]").innerHTML = window.icon(toneIcon(tone), "icon-sm");
    status.querySelector("[data-status-text]").textContent = statusText(item);

    const favorite = node.querySelector("[data-favorite]");
    setFavoriteState(favorite, asset);

    const full = node.querySelector("[data-full-passport]");
    full.setAttribute("href", passportHref(item));

    /* "The provider says its data is good" and "there is a price to show" are two
       different facts. Keying the honesty note off the first let a card display `--`
       while claiming nothing was wrong, whenever a ticker came back only partly
       filled. The note follows the value itself. */
    const hasPrice = isFiniteNumber(next);
    const trustworthy = hasPrice && Boolean(item.data_available);

    if (isCard) {
      const volume = node.querySelector("[data-meta-volume]");
      volume.innerHTML = `${window.icon("chart", "icon-sm")}<span>24h volume ${formatCompact(item.quote_volume_24h)}</span>`;
      const note = node.querySelector("[data-nodata]");
      note.hidden = trustworthy;
      if (!trustworthy) {
        note.querySelector("[data-nodata-text]").textContent = hasPrice
          ? "This price has not been fully checked"
          : "No live price right now. The review below is unaffected.";
      }
      node.dataset.quote = trustworthy ? "ok" : "missing";
      paintRange(node, item, next);
      node.querySelector("[data-quick-view]").setAttribute(
        "aria-label",
        `See the Shariah evidence for ${asset}`,
      );
      full.setAttribute("aria-label", `Open the full ${asset} Passport`);
    } else {
      node.querySelector("[data-volume]").textContent = formatCompact(item.quote_volume_24h);
      node.querySelector("[data-quick-label]").textContent = `See the Shariah evidence for ${asset}`;
      node.querySelector("[data-full-label]").textContent = `Open the full ${asset} Passport`;
      priceNode.title = trustworthy
        ? ""
        : hasPrice
          ? "This price has not been fully checked"
          : "No live price right now. The Shariah review is unaffected.";
      node.dataset.quote = trustworthy ? "ok" : "missing";
    }
  }

  /* Where the last price sits between the day's low and high. Drawn only when all
     three numbers are present and the range is real; a guessed marker position
     would be inventing market data. */
  function paintRange(node, item, last) {
    const range = node.querySelector("[data-range]");
    const { low_24h: low, high_24h: high } = item;
    const usable = isFiniteNumber(low) && isFiniteNumber(high) && isFiniteNumber(last) && high > low;
    range.hidden = !usable;
    if (!usable) return;
    const position = Math.min(100, Math.max(0, ((last - low) / (high - low)) * 100));
    range.querySelector("[data-range-marker]").style.left = `${position}%`;
    range.querySelector("[data-range-low]").textContent = `Low ${formatPrice(low, item.quote_asset)}`;
    range.querySelector("[data-range-high]").textContent = `High ${formatPrice(high, item.quote_asset)}`;
    range.querySelector("[data-range-marker]").setAttribute(
      "title",
      `Now ${formatPrice(last, item.quote_asset)} between the 24 hour low and high`,
    );
  }

  function setFavoriteState(button, asset) {
    const active = favorites.has(asset);
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute(
      "aria-label",
      active
        ? `Stop following ${asset}. You will no longer be told when its status changes.`
        : `Follow ${asset} and be told when its Shariah status changes.`,
    );
    const label = button.querySelector("[data-favorite-label]");
    if (label) label.textContent = active ? `Following ${asset}` : `Follow ${asset}`;
    const icon = button.querySelector("[data-icon]");
    const name = active ? "heart_filled" : "heart";
    if (icon.dataset.icon !== name) {
      icon.dataset.icon = name;
      icon.innerHTML = window.icon(name, icon.dataset.iconClass || "icon");
    }
  }

  /* ── Filtering, sorting, counting ──────────────────────────────────────── */

  const normalize = (value) => String(value || "").normalize("NFKD").toUpperCase();
  const squash = (value) => normalize(value).replace(/[^A-Z0-9]/g, "");

  function matchesSearch(item) {
    const needle = normalize(state.search).trim();
    if (!needle) return true;
    const squashed = squash(needle);
    return [item.symbol, item.canonical_asset, item.asset_name].some((candidate) => {
      const text = normalize(candidate);
      return text.includes(needle) || (squashed && squash(text).includes(squashed));
    });
  }

  function matchesFilter(item) {
    if (state.filter === "clean") {
      return assetTone(item.status) === "eligible" && !carriesCondition(item.status);
    }
    if (state.filter === "conditional") return carriesCondition(item.status);
    if (state.filter === "following") {
      return favorites.has(String(item.canonical_asset || "").toUpperCase());
    }
    return true;
  }

  function sortValue(item) {
    if (state.sort.key === "symbol") return String(item.canonical_asset || "");
    if (state.sort.key === "price") {
      return isFiniteNumber(item.last) ? item.last : Number.NEGATIVE_INFINITY;
    }
    if (state.sort.key === "change") {
      return isFiniteNumber(item.percentage_24h) ? item.percentage_24h : Number.NEGATIVE_INFINITY;
    }
    return isFiniteNumber(item.quote_volume_24h) ? item.quote_volume_24h : Number.NEGATIVE_INFINITY;
  }

  function visibleItems() {
    const list = Array.from(items.values()).filter(
      (item) => matchesSearch(item) && matchesFilter(item),
    );
    const factor = state.sort.direction === "asc" ? 1 : -1;
    return list.sort((left, right) => {
      const a = sortValue(left);
      const b = sortValue(right);
      if (typeof a === "string" || typeof b === "string") {
        return String(a).localeCompare(String(b)) * factor;
      }
      return (a - b) * factor;
    });
  }

  function applyView() {
    const visible = visibleItems();
    const shown = new Set(visible.map((item) => item.symbol));

    cardNodes.forEach((node, symbol) => { node.hidden = !shown.has(symbol); });
    rowNodes.forEach((node, symbol) => { node.hidden = !shown.has(symbol); });

    /* Reorder so a sort is real for the eye *and* for the keyboard: the DOM order
       is the tab order, so reordering only visually would break the two apart. */
    visible.forEach((item) => {
      const card = cardNodes.get(item.symbol);
      const row = rowNodes.get(item.symbol);
      if (card) cards.append(card);
      if (row) tableBody.append(row);
    });

    const total = items.size;
    cards.hidden = state.view !== "cards" || visible.length === 0;
    tableWrap.hidden = state.view !== "table" || visible.length === 0;
    emptyState.hidden = visible.length !== 0 || total === 0;

    if (emptyMessage && visible.length === 0 && total > 0) {
      emptyMessage.textContent = state.search
        ? `No coin matches "${state.search}". Try a shorter search or clear the filter.`
        : "No coin matches this filter right now.";
    }

    if (resultNote) {
      resultNote.textContent = total === 0
        ? "No coins to show yet."
        : `Showing ${visible.length} of ${total} screened coins on ${state.exchange === "bybit" ? "Bybit" : "Binance"}.`;
    }

    updateCounts();
  }

  function updateCounts() {
    const all = Array.from(items.values());
    const conditional = all.filter((item) => carriesCondition(item.status));
    const tally = {
      all: all.length,
      clean: all.length - conditional.length,
      conditional: conditional.length,
      following: favorites.size,
    };
    Object.entries(tally).forEach(([key, value]) => {
      const node = root.querySelector(`[data-count="${key}"]`);
      if (node) countTo(node, value);
    });
    const badge = root.querySelector("[data-favorite-count]");
    if (badge) {
      badge.hidden = favorites.size === 0;
      badge.textContent = String(favorites.size);
    }
  }

  /* ── Data ──────────────────────────────────────────────────────────────── */

  function setLive(status, label, detail) {
    if (!livePill) return;
    livePill.dataset.state = status;
    liveLabel.textContent = label;
    liveDetail.textContent = detail;
  }

  async function refresh() {
    window.clearTimeout(timer);
    if (state.paused) return;
    if (document.hidden) {
      timer = window.setTimeout(refresh, refreshAfter);
      return;
    }
    controller?.abort();
    controller = new AbortController();
    const params = new URLSearchParams({ exchange: state.exchange, quote_asset: state.quote });
    if (methodologyId) params.set("methodology_id", methodologyId);
    try {
      const response = await fetch(`${endpoint}?${params}`, {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(
          payload.detail?.message || payload.detail || `The price service answered ${response.status}.`,
        );
      }
      const payload = await response.json();
      absorb(payload);
      errorBox.hidden = !payload.warning;
      if (payload.warning) errorMessage.textContent = payload.warning;
      const at = new Date(payload.captured_at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      setLive(
        payload.stale ? "stale" : "live",
        payload.stale ? "Last checked prices" : "Live prices",
        `Updated ${at} from ${payload.provider}`,
      );
      refreshAfter = Math.max(2000, Number(payload.refresh_after_ms) || 4000);
    } catch (error) {
      if (error.name === "AbortError") return;
      errorBox.hidden = false;
      errorMessage.textContent = `${error.message} No price was guessed. The Shariah review shown for each coin is unaffected.`;
      setLive("down", "Prices unavailable", "The review results below are still correct");
    } finally {
      if (!state.paused) timer = window.setTimeout(refresh, refreshAfter);
    }
  }

  function absorb(payload) {
    const arriving = Array.isArray(payload.items) ? payload.items : [];
    const seen = new Set();
    const fresh = [];

    arriving.forEach((item) => {
      seen.add(item.symbol);
      const isNew = !items.has(item.symbol);
      items.set(item.symbol, item);
      const card = cardNodes.get(item.symbol) || buildCard(item);
      const row = rowNodes.get(item.symbol) || buildRow(item);
      paint(card, item, { isCard: true });
      paint(row, item, { isCard: false });
      if (isNew) fresh.push(card);
    });

    /* A symbol the snapshot no longer carries is gone from the market, so its card and
       row go too. The keys are collected before anything is removed, so the map is
       never mutated while it is being walked. */
    const departed = Array.from(items.keys()).filter((symbol) => !seen.has(symbol));
    departed.forEach((symbol) => {
      cardNodes.get(symbol)?.remove();
      rowNodes.get(symbol)?.remove();
      cardNodes.delete(symbol);
      rowNodes.delete(symbol);
      items.delete(symbol);
    });

    if (state.firstPaint) {
      state.firstPaint = false;
      skeletons?.remove();
    }
    applyView();
    if (fresh.length) settleIn(fresh.filter((node) => !node.hidden).slice(0, 24));
  }

  /* ── Favorites ─────────────────────────────────────────────────────────── */

  async function toggleFavorite(item, trigger) {
    const asset = String(item.canonical_asset || "").toUpperCase();
    if (!asset || trigger.disabled) return;
    const wasFavorite = favorites.has(asset);
    trigger.disabled = true;
    try {
      if (wasFavorite) {
        if (!favoriteWatchlistId) {
          throw new Error("Your Favorites list needs to reload before this coin can be removed.");
        }
        const response = await fetch(
          `/api/v1/sharia/watchlists/${encodeURIComponent(favoriteWatchlistId)}/assets/${encodeURIComponent(asset)}?confirmed=true`,
          {
            method: "DELETE",
            credentials: "same-origin",
            headers: { "X-CSRF-Token": document.body.dataset.csrfToken || "" },
          },
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail?.message || payload.detail || "Could not stop following this coin.");
        }
        favorites.delete(asset);
        window.showDashToast?.(`You no longer follow ${asset}.`);
      } else {
        const body = new URLSearchParams({ methodology_id: item.methodology_id || methodologyId || "" });
        const response = await fetch(
          `/dashboard/market/${encodeURIComponent(asset)}/watchlist?format=json`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
              "X-CSRF-Token": document.body.dataset.csrfToken || "",
              Accept: "application/json",
            },
            body: body.toString(),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail?.message || payload.detail || "Could not follow this coin.");
        }
        favoriteWatchlistId = payload.watchlist_id || favoriteWatchlistId;
        favorites.add(asset);
        window.showDashToast?.(`You now follow ${asset}. We will tell you if its status changes.`);
      }
      [cardNodes.get(item.symbol), rowNodes.get(item.symbol)].forEach((node) => {
        const button = node?.querySelector("[data-favorite]");
        if (button) setFavoriteState(button, asset);
      });
      applyView();
      window.HilalFavorites?.sync(favorites, favoriteWatchlistId);
    } catch (error) {
      window.showDashToast?.(error.message || "Could not update Favorites.", true);
    } finally {
      trigger.disabled = false;
    }
  }

  /* ── Wiring ────────────────────────────────────────────────────────────── */

  function itemFor(node) {
    return items.get(node?.closest("[data-symbol]")?.dataset.symbol);
  }

  [cards, tableBody].forEach((scope) => {
    scope.addEventListener("click", (event) => {
      const favorite = event.target.closest("[data-favorite]");
      if (favorite) {
        const item = itemFor(favorite);
        if (item) toggleFavorite(item, favorite);
        return;
      }
      const quick = event.target.closest("[data-quick-view]");
      if (!quick) return;
      const item = itemFor(quick);
      if (!item) return;
      window.HilalPassportQuickView?.open(
        {
          asset: item.canonical_asset,
          methodologyId: item.methodology_id || methodologyId,
          basePath,
        },
        quick,
      );
    });
  });

  searchInput?.addEventListener("input", () => {
    state.search = searchInput.value;
    applyView();
  });

  root.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter;
      root.querySelectorAll("[data-filter]").forEach((other) => {
        other.setAttribute("aria-pressed", String(other === button));
      });
      applyView();
      animate(button, { transform: ["scale(.98)", "scale(1)"] }, { duration: 0.2 });
    });
  });

  root.querySelectorAll("[data-exchange]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.exchange === button.dataset.exchange) return;
      state.exchange = button.dataset.exchange;
      root.querySelectorAll("[data-exchange]").forEach((other) => {
        other.setAttribute("aria-checked", String(other === button));
      });
      items.clear();
      cardNodes.clear();
      rowNodes.clear();
      cards.replaceChildren();
      tableBody.replaceChildren();
      setLive("loading", "Switching exchange", `Reading the ${button.textContent.trim()} spot market`);
      applyView();
      refresh();
    });
  });

  root.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      root.querySelectorAll("[data-view]").forEach((other) => {
        other.setAttribute("aria-checked", String(other === button));
      });
      applyView();
      const shown = state.view === "cards" ? cards : tableWrap;
      if (!shown.hidden) reveal(shown);
    });
  });

  root.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      state.sort = state.sort.key === key
        ? { key, direction: state.sort.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "symbol" ? "asc" : "desc" };
      root.querySelectorAll("th").forEach((header) => header.removeAttribute("aria-sort"));
      button.closest("th")?.setAttribute(
        "aria-sort",
        state.sort.direction === "asc" ? "ascending" : "descending",
      );
      applyView();
    });
  });

  liveToggle?.addEventListener("click", () => {
    state.paused = !state.paused;
    liveToggle.setAttribute("aria-pressed", String(state.paused));
    liveToggle.querySelector("[data-icon]").innerHTML = window.icon(
      state.paused ? "play" : "pause",
      "icon-sm",
    );
    liveToggleLabel.textContent = state.paused
      ? "Start live price updates again"
      : "Pause live price updates";
    if (state.paused) {
      window.clearTimeout(timer);
      setLive("stale", "Updates paused", "Prices are held at the last check until you start again");
    } else {
      setLive("loading", "Starting again", "Reading the latest prices");
      refresh();
    }
  });

  root.querySelector("[data-quote-retry]")?.addEventListener("click", () => {
    setLive("loading", "Trying again", "Reading the latest prices");
    refresh();
  });

  root.querySelector("[data-clear-filters]")?.addEventListener("click", () => {
    state.search = "";
    state.filter = "all";
    if (searchInput) searchInput.value = "";
    root.querySelectorAll("[data-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === "all"));
    });
    applyView();
  });

  standardSelect?.addEventListener("change", () => standardForm?.requestSubmit());

  root.querySelector("[data-open-favorites]")?.addEventListener("click", (event) => {
    window.HilalFavorites?.open(favorites, favoriteWatchlistId, event.currentTarget);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !state.paused) refresh();
  });

  /* The Favorites popup and the grid share one truth about what is followed. */
  window.addEventListener("hilal:favorites-changed", (event) => {
    const next = event.detail?.assets;
    if (!next) return;
    favorites.clear();
    next.forEach((asset) => favorites.add(String(asset).toUpperCase()));
    cardNodes.forEach((node, symbol) => {
      const item = items.get(symbol);
      if (item) setFavoriteState(node.querySelector("[data-favorite]"), String(item.canonical_asset).toUpperCase());
    });
    rowNodes.forEach((node, symbol) => {
      const item = items.get(symbol);
      if (item) setFavoriteState(node.querySelector("[data-favorite]"), String(item.canonical_asset).toUpperCase());
    });
    applyView();
  });

  if (methodologyId) {
    refresh();
  } else {
    /* No published standard means there is nothing to price. Say so, and do not
       leave a skeleton shimmering over an empty list forever. */
    skeletons?.remove();
    setLive("down", "No screening standard", "Publish a standard to see screened coins");
    if (resultNote) resultNote.textContent = "No coins to show yet.";
  }

  window.addEventListener("beforeunload", () => {
    window.clearTimeout(timer);
    controller?.abort();
  });

  /* Open the Favorites popup straight away when arriving from a link that asks for it. */
  if (new URLSearchParams(window.location.search).get("saved_assets") === "1") {
    window.requestAnimationFrame(() =>
      window.HilalFavorites?.open(favorites, favoriteWatchlistId, null),
    );
  }
}
