(() => {
  async function copy(value, button) {
    try {
      await navigator.clipboard.writeText(value);
      const previous = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = previous; }, 1400);
    } catch (_error) {
      button.setAttribute("title", "Copy failed. Select and copy this value manually.");
    }
  }

  document.querySelectorAll("[data-copy-value]").forEach((button) => {
    button.addEventListener("click", () => copy(button.dataset.copyValue, button));
  });

  const form = document.querySelector("[data-passport-problem-form]");
  if (!form) return;
  const status = form.querySelector("[data-passport-problem-status]");
  const submit = form.querySelector("button[type='submit']");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled || !form.reportValidity()) return;
    submit.disabled = true;
    status.textContent = "Sending your report...";
    const data = new FormData(form);
    const payload = {
      report_type: data.get("report_type"),
      details: data.get("details"),
    };
    if (data.get("passport_version_id")) payload.passport_version_id = data.get("passport_version_id");
    try {
      const response = await fetch(`/api/v1/sharia/passports/${form.dataset.canonicalAssetId}/problem-reports`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRF-Token": document.body.dataset.csrfToken || "",
        },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = result.detail;
        throw new Error(typeof detail === "string" ? detail : detail?.message || "The report could not be recorded.");
      }
      form.reset();
      status.textContent = result.message;
    } catch (error) {
      status.textContent = error.message || "The report could not be recorded. Please try again.";
    } finally {
      submit.disabled = false;
    }
  });
})();
