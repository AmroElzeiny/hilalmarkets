document.querySelectorAll("[data-filter-target]").forEach((input) => {
  input.addEventListener("input", () => {
    const table = document.getElementById(input.dataset.filterTarget);
    const query = input.value.trim().toLowerCase();
    table?.querySelectorAll("tbody tr").forEach((row) => {
      row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
    });
  });
});

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!reduceMotion) {
  document.querySelectorAll("[data-count]").forEach((node) => {
    const raw = String(node.dataset.count || "");
    const target = Number.parseFloat(raw);
    if (!Number.isFinite(target) || target <= 0) return;
    const suffix = raw.endsWith("h") ? "h" : "";
    const decimals = raw.includes(".") ? 1 : 0;
    const started = performance.now();
    const duration = 520;
    const tick = (now) => {
      const progress = Math.min((now - started) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = `${(target * eased).toFixed(decimals)}${suffix}`;
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

document.querySelectorAll("[data-decision-form], [data-guarded-form]").forEach((form) => {
  form.querySelectorAll("button[name='action']").forEach((button) => {
    button.addEventListener("click", () => {
      if (["approve_with_qualification", "request_more_evidence"].includes(button.value)) {
        const extras = form.querySelector("[data-decision-extras]");
        if (extras) extras.open = true;
      }
    });
  });
  form.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    const prompt = submitter?.dataset.confirm;
    if (prompt && !window.confirm(prompt)) {
      event.preventDefault();
      return;
    }
    if (!form.checkValidity()) return;
    form.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    if (submitter) {
      submitter.disabled = false;
      submitter.setAttribute("aria-busy", "true");
      const original = submitter.textContent.trim();
      submitter.textContent = `Recording: ${original}`;
    }
  });
});

const publicationDialog = document.querySelector("[data-publication-dialog]");
const publicationLauncher = document.querySelector("[data-open-publication]");
if (publicationDialog && publicationLauncher) {
  publicationLauncher.addEventListener("click", () => {
    publicationDialog.showModal();
  });
  publicationDialog.querySelectorAll("[data-close-publication]").forEach((button) => {
    button.addEventListener("click", () => publicationDialog.close());
  });
  publicationDialog.addEventListener("click", (event) => {
    if (event.target === publicationDialog) publicationDialog.close();
  });
  publicationDialog.addEventListener("close", () => publicationLauncher.focus());
}
