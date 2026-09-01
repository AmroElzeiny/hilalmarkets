/* Drawing a coin's picture, once, for every surface in the product.
 *
 * The server decides *which* pictures exist for a coin — `core/asset_logos.py` is the
 * one owner of that. This walks them in order and draws the first one that genuinely
 * renders, leaving the letter monogram in place when none of them do.
 *
 * Two failures used to end here as a coin with no logo:
 *
 *   1. A stored picture was used *instead of* the catalogue rather than *before* it.
 *      A stored address that 404s or is withdrawn left `<img>` broken — no picture, no
 *      monogram, no fall-through to the catalogue entry that would have worked.
 *   2. Nothing waited to see whether the picture loaded. `img.src = url` is optimistic;
 *      the failure arrives later, as an event nobody was listening for.
 *
 * So a source is only accepted once the browser has actually decoded it.
 */
(() => {
  const catalogPrefix = "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/";
  /** moduleUrl -> object URL, or null once the catalog is known not to have it. */
  const catalogCache = new Map();

  async function importSvg(moduleUrl) {
    try {
      const module = await import(moduleUrl);
      if (typeof module.default === "string" && module.default.trim().startsWith("<svg")) {
        return URL.createObjectURL(new Blob([module.default], { type: "image/svg+xml" }));
      }
    } catch (_error) {
      // The catalog has no entry under this name. Not an error: most coins are not in it.
    }
    return null;
  }

  async function resolveCatalog(moduleUrl) {
    if (!moduleUrl || !moduleUrl.startsWith(catalogPrefix)) return null;
    if (catalogCache.has(moduleUrl)) return catalogCache.get(moduleUrl);
    const candidates = [moduleUrl];
    // Exchanges prepend a supply multiplier to some meme-coin tickers (1000SHIB,
    // 10000SATS, 1MBABYDOGE) that the icon catalog does not use in its own filenames.
    const symbol = moduleUrl.match(/\/([A-Z0-9]+)\.svg\.js$/)?.[1];
    const withoutMultiplier = symbol?.replace(/^(?:1000|10000|1M)(?=[A-Z])/, "");
    if (withoutMultiplier && withoutMultiplier !== symbol) {
      candidates.push(moduleUrl.replace(`/${symbol}.svg.js`, `/${withoutMultiplier}.svg.js`));
    }
    let result = null;
    for (const candidate of candidates) {
      result = await importSvg(candidate);
      if (result) break;
    }
    catalogCache.set(moduleUrl, result);
    return result;
  }

  /** An <img> that has finished loading, or null if this address does not draw. */
  function drawable(source, alt) {
    return new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.alt = alt;
      image.addEventListener("load", () => resolve(image), { once: true });
      image.addEventListener("error", () => resolve(null), { once: true });
      image.src = source;
    });
  }

  /**
   * Put the best available picture into `container`.
   *
   * `container` already holds the letter monogram, drawn by the server. It is only
   * replaced once a real picture has loaded, so a coin never flickers from letters to
   * emptiness — the fallback is the starting state, not a recovery step.
   */
  async function load(container, moduleUrl, symbol, directUrl = null, providerUrl = null) {
    // Stored picture first, then the market-data provider's, then the catalog. That is
    // the order `core/asset_logos.py` publishes and this must not invent its own: the
    // first two are pictures of *this coin*, the catalog is addressed by ticker alone,
    // so two coins sharing a ticker share its file.
    //
    // `directUrl` also accepts an array, so a caller holding the whole ordered list can
    // hand it over rather than picking one and losing the rest.
    const safe = (value) =>
      typeof value === "string" && value.startsWith("https://") ? value : null;
    const direct = Array.isArray(directUrl) ? directUrl : [directUrl];
    const sources = [...new Set(
      [...direct.map(safe), safe(providerUrl), moduleUrl].filter(Boolean),
    )];
    const identity = sources.join("|");
    if (!container || !sources.length || container.dataset.assetLogoLoaded === identity) return;
    container.dataset.assetLogoLoaded = identity;
    const alt = `${symbol || "Asset"} logo`;

    for (const source of sources) {
      const address = source === moduleUrl ? await resolveCatalog(moduleUrl) : source;
      if (!address) continue;
      const image = await drawable(address, alt);
      // Another load for this container started while this one was in flight — that one
      // owns the container now, so this result is dropped rather than drawn over it.
      if (container.dataset.assetLogoLoaded !== identity) return;
      if (image) {
        container.replaceChildren(image);
        container.dataset.assetLogoDrawn = "true";
        return;
      }
    }
    // Every source failed. The monogram is still there, which is the point of it.
  }

  function hydrate(root = document) {
    const selector =
      "[data-asset-logo-module], [data-asset-logo-url], [data-asset-logo-provider-url]";
    root.querySelectorAll(selector).forEach((container) => {
      load(
        container,
        container.dataset.assetLogoModule,
        container.dataset.assetLogoSymbol,
        container.dataset.assetLogoUrl,
        container.dataset.assetLogoProviderUrl,
      );
    });
  }

  window.HilalAssetLogos = { load, hydrate };
  hydrate();
})();
