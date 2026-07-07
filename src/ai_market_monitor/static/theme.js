(function () {
  const storageKey = "amm-theme";

  function currentTheme() {
    return "light";
  }

  function applyTheme(theme, persist = true) {
    const resolved = "light";
    document.documentElement.dataset.theme = resolved;
    document.body?.classList.remove("theme-light", "theme-dark", "theme-system");
    document.body?.classList.add(`theme-${resolved}`);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.hidden = true;
      button.setAttribute("aria-hidden", "true");
      button.setAttribute("aria-pressed", String(resolved === "light"));
      button.setAttribute("aria-label", "Color mode is fixed to light mode");
      button.dataset.theme = resolved;
    });
    if (persist) window.localStorage.setItem(storageKey, resolved);
    return resolved;
  }

  async function saveDashboardTheme(theme) {
    if (!document.body?.dataset.dashboardApi) return;
    try {
      await fetch(`${document.body.dataset.dashboardApi}/preferences/theme`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme }),
      });
    } catch {
      // The local preference still keeps the interface usable if persistence is unavailable.
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(currentTheme(), false);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.hidden = true;
      button.setAttribute("aria-hidden", "true");
    });
  });
})();
