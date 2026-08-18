/* Behaviour for the Passport page and its printable report.
 *
 * The page is readable and complete without any of this: every section is server
 * rendered, and the disclosures are real <details> elements that open without help.
 * What runs here is only what improves the reading — the section a person is in,
 * copy buttons, and the problem-report form.
 */

import { settleIn, whenSeen } from "./hm-motion.js";

setUpSectionTracking();
setUpCopyButtons();
setUpProblemForm();
setUpReportActions();

/** Keep the sticky section links pointing at the section actually on screen. */
function setUpSectionTracking() {
  const tabs = document.querySelector("[data-passport-tabs]");
  if (!tabs) return;
  const links = Array.from(tabs.querySelectorAll("a[href^='#']"));
  const sections = links
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if (!sections.length) return;

  const mark = (id) => {
    links.forEach((link) => {
      const current = link.getAttribute("href") === `#${id}`;
      if (current) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  };

  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (visible) mark(visible.target.id);
    },
    { rootMargin: "-88px 0px -60% 0px", threshold: 0 },
  );
  sections.forEach((section) => observer.observe(section));

  /* Each section's panels settle in the first time they are reached, so a long
     document reveals itself as it is read rather than all at once. */
  sections.forEach((section) => {
    whenSeen(section, () => settleIn(section.querySelectorAll(".t-panel, .t-more"), { from: 8 }));
  });
}

function setUpCopyButtons() {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-reference]");
    if (!button) return;
    const value = button.dataset.copyReference;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      window.showDashToast?.("Copied.");
    } catch {
      window.showDashToast?.("This browser did not allow copying.", true);
    }
  });
}

function setUpReportActions() {
  document.querySelector("[data-print]")?.addEventListener("click", () => window.print());
  document.querySelector("[data-copy-link]")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      window.showDashToast?.("Link copied.");
    } catch {
      window.showDashToast?.("This browser did not allow copying.", true);
    }
  });
}

function setUpProblemForm() {
  const form = document.querySelector("[data-problem-form]");
  if (!form) return;
  const status = form.querySelector("[data-problem-status]");
  const submit = form.querySelector("button[type='submit']");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const details = form.querySelector("[name='details']");
    if (!form.reportValidity()) return;
    submit.disabled = true;
    status.textContent = "Sending...";
    try {
      const versionId = form.querySelector("[name='passport_version_id']").value;
      const response = await fetch(
        `/api/v1/sharia/passports/${encodeURIComponent(form.dataset.canonicalAssetId)}/problem-reports`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": document.body.dataset.csrfToken || "",
            Accept: "application/json",
          },
          body: JSON.stringify({
            report_type: form.querySelector("[name='report_type']").value,
            details: details.value,
            ...(versionId ? { passport_version_id: versionId } : {}),
          }),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(
          payload.detail?.message || payload.detail || `The service answered ${response.status}.`,
        );
      }
      form.reset();
      status.textContent = "Thank you. A reviewer will look at this. The published result has not changed.";
      window.showDashToast?.("Your report was sent.");
    } catch (error) {
      status.textContent = `${error.message} Nothing was sent. Please try again.`;
    } finally {
      submit.disabled = false;
    }
  });
}
