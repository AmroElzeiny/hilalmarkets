(() => {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");

  function scrollMode() {
    return reducedMotion?.matches ? "auto" : "smooth";
  }

  function targetFromHash(hash) {
    if (!hash || hash === "#") return null;
    const id = decodeURIComponent(hash.slice(1));
    return document.getElementById(id) || document.getElementsByName(id)[0] || null;
  }

  document.addEventListener("click", (event) => {
    const link = event.target instanceof Element
      ? event.target.closest("a[href^='#']")
      : null;
    if (!link || link.target || link.hasAttribute("download")) return;

    const url = new URL(link.getAttribute("href"), window.location.href);
    if (url.pathname !== window.location.pathname || url.search !== window.location.search) return;

    const target = targetFromHash(url.hash);
    if (!target) return;

    event.preventDefault();
    target.scrollIntoView({ behavior: scrollMode(), block: "start" });
    window.history.pushState(null, "", url.hash);
  });
})();
