document.addEventListener("DOMContentLoaded", () => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sidebar = document.querySelector("[data-hilal-sidebar], [data-sidebar]");
  const backdrop = document.querySelector("[data-hilal-sidebar-backdrop], [data-sidebar-backdrop]");

  const closeSidebar = () => {
    sidebar?.classList.remove("is-open");
    backdrop?.classList.remove("is-open");
    document.body.classList.remove("no-scroll");
  };
  const openSidebar = () => {
    sidebar?.classList.add("is-open");
    backdrop?.classList.add("is-open");
    document.body.classList.add("no-scroll");
  };

  document.querySelectorAll("[data-hilal-open-sidebar], [data-open-sidebar]").forEach((button) => {
    button.addEventListener("click", openSidebar);
  });
  document.querySelectorAll("[data-hilal-close-sidebar], [data-close-sidebar]").forEach((button) => {
    button.addEventListener("click", closeSidebar);
  });
  backdrop?.addEventListener("click", closeSidebar);

  const publicMenuButton = document.querySelector("[data-public-menu]");
  const publicLinks = document.querySelector(".public-links");
  const closePublicMenu = () => {
    publicLinks?.classList.remove("is-open");
    publicMenuButton?.setAttribute("aria-expanded", "false");
  };
  publicMenuButton?.addEventListener("click", () => {
    const open = !publicLinks?.classList.contains("is-open");
    publicLinks?.classList.toggle("is-open", open);
    publicMenuButton.setAttribute("aria-expanded", String(open));
  });
  publicLinks?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", closePublicMenu);
  });

  document.querySelectorAll("[data-accordion-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = button.closest(".accordion-item");
      const open = item?.classList.toggle("is-open") || false;
      button.setAttribute("aria-expanded", String(open));
    });
  });

  const revealElements = [...document.querySelectorAll(".reveal")];
  if (reducedMotion || !("IntersectionObserver" in window)) {
    revealElements.forEach((element) => element.classList.add("is-visible"));
  } else {
    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.08, rootMargin: "0px 0px 80px" });
    revealElements.forEach((element) => revealObserver.observe(element));

    let revealFrame = 0;
    const revealInViewport = () => {
      revealFrame = 0;
      revealElements.forEach((element) => {
        if (element.classList.contains("is-visible")) return;
        const bounds = element.getBoundingClientRect();
        if (bounds.top <= window.innerHeight + 80 && bounds.bottom >= -80) {
          element.classList.add("is-visible");
          revealObserver.unobserve(element);
        }
      });
    };
    const scheduleRevealCheck = () => {
      if (revealFrame) return;
      revealFrame = requestAnimationFrame(revealInViewport);
    };
    window.addEventListener("scroll", scheduleRevealCheck, { passive: true });
    window.addEventListener("resize", scheduleRevealCheck);
    scheduleRevealCheck();
  }

  document.querySelectorAll("[data-hilal-progress]").forEach((element) => {
    const value = Math.max(0, Math.min(100, Number(element.dataset.hilalProgress || 0)));
    const fill = element.querySelector("span");
    if (fill) {
      if (reducedMotion) {
        fill.style.transition = "none";
      }
      requestAnimationFrame(() => {
        fill.style.width = `${value}%`;
      });
    }
  });

  document.querySelectorAll(".badge").forEach((badge) => {
    if (badge.textContent.trim() !== "Live worker evidence") return;
    badge.classList.add("observability-live");
    if (!badge.querySelector("i")) badge.prepend(document.createElement("i"));
  });

  document.querySelectorAll("[data-progress]").forEach((fill) => {
    const value = Math.max(0, Math.min(100, Number(fill.dataset.progress || 0)));
    if (reducedMotion) fill.style.transition = "none";
    requestAnimationFrame(() => { fill.style.width = `${value}%`; });
  });

  document.querySelectorAll("[data-tabs]").forEach((group) => {
    const buttons = [...group.querySelectorAll("[data-tab]")];
    const scope = group.closest("[data-tab-scope]") || group.parentElement;
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        buttons.forEach((candidate) => candidate.classList.remove("is-active"));
        button.classList.add("is-active");
        scope?.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.classList.toggle("is-active", panel.dataset.panel === button.dataset.tab);
        });
      });
    });
  });

  document.querySelectorAll(".chip input").forEach((input) => {
    const sync = () => input.closest(".chip")?.classList.toggle("is-active", input.checked);
    input.addEventListener("change", sync);
    sync();
  });

  document.querySelectorAll("[data-copy-value]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyValue || "");
        const original = button.textContent;
        button.textContent = "Copied";
        window.setTimeout(() => { button.textContent = original; }, 1500);
      } catch {
        button.setAttribute("title", "Copy is unavailable in this browser");
      }
    });
  });

  document.querySelectorAll("[data-hilal-dismiss]").forEach((button) => {
    button.addEventListener("click", () => {
      button.closest("[data-hilal-dismissible]")?.remove();
    });
  });

  const preferencesTarget = document.getElementById("strategy-preferences");
  const preferenceButton = document.querySelector("[data-forget-strategy-preferences]");
  const preferenceRequest = async (options = {}) => {
    const response = await fetch("/api/v1/dashboard/cockpit/preferences", {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || response.statusText);
    return payload;
  };
  const renderPreferences = (result) => {
    if (!preferencesTarget) return;
    const entries = Object.entries(result.preferences || {});
    preferencesTarget.replaceChildren();
    if (!entries.length) {
      preferencesTarget.textContent = "No personal Watchlist preferences are stored yet.";
      return;
    }
    const list = document.createElement("ul");
    entries.forEach(([key, value]) => {
      const item = document.createElement("li");
      const label = document.createElement("strong");
      label.textContent = key.replaceAll("_", " ");
      item.append(label, `: ${Array.isArray(value) ? value.join(", ") : value}`);
      list.append(item);
    });
    const evidence = document.createElement("small");
    evidence.textContent = `Derived from ${result.evidence?.strategy_versions_reviewed || 0} saved Watchlist versions.`;
    preferencesTarget.append(list, evidence);
  };
  if (preferencesTarget) {
    preferenceRequest().then(renderPreferences).catch((error) => {
      preferencesTarget.textContent = error.message;
      preferencesTarget.classList.add("notice-error");
    });
  }
  preferenceButton?.addEventListener("click", async () => {
    if (!window.confirm("Clear the Watchlist preferences derived for this account?")) return;
    try {
      await preferenceRequest({ method: "DELETE" });
      if (preferencesTarget) preferencesTarget.textContent = "Your personal Watchlist preferences were cleared.";
    } catch (error) {
      if (preferencesTarget) preferencesTarget.textContent = error.message;
    }
  });
});
