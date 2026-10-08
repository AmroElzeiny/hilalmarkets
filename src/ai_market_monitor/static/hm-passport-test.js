/* Behaviour for the Passport page and its printable report.
 *
 * The page is readable and complete without any of this: every section is server
 * rendered, the disclosures are real <details> elements that open without help, and
 * the standard picker is a plain form with its own button. What runs here is only what
 * improves the reading — the section a person is in, the standard opening as soon as
 * it is picked, copy buttons, and the problem-report form.
 */

import { followSections, holdBelowFixedHeader } from "./hm-jump.js";
import { settleIn, whenSeen } from "./hm-motion.js";
import { publish } from "./hm-page-context.js";
import { pageNote } from "./hm-page-notes.js";

/* On the public website the sticky section links stop under the site's fixed header.
   In the dashboard the topbar's own stylesheet already says where that is. */
if (document.body.classList.contains("hm-public-market")) holdBelowFixedHeader();

setUpStandardPicker();
setUpSectionTracking();
setUpCopyButtons();
setUpProblemForm();
setUpReportActions();

/** A short "done" or "that failed" line: the dashboard's toast, or the button's own words. */
function say(message, failed = false, button = null) {
  if (window.showDashToast) {
    window.showDashToast(message, failed);
    return;
  }
  if (!button) return;
  const original = button.dataset.originalLabel || button.innerHTML;
  button.dataset.originalLabel = original;
  button.textContent = message;
  window.setTimeout(() => {
    button.innerHTML = original;
  }, 2000);
}

/* One Passport per coin, and the standard is picked on it. Picking one opens the same
 * page on that standard straight away; the address each option opens was written by the
 * server, so this never builds one. Without scripting the form's own button does it. */
function setUpStandardPicker() {
  const form = document.querySelector("[data-passport-standard]");
  const select = form?.querySelector("[data-passport-standard-select]");
  if (!form || !select) return;
  form.classList.add("is-enhanced");
  select.addEventListener("change", () => {
    const chosen = select.selectedOptions[0];
    const target = chosen?.dataset.href;
    if (!target) {
      form.submit();
      return;
    }
    select.disabled = true;
    window.location.assign(target);
  });
}

/* The Passport in front of them, in the page's own words: which coin, what the
 * answer reads as, and under which standard. Facts still come from the records;
 * this only says what the person is looking at. */
if (document.querySelector("[data-passport-page]")) {
  publish("passport", () => {
    const answer = document.querySelector(".t-pq-answer");
    const facts = [...document.querySelectorAll(".t-facts > div")].map((fact) =>
      fact.textContent.trim().replace(/\s+/g, " "),
    ).filter(Boolean);
    const summary = answer ? answer.textContent.trim().replace(/\s+/g, " ") : null;
    return pageNote({ summary, points: facts });
  });
}

/* The printable evidence report, in its own words: the coin and its sections. */
if (document.querySelector("[data-report-page]")) {
  publish("report", () => {
    const sections = [...document.querySelectorAll(".t-report h2")].map((heading) =>
      heading.textContent.trim().replace(/\s+/g, " "),
    ).filter(Boolean);
    return pageNote({ points: sections });
  });
}

/** Keep the sticky section links pointing at the section actually on screen. */
function setUpSectionTracking() {
  const tabs = document.querySelector("[data-passport-tabs]");
  if (!tabs) return;
  const links = Array.from(tabs.querySelectorAll("a[href^='#']"));
  const sections = links
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if (!sections.length) return;

  /* The shared tracker, measured against the bar itself: it is held under the topbar
     or the site's header, so a fixed margin would be wrong on one of them. */
  followSections(links, document, { bar: tabs });

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
      say("Copied.", false, button);
    } catch {
      say("This browser did not allow copying.", true, button);
    }
  });
}

function setUpReportActions() {
  document.querySelector("[data-print]")?.addEventListener("click", () => window.print());
  document.querySelector("[data-copy-link]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(window.location.href);
      say("Link copied.", false, button);
    } catch {
      say("This browser did not allow copying.", true, button);
    }
  });
}

function setUpProblemForm() {
  const form = document.querySelector("[data-problem-form]");
  if (!form) return;
  const status = form.querySelector("[data-problem-status]");
  const submit = form.querySelector("button[type='submit']");

  /* A visitor without an account sends the form through the public forms' guard: a
     token from its bootstrap, their email address and the hidden trap field. A member
     sends it as themselves, with their own token. */
  const isVisitor = form.dataset.visitor === "true";

  async function visitorToken() {
    const response = await fetch("/api/v1/public-forms/bootstrap", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("The report form is not available just now.");
    return (await response.json()).csrf_token || "";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const details = form.querySelector("[name='details']");
    if (!form.reportValidity()) return;
    submit.disabled = true;
    status.textContent = "Sending...";
    try {
      const versionId = form.querySelector("[name='passport_version_id']").value;
      const assetId = encodeURIComponent(form.dataset.canonicalAssetId);
      const body = {
        report_type: form.querySelector("[name='report_type']").value,
        details: details.value,
        ...(versionId ? { passport_version_id: versionId } : {}),
      };
      let address = `/api/v1/sharia/passports/${assetId}/problem-reports`;
      // On the public page the reader's token rides on the form; in the dashboard it is
      // on the body.
      let token = form.dataset.csrfToken || document.body.dataset.csrfToken || "";
      if (isVisitor) {
        address = `/api/v1/public-forms/passports/${assetId}/problem-reports`;
        token = await visitorToken();
        body.email = form.querySelector("[name='email']").value;
        body.company_website = form.querySelector("[name='company_website']")?.value || "";
      }
      const response = await fetch(address, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": token,
          Accept: "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(
          payload.detail?.message || payload.detail || `The service answered ${response.status}.`,
        );
      }
      form.reset();
      status.textContent = isVisitor
        ? "Thank you. A reviewer will look at this and may write to you by email. The published result has not changed."
        : "Thank you. A reviewer will look at this. The published result has not changed.";
      say("Your report was sent.");
    } catch (error) {
      status.textContent = `${error.message} Nothing was sent. Please try again.`;
    } finally {
      submit.disabled = false;
    }
  });
}
