/* Hilal, the assistant in the corner of every redesigned dashboard page.
 *
 * Three rules shape this file, and all three are about not lying to the person:
 *
 *   * **The server owns the transcript.** This script draws what /history returns. It
 *     keeps no copy that could survive a reload and disagree with the server, so a
 *     conversation reads the same on a phone as it does on a laptop.
 *   * **The server owns the allowance.** The status is polled, never computed here. A
 *     locked box, its reason and the moment it unlocks all come from the same reply, so
 *     the box cannot say "you have messages left" while the server refuses them.
 *   * **Nothing arrives as markup.** Every word from the server is set as text. Hilal
 *     is forbidden from producing code, and this end refuses to render any if it did.
 *
 * Motion comes from `hm-motion.js`, which is where `prefers-reduced-motion` is decided
 * once for the whole path.
 */

import { animate, dismiss, prefersReducedMotion, reveal, settleIn } from "./hm-motion.js";
import { snapshot } from "./hm-page-context.js";

const POLL_MS = 1000;
//: How tall the writing box may grow before it starts scrolling instead.
const GROW_LIMIT = 132;
const icon = (name, cls = "icon") => (window.icon ? window.icon(name, cls) : "");

/* The small line under an answer that says what kind of answer it is.
 *
 * Only two kinds get one, and both are promises about the answer rather than decoration.
 * A refusal says the door is closed and why. Guidance says this help is new and can be
 * wrong — the honest caveat on help about a screen, said every time rather than once in
 * a welcome nobody rereads. */
const MODE_NOTES = {
  REFUSAL: { icon: "shield", text: "This one stays your call" },
  GUIDE: { icon: "info", text: "New help — check the screen as you go" },
};

const STAR_WORDS = {
  1: "Sorry that was not useful. Tell us what went wrong.",
  2: "Not good enough. What was missing?",
  3: "Somewhere in the middle. What would have helped?",
  4: "Good. Anything that would have made it better?",
  5: "Thank you. That is good to hear.",
};

function ready(run) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
}

class HilalChat {
  constructor(root) {
    this.root = root;
    this.find = (selector) => root.querySelector(selector);

    this.orb = this.find("[data-hilal-open]");
    this.badge = this.find("[data-hilal-badge]");
    this.window = this.find("[data-hilal-window]");
    this.scroll = this.find("[data-hilal-scroll]");
    this.thread = this.find("[data-hilal-thread]");
    this.typing = this.find("[data-hilal-typing]");
    this.chips = this.find("[data-hilal-chips]");
    this.locked = this.find("[data-hilal-locked]");
    this.form = this.find("[data-hilal-form]");
    this.input = this.find("[data-hilal-input]");
    this.send = this.find("[data-hilal-send]");
    this.note = this.find("[data-hilal-note]");
    this.announce = this.find("[data-hilal-announce]");
    this.meter = this.find("[data-hilal-meter]");
    this.meterFill = this.find("[data-hilal-meter-fill]");
    this.meterText = this.find("[data-hilal-meter-text]");
    this.reportDialog = this.find("[data-hilal-report]");
    this.rateDialog = this.find("[data-hilal-rate]");

    this.open = false;
    this.loaded = false;
    this.sending = false;
    this.status = null;
    this.timer = null;
    this.resetTimer = null;
    this.lastAssistantId = null;
    this.reportingId = null;
    this.stars = 0;
    this.opener = null;
    this.turnsThisVisit = 0;

    this.csrf = document.body.dataset.csrfToken || "";
    this.maxLength = Number(root.dataset.maxLength) || 800;
    //: The invitation the box normally carries, kept so locking can borrow it back.
    this.placeholder = this.input.placeholder;

    this.bind();
    this.watchCookieBanner();
    // One status read on load, so the button already knows whether it can be used
    // before anybody clicks it. Polling only starts when the window is open.
    this.refreshStatus();
  }

  /**
   * Stay out from under the cookie banner.
   *
   * The banner is fixed across the bottom of the dashboard at a higher layer than
   * anything else, so it covered this button completely: a first-time visitor could
   * see Hilal and could not click it until they had answered the cookie question.
   *
   * Measured rather than guessed. The banner is one line on a wide screen and three on
   * a narrow one, and a hard-coded offset would be wrong on one of them — which is the
   * kind of "nearly right" that leaves the button half covered.
   */
  watchCookieBanner() {
    const banner = document.querySelector("[data-cookie-banner]");
    if (!banner) return;
    const lift = () => {
      const box = banner.getBoundingClientRect();
      const showing = banner.classList.contains("is-visible") && box.height > 0;
      this.root.style.setProperty("--h-lift", showing ? `${Math.ceil(box.height) + 12}px` : "0px");
    };
    lift();
    // Its height changes with the width of the window; its visibility changes when the
    // person answers. Both have to move the button, so both are watched.
    new ResizeObserver(lift).observe(banner);
    new MutationObserver(lift).observe(banner, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }

  /* ── Wiring ──────────────────────────────────────────────────────────────── */

  bind() {
    this.orb.addEventListener("click", () => (this.open ? this.closeChat() : this.openChat()));
    this.find("[data-hilal-close]").addEventListener("click", () => this.closeChat({ ask: true }));

    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.ask(this.input.value);
    });

    this.input.addEventListener("input", () => {
      this.grow();
      this.updateSend();
    });

    this.input.addEventListener("keydown", (event) => {
      // Enter sends, Shift+Enter makes a new line. The usual arrangement, and the one
      // people try first.
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this.ask(this.input.value);
      }
    });

    this.chips.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-hilal-chip]");
      if (chip) this.ask(chip.dataset.hilalChip);
    });

    this.thread.addEventListener("click", (event) => {
      const report = event.target.closest("[data-hilal-report-one]");
      if (report) this.openReport(report.dataset.hilalReportOne);
    });

    this.find("[data-hilal-report-open]").addEventListener("click", () => {
      if (!this.lastAssistantId) {
        this.say("There is no answer to report yet.");
        return;
      }
      this.openReport(this.lastAssistantId);
    });

    // Escape closes the window. Asked for a rating on the way out only when the X was
    // used — pressing Escape reads as "get this out of my way", and a popup in answer
    // to that would be the opposite of what was asked for.
    this.window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.stopPropagation(); this.closeChat(); }
      if (event.key === "Tab") this.trapFocus(event);
    });

    this.bindReport();
    this.bindRating();

    // A hidden tab has nobody watching it. Asking the server for a status once a
    // second on behalf of nobody is load for nothing.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.stopPolling();
      else if (this.open) this.startPolling();
    });

    // The box is narrower on a narrow screen, so the same words take more lines. Left
    // unmeasured, turning a laptop into a split screen put the scrollbar straight back.
    window.addEventListener("resize", () => {
      if (this.open) this.grow();
    });
  }

  bindReport() {
    const close = () => this.reportDialog.close();
    this.find("[data-hilal-report-close]").addEventListener("click", close);
    this.find("[data-hilal-report-cancel]").addEventListener("click", close);
    this.reportDialog.addEventListener("close", () => this.input.focus());
    this.find("[data-hilal-report-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendReport();
    });
  }

  bindRating() {
    const stars = [...this.root.querySelectorAll("[data-hilal-star]")];
    const word = this.find("[data-hilal-stars-word]");
    const sendButton = this.find("[data-hilal-rate-send]");

    const light = (upTo) => {
      stars.forEach((star) => {
        const value = Number(star.dataset.hilalStar);
        star.dataset.lit = value <= upTo ? "true" : "false";
        star.setAttribute("aria-checked", value === this.stars ? "true" : "false");
      });
    };

    for (const star of stars) {
      const value = Number(star.dataset.hilalStar);
      star.addEventListener("click", () => {
        this.stars = value;
        light(value);
        word.textContent = STAR_WORDS[value] || "";
        sendButton.disabled = false;
      });
      // Hover and keyboard focus preview the same thing, so the rating reads the same
      // way whichever is being used.
      star.addEventListener("pointerenter", () => light(value));
      star.addEventListener("focus", () => light(value));
    }
    this.root.querySelector("[data-hilal-stars]").addEventListener("pointerleave", () =>
      light(this.stars),
    );

    for (const skip of this.root.querySelectorAll("[data-hilal-rate-skip]")) {
      skip.addEventListener("click", () => this.rateDialog.close());
    }
    this.find("[data-hilal-rate-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      this.sendRating();
    });
    this.rateDialog.addEventListener("close", () => {
      if (this.opener && document.contains(this.opener)) this.opener.focus();
      else this.orb.focus();
    });
  }

  /* ── Opening and closing ─────────────────────────────────────────────────── */

  async openChat() {
    this.opener = document.activeElement;
    this.open = true;
    this.window.hidden = false;
    this.orb.setAttribute("aria-expanded", "true");
    this.orb.setAttribute("aria-label", "Close Hilal");
    this.badge.hidden = true;

    if (!prefersReducedMotion()) {
      animate(
        this.window,
        { opacity: [0, 1], transform: ["translateY(16px) scale(.96)", "translateY(0) scale(1)"] },
        { duration: 0.24 },
      );
    }

    if (!this.loaded) await this.loadHistory();
    this.startPolling();
    window.requestAnimationFrame(() => {
      // Measured now rather than at build time: the box has only just been given a
      // width, and until it has one it cannot know how many lines its invitation takes.
      this.grow();
      this.toBottom({ jump: true });
      if (!this.input.disabled) this.input.focus();
      else this.window.querySelector("[data-hilal-close]").focus();
    });
  }

  closeChat({ ask = false } = {}) {
    this.open = false;
    this.stopPolling();
    this.orb.setAttribute("aria-expanded", "false");
    this.orb.setAttribute("aria-label", "Open Hilal, your Hilal Markets assistant");

    const finish = () => {
      this.window.hidden = true;
      // The rating is asked for after the window has gone, so the person is answering
      // about a conversation rather than being asked over the top of one. Only when
      // there was actually a conversation: rating an empty window means nothing.
      if (ask && this.turnsThisVisit > 0) this.askForRating();
      else if (this.opener && document.contains(this.opener)) this.opener.focus();
      else this.orb.focus();
    };
    if (prefersReducedMotion()) finish();
    else dismiss(this.window, { to: 0.96 }).then(finish);
  }

  askForRating() {
    this.stars = 0;
    this.find("[data-hilal-stars-word]").textContent = "";
    this.find("[data-hilal-rate-send]").disabled = true;
    this.find("[data-hilal-rate-comment]").value = "";
    for (const star of this.root.querySelectorAll("[data-hilal-star]")) {
      star.dataset.lit = "false";
      star.setAttribute("aria-checked", "false");
    }
    this.rateDialog.showModal();
    reveal(this.rateDialog.querySelector(".t-dialog-shell"), { from: 0.97 });
  }

  /** Keep Tab inside the window while it is open. */
  trapFocus(event) {
    const focusable = [...this.window.querySelectorAll(
      "button:not([disabled]), a[href], textarea:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])",
    )].filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /* ── Talking to the server ───────────────────────────────────────────────── */

  async call(url, { method = "GET", body } = {}) {
    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(method === "GET" ? {} : { "X-CSRF-Token": this.csrf }),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload && payload.detail;
      const error = new Error(
        (detail && detail.message) || "Hilal could not be reached just now.",
      );
      error.code = (detail && detail.code) || `HTTP_${response.status}`;
      error.status = detail && detail.status;
      throw error;
    }
    return payload;
  }

  async loadHistory() {
    try {
      const payload = await this.call(this.root.dataset.historyUrl);
      this.loaded = true;
      this.thread.replaceChildren();
      for (const message of payload.messages || []) this.draw(message, { animate: false });
      if (!(payload.messages || []).length) this.drawWelcome();
      this.applyStatus(payload.status);
      settleIn(this.thread.children, { from: 8 });
    } catch (error) {
      this.loaded = true;
      this.drawSystem(error.message);
    }
  }

  startPolling() {
    this.stopPolling();
    // Once a second while somebody is looking at it. The allowance has to lock the box
    // the moment it runs out and let it go the moment the day turns over, and neither
    // of those is something the person did — so nothing else would tell us.
    this.timer = window.setInterval(() => this.refreshStatus(), POLL_MS);
  }

  stopPolling() {
    if (this.timer) window.clearInterval(this.timer);
    this.timer = null;
  }

  async refreshStatus() {
    try {
      this.applyStatus(await this.call(this.root.dataset.statusUrl));
    } catch {
      // A status that could not be read is not a reason to lock somebody out, and not
      // a reason to shout at them either. The next tick is a second away.
    }
  }

  /* ── The allowance ───────────────────────────────────────────────────────── */

  applyStatus(status) {
    if (!status) return;
    const was = this.status ? this.status.can_send : null;
    this.status = status;

    const allowance = Number(status.allowance_usd);
    const remaining = Number(status.remaining_usd);
    const share = allowance > 0 ? Math.max(0, Math.min(1, remaining / allowance)) : 1;
    this.meterFill.style.width = `${Math.round(share * 100)}%`;
    this.meter.dataset.state = !status.can_send ? "out" : share <= 0.25 ? "low" : "ok";
    // Said as a share of the day rather than in money. "You have used most of today's
    // messages" is something a person can act on; "$0.0731 of $0.10" is not.
    this.meterText.textContent = !status.can_send
      ? "Today's messages are used up"
      : share <= 0.25
        ? "Nearly all of today's messages are used"
        : share <= 0.6
          ? "About half of today's messages left"
          : "Today's messages: plenty left";

    this.setLocked(!status.can_send, status);

    if (was === false && status.can_send === true) {
      // The day turned over, or a global pause lifted, while they were sitting there.
      this.say("Hilal is ready again.");
      this.drawSystem("Your messages have been topped up. Ask away.");
    }
  }

  setLocked(locked, status) {
    this.root.dataset.state = this.sending ? "thinking" : locked ? "locked" : "";
    this.locked.hidden = !locked;
    this.input.disabled = locked;
    this.updateSend();

    if (!locked) {
      if (this.resetTimer) window.clearInterval(this.resetTimer);
      this.resetTimer = null;
      this.note.textContent = this.note.dataset.restNote || "";
      // Put back, not left behind. Locking rewrites the placeholder to say when
      // messages return; without restoring it, a box that had unlocked hours ago still
      // told the person to come back after midnight.
      this.setPlaceholder(this.placeholder);
      return;
    }

    this.find("[data-hilal-locked-text]").textContent =
      status.locked_reason || "You have used today's messages with Hilal.";
    this.note.textContent = "Back tomorrow";
    this.setPlaceholder("Back after midnight UTC");

    const upgrade = this.find("[data-hilal-upgrade]");
    upgrade.hidden = !status.offer_upgrade;
    upgrade.href = this.root.dataset.billingUrl;
    this.find("[data-hilal-locked-title]").textContent = status.offer_upgrade
      ? "That is all for today"
      : "Paused for now";

    this.startCountdown(status.resets_at);
  }

  /** Count down to the reset, from the instant the server gave. */
  startCountdown(resetsAt) {
    if (this.resetTimer) window.clearInterval(this.resetTimer);
    const target = new Date(resetsAt);
    const line = this.find("[data-hilal-locked-reset]");
    if (Number.isNaN(target.getTime())) { line.textContent = ""; return; }
    const tick = () => {
      const left = Math.max(0, target.getTime() - Date.now());
      const hours = Math.floor(left / 3_600_000);
      const minutes = Math.floor((left % 3_600_000) / 60_000);
      line.textContent = hours > 0
        ? `More in ${hours} hour${hours === 1 ? "" : "s"} ${minutes} minute${minutes === 1 ? "" : "s"}.`
        : `More in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    };
    tick();
    this.resetTimer = window.setInterval(tick, 30_000);
  }

  /* ── Asking ──────────────────────────────────────────────────────────────── */

  async ask(rawText) {
    const text = (rawText || "").trim();
    if (!text || this.sending) return;
    if (this.status && !this.status.can_send) return;
    if (text.length > this.maxLength) {
      this.drawSystem(`That is a bit long. Try asking one thing at a time.`);
      return;
    }

    this.input.value = "";
    this.grow();
    this.chips.hidden = true;
    this.chips.replaceChildren();
    this.draw({ role: "user", text });
    this.setSending(true);

    try {
      const payload = await this.call(this.root.dataset.messageUrl, {
        method: "POST",
        body: {
          message: text,
          // What they can see, from the one place that knows. Taken at the moment of
          // asking rather than when the window opened: somebody types a question about
          // the card they have just been editing, not about the board of a minute ago.
          view: snapshot(),
          // The server's guard against a double send. A retry finds the answer it
          // already produced rather than paying for a second one.
          client_message_id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        },
      });
      this.turnsThisVisit += 1;
      this.lastAssistantId = payload.message_id;
      this.draw({
        role: "assistant",
        text: payload.reply,
        mode: payload.mode,
        id: payload.message_id,
      });
      this.offer(payload.suggestions || []);
      this.applyStatus(payload.status);
      this.say("Hilal replied.");
    } catch (error) {
      if (error.status) this.applyStatus(error.status);
      this.drawSystem(error.message);
      this.say(error.message);
    } finally {
      this.setSending(false);
      if (!this.input.disabled) this.input.focus();
    }
  }

  setSending(sending) {
    this.sending = sending;
    this.typing.hidden = !sending;
    this.root.dataset.state = sending
      ? "thinking"
      : this.status && !this.status.can_send ? "locked" : "";
    this.updateSend();
    if (sending) this.toBottom();
  }

  updateSend() {
    const locked = Boolean(this.status && !this.status.can_send);
    this.send.disabled = this.sending || locked || !this.input.value.trim();
  }

  /* ── Drawing ─────────────────────────────────────────────────────────────── */

  draw(message, { animate: withMotion = true } = {}) {
    const row = document.createElement("li");
    row.className = "hilal-msg";
    row.dataset.who = message.role === "user" ? "user" : "assistant";
    if (message.id) row.dataset.messageId = message.id;

    if (message.role !== "user") {
      const mark = document.createElement("span");
      mark.className = "hilal-bubble-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.innerHTML = icon("spark", "icon-sm");
      row.append(mark);
    }

    const bubble = document.createElement("div");
    bubble.className = "hilal-bubble";
    if (message.mode) bubble.dataset.mode = message.mode;
    // Set as text, split into paragraphs. Never as markup: the answer is words from a
    // model, and a model's words are data.
    for (const paragraph of String(message.text || "").split(/\n{2,}/)) {
      const line = document.createElement("p");
      line.textContent = paragraph.trim();
      if (line.textContent) bubble.append(line);
    }
    // Said in words as well as by colour and edge — WCAG 2.2 SC 1.4.1, and the only
    // form that survives a screen reader or a printout.
    const mark = MODE_NOTES[message.mode];
    if (mark) {
      const note = document.createElement("span");
      note.className = "hilal-msg-note";
      note.dataset.kind = message.mode;
      note.innerHTML = icon(mark.icon, "icon-sm");
      note.append(document.createTextNode(mark.text));
      bubble.append(note);
    }
    row.append(bubble);

    if (message.role !== "user" && message.id) {
      const report = document.createElement("button");
      report.type = "button";
      report.className = "hilal-msg-report";
      report.dataset.hilalReportOne = message.id;
      report.setAttribute("aria-label", "Report this answer");
      report.innerHTML = icon("flag", "icon-sm");
      row.append(report);
    }

    this.thread.append(row);
    if (withMotion) {
      animate(
        row,
        { opacity: [0, 1], transform: ["translateY(8px)", "translateY(0)"] },
        { duration: 0.22 },
      );
      this.toBottom();
    }
    return row;
  }

  /** Hilal's own opening line, written here because no turn has happened yet.
   *
   *  Deliberately contains no Islamic greeting: those are Hilal's own words, written
   *  by the model when it answers, not a phrase this file stamps on every visitor. */
  drawWelcome() {
    this.draw({
      role: "assistant",
      text:
        "Hello, and welcome. I am Hilal, and I am glad you are here.\n\n"
        + "Ask me anything about what we have on record: whether a coin is listed, what "
        + "its Shariah review says, which standard was used, what changed and when, "
        + "what is in a Passport, or what your plan covers. I can also sit with you on "
        + "the Monitor page and walk you through building it, one card at a time. Write "
        + "in any language you like — I will answer in the same one.\n\n"
        + "Two honest things first. The choices and the numbers stay yours: I will "
        + "never tell you what to buy or sell, and I will never decide a strategy for "
        + "you. And I am new here, so I can get things wrong — please check the screen "
        + "as we go. What shall we look at?",
      mode: "GREETING",
    });
    this.offer(this.openers());
  }

  /** Three things worth asking, chosen for the page they are standing on. */
  openers() {
    if ((document.body.dataset.dashboardPage || "").includes("monitor")) {
      return [
        "What is my board still missing?",
        "How do I add a condition?",
        "What does this card mean?",
      ];
    }
    return [
      "Which coins are eligible?",
      "What is a Passport?",
      "What does my plan cover?",
    ];
  }

  drawSystem(text) {
    const row = document.createElement("li");
    row.className = "hilal-msg";
    row.dataset.who = "assistant";
    const mark = document.createElement("span");
    mark.className = "hilal-bubble-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.innerHTML = icon("info", "icon-sm");
    const bubble = document.createElement("div");
    bubble.className = "hilal-bubble";
    const line = document.createElement("p");
    line.textContent = text;
    bubble.append(line);
    row.append(mark, bubble);
    this.thread.append(row);
    reveal(row, { from: 0.98 });
    this.toBottom();
  }

  offer(suggestions) {
    this.chips.replaceChildren();
    if (!suggestions.length) { this.chips.hidden = true; return; }
    for (const suggestion of suggestions) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "hilal-chip";
      chip.dataset.hilalChip = suggestion;
      chip.textContent = suggestion;
      this.chips.append(chip);
    }
    this.chips.hidden = false;
    settleIn(this.chips.children, { from: 6, delayStep: 0.035 });
  }

  toBottom({ jump = false } = {}) {
    window.requestAnimationFrame(() => {
      this.scroll.scrollTo({
        top: this.scroll.scrollHeight,
        behavior: jump || prefersReducedMotion() ? "auto" : "smooth",
      });
    });
  }

  /**
   * Keep the writing box exactly as tall as what is in it.
   *
   * A scrollbar appeared on an *empty* box. The invitation inside it was longer than
   * one line at this width, so the box was already overflowing before anybody had typed
   * a character — a bar down the side of a box with nothing in it, which reads as
   * broken because it is.
   *
   * Two things fix it and both are needed. The words are short enough to fit on one
   * line, and the box measures itself instead of trusting that they will: at start-up,
   * when the window changes width, and whenever the invitation is rewritten. The bar
   * itself only ever appears once the box has stopped growing, which is the only moment
   * there is anything to scroll.
   */
  grow() {
    // A box with no width cannot say how many lines its words take. The status is read
    // once before anybody opens the window, and measuring then produced a height of
    // zero that stayed until the next keystroke.
    if (!this.input.offsetParent) return;
    this.input.style.height = "auto";
    // The box is `border-box`, so a height of exactly `scrollHeight` leaves the
    // content two pixels short of what it needs — its own border eats them. Measured
    // rather than assumed: a hard-coded 2 is right until somebody changes the border.
    const frame = this.input.offsetHeight - this.input.clientHeight;
    const wanted = this.input.scrollHeight + frame;
    this.input.style.height = `${Math.min(GROW_LIMIT, wanted)}px`;
    this.input.style.overflowY = wanted > GROW_LIMIT ? "auto" : "hidden";
  }

  /** Change the invitation in the box, and let the box resize around it.
   *
   *  Does nothing when the words are already right. The status is read once a second
   *  while the window is open, and re-measuring the box on every one of those ticks
   *  would be work for nothing — while somebody is typing into it. */
  setPlaceholder(text) {
    if (this.input.placeholder === text) return;
    this.input.placeholder = text;
    this.grow();
  }

  say(text) {
    this.announce.textContent = "";
    window.requestAnimationFrame(() => { this.announce.textContent = text; });
  }

  /* ── Reporting and rating ────────────────────────────────────────────────── */

  openReport(messageId) {
    this.reportingId = messageId;
    const row = this.thread.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
    const quote = this.find("[data-hilal-report-quote]");
    quote.textContent = row ? row.querySelector(".hilal-bubble").textContent.trim() : "";
    this.find("[data-hilal-report-note]").value = "";
    this.reportDialog.showModal();
    reveal(this.reportDialog.querySelector(".t-dialog-shell"), { from: 0.97 });
  }

  async sendReport() {
    const reason = this.root.querySelector("input[name='hilal-reason']:checked");
    try {
      await this.call(this.root.dataset.reportUrl, {
        method: "POST",
        body: {
          message_id: this.reportingId,
          reason: reason ? reason.value : "other",
          note: this.find("[data-hilal-report-note]").value.trim() || null,
        },
      });
      this.reportDialog.close();
      const row = this.thread.querySelector(
        `[data-message-id="${CSS.escape(this.reportingId)}"]`,
      );
      const button = row && row.querySelector("[data-hilal-report-one]");
      if (button) {
        button.dataset.done = "true";
        button.innerHTML = icon("check", "icon-sm");
        button.setAttribute("aria-label", "You reported this answer");
        button.disabled = true;
      }
      this.say("Thank you. The report was sent.");
      this.drawSystem("Thank you — that is with the team. It helps more than you know.");
    } catch (error) {
      this.say(error.message);
      this.drawSystem(error.message);
      this.reportDialog.close();
    }
  }

  async sendRating() {
    if (!this.stars) return;
    try {
      await this.call(this.root.dataset.ratingUrl, {
        method: "POST",
        body: {
          stars: this.stars,
          comment: this.find("[data-hilal-rate-comment]").value.trim() || null,
        },
      });
      this.say("Thank you for the rating.");
    } catch {
      // A rating that did not send is not worth interrupting anybody over. They have
      // already closed the chat; an error about it would be the last thing they saw.
    }
    this.turnsThisVisit = 0;
    this.rateDialog.close();
  }
}

/* Started at the bottom, after the class exists.
 *
 * This is a module, so it runs deferred — by the time it executes the document has
 * already been parsed and `ready()` calls straight through. Started from the top of the
 * file that meant `new HilalChat(...)` ran before the class declaration had been
 * evaluated, which is a temporal-dead-zone error, and the whole assistant was dead on
 * every page: no history, no polling, no clicks. */
ready(() => {
  const root = document.querySelector("[data-hilal]");
  if (root) new HilalChat(root);
});
