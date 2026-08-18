/* The two popups on the redesigned dashboard pages: Favorites, and the Passport quick view.
 *
 * Both use a real <dialog> opened with showModal(), which is what gives them focus
 * trapping, Escape-to-close and inert background content from the browser rather than
 * from hand-written key handlers that always miss a case. The part the browser does not
 * do — giving the keyboard back, closing on a backdrop click, animating — is
 * `hm-dialog.js`, shared with every other popup on this path.
 */

import { manageDialog, paintIcons } from "./hm-dialog.js";
import {
  assetTone,
  coverageTone,
  formatDate,
  humanize,
  loadCoinLogo,
  statusMeaning,
  statusText,
  toneIcon,
} from "./hm-market-vocabulary.js";

/* ── Favorites ─────────────────────────────────────────────────────────────── */

function setUpFavorites() {
  const dialog = document.querySelector("[data-favorites-dialog]");
  if (!dialog) return;
  const { open, close } = manageDialog(dialog);

  const rows = () => Array.from(dialog.querySelectorAll("[data-favorite-row]"));
  const summary = dialog.querySelector("[data-favorites-summary]");
  const saveButton = dialog.querySelector("[data-favorites-save]");
  const saveLabel = dialog.querySelector("[data-favorites-save-label]");
  const errorBox = dialog.querySelector("[data-favorites-error]");
  const errorMessage = dialog.querySelector("[data-favorites-error-message]");

  const marked = () => rows().filter((row) => row.dataset.marked === "true");

  function refreshFooter() {
    const count = marked().length;
    if (saveButton) saveButton.hidden = count === 0;
    if (saveLabel) saveLabel.textContent = count === 1 ? "Stop following 1 coin" : `Stop following ${count} coins`;
    if (summary) {
      summary.textContent = count === 0
        ? "Nothing marked yet."
        : `${count} ${count === 1 ? "coin" : "coins"} will stop sending you status messages.`;
    }
  }

  dialog.addEventListener("click", (event) => {
    const button = event.target.closest("[data-favorite-remove]");
    if (!button) return;
    const row = button.closest("[data-favorite-row]");
    const next = row.dataset.marked !== "true";
    row.dataset.marked = String(next);
    button.setAttribute("aria-pressed", String(next));
    button.querySelector("[data-favorite-remove-label]").textContent = next ? "Undo" : "Stop following";
    const icon = button.querySelector("[data-icon]");
    icon.dataset.icon = next ? "refresh" : "minus";
    icon.innerHTML = window.icon(icon.dataset.icon, "icon");
    refreshFooter();
  });

  dialog.querySelector("[data-favorites-close]")?.addEventListener("click", () => close());
  dialog.querySelector("[data-favorites-cancel]")?.addEventListener("click", () => {
    rows().forEach((row) => {
      row.dataset.marked = "false";
      const button = row.querySelector("[data-favorite-remove]");
      button.setAttribute("aria-pressed", "false");
      button.querySelector("[data-favorite-remove-label]").textContent = "Stop following";
      const icon = button.querySelector("[data-icon]");
      icon.dataset.icon = "minus";
      icon.innerHTML = window.icon("minus", "icon");
    });
    refreshFooter();
    close();
  });

  saveButton?.addEventListener("click", async () => {
    const targets = marked();
    if (!targets.length) return;
    saveButton.disabled = true;
    errorBox.hidden = true;
    const failed = [];
    for (const row of targets) {
      try {
        const response = await fetch(
          `/api/v1/sharia/watchlists/${encodeURIComponent(row.dataset.watchlistId)}/assets/${encodeURIComponent(row.dataset.asset)}?confirmed=true`,
          {
            method: "DELETE",
            credentials: "same-origin",
            headers: { "X-CSRF-Token": document.body.dataset.csrfToken || "" },
          },
        );
        if (!response.ok) throw new Error(row.dataset.asset);
        row.remove();
      } catch {
        failed.push(row.dataset.asset);
      }
    }
    saveButton.disabled = false;
    refreshFooter();

    if (failed.length) {
      errorBox.hidden = false;
      errorMessage.textContent = `${failed.join(", ")} could not be removed. Nothing else was changed. Please try again.`;
      return;
    }

    /* Tell the grid what is followed now, so the hearts and the counts agree without
       a page reload — one truth, published once. */
    const remaining = rows().map((row) => String(row.dataset.asset).toUpperCase());
    window.dispatchEvent(
      new CustomEvent("hilal:favorites-changed", { detail: { assets: remaining } }),
    );
    window.showDashToast?.(
      targets.length === 1
        ? "You stopped following 1 coin."
        : `You stopped following ${targets.length} coins.`,
    );
    close();
  });

  refreshFooter();

  window.HilalFavorites = {
    open: (_assets, _watchlistId, trigger) => open(trigger),
    close,
    sync: () => refreshFooter(),
  };
}

/* ── Passport quick view ───────────────────────────────────────────────────── */

function setUpQuickView() {
  const dialog = document.querySelector("[data-passport-dialog]");
  if (!dialog) return;
  const { open, close } = manageDialog(dialog);

  const find = (selector) => dialog.querySelector(selector);
  const loading = find("[data-pq-loading]");
  const errorBox = find("[data-pq-error]");
  const errorMessage = find("[data-pq-error-message]");
  const content = find("[data-pq-content]");

  let request = null;
  let controller = null;

  function showState(which) {
    loading.hidden = which !== "loading";
    errorBox.hidden = which !== "error";
    content.hidden = which !== "content";
  }

  async function load() {
    if (!request) return;
    showState("loading");
    controller?.abort();
    controller = new AbortController();
    const params = new URLSearchParams();
    if (request.methodologyId) params.set("methodology", request.methodologyId);
    try {
      const response = await fetch(
        `/api/v1/sharia/assets/${encodeURIComponent(request.asset)}/passport/quick-view?${params}`,
        {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(
          payload.detail?.message || payload.detail || `The evidence service answered ${response.status}.`,
        );
      }
      render(await response.json());
      showState("content");
    } catch (error) {
      if (error.name === "AbortError") return;
      errorMessage.textContent = `${error.message} No status or source was guessed.`;
      showState("error");
    }
  }

  function render(data) {
    const identity = data.identity || {};
    const assessment = data.assessment || {};
    const asset = identity.symbol || assessment.canonical_asset || request.asset;

    loadCoinLogo(find("[data-pq-logo]"), asset, identity);

    find("[data-pq-name]").textContent = identity.name || assessment.asset_name || asset;
    find("[data-pq-identity]").textContent = [
      asset,
      identity.network,
      identity.native_asset === true ? "Native coin" : identity.native_asset === false ? "Token" : null,
    ].filter(Boolean).join(" · ");

    const tone = assetTone(assessment.status);
    find("[data-pq-answer]").dataset.tone = tone;
    const status = find("[data-pq-status]");
    status.dataset.tone = tone;
    find("[data-pq-status-icon]").innerHTML = window.icon(toneIcon(tone), "icon-sm");
    find("[data-pq-status-text]").textContent = statusText(assessment);
    find("[data-pq-meaning]").textContent = data.primary_wording || statusMeaning(assessment);

    find("[data-pq-methodology]").textContent = assessment.methodology_name
      ? `${assessment.methodology_name}${assessment.methodology_version ? ` v${assessment.methodology_version}` : ""}`
      : "Not recorded";
    find("[data-pq-decision]").textContent = formatDate(data.decision_date);
    find("[data-pq-published]").textContent = formatDate(data.publication_date);
    find("[data-pq-next-scan]").textContent = data.next_source_scan_at
      ? formatDate(data.next_source_scan_at)
      : "Not scheduled";

    const reasons = find("[data-pq-reasons]");
    reasons.replaceChildren();
    const reasonList = Array.isArray(data.main_reasons) ? data.main_reasons.filter(Boolean) : [];
    if (!reasonList.length) {
      const item = document.createElement("li");
      item.textContent = "No short reason was kept with this record. The full Passport has the criterion-by-criterion detail.";
      reasons.append(item);
    } else {
      reasonList.forEach((reason) => {
        const item = document.createElement("li");
        item.innerHTML = `${window.icon("check", "icon-sm")}<span></span>`;
        item.querySelector("span").textContent = reason;
        reasons.append(item);
      });
    }

    const qualification = find("[data-pq-qualification]");
    qualification.hidden = !data.main_qualification;
    if (data.main_qualification) {
      find("[data-pq-qualification-text]").textContent = data.main_qualification;
    }

    const uses = find("[data-pq-uses]");
    uses.replaceChildren();
    const coverage = Array.isArray(data.use_coverage) ? data.use_coverage : [];
    if (!coverage.length) {
      const row = document.createElement("p");
      row.className = "t-foot-note";
      row.textContent = "No separate use decisions were kept for this record.";
      uses.append(row);
    } else {
      coverage.slice(0, 6).forEach((use) => {
        const row = document.createElement("div");
        row.className = "t-use";
        const useTone = coverageTone(use.status);
        row.innerHTML = `<span></span><span class="t-pill" data-tone="${useTone}">${window.icon(toneIcon(useTone), "icon-sm")}<span></span></span>`;
        row.children[0].textContent = use.label || humanize(use.status);
        row.querySelector(".t-pill span").textContent = humanize(use.status);
        uses.append(row);
      });
    }

    const more = find("[data-pq-more]");
    const references = Array.isArray(data.methodology_references) ? data.methodology_references : [];
    more.hidden = references.length < 2;
    if (references.length >= 2) {
      find("[data-pq-more-count]").textContent = `${references.length} standards`;
      const list = find("[data-pq-methodologies]");
      list.replaceChildren();
      references.forEach((reference) => {
        const row = document.createElement("div");
        row.className = "t-use";
        row.innerHTML = '<span></span><span class="t-pill" data-tone="neutral"><span></span></span>';
        row.children[0].textContent = reference.methodology_name || "Standard";
        row.querySelector(".t-pill span").textContent = reference.status_label || "Recorded";
        list.append(row);
      });
    }

    const restriction = find("[data-pq-restriction]");
    restriction.hidden = !data.restriction_explanation;
    if (data.restriction_explanation) restriction.textContent = data.restriction_explanation;

    const history = find("[data-pq-history]");
    const isHistorical = Boolean(data.historical?.is_historical);
    history.hidden = !isHistorical;
    if (isHistorical) {
      find("[data-pq-history-copy]").textContent =
        `This is the version used at the time of an alert${data.historical.event_time ? ` on ${formatDate(data.historical.event_time, { withTime: true })}` : ""}. Newer versions may exist.`;
    }

    find("[data-pq-freshness]").textContent = data.last_verified_at
      ? `Evidence last checked ${formatDate(data.last_verified_at)}`
      : data.freshness || "";

    const source = find("[data-pq-source]");
    source.hidden = !data.official_source_url;
    if (data.official_source_url) source.href = data.official_source_url;

    /* The full Passport link stays on whichever path opened this popup. */
    const base = request.basePath || "/dashboard/market";
    const query = assessment.methodology_id ? `?methodology_id=${encodeURIComponent(assessment.methodology_id)}` : "";
    find("[data-pq-full]").href = `${base}/${encodeURIComponent(String(asset).toLowerCase())}${query}`;

    find("[data-pq-copy]").dataset.reference = data.evidence_reference || "";
    paintIcons(dialog);
  }

  find("[data-passport-close]")?.addEventListener("click", () => close());
  find("[data-pq-retry]")?.addEventListener("click", () => load());
  find("[data-pq-copy]")?.addEventListener("click", async (event) => {
    const reference = event.currentTarget.dataset.reference;
    if (!reference) return;
    try {
      await navigator.clipboard.writeText(reference);
      window.showDashToast?.("Evidence reference copied.");
    } catch {
      window.showDashToast?.("This browser did not allow copying.", true);
    }
  });

  window.HilalPassportQuickView = {
    open(options, trigger) {
      request = options;
      open(trigger);
      load();
    },
    close,
  };
}

setUpFavorites();
setUpQuickView();
