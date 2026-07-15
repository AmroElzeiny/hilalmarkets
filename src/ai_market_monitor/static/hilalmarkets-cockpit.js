document.addEventListener("DOMContentLoaded", () => {
  const base = "/api/v1/dashboard/cockpit";
  const request = async (path, options = {}) => {
    const response = await fetch(`${base}${path}`, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || response.statusText);
    return payload;
  };
  const clean = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
  const toast = (message, isError = false) => {
    const element = document.getElementById("dash-toast");
    if (!element) return;
    element.textContent = message;
    element.className = `dash-toast ${isError ? "error" : "success"}`;
    element.hidden = false;
    window.setTimeout(() => { element.hidden = true; }, 6000);
  };

  document.querySelectorAll("[data-cockpit-suggestion]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById("cockpit-suggestion-result");
      if (!target) return;
      target.hidden = false;
      target.textContent = "Preparing a reviewable draft adjustment...";
      try {
        const result = await request(`/strategies/${button.dataset.strategyId}/suggestions`, {
          method: "POST",
          body: JSON.stringify({ action: button.dataset.cockpitSuggestion }),
        });
        target.innerHTML = `
          <strong>${clean(result.reason)}</strong>
          <p>${clean(result.diff.length)} strategy section${result.diff.length === 1 ? "" : "s"} would change.</p>
          <ul>${result.diff.map((item) => `<li>${clean(item.section)}</li>`).join("") || "<li>No rule change; explanation only.</li>"}</ul>
          <button class="button button-primary" type="button" data-apply-suggestion="${clean(result.id)}">Confirm and save draft</button>
          <small>This cannot activate the draft.</small>`;
        target.querySelector("[data-apply-suggestion]")?.addEventListener("click", async (event) => {
          const applied = await request(`/suggestions/${event.currentTarget.dataset.applySuggestion}/apply`, { method: "POST" });
          target.innerHTML = `<strong>${clean(applied.message)}</strong><p>Draft version ${clean(applied.draft_version.version_number)} is ready for review.</p>`;
          toast("Suggestion saved as a draft version.");
        });
      } catch (error) {
        target.textContent = error.message;
        target.classList.add("error");
      }
    });
  });

  document.querySelectorAll("[data-bottleneck-detail]").forEach((button) => {
    button.addEventListener("click", () => {
      const detail = button.querySelector(".bottleneck-breakdown");
      if (!detail) return;
      detail.hidden = !detail.hidden;
      button.setAttribute("aria-expanded", String(!detail.hidden));
    });
  });

  document.querySelector("[data-universe-preview]")?.addEventListener("click", async (event) => {
    const target = document.querySelector("[data-universe-result]");
    if (!target) return;
    target.hidden = false;
    target.textContent = "Applying the approved screened-universe rules...";
    try {
      const result = await request(`/strategies/${event.currentTarget.dataset.universePreview}/universe-preview`, {
        method: "POST",
        body: JSON.stringify({ include_symbols: [], exclude_symbols: [] }),
      });
      const reasons = Object.entries(result.summary.exclusion_reasons || {});
      target.innerHTML = `<strong>${clean(result.summary.included_count)} pairs included</strong><p>${clean(result.summary.excluded_count)} excluded.</p><ul>${reasons.map(([reason, count]) => `<li>${clean(reason.replaceAll("_", " "))}: ${clean(count)}</li>`).join("") || "<li>No static exclusions.</li>"}</ul><small>${clean(result.summary.note)}</small>`;
    } catch (error) {
      target.textContent = error.message;
    }
  });

  document.querySelector("[data-experiment-form]")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const target = document.querySelector("[data-experiment-result]");
    if (!target) return;
    const values = new FormData(form);
    const versionIds = [values.get("left_version_id"), values.get("right_version_id")];
    target.hidden = false;
    if (versionIds[0] === versionIds[1]) {
      target.textContent = "Choose two different Watchlist versions.";
      return;
    }
    target.textContent = "Comparing retained monitoring evidence...";
    try {
      const result = await request(`/strategies/${form.dataset.strategyId}/experiments`, {
        method: "POST",
        body: JSON.stringify({ name: "Dashboard version comparison", version_ids: versionIds, mode: "dry_run" }),
      });
      const left = result.comparison.left;
      const right = result.comparison.right;
      target.innerHTML = `<strong>Evidence comparison</strong><p>Confirmed opportunities: ${clean(left.confirmed_matches)} versus ${clean(right.confirmed_matches)}</p><p>Forming opportunities: ${clean(left.forming_setups)} versus ${clean(right.forming_setups)}</p><p>Corrective feedback: ${clean(left.false_alert_feedback)} versus ${clean(right.false_alert_feedback)}</p><small>${clean(result.comparison.non_advisory_notice)}</small>`;
    } catch (error) {
      target.textContent = error.message;
    }
  });
});
