(function () {
  const storageKey = "amm-theme";

  function systemTheme() {
    return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function currentTheme() {
    const stored = window.localStorage.getItem(storageKey);
    if (stored === "light" || stored === "dark") return stored;
    const dashboardTheme = document.body?.className.match(/theme-(light|dark)/)?.[1];
    return dashboardTheme || systemTheme();
  }

  function applyTheme(theme, persist = true) {
    const resolved = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = resolved;
    document.body?.classList.remove("theme-light", "theme-dark", "theme-system");
    document.body?.classList.add(`theme-${resolved}`);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.setAttribute("aria-pressed", String(resolved === "light"));
      button.setAttribute(
        "aria-label",
        resolved === "light" ? "Switch to dark mode" : "Switch to light mode",
      );
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
      button.addEventListener("click", async () => {
        const next = currentTheme() === "dark" ? "light" : "dark";
        applyTheme(next);
        await saveDashboardTheme(next);
      });
    });
  });
})();
