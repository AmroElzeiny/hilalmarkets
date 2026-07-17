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
      preferencesTarget.textContent = "No personal Watch Plan preferences are stored yet.";
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
    evidence.textContent = `Derived from ${result.evidence?.strategy_versions_reviewed || 0} saved Watch Plan versions.`;
    preferencesTarget.append(list, evidence);
  };
  if (preferencesTarget) {
    preferenceRequest().then(renderPreferences).catch((error) => {
      preferencesTarget.textContent = error.message;
      preferencesTarget.classList.add("notice-error");
    });
  }
  preferenceButton?.addEventListener("click", async () => {
    if (!window.confirm("Clear the Watch Plan preferences derived for this account?")) return;
    try {
      await preferenceRequest({ method: "DELETE" });
      if (preferencesTarget) preferencesTarget.textContent = "Your personal Watch Plan preferences were cleared.";
    } catch (error) {
      if (preferencesTarget) preferencesTarget.textContent = error.message;
    }
  });

  const removalDialog = document.querySelector("[data-asset-removal-dialog]");
  const removalSummary = removalDialog?.querySelector("[data-asset-removal-summary]");
  const removalPlans = removalDialog?.querySelector("[data-asset-removal-plans]");
  const removalWarning = removalDialog?.querySelector(".asset-removal-warning");
  const removalError = removalDialog?.querySelector("[data-asset-removal-error]");
  const removalConfirm = removalDialog?.querySelector("[data-asset-removal-confirm]");
  let pendingAssetRemoval = null;

  const removalErrorMessage = (payload, fallback) => {
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    return detail?.message || payload?.message || fallback;
  };
  const closeRemovalDialog = () => {
    removalDialog?.close();
    pendingAssetRemoval?.button?.removeAttribute("disabled");
    pendingAssetRemoval = null;
  };
  removalDialog?.querySelectorAll("[data-asset-removal-close], [data-asset-removal-cancel]")
    .forEach((button) => button.addEventListener("click", closeRemovalDialog));
  removalDialog?.addEventListener("cancel", () => {
    pendingAssetRemoval?.button?.removeAttribute("disabled");
    pendingAssetRemoval = null;
  });

  document.querySelectorAll("[data-remove-saved-asset]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (pendingAssetRemoval) return;
      button.setAttribute("disabled", "disabled");
      const watchlistId = button.dataset.watchlistId || "";
      const asset = button.dataset.asset || "";
      const basePath = `/api/v1/sharia/watchlists/${encodeURIComponent(watchlistId)}/assets/${encodeURIComponent(asset)}`;
      try {
        const response = await fetch(`${basePath}/removal-impact`, {
          credentials: "same-origin",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(removalErrorMessage(payload, "The removal impact could not be loaded."));
        }
        pendingAssetRemoval = {
          asset,
          basePath,
          button,
          row: button.closest("[data-saved-asset-row]"),
        };
        if (removalSummary) {
          const count = payload.affected_watch_plans?.length || 0;
          removalSummary.textContent = count
            ? `${asset} is currently selected by ${count} Watch Plan${count === 1 ? "" : "s"}.`
            : `${asset} is not selected by a Watch Plan.`;
        }
        if (removalPlans) {
          removalPlans.replaceChildren();
          (payload.affected_watch_plans || []).forEach((plan) => {
            const item = document.createElement("div");
            item.className = "asset-removal-plan";
            const copy = document.createElement("div");
            const name = document.createElement("strong");
            const detail = document.createElement("small");
            const status = document.createElement("span");
            name.textContent = plan.name;
            detail.textContent = `Active version ${plan.strategy_version_number}`;
            status.className = "badge badge-soft";
            status.textContent = String(plan.status || "active").replaceAll("_", " ");
            copy.append(name, detail);
            item.append(copy, status);
            removalPlans.append(item);
          });
        }
        if (removalWarning) removalWarning.hidden = !payload.requires_confirmation;
        if (removalError) removalError.hidden = true;
        removalDialog?.showModal();
      } catch (error) {
        button.removeAttribute("disabled");
        const toast = document.getElementById("dash-toast");
        if (toast) {
          toast.textContent = error.message;
          toast.hidden = false;
          window.setTimeout(() => { toast.hidden = true; }, 4000);
        }
      }
    });
  });

  removalConfirm?.addEventListener("click", async () => {
    if (!pendingAssetRemoval) return;
    removalConfirm.setAttribute("disabled", "disabled");
    if (removalError) removalError.hidden = true;
    try {
      const response = await fetch(`${pendingAssetRemoval.basePath}?confirmed=true`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": document.body.dataset.csrfToken || "" },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(removalErrorMessage(payload, "The asset could not be removed."));
      }
      pendingAssetRemoval.row?.remove();
      closeRemovalDialog();
    } catch (error) {
      if (removalError) {
        removalError.textContent = error.message;
        removalError.hidden = false;
      }
    } finally {
      removalConfirm.removeAttribute("disabled");
    }
  });
});
