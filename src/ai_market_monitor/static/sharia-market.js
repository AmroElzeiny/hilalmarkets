const root = document.querySelector("[data-live-market-root]");

if (root) {
  const endpoint = root.dataset.endpoint;
  const body = root.querySelector("[data-live-market-body]");
  const search = root.querySelector("[data-live-market-search]");
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
  const dialog = document.querySelector("[data-market-passport-dialog]");
  const rows = new Map();
  const latestItems = new Map();
  const logoUrls = new Map();
  let controller = null;
  let timer = null;
  let refreshAfter = 1000;
  let firstSnapshot = true;

  const logoPrefix = "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/";
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
      <div role="cell"><span class="badge badge-eligible live-test-badge">Halal <small>Test</small></span></div>
      <strong class="live-quote" role="cell" data-bid>--</strong>
      <strong class="live-quote" role="cell" data-ask>--</strong>
      <strong class="live-change" role="cell" data-change>--</strong>
      <span role="cell" data-spread>--</span>
      <span role="cell" data-volume>--</span>
      <div role="cell"><button class="btn btn-secondary btn-xs" type="button" data-show-passport>Show passport</button></div>`;
    row.querySelector("[data-coin-symbol]").textContent = item.symbol;
    row.querySelector("[data-coin-name]").textContent = `${item.exchange.toUpperCase()} spot`;
    row.querySelector("[data-coin-fallback]").textContent = item.canonical_asset.slice(0, 3);
    body.append(row);
    observeLogo(row.querySelector("[data-coin-logo]"), item);
    rows.set(item.symbol, row);
    return row;
  }

  async function importLogo(item) {
    if (!item.logo_module_url || !item.logo_module_url.startsWith(logoPrefix)) return null;
    if (logoUrls.has(item.canonical_asset)) return logoUrls.get(item.canonical_asset);
    const candidates = [item.logo_module_url];
    const withoutMultiplier = item.canonical_asset.replace(/^(?:1000|10000|1M)(?=[A-Z])/, "");
    if (withoutMultiplier !== item.canonical_asset) {
      candidates.push(item.logo_module_url.replace(`/${item.canonical_asset}.svg.js`, `/${withoutMultiplier}.svg.js`));
    }
    for (const url of candidates) {
      try {
        const module = await import(url);
        if (typeof module.default !== "string" || !module.default.trim().startsWith("<svg")) continue;
        const objectUrl = URL.createObjectURL(new Blob([module.default], { type: "image/svg+xml" }));
        logoUrls.set(item.canonical_asset, objectUrl);
        return objectUrl;
      } catch (_error) {
        // A branded fallback remains visible when the catalog has no exact ticker match.
      }
    }
    logoUrls.set(item.canonical_asset, null);
    return null;
  }

  async function loadLogo(container, item) {
    if (!container || container.dataset.logoLoaded === "true") return;
    container.dataset.logoLoaded = "true";
    const src = await importLogo(item);
    if (!src) return;
    const image = document.createElement("img");
    image.src = src;
    image.alt = `${item.canonical_asset} logo`;
    image.decoding = "async";
    container.replaceChildren(image);
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
    row.querySelector("[data-spread]").textContent = finite(item.spread_bps) ? `${item.spread_bps.toFixed(2)} bps` : "--";
    row.querySelector("[data-volume]").textContent = formatCompact(item.quote_volume_24h);
    const change = row.querySelector("[data-change]");
    change.textContent = formatChange(item.percentage_24h);
    change.classList.toggle("is-positive", finite(item.percentage_24h) && item.percentage_24h > 0);
    change.classList.toggle("is-negative", finite(item.percentage_24h) && item.percentage_24h < 0);
    row.classList.toggle("is-unavailable", !item.data_available);
  }

  function applySearch() {
    const needle = (search.value || "").trim().toUpperCase();
    let visible = 0;
    rows.forEach((row, symbol) => {
      const item = latestItems.get(symbol);
      const matches = !needle || symbol.includes(needle) || item?.asset_name?.toUpperCase().includes(needle);
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
    const params = new URLSearchParams({ exchange: exchange.value, quote_asset: quote.value });
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
      count.textContent = String(payload.total);
      eligible.textContent = String(payload.total);
      provider.textContent = payload.exchange[0].toUpperCase() + payload.exchange.slice(1);
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

  function setDialogText(selector, value) {
    const node = dialog.querySelector(selector);
    if (node) node.textContent = value;
  }

  async function openPassport(item) {
    setDialogText("[data-passport-symbol]", item.symbol);
    setDialogText("[data-passport-name]", `${item.asset_name} | ${item.exchange.toUpperCase()} spot`);
    setDialogText("[data-passport-exchange]", item.exchange.toUpperCase());
    setDialogText("[data-passport-date]", new Date(item.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" }));
    setDialogText("[data-passport-bid]", formatPrice(item.bid));
    setDialogText("[data-passport-ask]", formatPrice(item.ask));
    setDialogText("[data-passport-last]", formatPrice(item.last));
    setDialogText("[data-passport-bid-size]", finite(item.bid_size) ? `Size ${formatCompact(item.bid_size)}` : "Size unavailable");
    setDialogText("[data-passport-ask-size]", finite(item.ask_size) ? `Size ${formatCompact(item.ask_size)}` : "Size unavailable");
    setDialogText("[data-passport-change]", `${formatChange(item.percentage_24h)} over 24h`);
    setDialogText("[data-passport-range]", `${formatPrice(item.low_24h)} - ${formatPrice(item.high_24h)}`);
    setDialogText("[data-passport-volume]", `${formatCompact(item.quote_volume_24h)} ${item.quote_asset} volume`);
    setDialogText("[data-passport-provider]", `Prices: ${item.exchange.toUpperCase()} via CCXT | Updated ${new Date(item.updated_at).toLocaleTimeString()}`);
    const logo = dialog.querySelector("[data-passport-logo]");
    logo.replaceChildren();
    const fallback = document.createElement("span");
    fallback.textContent = item.canonical_asset.slice(0, 3);
    logo.append(fallback);
    const src = await importLogo(item);
    if (src) {
      const image = document.createElement("img");
      image.src = src;
      image.alt = `${item.canonical_asset} logo`;
      logo.replaceChildren(image);
    }
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  body.addEventListener("click", (event) => {
    const button = event.target.closest("[data-show-passport]");
    if (!button) return;
    const item = latestItems.get(button.closest("[data-symbol]")?.dataset.symbol);
    if (item) openPassport(item);
  });
  search.addEventListener("input", applySearch);
  [exchange, quote].forEach((control) => control.addEventListener("change", () => {
    clearRows();
    refresh();
  }));
  retry.addEventListener("click", refresh);
  dialog.querySelectorAll("[data-market-passport-close], [data-market-passport-done]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
}
