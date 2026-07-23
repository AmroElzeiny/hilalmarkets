(() => {
  const catalogPrefix = "https://cdn.jsdelivr.net/npm/@web3icons/core@4.0.53/";
  const cache = new Map();

  async function resolve(moduleUrl) {
    if (!moduleUrl || !moduleUrl.startsWith(catalogPrefix)) return null;
    if (cache.has(moduleUrl)) return cache.get(moduleUrl);
    try {
      const module = await import(moduleUrl);
      if (typeof module.default !== "string" || !module.default.trim().startsWith("<svg")) {
        cache.set(moduleUrl, null);
        return null;
      }
      const objectUrl = URL.createObjectURL(
        new Blob([module.default], { type: "image/svg+xml" }),
      );
      cache.set(moduleUrl, objectUrl);
      return objectUrl;
    } catch (_error) {
      cache.set(moduleUrl, null);
      return null;
    }
  }

  async function load(container, moduleUrl, symbol) {
    if (!container || container.dataset.assetLogoLoaded === moduleUrl) return;
    container.dataset.assetLogoLoaded = moduleUrl || "";
    const source = await resolve(moduleUrl);
    if (!source || container.dataset.assetLogoLoaded !== moduleUrl) return;
    const image = document.createElement("img");
    image.src = source;
    image.alt = `${symbol || "Asset"} logo`;
    image.decoding = "async";
    container.replaceChildren(image);
  }

  function hydrate(root = document) {
    root.querySelectorAll("[data-asset-logo-module]").forEach((container) => {
      load(
        container,
        container.dataset.assetLogoModule,
        container.dataset.assetLogoSymbol,
      );
    });
  }

  window.HilalAssetLogos = { load, hydrate };
  hydrate();
})();
