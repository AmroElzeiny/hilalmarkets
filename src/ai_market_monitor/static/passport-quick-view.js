(() => {
  const dialog = document.querySelector("[data-passport-quick-dialog]");
  if (!dialog) return;

  const loading = dialog.querySelector("[data-passport-quick-loading]");
  const content = dialog.querySelector("[data-passport-quick-content]");
  const errorBox = dialog.querySelector("[data-passport-quick-error]");
  const errorMessage = dialog.querySelector("[data-passport-quick-error-message]");
  let opener = null;
  let activeRequest = null;
  let activeOptions = null;
  let evidenceReference = "";
  let contractAddress = "";

  const text = (selector, value) => {
    const node = dialog.querySelector(selector);
    if (node) node.textContent = value == null || value === "" ? "--" : String(value);
  };

  const formatDate = (value, includeTime = false) => {
    if (!value) return "Not recorded";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "Not recorded";
    return new Intl.DateTimeFormat("en", includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" }).format(parsed);
  };

  const label = (value) => String(value || "unavailable")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

  function setState(state) {
    loading.hidden = state !== "loading";
    content.hidden = state !== "ready";
    errorBox.hidden = state !== "error";
  }

  function statusClass(status) {
    if (["eligible", "eligible_with_qualifications", "approved", "approved_with_qualifications"].includes(status)) return "badge badge-eligible";
    if (["under_review", "disputed"].includes(status)) return "badge badge-review";
    if (["excluded", "rejected"].includes(status)) return "badge badge-excluded";
    return "badge badge-neutral";
  }

  function coverageClass(status) {
    if (status === "covered") return "passport-use-status is-covered";
    if (["qualified", "covered_with_qualification"].includes(status)) return "passport-use-status is-qualified";
    if (["excluded", "not_covered", "not_covered_by_this_decision"].includes(status)) return "passport-use-status is-not-covered";
    return "passport-use-status";
  }

  function endpointFor(options) {
    const parameters = new URLSearchParams();
    if (options.methodologyId) parameters.set("methodology", options.methodologyId);
    if (options.passportVersionId) parameters.set("passport_version_id", options.passportVersionId);
    if (options.canonicalAssetId) parameters.set("canonical_asset_id", options.canonicalAssetId);
    if (options.eventTime) parameters.set("event_time", options.eventTime);
    const query = parameters.toString();
    return `/api/v1/sharia/assets/${encodeURIComponent(options.asset)}/passport/quick-view${query ? `?${query}` : ""}`;
  }

  function render(payload) {
    const identity = payload.identity;
    const assessment = payload.assessment;
    const historical = payload.historical || {};
    const symbol = identity.symbol || assessment.canonical_asset;
    const kind = identity.native_asset === true ? "Native asset" : identity.native_asset === false ? "Token" : label(identity.asset_type);
    const network = identity.network || "Network not recorded";
    text("[data-passport-quick-logo]", symbol.slice(0, 3));
    text("[data-passport-quick-name]", `${identity.name} · ${symbol}`);
    text("[data-passport-quick-identity]", `${network} · ${kind}`);
    text("[data-passport-quick-primary]", payload.primary_wording);
    text("[data-passport-quick-methodology]", `${assessment.methodology_name} · v${assessment.methodology_version}`);
    text("[data-passport-quick-authority]", payload.review_authority);
    text("[data-passport-quick-decision]", formatDate(payload.decision_date));
    text("[data-passport-quick-publication]", formatDate(payload.publication_date));
    text("[data-passport-quick-next-review]", payload.next_review_at ? formatDate(payload.next_review_at) : "Not scheduled");

    const status = dialog.querySelector("[data-passport-quick-status]");
    status.textContent = assessment.status_label;
    status.className = statusClass(assessment.status);
    text("[data-passport-quick-freshness]", `Evidence: ${label(payload.freshness)}`);

    const contracts = Object.entries(identity.contract_addresses || {});
    const contractRow = dialog.querySelector("[data-passport-contract-row]");
    contractAddress = contracts[0]?.[1] || "";
    contractRow.hidden = !contractAddress;
    if (contractAddress) {
      const short = contractAddress.length > 18
        ? `${contractAddress.slice(0, 9)}…${contractAddress.slice(-7)}`
        : contractAddress;
      text("[data-passport-contract-address]", `${contracts[0][0]} · ${short}`);
    }

    const reasons = dialog.querySelector("[data-passport-quick-reasons]");
    reasons.replaceChildren();
    (payload.main_reasons || []).slice(0, 4).forEach((reason) => {
      const item = document.createElement("li");
      item.innerHTML = window.icon("check", "icon-sm");
      const copy = document.createElement("span");
      copy.textContent = reason;
      item.append(copy);
      reasons.append(item);
    });
    if (!reasons.children.length) {
      const item = document.createElement("li");
      item.textContent = "No plain-language reason was retained for this version.";
      reasons.append(item);
    }

    const qualification = dialog.querySelector("[data-passport-quick-qualification]");
    qualification.hidden = !payload.main_qualification;
    qualification.querySelector("p").textContent = payload.main_qualification || "";

    const uses = dialog.querySelector("[data-passport-quick-uses]");
    uses.replaceChildren();
    (payload.use_coverage || []).forEach((use) => {
      const row = document.createElement("div");
      row.className = "passport-use-row";
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = use.label;
      const reason = document.createElement("small");
      reason.textContent = use.reason;
      copy.append(title, reason);
      const state = document.createElement("span");
      state.className = coverageClass(use.status);
      state.textContent = label(use.status);
      row.append(copy, state);
      uses.append(row);
    });

    const historyNotice = dialog.querySelector("[data-passport-history-notice]");
    historyNotice.hidden = !historical.is_historical;
    if (historical.is_historical) {
      text("[data-passport-history-copy]", `This is Passport version ${historical.passport_version} used when the event was evaluated${historical.event_time ? ` on ${formatDate(historical.event_time, true)}` : ""}.`);
      text("[data-passport-current-copy]", historical.current_status
        ? `Current asset status: ${label(historical.current_status)}, reviewed ${formatDate(historical.current_reviewed_at)}.`
        : "A current published assessment is not available.");
    }

    const restriction = dialog.querySelector("[data-passport-quick-restriction]");
    restriction.hidden = !payload.restriction_explanation;
    restriction.querySelector("p").textContent = payload.restriction_explanation || "";

    const fullPage = dialog.querySelector("[data-passport-full-page]");
    const allUses = dialog.querySelector("[data-passport-view-all-uses]");
    fullPage.href = payload.full_passport_url;
    allUses.href = `${payload.full_passport_url}#use-cases`;
    const source = dialog.querySelector("[data-passport-official-source]");
    source.hidden = !payload.official_source_url;
    if (payload.official_source_url) source.href = payload.official_source_url;
    const createPlan = dialog.querySelector("[data-passport-create-plan]");
    createPlan.hidden = !payload.can_create_watch_plan;
    createPlan.href = `/dashboard/strategies/new?asset=${encodeURIComponent(assessment.canonical_asset)}`;
    const addWatchlist = dialog.querySelector("[data-passport-add-watchlist]");
    addWatchlist.hidden = !payload.watchlist_action_url;
    if (payload.watchlist_action_url) addWatchlist.action = payload.watchlist_action_url;
    const watchlistMethodology = dialog.querySelector("[data-passport-watchlist-methodology]");
    watchlistMethodology.value = assessment.methodology_id || "";
    const complianceChange = dialog.querySelector("[data-passport-compliance-change]");
    complianceChange.hidden = !payload.compliance_change_url;
    if (payload.compliance_change_url) complianceChange.href = payload.compliance_change_url;
    evidenceReference = payload.evidence_reference || "";
    setState("ready");
  }

  async function load(options) {
    activeOptions = options;
    activeRequest?.abort();
    activeRequest = new AbortController();
    setState("loading");
    try {
      const response = await fetch(endpointFor(options), {
        credentials: "same-origin",
        cache: "no-store",
        signal: activeRequest.signal,
        headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(typeof detail === "string" ? detail : detail?.message || "The stored Passport could not be loaded.");
      }
      render(payload);
    } catch (error) {
      if (error.name === "AbortError") return;
      errorMessage.textContent = error.message || "The stored Passport could not be loaded. No evidence was invented.";
      setState("error");
    }
  }

  function open(options, trigger = null) {
    if (!options?.asset) return;
    opener = trigger || document.activeElement;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    load(options);
  }

  function close() {
    activeRequest?.abort();
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  async function copy(value, successMessage) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      window.showDashToast?.(successMessage);
    } catch (_error) {
      window.showDashToast?.("Copy failed. Select the value and copy it manually.", true);
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-passport-quick-view]");
    if (!trigger) return;
    event.preventDefault();
    open({
      asset: trigger.dataset.passportAsset,
      methodologyId: trigger.dataset.passportMethodologyId,
      passportVersionId: trigger.dataset.passportVersionId,
      canonicalAssetId: trigger.dataset.passportCanonicalAssetId,
      eventTime: trigger.dataset.passportEventTime,
    }, trigger);
  });

  dialog.querySelector("[data-passport-quick-retry]").addEventListener("click", () => {
    if (activeOptions) load(activeOptions);
  });
  dialog.querySelector("[data-passport-copy-reference]").addEventListener("click", () => copy(evidenceReference, "Evidence reference copied."));
  dialog.querySelector("[data-passport-copy-contract]").addEventListener("click", () => copy(contractAddress, "Contract address copied."));
  dialog.querySelectorAll("[data-passport-quick-close]").forEach((button) => button.addEventListener("click", close));
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => {
    opener?.focus?.();
    opener = null;
  });

  window.HilalPassportQuickView = { open, close };
})();
