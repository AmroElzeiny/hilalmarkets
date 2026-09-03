/* The plan page at /dashboard/subscription.
 *
 * Nothing here decides a price. The server rendered every plan, every figure and
 * whether each one can be bought at all; this file walks somebody through the three
 * steps of paying and shows the same numbers back to them at each one.
 *
 * Popups are `hm-dialog.js` and motion is `hm-motion.js`, shared with every other page
 * on this path — one focus trap, one easing scale, one reduced-motion decision.
 */

import { manageDialog, paintIcons } from "./hm-dialog.js";
import { followSections } from "./hm-jump.js";
import { animate, countTo, prefersReducedMotion, settleIn } from "./hm-motion.js";

const root = document.querySelector("[data-subscription-root]");
if (root) start(root);

/** The last step. Named once, because three places ask "are we at the end yet?". */
const LAST_STEP = 3;

function start(scope) {
  const facts = readFacts();
  const dialog = document.querySelector("[data-s-dialog]");
  const checkout = manageDialog(dialog, { closers: ["[data-s-close]"] });

  /* ── Arriving ────────────────────────────────────────────────────────────
   *
   * Each allowance bar fills to its real width, and each count counts up to its real
   * number. Both movements say the same thing the words beside them say — "this much
   * of it is gone" — so the motion is the meaning rather than an entrance.
   */

  for (const bar of scope.querySelectorAll("[data-s-bar]")) {
    const fill = `${Math.max(0, Math.min(100, Number(bar.dataset.fill) || 0))}%`;
    if (prefersReducedMotion()) {
      bar.style.width = fill;
    } else {
      bar.style.width = "0%";
      window.requestAnimationFrame(() => { bar.style.width = fill; });
    }
  }
  for (const node of scope.querySelectorAll("[data-s-count]")) {
    // A count of things a person can go and look at, never a price. A price that
    // visibly travelled through values it never had would be inventing a charge.
    countTo(node, Number(node.dataset.countTo) || 0);
  }

  settleIn(scope.querySelectorAll("[data-s-plan]"), { from: 12 });
  settleIn(scope.querySelectorAll("[data-s-payment]"), { from: 6, delayStep: 0.02 });
  const banner = scope.querySelector("[data-s-banner]");
  if (banner) animate(banner, { opacity: [0, 1] }, { duration: 0.24 });

  /* The jump links mark where the person actually is, rather than where they last
     pressed. Shared with the Settings page, which needs the same bar. */
  followSections(scope.querySelectorAll("[data-s-jump-link]"), scope);

  /* ── Choosing a plan ─────────────────────────────────────────────────────── */

  let step = 1;
  let plan = null;

  const stepOf = dialog?.querySelector("[data-s-step-of]");
  const steps = [...(dialog?.querySelectorAll("[data-s-step]") || [])];
  const panels = [...(dialog?.querySelectorAll("[data-s-panel]") || [])];
  const back = dialog?.querySelector("[data-s-back]");
  const next = dialog?.querySelector("[data-s-next]");
  const pay = dialog?.querySelector("[data-s-pay]");
  const said = dialog?.querySelector("[data-s-said]");
  const form = dialog?.querySelector("[data-s-form]");
  const agree = dialog?.querySelector("[data-s-agree]");
  const methods = [...(dialog?.querySelectorAll('[data-s-methods] input[type="radio"]') || [])];

  /** Say one thing, once, where it is both seen and heard.
   *
   * Written straight in rather than on the next frame. The line is hidden while it is
   * empty, and a hidden element cannot take the keyboard — so setting it a frame later
   * meant every attempt to move focus onto an error message quietly did nothing. The
   * clear-and-rewrite dance is kept only for repeating the *same* sentence, which is
   * the one case a screen reader would otherwise stay silent about. */
  function say(text, tone = "") {
    if (!said) return;
    said.dataset.tone = tone;
    if (said.textContent === text) {
      said.textContent = "";
      window.requestAnimationFrame(() => { said.textContent = text; });
      return;
    }
    said.textContent = text;
  }

  /** Draw the popup for the step we are on. */
  function draw() {
    for (const item of steps) {
      const number = Number(item.dataset.sStep);
      item.dataset.state = number < step ? "done" : number === step ? "now" : "next";
      const dot = item.querySelector(".s-step-dot");
      if (dot) {
        // A finished step becomes a tick. The number is only useful while it is ahead
        // of you; behind you, "done" is the fact worth showing.
        dot.innerHTML = number < step
          ? '<span data-icon="check" data-icon-class="icon-sm"></span>'
          : String(number);
        paintIcons(dot);
      }
    }
    for (const panel of panels) {
      const shown = Number(panel.dataset.sPanel) === step;
      panel.hidden = !shown;
      if (shown && !prefersReducedMotion()) {
        animate(panel, { opacity: [0, 1], transform: ["translateY(6px)", "translateY(0px)"] }, { duration: 0.2 });
      }
    }
    if (stepOf) stepOf.textContent = `Step ${step} of ${LAST_STEP}`;
    if (back) back.hidden = step === 1;
    if (next) next.hidden = step === LAST_STEP;
    if (pay) pay.hidden = step !== LAST_STEP;
    refreshPay();
  }

  /** Draw the ways of paying that really work for the plan being bought.
   *
   * The server decided this, per plan, and sent the answer with the plan. Nothing here
   * works out availability: a second copy of that rule living in the browser is how the
   * popup came to offer Card while checkout refused it.
   */
  function fillMethods() {
    const decided = (plan && plan.pay_methods) || {};
    for (const input of methods) {
      const choice = decided[input.value] || null;
      // No answer for this method means we were not told it works. Refusing to offer it
      // is the safe reading; offering it is the one that ends in a dead end.
      const available = Boolean(choice && choice.available);
      input.disabled = !available;
      if (!available) input.checked = false;
      const label = input.closest("[data-s-method]");
      if (label) {
        label.dataset.available = available ? "true" : "false";
        label.setAttribute("aria-disabled", String(!available));
        const note = label.querySelector("[data-s-method-note]");
        if (note && choice && typeof choice.note === "string") note.textContent = choice.note;
      }
    }
    // One way left is not a choice. Ticking it saves a step, and the Pay button still
    // waits for the agreement box below it.
    const usable = methods.filter((input) => !input.disabled);
    if (usable.length === 1) usable[0].checked = true;
    refreshPay();
  }

  /** Whether the last button may be pressed, and what it says while it cannot. */
  function refreshPay() {
    if (!pay) return;
    const method = methods.find((input) => input.checked && !input.disabled);
    const agreed = Boolean(agree?.checked);
    pay.disabled = step !== LAST_STEP || !method || !agreed;
    const label = pay.querySelector("[data-s-pay-label]");
    if (!label) return;
    if (!method) label.textContent = "Choose how to pay";
    else if (!agreed) label.textContent = "Tick the box above first";
    else if (method.value === "crypto") label.textContent = "Go to the crypto payment page";
    else label.textContent = "Go to the card payment page";
  }

  /** The order line, for whatever this order now costs.
   *
   * `full_price` is what a checkout charges with no code, so that is what the popup
   * opens at. It used to open at the card's headline figure, which is the launch-code
   * price — so somebody who typed no code read one number here and was sent to a payment
   * page for a larger one.
   */
  function paintOrder(amount, wasAmount) {
    if (!dialog || !plan) return;
    const total = dialog.querySelector("[data-s-order-total]");
    const when = dialog.querySelector("[data-s-order-when]");
    if (total) {
      total.textContent = money(amount);
      // The old price stays visible beside the new one, crossed out, so the discount is
      // something a person can see rather than something they have to remember.
      const struck = wasAmount ? ` was ${money(wasAmount)}` : "";
      total.setAttribute(
        "aria-label",
        wasAmount ? `${money(amount)} a month,${struck}` : `${money(amount)} a month`,
      );
    }
    const struckNode = dialog.querySelector("[data-s-order-was]");
    if (struckNode) {
      struckNode.textContent = wasAmount ? money(wasAmount) : "";
      struckNode.hidden = !wasAmount;
    }
    if (when) {
      when.textContent =
        `${money(amount)} today, then ${money(amount)} every month until you stop it. ` +
        "You can stop it whenever you like.";
    }
  }

  /** Money as this product writes it: `$15`, and cents only when there are cents. */
  function money(amount) {
    const value = Number(amount);
    if (!Number.isFinite(value)) return String(amount);
    const rounded = Math.round(value * 100) / 100;
    return Number.isInteger(rounded) ? `$${rounded}` : `$${rounded.toFixed(2)}`;
  }

  /** Everything in the popup that is about *which* plan, in one place. */
  function fillOrder(code) {
    plan = facts.find((item) => item.code === code) || null;
    if (!plan || !dialog) return false;
    // What a checkout really charges with no code. `monthly_price` is the card headline,
    // which already carries the launch-code discount.
    const price = Number(plan.full_price ?? plan.monthly_price) || 0;
    dialog.querySelector("[data-s-plan-code]").value = plan.code;
    dialog.querySelector("[data-s-order-plan]").textContent = plan.name;
    paintOrder(price, "");
    const box = dialog.querySelector("[data-discount]");
    if (box) {
      // The box prices against the plan now being bought, so it has to be told which one
      // that is before anybody presses Apply.
      box.dataset.discountFull = String(price);
    }
    const includes = dialog.querySelector("[data-s-includes]");
    includes.textContent = "";
    for (const feature of (plan.features || []).slice(0, 5)) {
      const row = document.createElement("li");
      const glyph = document.createElement("span");
      glyph.dataset.icon = "check";
      glyph.dataset.iconClass = "icon-sm";
      const words = document.createElement("span");
      // Written in as text, never as markup: a feature name is content, and content
      // that can become HTML is a way in.
      words.textContent = feature;
      row.append(glyph, words);
      includes.append(row);
    }
    paintIcons(includes);
    const refund = dialog.querySelector("[data-s-refund]");
    if (refund) {
      refund.textContent =
        plan.money_back || "You can stop this at any time from this page.";
    }
    fillMethods();
    return true;
  }

  for (const button of scope.querySelectorAll("[data-s-choose]")) {
    button.addEventListener("click", () => {
      if (!fillOrder(button.dataset.sChoose)) return;
      step = 1;
      draw();
      say("");
      checkout.open(button);
    });
  }

  back?.addEventListener("click", () => {
    step = Math.max(1, step - 1);
    draw();
  });

  next?.addEventListener("click", () => {
    // Step two is the only one that can be incomplete, and it says which field is
    // missing rather than refusing as a whole.
    if (step === 2 && !checkFields()) return;
    step = Math.min(LAST_STEP, step + 1);
    draw();
  });

  methods.forEach((input) => input.addEventListener("change", refreshPay));
  agree?.addEventListener("change", refreshPay);

  /* A code was applied or cleared. The box owns the code; the order line owns the price
     on screen. It listens rather than being written into, so neither file has to know
     how the other draws its half. */
  form?.addEventListener("hm:discount", (event) => {
    const { amount, was } = event.detail || {};
    if (amount) paintOrder(amount, was || "");
  });

  /** Mark every empty required field, and say how many there are. */
  function checkFields() {
    const fields = [...dialog.querySelectorAll('[data-s-panel="2"] [data-s-field]')];
    let missing = 0;
    for (const field of fields) {
      const input = field.querySelector("input");
      const empty = !input || !input.value.trim();
      field.dataset.wrong = empty ? "true" : "false";
      if (empty) missing += 1;
    }
    if (!missing) {
      say("");
      return true;
    }
    const first = dialog.querySelector('[data-s-field][data-wrong="true"] input');
    first?.focus({ preventScroll: false });
    say(
      missing === 1
        ? "One thing is still missing. It is marked below."
        : `${missing} things are still missing. They are marked below.`,
      "danger",
    );
    return false;
  }

  /* ── Paying ──────────────────────────────────────────────────────────────
   *
   * The form is posted here rather than by the browser so a refusal can be shown
   * inside the popup. The person is only sent away once we hold a real payment page
   * to send them to.
   */

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pay?.disabled || pay?.dataset.busy === "true") return;
    if (!checkFields()) {
      step = 2;
      draw();
      return;
    }
    pay.dataset.busy = "true";
    pay.disabled = true;
    const label = pay.querySelector("[data-s-pay-label]");
    if (label) label.textContent = "Opening the payment page…";
    say("Opening the payment page. Please wait.");
    try {
      const response = await window.fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const body = await response.json().catch(() => ({}));
      const url = typeof body.checkout_url === "string" ? body.checkout_url : "";
      if (!response.ok || !url) {
        throw new Error(
          body?.error?.message ||
            "We could not open the payment page. Nothing was charged.",
        );
      }
      window.location.assign(url);
    } catch (error) {
      delete pay.dataset.busy;
      say(
        error instanceof Error
          ? `${error.message} Nothing was charged.`
          : "We could not open the payment page. Nothing was charged.",
        "danger",
      );
      refreshPay();
      said?.focus({ preventScroll: true });
    }
  });

  /* Arriving with a plan already chosen — somebody pressed "choose this plan"
     somewhere else and was sent here. Only for a plan the server said is buyable. */
  const wanted = scope.dataset.openFor || "";
  if (wanted && fillOrder(wanted)) {
    step = 1;
    draw();
    checkout.open(null);
  }
}

/** The plans the server rendered, as plain data. */
function readFacts() {
  const node = document.getElementById("s-plan-facts");
  if (!node) return [];
  try {
    const parsed = JSON.parse(node.textContent || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // A page that cannot read its own plan list still works: every card is rendered by
    // the server, and only the popup needs this.
    return [];
  }
}
