document.addEventListener("DOMContentLoaded", () => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The side menu is not opened, closed or minimized here any more. It has one owner,
  // `hm-shell.js`. This file and `dashboard.js` each used to run their own copy of that
  // code against the same `sidebar-collapsed` class on `<body>`, from two different
  // stored values, so whichever happened to run last decided what a person saw and the
  // menu appeared to forget the state they had chosen.

  const brandedSelects = [...document.querySelectorAll("[data-hm-select]")];
  const closeBrandedSelect = (wrapper, restoreFocus = false) => {
    const trigger = wrapper.querySelector("[data-hm-select-trigger]");
    const menu = wrapper.querySelector("[data-hm-select-menu]");
    if (!trigger || !menu || menu.hidden) return;
    menu.hidden = true;
    wrapper.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  };
  brandedSelects.forEach((wrapper, index) => {
    const select = wrapper.querySelector("select");
    if (!select || wrapper.dataset.hmSelectReady === "true") return;
    wrapper.dataset.hmSelectReady = "true";

    const fieldLabel = wrapper.closest(".field")?.querySelector(":scope > span")
      ?.textContent?.trim() || "Select option";
    const trigger = document.createElement("button");
    const selectedIcon = document.createElement("img");
    const selectedLabel = document.createElement("span");
    const chevron = document.createElement("span");
    const menu = document.createElement("span");
    const menuId = `hm-select-menu-${index}`;
    const optionElements = [];

    trigger.className = "hm-select-trigger";
    trigger.dataset.hmSelectTrigger = "";
    trigger.type = "button";
    trigger.setAttribute("tabindex", select.disabled ? "-1" : "0");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", menuId);
    selectedIcon.className = "hm-select-brand-icon";
    selectedIcon.alt = "";
    selectedIcon.hidden = true;
    selectedLabel.className = "hm-select-value";
    selectedLabel.dataset.hmSelectValue = "";
    chevron.className = "hm-select-chevron";
    trigger.append(selectedIcon, selectedLabel, chevron);

    menu.id = menuId;
    menu.className = "hm-select-menu";
    menu.dataset.hmSelectMenu = "";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    const sync = () => {
      const selected = select.options[select.selectedIndex];
      selectedLabel.textContent = selected?.textContent?.trim() || "Choose";
      const selectedIconSource = selected?.dataset.hmSelectIcon || "";
      selectedIcon.hidden = !selectedIconSource;
      selectedIcon.src = selectedIconSource;
      trigger.classList.toggle("has-brand-icon", Boolean(selectedIconSource));
      trigger.setAttribute(
        "aria-label",
        `${fieldLabel}: ${selectedLabel.textContent}`,
      );
      trigger.setAttribute("aria-disabled", String(select.disabled));
      trigger.setAttribute("tabindex", select.disabled ? "-1" : "0");
      optionElements.forEach((option) => {
        const active = option.dataset.value === select.value;
        option.classList.toggle("is-selected", active);
        option.setAttribute("aria-selected", String(active));
      });
    };
    const focusOption = (position) => {
      const enabled = optionElements.filter(
        (option) => option.getAttribute("aria-disabled") !== "true",
      );
      if (!enabled.length) return;
      const current = enabled.indexOf(document.activeElement);
      const next = position === "first"
        ? 0
        : position === "last"
          ? enabled.length - 1
          : (current + position + enabled.length) % enabled.length;
      enabled[next].focus();
    };
    const open = (direction = 1) => {
      if (select.disabled) return;
      brandedSelects.forEach((candidate) => {
        if (candidate !== wrapper) closeBrandedSelect(candidate);
      });
      menu.hidden = false;
      wrapper.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      const selected = optionElements.find((option) => option.classList.contains("is-selected"));
      window.requestAnimationFrame(() => {
        (selected || optionElements[direction < 0 ? optionElements.length - 1 : 0])?.focus();
      });
    };

    [...select.options].forEach((nativeOption) => {
      const option = document.createElement("span");
      const optionIcon = document.createElement("img");
      const optionLabel = document.createElement("span");
      option.className = "hm-select-option";
      option.dataset.value = nativeOption.value;
      optionIcon.className = "hm-select-brand-icon";
      optionIcon.alt = "";
      optionIcon.hidden = !nativeOption.dataset.hmSelectIcon;
      optionIcon.src = nativeOption.dataset.hmSelectIcon || "";
      optionLabel.textContent = nativeOption.textContent;
      option.append(optionIcon, optionLabel);
      option.setAttribute("role", "option");
      option.setAttribute("tabindex", "-1");
      option.setAttribute("aria-disabled", String(nativeOption.disabled));
      option.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (nativeOption.disabled) return;
        const changed = select.value !== nativeOption.value;
        select.value = nativeOption.value;
        sync();
        closeBrandedSelect(wrapper, true);
        if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      option.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          focusOption(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          focusOption(event.key === "Home" ? "first" : "last");
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          option.click();
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeBrandedSelect(wrapper, true);
        }
      });
      optionElements.push(option);
      menu.append(option);
    });

    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (menu.hidden) open();
      else closeBrandedSelect(wrapper);
    });
    trigger.addEventListener("keydown", (event) => {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        open(event.key === "ArrowUp" ? -1 : 1);
      } else if (event.key === "Escape") {
        closeBrandedSelect(wrapper);
      }
    });
    select.addEventListener("change", sync);
    select.classList.add("hm-select-native-enhanced");
    wrapper.classList.add("is-enhanced");
    wrapper.append(trigger, menu);
    sync();
  });
  document.addEventListener("click", (event) => {
    brandedSelects.forEach((wrapper) => {
      if (!wrapper.contains(event.target)) closeBrandedSelect(wrapper);
    });
  });

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

  const removalErrorMessage = (payload, fallback) => {
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    return detail?.message || payload?.message || fallback;
  };

  const savedAssetsDialog = document.querySelector("[data-saved-assets-dialog]");
  const savedAssetsSave = savedAssetsDialog?.querySelector("[data-saved-assets-save]");
  const savedAssetsFeedback = savedAssetsDialog?.querySelector("[data-saved-assets-feedback]");
  const savedAssetRows = () => [...(savedAssetsDialog?.querySelectorAll("[data-saved-asset]") || [])];

  const markedSavedAssets = () => savedAssetRows().filter((row) => (
    row.querySelector("[data-saved-asset-mark]")?.getAttribute("aria-checked") === "true"
  ));

  const syncSavedAssetActions = () => {
    if (savedAssetsSave) savedAssetsSave.hidden = markedSavedAssets().length === 0;
  };

  const resetSavedAssetChanges = () => {
    savedAssetRows().forEach((row) => {
      const mark = row.querySelector("[data-saved-asset-mark]");
      const label = row.querySelector("[data-saved-asset-mark-label]");
      const impact = row.querySelector("[data-saved-asset-impact]");
      mark?.setAttribute("aria-checked", "false");
      mark?.classList.remove("is-marked");
      if (label) label.textContent = "Mark to remove";
      if (impact) {
        impact.hidden = true;
        impact.replaceChildren();
      }
    });
    if (savedAssetsFeedback) {
      savedAssetsFeedback.hidden = true;
      savedAssetsFeedback.classList.remove("notice-error");
      savedAssetsFeedback.replaceChildren();
    }
    syncSavedAssetActions();
  };

  const closeSavedAssets = () => {
    resetSavedAssetChanges();
    savedAssetsDialog?.close();
  };

  const openSavedAssets = () => {
    resetSavedAssetChanges();
    savedAssetsDialog?.showModal();
  };

  document.querySelectorAll("[data-saved-assets-open]").forEach((button) => {
    button.addEventListener("click", openSavedAssets);
  });
  savedAssetsDialog?.querySelectorAll("[data-saved-assets-close], [data-saved-assets-cancel]")
    .forEach((button) => button.addEventListener("click", closeSavedAssets));
  savedAssetsDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeSavedAssets();
  });

  savedAssetRows().forEach((row) => {
    const mark = row.querySelector("[data-saved-asset-mark]");
    const label = row.querySelector("[data-saved-asset-mark-label]");
    const impact = row.querySelector("[data-saved-asset-impact]");
    mark?.addEventListener("click", async () => {
      const shouldRemove = mark.getAttribute("aria-checked") !== "true";
      mark.setAttribute("aria-checked", String(shouldRemove));
      mark.classList.toggle("is-marked", shouldRemove);
      if (label) label.textContent = shouldRemove ? "Will remove" : "Mark to remove";
      if (!shouldRemove) {
        if (impact) impact.hidden = true;
        syncSavedAssetActions();
        return;
      }
      syncSavedAssetActions();
      if (!impact) return;
      impact.hidden = false;
      impact.textContent = "Checking affected Watchlists...";
      const watchlistId = row.dataset.watchlistId || "";
      const asset = row.dataset.asset || "";
      try {
        const response = await fetch(
          `/api/v1/sharia/watchlists/${encodeURIComponent(watchlistId)}/assets/${encodeURIComponent(asset)}/removal-impact`,
          { credentials: "same-origin", headers: { Accept: "application/json" } },
        );
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(removalErrorMessage(payload, "Impact unavailable."));
        const plans = payload.affected_watch_plans || [];
        impact.replaceChildren();
        const summary = document.createElement("strong");
        summary.textContent = plans.length
          ? `${plans.length} Watchlist${plans.length === 1 ? " uses" : "s use"} this asset`
          : "No Watchlists use this asset";
        impact.append(summary);
        plans.slice(0, 5).forEach((plan) => {
          const item = document.createElement("span");
          item.textContent = `${plan.name} | Version ${plan.strategy_version_number}`;
          impact.append(item);
        });
      } catch (error) {
        impact.textContent = error.message || "The removal impact could not be checked.";
        mark.setAttribute("aria-checked", "false");
        mark.classList.remove("is-marked");
        if (label) label.textContent = "Mark to remove";
        syncSavedAssetActions();
      }
    });
  });

  savedAssetsSave?.addEventListener("click", async () => {
    const rows = markedSavedAssets();
    if (!rows.length) return;
    savedAssetsSave.disabled = true;
    if (savedAssetsFeedback) {
      savedAssetsFeedback.hidden = false;
      savedAssetsFeedback.textContent = `Removing ${rows.length} saved asset${rows.length === 1 ? "" : "s"}...`;
    }
    const failures = [];
    for (const row of rows) {
      const watchlistId = row.dataset.watchlistId || "";
      const asset = row.dataset.asset || "";
      try {
        const response = await fetch(
          `/api/v1/sharia/watchlists/${encodeURIComponent(watchlistId)}/assets/${encodeURIComponent(asset)}?confirmed=true`,
          {
            method: "DELETE",
            credentials: "same-origin",
            headers: { "X-CSRF-Token": document.body.dataset.csrfToken || "" },
          },
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(removalErrorMessage(payload, "Removal failed."));
        }
        row.remove();
      } catch (error) {
        failures.push(`${asset}: ${error.message || "Removal failed."}`);
      }
    }
    savedAssetsSave.disabled = false;
    if (failures.length) {
      if (savedAssetsFeedback) {
        savedAssetsFeedback.classList.remove("is-info");
        savedAssetsFeedback.classList.add("notice-error");
        savedAssetsFeedback.textContent = failures.join(" ");
      }
      syncSavedAssetActions();
      return;
    }
    savedAssetsDialog?.close();
  });

  if (savedAssetsDialog?.dataset.openOnLoad === "true") openSavedAssets();

  const evidenceDialog = document.querySelector("[data-evidence-dialog]");
  const evidenceTitle = evidenceDialog?.querySelector("[data-evidence-dialog-title]");
  const evidenceContent = evidenceDialog?.querySelector("[data-evidence-dialog-content]");
  let evidenceTrigger = null;

  const readableEvidence = (value) => String(value || "Not recorded")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
  const evidenceValue = (value) => {
    if (value === null || value === undefined || value === "") return "Not recorded";
    if (typeof value === "object") {
      if ("value" in value) return evidenceValue(value.value);
      if ("current" in value) return evidenceValue(value.current);
      return "Stored in the full evidence receipt";
    }
    return String(value);
  };
  const evidenceBlock = (heading, body, className = "") => {
    const section = document.createElement("section");
    section.className = `evidence-summary-block ${className}`.trim();
    const title = document.createElement("h3");
    title.textContent = heading;
    section.append(title, body);
    return section;
  };
  const evidenceFacts = (facts) => {
    const grid = document.createElement("div");
    grid.className = "evidence-fact-grid";
    facts.filter((item) => item.value !== null && item.value !== undefined && item.value !== "")
      .forEach((item) => {
        const fact = document.createElement("div");
        const label = document.createElement("small");
        const value = document.createElement("strong");
        label.textContent = item.label;
        value.textContent = evidenceValue(item.value);
        fact.append(label, value);
        grid.append(fact);
      });
    return grid;
  };
  const closeEvidenceDialog = () => {
    evidenceDialog?.close();
    evidenceTrigger?.focus?.();
    evidenceTrigger = null;
  };
  evidenceDialog?.querySelectorAll("[data-evidence-dialog-close]")
    .forEach((button) => button.addEventListener("click", closeEvidenceDialog));
  evidenceDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEvidenceDialog();
  });

  document.querySelectorAll("[data-evidence-dialog-open]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!evidenceDialog || !evidenceContent) return;
      evidenceTrigger = button;
      if (evidenceTitle) evidenceTitle.textContent = button.dataset.evidenceTitle || "Evidence details";
      evidenceContent.replaceChildren();
      const summary = document.createElement("p");
      summary.textContent = button.dataset.evidenceSummary || "Stored evidence for this update.";
      evidenceContent.append(evidenceBlock("What happened", summary));
      const metadata = evidenceFacts([
        { label: "Watchlist", value: button.dataset.evidenceMonitor },
        { label: "Recorded", value: button.dataset.evidenceTime },
      ]);
      if (metadata.childElementCount) evidenceContent.append(metadata);
      const loading = document.createElement("p");
      loading.className = "muted";
      loading.textContent = "Loading the retained evidence...";
      evidenceContent.append(loading);
      evidenceDialog.showModal();
      try {
        const type = button.dataset.evidenceType;
        const id = button.dataset.evidenceId;
        if (type === "compliance_change") {
          const response = await fetch(`/api/v1/dashboard/compliance-notifications/${encodeURIComponent(id)}/difference`, { credentials: "same-origin", headers: { Accept: "application/json" } });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.detail || "Evidence difference is unavailable.");
          loading.remove();
          evidenceContent.append(evidenceBlock("Decision difference", evidenceFacts([
            { label: "Asset", value: payload.asset },
            { label: "Before", value: readableEvidence(payload.previous_status) },
            { label: "Now", value: readableEvidence(payload.current_status) },
            { label: "Methodology version", value: payload.methodology_version },
            { label: "Watchlist action", value: readableEvidence(payload.watch_plan_action) },
            { label: "Review state", value: readableEvidence(payload.review_state) },
          ]), "is-change"));
          const changes = document.createElement("div");
          changes.className = "evidence-change-list";
          (payload.evidence_changes || []).forEach((item) => {
            const row = document.createElement("div");
            const label = document.createElement("strong");
            const detail = document.createElement("span");
            label.textContent = item.label;
            detail.textContent = item.detail;
            row.append(label, detail);
            changes.append(row);
          });
          if (!changes.childElementCount) {
            const note = document.createElement("p");
            note.textContent = payload.evidence_available
              ? "The reviewed record changed the status, but no compact field-level comparison was retained."
              : "The exact source difference is unavailable; no values were inferred.";
            changes.append(note);
          }
          evidenceContent.append(evidenceBlock("Evidence that changed", changes));
        } else if (type === "alert") {
          const response = await fetch(`/api/v1/dashboard/cockpit/alerts/${encodeURIComponent(id)}/proof`, { credentials: "same-origin", headers: { Accept: "application/json" } });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.detail || "Alert proof is unavailable.");
          loading.remove();
          const proof = payload.proof_receipt || {};
          evidenceContent.append(evidenceBlock("Proof receipt", evidenceFacts([
            { label: "Symbol", value: proof.symbol },
            { label: "Exchange", value: proof.exchange },
            { label: "Timeframe", value: proof.timeframe },
            { label: "Strategy version", value: payload.strategy_version?.number },
            { label: "Candle time", value: proof.candle_timestamp || proof.timestamp },
            { label: "Integrity", value: payload.proof_integrity?.verified ? "Verified" : "Unavailable" },
          ])));
          const conditionList = document.createElement("div");
          conditionList.className = "evidence-condition-list";
          (proof.conditions || []).slice(0, 8).forEach((condition) => {
            const row = document.createElement("div");
            const copy = document.createElement("span");
            const name = document.createElement("strong");
            const values = document.createElement("small");
            const state = document.createElement("b");
            name.textContent = condition.name || condition.condition_id || "Condition";
            values.textContent = `Actual: ${evidenceValue(condition.actual_value)} | Required: ${evidenceValue(condition.required_value)}`;
            state.textContent = readableEvidence(condition.state || condition.outcome);
            copy.append(name, values);
            row.append(copy, state);
            conditionList.append(row);
          });
          if (conditionList.childElementCount) evidenceContent.append(evidenceBlock("Rule checks", conditionList));
        } else {
          loading.textContent = "This summary is the closest retained evidence for the selected record. Exact historical values are shown only when they were stored.";
        }
      } catch (error) {
        loading.classList.add("notice", "notice-error");
        loading.textContent = error.message || "Evidence could not be loaded. No values were guessed.";
      }
    });
  });

  const notificationCenter = document.querySelector("[data-notification-center]");
  const notificationTrigger = notificationCenter?.querySelector("[data-notification-center-trigger]");
  const notificationPopover = notificationCenter?.querySelector("[data-notification-center-popover]");
  const notificationList = notificationCenter?.querySelector("[data-notification-center-list]");
  const notificationCount = notificationCenter?.querySelector("[data-notification-count]");
  let notificationCenterLoaded = false;

  const notificationIcon = (kind) => ({
    alert: "bell",
    compliance: "compliance",
    billing: "billing",
    integration: "telegram",
    system: "info",
  })[kind] || "bell";
  const notificationTime = (value) => {
    if (!value) return "Time unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Time unavailable" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  };
  const renderNotificationCenter = (items) => {
    if (!notificationList) return;
    notificationList.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "notification-center-empty";
      empty.textContent = "No notifications have been recorded yet.";
      notificationList.append(empty);
      return;
    }
    items.forEach((item) => {
      const entry = document.createElement(item.action_url ? "a" : "article");
      entry.className = `notification-center-item kind-${item.kind || "system"}`;
      if (item.action_url) entry.href = item.action_url;
      const icon = document.createElement("span");
      icon.className = "icon-box";
      icon.innerHTML = window.icon?.(notificationIcon(item.kind), "icon") || "";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      const body = document.createElement("small");
      const time = document.createElement("time");
      title.textContent = item.title || "Dashboard update";
      body.textContent = item.body || "A new update was recorded.";
      time.textContent = notificationTime(item.created_at);
      copy.append(title, body, time);
      entry.append(icon, copy);
      notificationList.append(entry);
    });
  };
  const loadNotificationCenter = async () => {
    if (!notificationList) return;
    try {
      const response = await fetch("/api/v1/dashboard/notifications/center", { credentials: "same-origin", headers: { Accept: "application/json" } });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Notifications are unavailable.");
      renderNotificationCenter(payload.items || []);
      notificationCenterLoaded = true;
      await fetch("/api/v1/dashboard/notifications/center/read", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": document.body.dataset.csrfToken || "" },
      }).catch(() => null);
      if (notificationCount) notificationCount.hidden = true;
    } catch (error) {
      notificationList.textContent = error.message || "Notifications could not be loaded.";
    }
  };
  const closeNotificationCenter = () => {
    if (!notificationPopover || !notificationTrigger) return;
    notificationPopover.hidden = true;
    notificationTrigger.setAttribute("aria-expanded", "false");
  };
  notificationTrigger?.addEventListener("click", () => {
    const opening = notificationPopover?.hidden;
    if (!notificationPopover) return;
    notificationPopover.hidden = !opening;
    notificationTrigger.setAttribute("aria-expanded", String(opening));
    if (opening && !notificationCenterLoaded) void loadNotificationCenter();
    if (opening && notificationCount) notificationCount.hidden = true;
  });
  document.addEventListener("click", (event) => {
    if (notificationCenter && !notificationCenter.contains(event.target)) closeNotificationCenter();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && notificationPopover && !notificationPopover.hidden) {
      closeNotificationCenter();
      notificationTrigger?.focus();
    }
  });

  const dashboardNotificationEnabled = document.body.dataset.dashboardNotifications !== "false";
  const formingNotificationEnabled = document.body.dataset.formingNotifications === "true";
  let audioContext = null;
  const playDashboardSound = (soundName) => {
    if (!soundName || soundName === "none") return;
    try {
      audioContext ||= new (window.AudioContext || window.webkitAudioContext)();
      const patterns = {
        chime: [[660, 0, .09], [880, .11, .12]],
        pulse: [[520, 0, .12]],
        bell: [[784, 0, .1], [1046, .1, .16]],
        soft: [[440, 0, .1]],
      };
      (patterns[soundName] || patterns.chime).forEach(([frequency, delay, duration]) => {
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.frequency.value = frequency;
        oscillator.type = "sine";
        gain.gain.setValueAtTime(0.0001, audioContext.currentTime + delay);
        gain.gain.exponentialRampToValueAtTime(0.055, audioContext.currentTime + delay + .01);
        gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + delay + duration);
        oscillator.connect(gain).connect(audioContext.destination);
        oscillator.start(audioContext.currentTime + delay);
        oscillator.stop(audioContext.currentTime + delay + duration + .02);
      });
    } catch (_error) {
      // Browser autoplay policy may suppress sound; visual delivery remains intact.
    }
  };
  /* The one player, reachable by name.
   *
   * The Settings page lets somebody hear a sound before choosing it, and a preview that
   * played a *different* tone from the real notice would be a lie told in the one place
   * a person goes to check. There is one pattern table and one player: this one. */
  window.hmPlayAlertSound = playDashboardSound;
  let notificationStack = notificationCenter
    ? document.getElementById("web-notification-stack")
    : null;
  if (notificationCenter && !notificationStack) {
    notificationStack = document.createElement("div");
    notificationStack.id = "web-notification-stack";
    notificationStack.className = "web-notification-stack";
    document.body.append(notificationStack);
  }
  const showDashboardNotification = (item) => {
    if (!notificationStack) return;
    const completionRate = Number(item.completion_rate);
    const forming = ["forming", "near_miss"].includes(item.alert_type)
      || (item.alert_type === "lifecycle" && (!Number.isFinite(completionRate) || completionRate < 100));
    const confirmed = item.alert_type === "confirmed"
      || (Number.isFinite(completionRate) && completionRate >= 100);
    if (!forming && !confirmed) return;
    if (forming && !formingNotificationEnabled) return;
    if (confirmed && !dashboardNotificationEnabled) return;
    const card = document.createElement("article");
    card.className = `web-notification-popup ${forming ? "is-forming" : "is-confirmed"}`;
    const icon = document.createElement("span");
    icon.className = "icon-box";
    icon.innerHTML = window.icon?.(forming ? "activity" : "bell", "icon") || "";
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const detail = document.createElement("small");
    const dismiss = document.createElement("button");
    title.textContent = item.title || "Watchlist update";
    detail.textContent = item.body || item.symbol || "Open Opportunities & Evidence for details.";
    dismiss.type = "button";
    dismiss.setAttribute("aria-label", "Dismiss notification");
    dismiss.innerHTML = window.icon?.("close", "icon-sm") || "Close";
    copy.append(title, detail);
    card.append(icon, copy, dismiss);
    const remove = () => {
      card.classList.add("is-leaving");
      window.setTimeout(() => card.remove(), reducedMotion ? 0 : 240);
    };
    dismiss.addEventListener("click", remove);
    notificationStack.prepend(card);
    requestAnimationFrame(() => card.classList.add("is-visible"));
    window.setTimeout(remove, 10000);
    playDashboardSound(forming
      ? document.body.dataset.formingNotificationSound
      : document.body.dataset.dashboardNotificationSound);
  };
  const pollDashboardNotifications = async () => {
    if (!notificationCenter || document.hidden || (!dashboardNotificationEnabled && !formingNotificationEnabled)) return;
    try {
      const response = await fetch("/api/v1/dashboard/notifications/web?limit=10", { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const payload = await response.json();
      (payload.items || []).reverse().forEach(showDashboardNotification);
      if ((payload.items || []).length) {
        notificationCenterLoaded = false;
        if (notificationCount) {
          notificationCount.textContent = String((payload.items || []).length);
          notificationCount.hidden = false;
        }
      }
    } catch (_error) {
      // Notification polling never interrupts dashboard work.
    }
  };
  void pollDashboardNotifications();
  window.setInterval(pollDashboardNotifications, 15000);
});
