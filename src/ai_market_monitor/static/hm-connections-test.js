/* The Connections page at /dashboard/connections.
 *
 * Nothing here decides a fact. The server said which ways of being told exist, which
 * are available, and which are switched on; this file switches them, sends a test, and
 * animates the result.
 *
 * Popups are `hm-dialog.js` and motion is `hm-motion.js`, both shared with every other
 * page on this path — one focus trap, one easing scale, one reduced-motion decision.
 */

import { manageDialog } from "./hm-dialog.js";
import { animate, attention, prefersReducedMotion, settleIn } from "./hm-motion.js";

const root = document.querySelector("[data-connections-root]");
if (root) start(root);

/** What each switch says about itself, in both positions. */
const SWITCH_WORDS = {
  on: { label: "Sending here", detail: "Turn it off and nothing arrives here." },
  off: { label: "Not sending here", detail: "Turn it on to be told here." },
};

/** What a card's state line says, for each state the browser can put it into. */
const STATES = {
  on: {
    tone: "success",
    icon: "check",
    label: "On",
    meaning: "Messages are being sent here.",
  },
  off: {
    tone: "warning",
    icon: "pause",
    label: "Connected, but switched off",
    meaning: "It is ready. Nothing is being sent here until you switch it on.",
  },
};

function start(scope) {
  const csrf = document.body.dataset.csrfToken || "";
  const said = scope.querySelector("[data-c-said]");
  const switches = [...scope.querySelectorAll("[data-c-switch]")];

  /** Say one thing, once, to a screen reader. */
  function say(text) {
    if (!said) return;
    said.textContent = "";
    window.requestAnimationFrame(() => { said.textContent = text; });
  }

  /** Redraw the icons inside markup this file just changed. */
  function paint(element) {
    if (!element || typeof window.icon !== "function") return;
    element.querySelectorAll("[data-icon]").forEach((node) => {
      node.innerHTML = window.icon(node.dataset.icon, node.dataset.iconClass || "icon");
    });
  }

  /* ── Switching a channel on and off ──────────────────────────────────────── */

  /** Every channel currently switched on, as the server wants it: the whole set. */
  function chosenChannels() {
    return switches
      .filter((button) => button.getAttribute("aria-checked") === "true")
      .map((button) => button.dataset.cSwitch);
  }

  /** Put one card into a state, in words, in colour and in the accessibility tree. */
  function drawState(card, on) {
    const state = on ? STATES.on : STATES.off;
    const line = card.querySelector(".c-state");
    const mark = card.querySelector(".c-mark");
    if (line) {
      line.dataset.tone = state.tone;
      line.querySelector("[data-c-state-label]").textContent = state.label;
      line.querySelector("[data-c-state-meaning]").textContent = state.meaning;
      const glyph = line.querySelector("[data-icon]");
      if (glyph) {
        glyph.dataset.icon = state.icon;
        paint(line);
      }
    }
    if (mark) mark.dataset.tone = state.tone;
    card.dataset.state = state.tone;
  }

  for (const button of switches) {
    button.addEventListener("click", async () => {
      if (button.dataset.busy === "true") return;
      const card = button.closest("[data-c-card]");
      const channel = button.dataset.cSwitch;
      const wasOn = button.getAttribute("aria-checked") === "true";
      const nowOn = !wasOn;

      // Moved first, then saved. The knob travelling *is* the answer to "did that
      // work?", and holding it still until the network replies makes a switch that
      // feels broken on a slow connection. If the save fails it moves back, and the
      // page says so — never a silent revert.
      button.setAttribute("aria-checked", String(nowOn));
      button.dataset.busy = "true";
      const words = nowOn ? SWITCH_WORDS.on : SWITCH_WORDS.off;
      button.querySelector("[data-c-switch-label]").textContent = words.label;
      button.querySelector("[data-c-switch-detail]").textContent = words.detail;
      drawState(card, nowOn);

      let trouble = null;
      try {
        const response = await fetch("/api/v1/dashboard/integrations/channels", {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify({ channels: chosenChannels() }),
        });
        if (!response.ok) {
          trouble = await reasonFor(
            response,
            "That did not save. Nothing changed. Please try again.",
          );
        }
      } catch {
        trouble = "We could not reach the server. Nothing changed. Please try again.";
      }
      if (trouble === null) {
        const label = card.querySelector(".c-title h2").textContent.trim();
        say(nowOn ? `${label} is on.` : `${label} is off.`);
      } else {
        button.setAttribute("aria-checked", String(wasOn));
        const back = wasOn ? SWITCH_WORDS.on : SWITCH_WORDS.off;
        button.querySelector("[data-c-switch-label]").textContent = back.label;
        button.querySelector("[data-c-switch-detail]").textContent = back.detail;
        drawState(card, wasOn);
        attention(card);
        say(trouble);
        showTrouble(trouble);
      }
      delete button.dataset.busy;
    });
  }

  /* ── Sending a test ──────────────────────────────────────────────────────── */

  for (const button of scope.querySelectorAll("[data-c-test]")) {
    const original = button.innerHTML;
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      button.innerHTML = '<span data-icon="clock" data-icon-class="icon-sm"></span>Sending…';
      paint(button);
      say("Sending a test message.");
      try {
        const response = await fetch("/api/v1/dashboard/integrations/email/test", {
          method: "POST",
          headers: { "X-CSRF-Token": csrf },
        });
        if (!response.ok) {
          // Read by the one reader, so a stale form token and an ended session say what
          // to do about themselves here as well as everywhere else on this page.
          throw new Error(
            await reasonFor(
              response,
              "We could not send it. Nothing about your account changed.",
            ),
          );
        }
        const body = await response.json().catch(() => ({}));
        button.innerHTML = '<span data-icon="check" data-icon-class="icon-sm"></span>Sent — go and look';
        paint(button);
        say(`A test was sent to ${body.sent_to || "your email address"}.`);
      } catch (error) {
        button.innerHTML = '<span data-icon="alert" data-icon-class="icon-sm"></span>It did not send';
        paint(button);
        const trouble = String(
          error.message || "We could not send it. Nothing about your account changed.",
        );
        say(trouble);
        // Shown as well as said. The button says "It did not send" and nothing else on
        // the page said why, so the reason reached a screen reader and nobody else.
        showTrouble(trouble);
      } finally {
        // Back to its ordinary self, so a person can try again. Long enough to read the
        // result, short enough that the button is not stuck saying something stale.
        window.setTimeout(() => {
          button.innerHTML = original;
          paint(button);
          button.disabled = false;
        }, 6000);
      }
    });
  }

  /* ── What the server said went wrong ─────────────────────────────────────── */

  /**
   * The reason a request failed, in the server's own words where it gave one.
   *
   * Every failure here used to read "Please try again", whatever had happened. A form
   * token that had gone stale, a session that had ended and a channel the plan does not
   * include all looked identical, so the one thing a person could do about it — reload
   * the page, or sign in again — was the one thing nothing told them to do.
   */
  async function reasonFor(response, fallback) {
    if (response.status === 403) {
      return "Your page has been open a while. Reload it and try again.";
    }
    if (response.status === 401) {
      return "You have been signed out. Sign in again and try once more.";
    }
    try {
      const body = await response.json();
      const detail = body?.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      if (message) return String(message);
    } catch {
      // No JSON body. The fallback below is still an honest answer.
    }
    return fallback;
  }

  /* ── Linking Telegram ────────────────────────────────────────────────────── */

  const linkDialog = document.querySelector("[data-c-link-dialog]");
  const link = manageDialog(linkDialog, {
    closers: ["[data-c-link-close]", "[data-c-link-cancel]"],
  });

  /**
   * Notice when the link has actually been made, and show it.
   *
   * The popup's third step promises "this page shows Telegram as linked", and nothing
   * ever checked. The person pressed the button, linked in Telegram, came back, and the
   * page still offered to link — so the one step that had worked looked like the one
   * that had failed.
   *
   * Two moments are watched, because linking happens somewhere else: while the popup is
   * open, and whenever this tab is looked at again after Telegram had it. The page is
   * then rebuilt by the server rather than patched here — which state a channel is in is
   * a server decision on this path, and a browser redrawing it would be a second opinion
   * about the same fact.
   */
  let watching = null;

  async function telegramIsLinked() {
    try {
      const response = await fetch("/api/v1/dashboard/integrations", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return false;
      const body = await response.json();
      return Boolean(body?.telegram);
    } catch {
      // Offline, or the request was refused. Not linked as far as this page knows, and
      // the next check will say otherwise if that changes.
      return false;
    }
  }

  async function checkLinked() {
    if (await telegramIsLinked()) {
      stopWatching();
      say("Telegram is linked. Loading the page again.");
      window.location.reload();
    }
  }

  function stopWatching() {
    if (watching === null) return;
    window.clearInterval(watching);
    watching = null;
    document.removeEventListener("visibilitychange", onVisible);
  }

  function onVisible() {
    if (document.visibilityState === "visible") void checkLinked();
  }

  function startWatching() {
    if (watching !== null) return;
    // Every four seconds while the popup is open. Linking takes a person about a minute,
    // and this is one small request — not a poll left running for the life of the page:
    // it stops when the popup closes.
    watching = window.setInterval(() => void checkLinked(), 4000);
    document.addEventListener("visibilitychange", onVisible);
  }

  for (const button of scope.querySelectorAll("[data-c-connect-telegram]")) {
    button.addEventListener("click", () => {
      link.open(button);
      startWatching();
    });
  }
  linkDialog?.addEventListener("close", stopWatching);

  const copy = linkDialog?.querySelector("[data-c-copy]");
  if (copy) {
    const originalCopy = copy.innerHTML;
    copy.addEventListener("click", async () => {
      const command = linkDialog.querySelector("[data-c-command]")?.textContent || "";
      try {
        await navigator.clipboard.writeText(command.trim());
        copy.innerHTML = '<span data-icon="check" data-icon-class="icon-sm"></span>Copied';
      } catch {
        // Some browsers refuse the clipboard without a gesture they recognise. Selecting
        // it for the person is the next best thing, and better than a silent no-op.
        const node = linkDialog.querySelector("[data-c-command]");
        const range = document.createRange();
        range.selectNodeContents(node);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        copy.innerHTML = '<span data-icon="hand" data-icon-class="icon-sm"></span>Copy it yourself';
      }
      paint(copy);
      window.setTimeout(() => {
        copy.innerHTML = originalCopy;
        paint(copy);
      }, 4000);
    });
  }

  /* ── Unlinking Telegram ──────────────────────────────────────────────────── */

  const askDialog = document.querySelector("[data-c-ask-dialog]");
  const ask = manageDialog(askDialog, {
    closers: ["[data-c-ask-close]", "[data-c-ask-cancel]"],
  });
  for (const button of scope.querySelectorAll("[data-c-unlink-telegram]")) {
    button.addEventListener("click", () => ask.open(button));
  }

  const confirm = askDialog?.querySelector("[data-c-ask-go]");
  if (confirm) {
    confirm.addEventListener("click", async () => {
      confirm.disabled = true;
      let trouble = null;
      try {
        const response = await fetch("/api/v1/dashboard/integrations/telegram", {
          method: "DELETE",
          headers: { "X-CSRF-Token": csrf },
        });
        if (!response.ok) {
          trouble = await reasonFor(
            response,
            "We could not unlink it. Nothing changed. Please try again.",
          );
        }
      } catch {
        trouble = "We could not reach the server. Nothing changed. Please try again.";
      }
      if (trouble === null) {
        // The page is rebuilt by the server rather than patched here. Which state a
        // channel is in is a server decision on this path, and a browser that redrew it
        // from a delete response would be a second opinion about the same fact.
        window.location.reload();
        return;
      }
      confirm.disabled = false;
      await ask.close();
      say(trouble);
      // Said out loud as well as to a screen reader. The line above is `sr-only`, so a
      // sighted person pressed "Unlink it", watched the popup close, and was told
      // nothing at all about why the account was still there.
      showTrouble(trouble);
    });
  }

  /**
   * Say something went wrong, where a person can see it.
   *
   * The page's only way of speaking was `[data-c-said]`, which is `sr-only` — so every
   * failure on this page was invisible to anybody who was not using a screen reader. The
   * banner is put above the cards, is `role="alert"` so it is announced as well, and is
   * replaced rather than stacked: two of the same message is noise, not emphasis.
   */
  function showTrouble(text) {
    let banner = scope.querySelector("[data-c-trouble]");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "t-banner c-banner";
      banner.dataset.tone = "danger";
      banner.dataset.cTrouble = "";
      banner.setAttribute("role", "alert");
      banner.innerHTML =
        '<span data-icon="alert" data-icon-class="icon"></span>'
        + '<div class="t-banner-body"><strong>That did not work</strong><p></p></div>';
      scope.querySelector(".c-grid")?.before(banner);
      paint(banner);
    }
    banner.querySelector("p").textContent = text;
  }

  /* ── Arriving ────────────────────────────────────────────────────────────── */

  settleIn(scope.querySelectorAll("[data-c-card]"), { from: 10 });
  if (!prefersReducedMotion()) {
    const banner = scope.querySelector("[data-c-banner]");
    if (banner) animate(banner, { opacity: [0, 1] }, { duration: 0.24 });
    // The kinds arrive after the cards, once they are actually looked at, so the page
    // does not move everything at once on load.
    settleIn(scope.querySelectorAll("[data-c-kind]"), { from: 6, delayStep: 0.02 });
  }
}
