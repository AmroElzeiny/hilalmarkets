/* The behaviour of the six sign-in pages.
 *
 * Everything here exists to remove one round trip to the server. The old pages could
 * only tell you what was wrong *after* you pressed the button and the page reloaded:
 * which password rule you had missed, that your two passwords differed,
 * that your email had a typo, that you had to wait before asking for another code. Each
 * of those is knowable in the browser, and each one is answered here while you type.
 *
 * Nothing here decides anything. The server is still the only authority on whether a
 * password is strong enough, whether a code is right and whether an account exists —
 * this file only says the same thing sooner, from the same rules, which arrive in
 * `window.HilalMarketsAuth` straight out of `core/auth_pages.py`.
 *
 * Motion comes from `hm-motion.js`, which owns the durations, the easing and the
 * reduced-motion decision for the whole product. No timing is written here.
 */

import { DURATION, animate, prefersReducedMotion, settleIn } from "./hm-motion.js";

const config = window.HilalMarketsAuth || {};
const root = document.body;

/* ── Small helpers ─────────────────────────────────────────────────────────── */

const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
/** The shared icon set, or nothing at all if it failed to load. Never a broken mark. */
const iconMarkup = (name) => (typeof window.icon === "function" ? window.icon(name, "icon") : "");
const one = (selector, scope = document) => scope.querySelector(selector);

/** Say something once, politely, and only when it has actually changed. */
function announce(node, text) {
  if (!node || node.textContent === text) return;
  node.textContent = text;
}

/** The field wrapper a control belongs to. */
const fieldOf = (control) => control.closest(".auth-field");

function setFieldError(control, message) {
  const field = fieldOf(control);
  if (!field) return;
  const slot = one("[data-field-error]", field);
  if (message) {
    field.dataset.invalid = "true";
    delete field.dataset.valid;
    control.setAttribute("aria-invalid", "true");
    if (slot) {
      slot.textContent = message;
      slot.hidden = false;
    }
  } else {
    delete field.dataset.invalid;
    control.removeAttribute("aria-invalid");
    if (slot) {
      slot.textContent = "";
      slot.hidden = true;
    }
  }
}

function markValid(control, valid) {
  const field = fieldOf(control);
  if (!field) return;
  if (valid) field.dataset.valid = "true";
  else delete field.dataset.valid;
}

/* ── What a field must contain ─────────────────────────────────────────────────
 *
 * Deliberately forgiving. The browser's own `type="email"` check is stricter than this
 * and the server's is stricter again; the only job here is to catch the mistakes people
 * really make — an empty box, a missing @, a stray space — and to catch them beside the
 * box rather than after a page reload. Anything it lets through is still checked by the
 * server, so a false pass costs nothing and a false failure would cost everything.
 */
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** What to say when a box is empty. One sentence per box, written out rather than built
 *  from the label — "Please fill in choose a password" is what building it produced. */
const MISSING = {
  display_name: "Please add your name.",
  email: "Please add your email address.",
  password: "Please type a password.",
  repeat_password: "Please type the password a second time.",
  code: "Please type the six-digit code from your email.",
};

function problemWith(control) {
  const value = (control.value || "").trim();
  if (control.required && !value) {
    return MISSING[control.name] || "Please fill this in.";
  }
  if (!value) return "";
  if (control.type === "email" && !LOOKS_LIKE_EMAIL.test(value)) {
    return value.includes("@")
      ? "That address is missing something after the @ — like .com."
      : "An email address needs an @ in it.";
  }
  if (control.name === "code" && !/^\d{6}$/.test(value.replace(/\s+/g, ""))) {
    return "The code is six numbers, with nothing else in it.";
  }
  return "";
}

/* ── The password rules, ticking themselves off ────────────────────────────── */

/** The password rules as the browser can test them, compiled once. */
const rules = (config.passwordRules || [])
  .map((rule) => {
    try {
      return { ...rule, test: new RegExp(rule.pattern, "u") };
    } catch {
      // A browser too old for Unicode property escapes simply gets no live checklist;
      // the list stays visible as a plain description and the server still decides.
      return null;
    }
  })
  .filter(Boolean);

function wirePasswordRules(control) {
  const field = fieldOf(control);
  const list = field && one("[data-password-rules]", field);
  const live = field && one("[data-password-live]", field);
  // A form with no checklist — signing in — has no rule to enforce here: an existing
  // password was made before today's rule and refusing it would lock somebody out of
  // their own account. `satisfied` says "nothing to check", not "everything passed".
  if (!list || !rules.length) {
    const noop = () => {};
    noop.satisfied = () => true;
    return noop;
  }

  const items = new Map(all(".auth-rule", list).map((item) => [item.dataset.rule, item]));
  let lastMet = -1;

  check.satisfied = () => rules.every((rule) => rule.test.test(control.value || ""));

  function check() {
    const value = control.value || "";
    let met = 0;
    for (const rule of rules) {
      const item = items.get(rule.key);
      const ok = value.length > 0 && rule.test.test(value);
      if (ok) met += 1;
      if (!item) continue;
      item.dataset.met = ok ? "true" : "false";
      const state = one(".auth-rule-state", item);
      if (state) state.textContent = ok ? "done" : "still needed";
    }
    markValid(control, met === rules.length);
    if (met !== lastMet) {
      lastMet = met;
      announce(
        live,
        met === rules.length
          ? "Password: all rules met."
          : `Password: ${met} of ${rules.length} rules met.`,
      );
    }
  }

  return check;
}

/** "These two are the same" / "not yet", said while typing rather than after a reload. */
function wirePasswordMatch(form) {
  const first = form.elements.password;
  const second = form.elements.repeat_password;
  if (!first || !second) return () => true;
  const field = fieldOf(second);
  const note = field && one("[data-password-match]", field);

  function check({ quiet = false } = {}) {
    const same = first.value === second.value;
    const ready = second.value.length > 0;
    if (note) {
      note.hidden = !ready;
      if (ready) {
        note.dataset.match = same ? "true" : "false";
        note.innerHTML = `${iconMarkup(same ? "check" : "close")}<span>${
          same ? "Both passwords are the same." : "These two are not the same yet."
        }</span>`;
      }
    }
    markValid(second, ready && same);
    if (!quiet && ready && !same) setFieldError(second, "Type the same password in both boxes.");
    else if (same) setFieldError(second, "");
    return same;
  }

  first.addEventListener("input", () => check({ quiet: true }));
  second.addEventListener("input", () => check({ quiet: true }));
  second.addEventListener("blur", () => check());
  return () => check();
}

/* ── Seeing what you typed, and Caps Lock ──────────────────────────────────── */

function wireReveal(button) {
  const control = document.getElementById(button.getAttribute("aria-controls"));
  if (!control) return;
  const text = one(".auth-reveal-text", button);
  const mark = one(".auth-reveal-icon", button);
  button.addEventListener("click", () => {
    const showing = control.type === "text";
    control.type = showing ? "password" : "text";
    button.setAttribute("aria-pressed", showing ? "false" : "true");
    if (text) text.textContent = showing ? "Show" : "Hide";
    if (mark) mark.innerHTML = iconMarkup(showing ? "eye" : "lock");
    // Typing continues where it left off rather than at the end of the box.
    const at = control.value.length;
    control.focus();
    try {
      control.setSelectionRange(at, at);
    } catch {
      /* a browser that will not move the caret is not a reason to fail */
    }
  });
}

function wireCapsLock(control) {
  const field = fieldOf(control);
  const warning = field && one("[data-caps]", field);
  if (!warning) return;
  const update = (event) => {
    const on = typeof event.getModifierState === "function" && event.getModifierState("CapsLock");
    warning.hidden = !on;
  };
  control.addEventListener("keydown", update);
  control.addEventListener("keyup", update);
  control.addEventListener("blur", () => {
    warning.hidden = true;
  });
}

/* ── Six digits ────────────────────────────────────────────────────────────── */

function wireCodeField(wrapper) {
  const input = one(".auth-code-input", wrapper);
  const cells = all(".auth-code-cell", wrapper);
  if (!input || cells.length !== 6) return;

  const live = one("[data-code-live]", wrapper.closest(".auth-field") || document);
  wrapper.classList.add("is-enhanced");
  // The box can now hold more than six characters for a moment, because a pasted code
  // often arrives with a space in the middle. `maxlength` would have cut "123 456" down
  // to "123 45" before anything here could clean it.
  input.maxLength = 20;

  let lastCount = -1;

  function paint() {
    const digits = (input.value || "").replace(/\D/g, "").slice(0, 6);
    if (input.value !== digits) input.value = digits;
    const focused = document.activeElement === input;
    cells.forEach((cell, index) => {
      const digit = digits[index] || "";
      if (cell.textContent !== digit) cell.textContent = digit;
      cell.dataset.filled = digit ? "true" : "false";
      cell.dataset.active =
        focused && index === Math.min(digits.length, 5) && digits.length < 6 ? "true" : "false";
    });
    if (digits.length !== lastCount) {
      lastCount = digits.length;
      announce(
        live,
        digits.length === 6
          ? "All six digits entered. The button below is ready."
          : `${digits.length} of 6 digits entered.`,
      );
      if (digits.length === 6 && !prefersReducedMotion()) {
        // One short settle when the code is complete, so it is clear the field is done.
        animate(cells[5], { transform: ["scale(1)", "scale(1.06)", "scale(1)"] }, {
          duration: DURATION.quick,
        });
      }
    }
  }

  input.addEventListener("input", paint);
  input.addEventListener("focus", paint);
  input.addEventListener("blur", paint);
  input.addEventListener("paste", (event) => {
    const text = (event.clipboardData || window.clipboardData)?.getData("text") || "";
    const digits = text.replace(/\D/g, "").slice(0, 6);
    if (!digits) return;
    event.preventDefault();
    input.value = digits;
    paint();
  });
  // Tapping any box puts the caret at the end, which is the only place typing continues.
  wrapper.addEventListener("click", () => {
    input.focus();
    const at = input.value.length;
    try {
      input.setSelectionRange(at, at);
    } catch {
      /* ignore */
    }
  });
  paint();
}

/* ── Asking for another code ───────────────────────────────────────────────── */

/* The wait is the server's, not this file's: `CODE_RESEND_SECONDS` comes from
 * `core/auth_pages.py`, which is the same constant the server refuses an early request
 * with. Before this the page said "wait one minute" in an error message and offered a
 * button that did nothing but earn a second telling-off.
 *
 * When the countdown started is remembered for this tab, so a reload does not hand
 * somebody a fresh minute they have already served. */
function wireResend(form) {
  const button = one("[data-resend]", form);
  const label = button && one("[data-resend-label]", button);
  const live = one("[data-resend-live]");
  const wait = Number(config.resendSeconds) || 0;
  if (!button || !label || !wait) return;

  const key = `hm-auth-code-sent:${config.page}:${config.email || ""}`;
  if (config.codeJustSent && !sessionStorage.getItem(key)) {
    sessionStorage.setItem(key, String(Date.now()));
  }
  const startedAt = Number(sessionStorage.getItem(key) || 0);
  if (!startedAt) return;

  const original = label.textContent;
  let timer = null;

  function tick() {
    const left = Math.ceil((startedAt + wait * 1000 - Date.now()) / 1000);
    if (left <= 0) {
      button.disabled = false;
      label.textContent = original;
      announce(live, "You can ask for a new code now.");
      if (timer) window.clearInterval(timer);
      return;
    }
    button.disabled = true;
    label.textContent = `Send it again in ${left}s`;
  }

  tick();
  timer = window.setInterval(tick, 1000);
  form.addEventListener("submit", () => {
    sessionStorage.removeItem(key);
  });
}

/* ── The Google door ───────────────────────────────────────────────────────── */

/* The button is an ordinary link to `/auth/google/start`, and this only upgrades it to a
 * popup. That order matters: with scripting off, and when a browser blocks the popup, the
 * link still works and Google simply opens in this tab. A button that only works with
 * script is a button that silently does nothing for the people it fails.
 *
 * The popup finishes on our own callback address, which posts a message back here saying
 * where to go, and closes itself. The message is checked twice — it must come from this
 * exact origin, and it must carry our own marker — because `message` is a public event
 * that any framed page could fire.
 */
function wireGoogle(link) {
  const live = one("[data-google-live]");
  const width = 480;
  const height = 640;

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const data = event.data;
    if (!data || data.source !== "hilal-markets-google") return;
    const target = String(data.target || "");
    // Only ever a path on this site. A full address here would be an open redirect.
    if (!target.startsWith("/")) return;
    announce(live, "Signing you in.");
    window.location.assign(target);
  });

  link.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    const href = link.getAttribute("href");
    if (!href) return;
    const separator = href.includes("?") ? "&" : "?";
    const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
    const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 3));
    const popup = window.open(
      `${href}${separator}popup=1`,
      "hilal-markets-google",
      `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`,
    );
    // Blocked, or refused. Let the browser follow the link the ordinary way instead of
    // leaving somebody looking at a button that did nothing.
    if (!popup) return;
    event.preventDefault();
    popup.focus();
    announce(live, "A Google window is open. Choose the account you want to use.");
  });
}

/* ── Sending the form ──────────────────────────────────────────────────────── */

function wireForm(form) {
  // Our own messages replace the browser's, which cannot say which password
  // rules is missing and cannot put the reason beside the box it belongs to. Set from
  // script, so a person with scripting off keeps the browser's checks.
  form.noValidate = true;

  const controls = all(".auth-control, .auth-code-input", form);
  const submitButton = one("[type=submit]", form);
  const submitLabel = submitButton && one("[data-submit-label]", submitButton);
  const live = one("[data-form-live]", form.parentElement || document);
  const checkPasswordRules = form.elements.password
    ? wirePasswordRules(form.elements.password)
    : () => {};
  const checkMatch = wirePasswordMatch(form);

  controls.forEach((control) => {
    control.addEventListener("blur", () => {
      const problem = problemWith(control);
      setFieldError(control, problem);
      if (!problem && control.value.trim() && control.type !== "password") {
        markValid(control, true);
      }
    });
    control.addEventListener("input", () => {
      if (fieldOf(control)?.dataset.invalid === "true") setFieldError(control, "");
      if (control.type !== "password") {
        markValid(control, Boolean(control.value.trim()) && !problemWith(control));
      }
    });
  });

  if (form.elements.password) {
    form.elements.password.addEventListener("input", checkPasswordRules);
    checkPasswordRules();
  }

  form.addEventListener("submit", (event) => {
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }

    let firstBad = null;
    for (const control of controls) {
      const problem = problemWith(control);
      setFieldError(control, problem);
      if (problem && !firstBad) firstBad = control;
    }
    // A password the checklist has not finished ticking would be refused by the server
    // anyway, so it is stopped here instead — the reason is already on screen, beside
    // the box, and the person keeps everything else they have typed.
    if (!firstBad && form.elements.password && !checkPasswordRules.satisfied()) {
      firstBad = form.elements.password;
      setFieldError(firstBad, "The list below shows what this password still needs.");
    }
    if (!firstBad && !checkMatch()) firstBad = form.elements.repeat_password;

    if (firstBad) {
      event.preventDefault();
      firstBad.focus();
      announce(live, "Something needs fixing before this can be sent.");
      if (!prefersReducedMotion()) {
        const field = fieldOf(firstBad);
        if (field) animate(field, { transform: ["translateX(-4px)", "translateX(0px)"] });
      }
      return;
    }

    form.dataset.submitting = "true";
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.dataset.busy = "true";
    }
    if (submitLabel) {
      submitLabel.dataset.originalText = submitLabel.textContent || "";
      submitLabel.textContent = "Just a moment…";
    }
    announce(live, "Sending.");
  });
}

/* ── Putting it together ───────────────────────────────────────────────────── */

function start() {
  all("[data-reveal]").forEach(wireReveal);
  all("input[type=password]").forEach(wireCapsLock);
  all("[data-code]").forEach(wireCodeField);
  all("form.auth-form").forEach(wireForm);
  all("[data-google-signin]").forEach(wireGoogle);
  const resendForm = one("#auth-resend-form");
  if (resendForm) wireResend(resendForm);

  // A failure is where the person's attention has to go. Moving focus is also the only
  // reliable way a screen reader reads a message that was already in the page when it
  // loaded — `role="alert"` announces an *inserted* node, not one that arrived with the
  // document.
  const alertBox = one("[data-auth-alert]");
  if (alertBox && alertBox.dataset.tone === "error") alertBox.focus({ preventScroll: false });

  // The card arrives top to bottom: heading, then the form, then the three promises.
  // A calm sequential reveal, which is the one kind of movement `brand guide.md`
  // section 15 asks for — it says "read this in this order", and then it stops.
  settleIn(all(".auth-card > *:not(.auth-form)"), { from: 10 });
  settleIn(all(".auth-form > *"), { from: 8 });
  settleIn(all(".auth-promise"), { from: 6 });

  /* Nothing sensitive survives the back button. A browser restoring a page from its
     cache restores what was typed into it, including a password on a shared machine. */
  const clearSensitive = () => {
    all('input[type="password"], input[name="code"]').forEach((field) => {
      field.value = "";
    });
  };
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      clearSensitive();
      // A restored page also restores the disabled button from the submit that took the
      // person away. Without this the form was dead on arrival back.
      all("form.auth-form").forEach((form) => {
        delete form.dataset.submitting;
        const button = one("[type=submit]", form);
        const label = button && one("[data-submit-label]", button);
        if (button) {
          button.disabled = false;
          delete button.dataset.busy;
        }
        if (label && label.dataset.originalText) label.textContent = label.dataset.originalText;
      });
    }
  });
  window.addEventListener("pagehide", clearSensitive);
}

if (root) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
}
