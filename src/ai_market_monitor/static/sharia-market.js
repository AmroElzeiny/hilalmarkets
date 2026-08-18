const root = document.querySelector("[data-live-market-root]");

if (root) {
  const endpoint = root.dataset.endpoint;
  const methodologyId = root.dataset.methodologyId;
  const body = root.querySelector("[data-live-market-body]");
  const search = root.querySelector("[data-live-market-search]");
  const methodology = root.querySelector("[data-live-market-methodology]");
  const exchange = root.querySelector("[data-live-market-exchange]");
  const quote = root.querySelector("[data-live-market-quote]");
  const status = root.querySelector("[data-live-market-status]");
  const updated = root.querySelector("[data-live-market-updated]");
  const errorBox = root.querySelector("[data-live-market-error]");
  const errorMessage = root.querySelector("[data-live-market-error-message]");
  const retry = root.querySelector("[data-live-market-retry]");
  const empty = root.querySelector("[data-live-market-empty]");
  const count = document.querySelector("[data-live-market-count]");
  const eligible = document.querySelector("[data-live-market-eligible]");
  const provider = document.querySelector("[data-live-market-provider]");
  const favoriteAssets = new Set(
    JSON.parse(root.dataset.favoriteAssets || "[]").map((asset) => String(asset).toUpperCase()),
  );
  let favoriteWatchlistId = root.dataset.favoriteWatchlistId || "";
  const rows = new Map();
  const latestItems = new Map();
  let controller = null;
  let timer = null;
  let refreshAfter = 1000;
  let firstSnapshot = true;
  let currentMethodology = null;

  const finite = (value) => typeof value === "number" && Number.isFinite(value);

  function formatPrice(value) {
    if (!finite(value)) return "--";
    const magnitude = Math.abs(value);
    const maximumFractionDigits = magnitude >= 1000 ? 2 : magnitude >= 1 ? 4 : magnitude >= 0.01 ? 6 : 10;
    return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
  }

  function formatCompact(value) {
    if (!finite(value)) return "--";
    return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);
  }

  function formatChange(value) {
    if (!finite(value)) return "--";
    return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
  }

  function flashPrice(node, previous, next) {
    if (!finite(previous) || !finite(next) || previous === next) return;
    node.classList.remove("quote-up", "quote-down");
    void node.offsetWidth;
    node.classList.add(next > previous ? "quote-up" : "quote-down");
    window.setTimeout(() => node.classList.remove("quote-up", "quote-down"), 300);
  }

  function createRow(item) {
    const row = document.createElement("div");
    row.className = "live-market-row";
    row.setAttribute("role", "row");
    row.dataset.symbol = item.symbol;
    row.innerHTML = `
      <div class="live-asset" role="cell"><span class="live-asset-logo" data-coin-logo><b data-coin-fallback></b></span><span><strong data-coin-symbol></strong><small data-coin-name></small></span></div>
      <div role="cell"><span class="badge badge-eligible" data-screening-status></span></div>
      <strong class="live-quote" role="cell" data-bid>--</strong>
      <strong class="live-quote" role="cell" data-ask>--</strong>
      <strong class="live-change" role="cell" data-change>--</strong>
      <span role="cell" data-volume>--</span>
      <div class="live-market-actions" role="cell"><button class="btn btn-secondary btn-xs" type="button" data-show-passport>Show passport</button><button class="favorite-toggle" type="button" data-favorite-toggle aria-pressed="false"><img class="favorite-heart-icon favorite-heart-outline" src="/static/ui-heart.svg" alt=""><img class="favorite-heart-icon favorite-heart-solid" src="/static/ui-heart-filled.svg" alt=""><span class="sr-only" data-favorite-label>Follow status changes</span></button></div>`;
    row.querySelector("[data-coin-symbol]").textContent = item.symbol;
    row.querySelector("[data-coin-name]").textContent = item.asset_name || `${item.exchange.toUpperCase()} spot`;
    row.querySelector("[data-coin-fallback]").textContent = item.canonical_asset.slice(0, 3);
    body.append(row);
    observeLogo(row.querySelector("[data-coin-logo]"), item);
    rows.set(item.symbol, row);
    return row;
  }

  function updateFavoriteButton(row, item) {
    const button = row.querySelector("[data-favorite-toggle]");
    if (!button) return;
    const active = favoriteAssets.has(String(item.canonical_asset || "").toUpperCase());
    button.classList.toggle("is-favorite", active);
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute(
      "aria-label",
      active
        ? `Remove ${item.canonical_asset} from Favorites`
        : `Add ${item.canonical_asset} to Favorites and follow status changes`,
    );
    const label = button.querySelector("[data-favorite-label]");
    if (label) label.textContent = active ? "Following status changes" : "Follow status changes";
  }

  function syncFavoriteCount() {
    const countNode = root.querySelector("[data-favorite-count]");
    if (!countNode) return;
    countNode.hidden = favoriteAssets.size === 0;
    countNode.textContent = String(favoriteAssets.size);
  }

  function marketStatusLabel(item) {
    const fallback = item.status === "eligible"
      ? "Halal"
      : item.status === "eligible_with_qualifications"
        ? "Halal with qualifications"
        : "Screened";
    return String(item.status_label || fallback).replace(/^eligible\b/i, "Halal");
  }

  function loadLogo(container, item) {
    window.HilalAssetLogos?.load(container, item.logo_module_url, item.canonical_asset, item.logo_url);
  }

  const logoObserver = "IntersectionObserver" in window
    ? new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const item = latestItems.get(entry.target.closest("[data-symbol]")?.dataset.symbol);
          if (item) loadLogo(entry.target, item);
          observer.unobserve(entry.target);
        });
      }, { root: root.querySelector(".live-market-scroll"), rootMargin: "160px" })
    : null;

  function observeLogo(container, item) {
    if (logoObserver) logoObserver.observe(container);
    else loadLogo(container, item);
  }

  function updateRow(item) {
    latestItems.set(item.symbol, item);
    const row = rows.get(item.symbol) || createRow(item);
    const bid = row.querySelector("[data-bid]");
    const ask = row.querySelector("[data-ask]");
    const previousBid = Number.parseFloat(bid.dataset.value || "NaN");
    const previousAsk = Number.parseFloat(ask.dataset.value || "NaN");
    flashPrice(bid, previousBid, item.bid);
    flashPrice(ask, previousAsk, item.ask);
    bid.textContent = formatPrice(item.bid);
    ask.textContent = formatPrice(item.ask);
    bid.dataset.value = finite(item.bid) ? String(item.bid) : "";
    ask.dataset.value = finite(item.ask) ? String(item.ask) : "";
    row.querySelector("[data-volume]").textContent = formatCompact(item.quote_volume_24h);
    const change = row.querySelector("[data-change]");
    change.textContent = formatChange(item.percentage_24h);
    change.classList.toggle("is-positive", finite(item.percentage_24h) && item.percentage_24h > 0);
    change.classList.toggle("is-negative", finite(item.percentage_24h) && item.percentage_24h < 0);
    const screeningStatus = row.querySelector("[data-screening-status]");
    screeningStatus.textContent = marketStatusLabel(item);
    screeningStatus.className = `badge ${
      ["under_review", "disputed"].includes(item.status)
        ? "badge-review"
        : item.status === "insufficient_information"
          ? "badge-neutral"
          : item.status === "excluded"
            ? "badge-excluded"
            : "badge-eligible"
    }`;
    row.classList.toggle("is-unavailable", !item.data_available);
    updateFavoriteButton(row, item);
  }

  function searchable(value) {
    return String(value || "").normalize("NFKD").toUpperCase();
  }

  function compactSearch(value) {
    return searchable(value).replace(/[^A-Z0-9]/g, "");
  }

  function applySearch() {
    const needle = searchable(search.value).trim();
    const compactNeedle = compactSearch(needle);
    let visible = 0;
    rows.forEach((row, symbol) => {
      const item = latestItems.get(symbol);
      const candidates = [
        symbol,
        item?.canonical_asset,
        item?.asset_name,
        `${item?.canonical_asset || ""}/${item?.quote_asset || ""}`,
      ];
      const matches = !needle || candidates.some((candidate) => {
        const normalized = searchable(candidate);
        return normalized.includes(needle) || (
          compactNeedle && compactSearch(normalized).includes(compactNeedle)
        );
      });
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    empty.hidden = visible !== 0 || rows.size === 0;
  }

  function clearRows() {
    logoObserver?.disconnect();
    rows.clear();
    latestItems.clear();
    body.replaceChildren();
    firstSnapshot = true;
  }

  async function refresh() {
    window.clearTimeout(timer);
    if (document.hidden) {
      timer = window.setTimeout(refresh, refreshAfter);
      return;
    }
    controller?.abort();
    controller = new AbortController();
    const params = new URLSearchParams({
      exchange: exchange.value,
      quote_asset: quote.value,
    });
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
        throw new Error(payload.detail?.message || payload.detail || `Quote request failed (${response.status})`);
      }
      const payload = await response.json();
      currentMethodology = payload.methodology;
      if (firstSnapshot) {
        body.replaceChildren();
        firstSnapshot = false;
      }
      payload.items.forEach(updateRow);
      const activeSymbols = new Set(payload.items.map((item) => item.symbol));
      rows.forEach((row, symbol) => {
        if (!activeSymbols.has(symbol)) {
          row.remove();
          rows.delete(symbol);
          latestItems.delete(symbol);
        }
      });
      applySearch();
      if (count) count.textContent = String(payload.total);
      if (eligible) eligible.textContent = String(payload.total);
      if (provider) provider.textContent = payload.exchange[0].toUpperCase() + payload.exchange.slice(1);
      status.textContent = payload.stale ? "Showing last verified snapshot" : "Live quotes connected";
      updated.textContent = `Updated ${new Date(payload.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} | ${payload.provider}`;
      errorBox.hidden = !payload.warning;
      if (payload.warning) errorMessage.textContent = payload.warning;
      refreshAfter = Math.max(1000, Number(payload.refresh_after_ms) || 1000);
    } catch (error) {
      if (error.name === "AbortError") return;
      errorBox.hidden = false;
      errorMessage.textContent = error.message || "The provider is unavailable. No values were invented.";
      status.textContent = "Provider unavailable";
    } finally {
      timer = window.setTimeout(refresh, refreshAfter);
    }
  }

  function openPassport(item, trigger) {
    window.HilalPassportQuickView?.open({
      asset: item.canonical_asset,
      methodologyId: item.methodology_id || currentMethodology?.id || methodologyId,
    }, trigger);
  }

  async function toggleFavorite(item, trigger) {
    const asset = String(item.canonical_asset || "").toUpperCase();
    if (!asset || trigger.dataset.loading === "true") return;
    trigger.dataset.loading = "true";
    trigger.disabled = true;
    try {
      if (favoriteAssets.has(asset)) {
        if (!favoriteWatchlistId) throw new Error("Your Favorites list needs to refresh before this asset can be removed.");
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
          throw new Error(payload.detail?.message || payload.detail || "Could not remove this Favorite.");
        }
        favoriteAssets.delete(asset);
        syncFavoriteCount();
        window.showDashToast?.(`${asset} was removed from Favorites.`);
      } else {
        const values = new URLSearchParams({ methodology_id: item.methodology_id || currentMethodology?.id || methodologyId || "" });
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
            body: values.toString(),
          },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail?.message || payload.detail || "Could not add this Favorite.");
        }
        favoriteWatchlistId = payload.watchlist_id || favoriteWatchlistId;
        favoriteAssets.add(asset);
        syncFavoriteCount();
        window.showDashToast?.(`${asset} is now followed for status changes.`);
      }
      const row = rows.get(item.symbol);
      if (row) updateFavoriteButton(row, item);
    } catch (error) {
      window.showDashToast?.(error.message || "Could not update Favorites.", true);
    } finally {
      trigger.disabled = false;
      delete trigger.dataset.loading;
    }
  }

  body.addEventListener("click", (event) => {
    const favoriteButton = event.target.closest("[data-favorite-toggle]");
    if (favoriteButton) {
      const item = latestItems.get(favoriteButton.closest("[data-symbol]")?.dataset.symbol);
      if (item) toggleFavorite(item, favoriteButton);
      return;
    }
    const button = event.target.closest("[data-show-passport]");
    if (!button) return;
    const item = latestItems.get(button.closest("[data-symbol]")?.dataset.symbol);
    if (item) openPassport(item, button);
  });
  search.addEventListener("input", applySearch);
  methodology?.addEventListener("change", () => methodology.form?.requestSubmit());
  [exchange, quote].forEach((control) => control.addEventListener("change", () => {
    clearRows();
    refresh();
  }));
  retry.addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
}
