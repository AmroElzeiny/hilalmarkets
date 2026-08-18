/* The Settings page at /dashboard/settings.
 *
 * There is no Save button. Every control saves the whole settings set as soon as it is
 * used, so nothing can be changed and then lost by walking away from the page.
 *
 * The *whole* set, never one field: two controls used quickly cannot then arrive out of
 * order and leave the account holding the older press. The server's one owner,
 * `AccountSettingsService`, decides which of the values it will keep.
 *
 * Nothing here decides what a setting may be. Which channels exist, which sounds exist
 * and which exchanges exist were all rendered by the server.
 */

import { manageDialog } from "./hm-dialog.js";
import { followSections } from "./hm-jump.js";
import { animate, attention, prefersReducedMotion, settleIn } from "./hm-motion.js";

const root = document.querySelector("[data-settings-root]");
if (root) start(root);

/** How long to wait after typing before saving. Long enough to finish a number. */
const TYPING_PAUSE = 700;

/** What the bar at the top says in each of its states. */
const SAVED_WORDS = {
  idle: {
    icon: "check",
    title: "Everything here saves by itself",
    detail: "",
  },
  saving: { icon: "refresh", title: "Saving…", detail: "One moment." },
  saved: { icon: "check", title: "Saved", detail: "Your change is kept." },
  failed: {
    icon: "alert",
    title: "Nothing was saved",
    detail: "Your settings are unchanged. Please try again.",
  },
};

function start(scope) {
  const csrf = document.body.dataset.csrfToken || "";
  const bar = scope.querySelector("[data-g-saved]");
  const said = scope.querySelector("[data-g-said]");
  const retry = scope.querySelector("[data-g-retry]");
  let timer = null;
  let backToIdle = null;
  let inFlight = false;

  /** Redraw the icons inside markup this file just changed. */
  function paint(element) {
    if (!element || typeof window.icon !== "function") return;
    element.querySelectorAll("[data-icon]").forEach((node) => {
      node.innerHTML = window.icon(node.dataset.icon, node.dataset.iconClass || "icon");
    });
  }

  /** Say one thing, once, to a screen reader. */
  function say(text) {
    if (!said) return;
    said.textContent = "";
    window.requestAnimationFrame(() => { said.textContent = text; });
  }

  /* ── The saving state ────────────────────────────────────────────────────── */

  function showState(state) {
    if (!bar) return;
    const words = SAVED_WORDS[state] || SAVED_WORDS.idle;
    bar.dataset.state = state;
    bar.querySelector("[data-g-saved-title]").textContent = words.title;
    bar.querySelector("[data-g-saved-detail]").textContent = words.detail;
    const glyph = bar.querySelector(".g-saved-icon [data-icon]");
    if (glyph) {
      glyph.dataset.icon = words.icon;
      paint(bar.querySelector(".g-saved-icon"));
    }
    if (retry) retry.hidden = state !== "failed";
    window.clearTimeout(backToIdle);
    if (state === "saved") {
      // Back to its ordinary self, so the bar is never stuck saying something stale.
      backToIdle = window.setTimeout(() => showState("idle"), 4000);
    }
  }

  /* ── Reading the page ──────────────────────────────────────────────────────
   *
   * One walk over every control, producing the whole settings set. Each control
   * carries the name of the setting it is, so nothing here has to know the page's
   * layout — a control moved to another group keeps working.
   */

  function pressedValues(key) {
    return [...scope.querySelectorAll(`[data-g-set="${key}"][aria-pressed="true"]`)]
      .filter((button) => !button.disabled)
      .map((button) => button.dataset.value);
  }

  function pickedValue(key) {
    const chosen = scope.querySelector(`[data-g-pick="${key}"] input:checked`);
    return chosen ? chosen.value : "";
  }

  function switchedOn(key) {
    const button = scope.querySelector(`[data-g-switch="${key}"]`);
    return button ? button.getAttribute("aria-checked") === "true" : false;
  }

  function numberValue(key, fallback) {
    const input = scope.querySelector(`[data-g-number="${key}"]`);
    const value = Number.parseInt(input?.value ?? "", 10);
    if (!Number.isFinite(value)) return fallback;
    // Held inside the control's own bounds before it is sent. The server clamps too;
    // doing it here as well is what stops the box showing 0 while the record holds 1.
    const low = Number.parseInt(input.min || "", 10);
    const high = Number.parseInt(input.max || "", 10);
    let held = value;
    if (Number.isFinite(low)) held = Math.max(low, held);
    if (Number.isFinite(high)) held = Math.min(high, held);
    if (held !== value) input.value = String(held);
    return held;
  }

  function collect() {
    const everyDay = scope.querySelector("[data-g-every-day]");
    const days =
      everyDay?.getAttribute("aria-checked") === "true"
        ? ["Every Day"]
        : pressedValues("alert_days");
    return {
      timezone: scope.querySelector('[data-g-select="timezone"]')?.value || "UTC",
      near_miss_enabled: switchedOn("near_miss_enabled"),
      near_miss_threshold: numberValue("near_miss_threshold", 70),
      maximum_alerts_per_hour: numberValue("maximum_alerts_per_hour", 50),
      maximum_alerts_per_day: numberValue("maximum_alerts_per_day", 500),
      alert_channels: pressedValues("alert_channels"),
      providers: pressedValues("providers"),
      alert_days: days,
      alert_hours: pressedValues("alert_hours"),
      finished_opportunity_alerts: switchedOn("finished_opportunity_alerts"),
      muted_symbols: [...scope.querySelectorAll("[data-g-chip]")].map(
        (chip) => chip.dataset.value,
      ),
      compliance_alert_channels: pressedValues("compliance_alert_channels"),
      compliance_alert_digest: pickedValue("compliance_alert_digest") || "immediate",
      dashboard_notifications_enabled: switchedOn("dashboard_notifications_enabled"),
      dashboard_notification_sound: pickedValue("dashboard_notification_sound") || "chime",
      forming_dashboard_notifications: switchedOn("forming_dashboard_notifications"),
      forming_notification_sound: pickedValue("forming_notification_sound") || "pulse",
      qualification_change_alerts: switchedOn("qualification_change_alerts"),
    };
  }

  /* ── Saving ──────────────────────────────────────────────────────────────── */

  async function save(what) {
    if (inFlight) {
      // Another press arrived while one was in the air. Queue exactly one more rather
      // than sending both: the second read of the page is the newer truth anyway.
      window.clearTimeout(timer);
      timer = window.setTimeout(() => save(what), 250);
      return;
    }
    inFlight = true;
    showState("saving");
    try {
      const response = await window.fetch("/api/v1/dashboard/preferences/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        credentials: "same-origin",
        body: JSON.stringify(collect()),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail?.message || "");
      showState("saved");
      say(what ? `${what} Saved.` : "Saved.");
    } catch (error) {
      showState("failed");
      const detail = bar?.querySelector("[data-g-saved-detail]");
      if (detail && error instanceof Error && error.message) {
        detail.textContent = `${error.message} Your settings are unchanged.`;
      }
      say("That did not save. Your settings are unchanged.");
      if (bar) attention(bar);
    } finally {
      inFlight = false;
    }
  }

  /** Save after a short pause, so typing a three-digit number is one save, not three. */
  function saveSoon(what) {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => save(what), TYPING_PAUSE);
  }

  retry?.addEventListener("click", () => save(""));

  /* ── Switches ────────────────────────────────────────────────────────────── */

  for (const button of scope.querySelectorAll("[data-g-switch]")) {
    button.addEventListener("click", () => {
      const on = button.getAttribute("aria-checked") !== "true";
      button.setAttribute("aria-checked", String(on));
      const name = button.querySelector(".t-switch-words strong")?.textContent?.trim() || "";
      showDependants();
      save(`${name}: ${on ? "on" : "off"}.`);
    });
  }

  /** A row that only makes sense while its switch is on. */
  function showDependants() {
    for (const row of scope.querySelectorAll("[data-g-shown-by]")) {
      const on = switchedOn(row.dataset.gShownBy);
      if (on === !row.hidden) continue;
      row.hidden = !on;
      if (on && !prefersReducedMotion()) {
        animate(row, { opacity: [0, 1] }, { duration: 0.2 });
      }
    }
  }
  showDependants();

  /* ── Multi-choice groups ─────────────────────────────────────────────────── */

  /** Flip one choice, tell the page, and save. */
  function applyChoice(button, on) {
    button.setAttribute("aria-pressed", String(on));
    const name =
      button.querySelector(".g-choice-copy strong")?.textContent?.trim() ||
      button.dataset.value;
    if (button.dataset.gSet === "alert_hours") drawHours();
    save(`${name}: ${on ? "on" : "off"}.`);
  }

  /* Asking before an exchange goes off. Every other control here is undone by pressing
     it again; this one stops Watchlists looking at the market, and somebody can walk
     away without noticing. */
  const askDialog = document.querySelector("[data-g-ask-dialog]");
  const ask = manageDialog(askDialog, {
    closers: ["[data-g-ask-close]", "[data-g-ask-cancel]"],
  });
  let asking = null;
  // Dismissed by Escape or by a click on the backdrop is still "no". Forgetting which
  // button asked keeps a later confirmation from acting on a stale one.
  askDialog?.addEventListener("close", () => { asking = null; });
  askDialog?.querySelector("[data-g-ask-go]")?.addEventListener("click", async () => {
    const button = asking;
    asking = null;
    await ask.close();
    if (button) applyChoice(button, false);
  });

  for (const button of scope.querySelectorAll("[data-g-set]")) {
    if (button.tagName !== "BUTTON") continue;
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const on = button.getAttribute("aria-pressed") !== "true";

      if (button.dataset.gSet === "providers" && !on) {
        // The last one may not be switched off. With none chosen the server falls back
        // to the first exchange, so the page would show nothing selected while the
        // account really had one — and a person cannot see a fallback happen.
        if (pressedValues("providers").length <= 1) {
          say("At least one exchange has to stay on, or nothing can be checked.");
          attention(button);
          return;
        }
        asking = button;
        const name = button.querySelector(".g-choice-copy strong")?.textContent?.trim() || "";
        const heading = askDialog?.querySelector("[data-g-ask-name]");
        if (heading) heading.textContent = name;
        ask.open(button);
        return;
      }

      applyChoice(button, on);
    });
  }

  /* ── Radio pickers ───────────────────────────────────────────────────────── */

  for (const group of scope.querySelectorAll("[data-g-pick]")) {
    group.addEventListener("change", () => {
      const chosen = group.querySelector("input:checked");
      const words = chosen?.closest(".g-pick")?.querySelector("span")?.textContent || "";
      save(words ? `${words}.` : "");
    });
  }

  /* ── Numbers and the time zone ───────────────────────────────────────────── */

  for (const input of scope.querySelectorAll("[data-g-number]")) {
    input.addEventListener("input", () => saveSoon(""));
    input.addEventListener("change", () => saveSoon(""));
  }

  const zone = scope.querySelector('[data-g-select="timezone"]');
  zone?.addEventListener("change", () => save(`Time zone: ${zone.value}.`));

  /* ── The week ────────────────────────────────────────────────────────────── */

  const everyDay = scope.querySelector("[data-g-every-day]");
  const week = scope.querySelector("[data-g-week]");
  everyDay?.addEventListener("click", () => {
    const on = everyDay.getAttribute("aria-checked") !== "true";
    everyDay.setAttribute("aria-checked", String(on));
    everyDay.querySelector("[data-g-every-day-label]").textContent = on
      ? "Every day"
      : "Only the days I pick";
    if (week) week.dataset.everyDay = String(on);
    // The single days are switched off rather than hidden while "every day" is on:
    // a control that disappears takes its state with it and a person cannot see what
    // they had chosen before.
    for (const day of scope.querySelectorAll('[data-g-set="alert_days"]')) {
      day.disabled = on;
    }
    save(on ? "Every day." : "Only the days you pick.");
  });

  /* ── The day ─────────────────────────────────────────────────────────────── */

  for (const part of scope.querySelectorAll("[data-g-part]")) {
    part.addEventListener("click", () => {
      const wanted = (part.dataset.gPart || "").split(",").filter(Boolean);
      const buttons = wanted
        .map((hour) => scope.querySelector(`[data-g-set="alert_hours"][data-value="${hour}"]`))
        .filter(Boolean);
      // Pressing a part of the day takes all of it, unless it is already all taken —
      // then it gives it all back. One control, both directions.
      const allOn = buttons.every((button) => button.getAttribute("aria-pressed") === "true");
      buttons.forEach((button) => button.setAttribute("aria-pressed", String(!allOn)));
      drawHours();
      const name = part.textContent.trim().split("—")[0].trim();
      save(`${name}: ${allOn ? "off" : "on"}.`);
    });
  }

  const hoursWords = scope.querySelector("[data-g-hours-words]");
  const hoursNote = scope.querySelector("[data-g-hours-note]");

  /** Say, in words, what the chosen hours actually mean. */
  function drawHours() {
    if (!hoursWords || !hoursNote) return;
    const chosen = pressedValues("alert_hours");
    if (!chosen.length) {
      hoursNote.dataset.tone = "";
      hoursWords.textContent = "No hour is picked, so we can tell you at any time of day.";
      return;
    }
    hoursNote.dataset.tone = chosen.length < 3 ? "warning" : "";
    const sorted = [...chosen].sort();
    hoursWords.textContent =
      chosen.length === 1
        ? `Only during the hour from ${sorted[0]}. Outside it, a notice waits for you here.`
        : `${chosen.length} hours picked, from ${sorted[0]} to ${sorted[sorted.length - 1]}. Outside them, a notice waits for you here.`;
  }
  drawHours();

  /* ── Silenced coins ──────────────────────────────────────────────────────── */

  const chips = scope.querySelector("[data-g-chips]");
  const chipsEmpty = scope.querySelector("[data-g-chip-empty]");
  const muteInput = scope.querySelector("[data-g-mute-input]");
  const muteAdd = scope.querySelector("[data-g-mute-add]");
  const muteLimit = Number(chips?.dataset.limit) || 50;

  function drawChips() {
    if (chipsEmpty) chipsEmpty.hidden = Boolean(chips?.querySelector("[data-g-chip]"));
  }

  function removeChip(chip) {
    const value = chip.dataset.value;
    chip.remove();
    drawChips();
    save(`${value} is no longer silenced.`);
  }

  chips?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-g-chip-remove]");
    if (!button) return;
    removeChip(button.closest("[data-g-chip]"));
  });

  function addChip() {
    if (!chips || !muteInput) return;
    const value = muteInput.value.trim().toUpperCase().replace(/-/g, "/");
    if (!value) return;
    if (chips.querySelector(`[data-g-chip][data-value="${CSS.escape(value)}"]`)) {
      muteInput.value = "";
      say(`${value} is already silenced.`);
      return;
    }
    if (chips.querySelectorAll("[data-g-chip]").length >= muteLimit) {
      say(`You can silence ${muteLimit} coins. Take one out before adding another.`);
      return;
    }
    const chip = document.createElement("li");
    chip.className = "g-chip";
    chip.dataset.gChip = "";
    chip.dataset.value = value;
    const words = document.createElement("span");
    // Written in as text. A coin name is content, and content that can become markup is
    // a way in.
    words.textContent = value;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.gChipRemove = "";
    remove.setAttribute("aria-label", `Stop silencing ${value}`);
    const glyph = document.createElement("span");
    glyph.dataset.icon = "close";
    glyph.dataset.iconClass = "icon-sm";
    remove.append(glyph);
    chip.append(words, remove);
    chips.append(chip);
    paint(chip);
    muteInput.value = "";
    drawChips();
    if (!prefersReducedMotion()) animate(chip, { opacity: [0, 1], transform: ["scale(.9)", "scale(1)"] }, { duration: 0.2 });
    save(`${value} is silenced.`);
  }

  muteAdd?.addEventListener("click", addChip);
  muteInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    // Enter inside this box adds a coin; it must not reach anything that would treat it
    // as submitting the page.
    event.preventDefault();
    addChip();
  });
  drawChips();

  /* ── Hearing a sound before choosing it ──────────────────────────────────── */

  for (const button of scope.querySelectorAll("[data-g-hear]")) {
    button.addEventListener("click", () => {
      const sound = pickedValue(button.dataset.gHear);
      if (!sound || sound === "none") {
        say("You chose no sound, so there is nothing to hear.");
        return;
      }
      // The one player the real notice uses, so the preview can never be a different
      // tone from the thing it is previewing.
      window.hmPlayAlertSound?.(sound);
    });
  }

  /* ── Arriving ────────────────────────────────────────────────────────────── */

  /* The bar at the top marks which group is really on screen, not which link was last
     pressed. Shared with the plan page, which has the same bar. */
  followSections(scope.querySelectorAll("[data-g-jump-link]"), scope);

  settleIn(scope.querySelectorAll("[data-g-group]"), { from: 10, delayStep: 0.024 });
}
