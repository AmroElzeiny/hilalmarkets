/* The discount-code box, for every screen that has one.
 *
 * Three screens ask "card or crypto" and can start a payment: the subscription popup,
 * the plan popup on /dashboard/billing, and the checkout review page. All three need the
 * same box, and a copy of this logic on each of them is three chances for one screen to
 * accept a code the other two refuse.
 *
 * **Nothing here decides anything about money.** It does not know which codes exist, what
 * any code is worth, or which ways of paying take one. It asks the server, and the server
 * answers with the price. The box then shows exactly what came back. When the payment is
 * really created the server prices the code again from scratch, so a browser that lied
 * about a discount changes nothing at all.
 *
 * The box is hidden unless the chosen way of paying takes a code, and which ways those
 * are is a server fact carried in `data-discount-methods`. A page working that out for
 * itself is how a box comes to be offered where checkout refuses it.
 */

const ENDPOINT = "/dashboard/billing/discount";

/** The shape a code may take. The same rule the server applies, so an obvious non-code
 *  is answered here instead of after a trip to the payment company. Never looser than
 *  the server's rule: a browser that says yes where the server says no is the failure
 *  this whole file exists to avoid. */
const CODE_SHAPE = /^[A-Z0-9][A-Z0-9_-]{1,39}$/;

for (const box of document.querySelectorAll("[data-discount]")) attach(box);

export function attach(box) {
  const form = box.closest("form");
  if (!form) return;

  const input = box.querySelector("[data-discount-input]");
  const apply = box.querySelector("[data-discount-apply]");
  const clear = box.querySelector("[data-discount-clear]");
  const said = box.querySelector("[data-discount-said]");
  const applied = box.querySelector("[data-discount-applied-row]");
  const appliedCode = box.querySelector("[data-discount-applied-code]");
  const hidden = form.querySelector('input[name="discount_code"]');
  if (!input || !apply || !hidden) return;

  /** Which ways of paying take a code, decided by the server. */
  const takesCode = new Set(
    (box.dataset.discountMethods || "")
      .split(",")
      .map((word) => word.trim())
      .filter(Boolean),
  );

  /** The price with no code, read **now** rather than remembered.
   *
   * On the two popups the plan is chosen after this file loads, so the price is written
   * onto the box later — and a copy taken at load time is empty for ever. That is not a
   * small bug: `drop()` uses this to put the full price back, so pressing Remove left the
   * discounted number on screen while the hidden field was already empty. The person
   * would have been charged the full price by a screen still showing the discount. */
  const fullAmount = () => box.dataset.discountFull || "";
  const currency = () => box.dataset.discountCurrency || "USD";

  let busy = false;

  /* ── Saying things ─────────────────────────────────────────────────────── */

  function say(text, tone = "") {
    if (!said) return;
    said.dataset.tone = tone;
    said.textContent = text;
    said.hidden = !text;
  }

  /** Money as this product writes it: `$15`, and `$11.25` only when there are cents. */
  function money(amount) {
    const value = Number(amount);
    if (!Number.isFinite(value)) return String(amount);
    const rounded = Math.round(value * 100) / 100;
    return Number.isInteger(rounded) ? `$${rounded}` : `$${rounded.toFixed(2)}`;
  }

  /** The bare number, to the cent: `15.00`.
   *
   * For the screens that write the currency out as a word — "15.00 USD" — rather than
   * with a symbol. Using the symbol form there produced "$15 USD", which names the
   * currency twice and reads like a different amount. */
  function plain(amount) {
    const value = Number(amount);
    return Number.isFinite(value) ? value.toFixed(2) : String(amount);
  }

  /** Write one amount into every place this page shows a total. */
  function paintTotals(amount, wasAmount) {
    const unit = currency();
    // Each place a total appears carries its own wording in the attribute — `{amount}`
    // for the `$15` form, `{plain}` for a page that writes the currency out as a word —
    // so one function fills all of them without knowing what any screen looks like.
    const fill = (pattern, value) =>
      pattern
        .replace("{amount}", money(value))
        .replace("{plain}", plain(value))
        .replace("{currency}", unit);
    for (const node of document.querySelectorAll("[data-discount-total]")) {
      node.textContent = fill(node.dataset.discountTotal || "{amount}", amount);
    }
    for (const node of document.querySelectorAll("[data-discount-original]")) {
      node.textContent = wasAmount
        ? fill(node.dataset.discountOriginal || "{amount}", wasAmount)
        : "";
      node.hidden = !wasAmount;
    }
    // Screens with a controller of their own — the two popups — keep their own order
    // line. They listen for this rather than being reached into from here.
    form.dispatchEvent(
      new CustomEvent("hm:discount", {
        bubbles: true,
        detail: {
          code: hidden.value,
          amount: String(amount),
          was: wasAmount || "",
          currency: unit,
        },
      }),
    );
  }

  /** Forget any applied code and put the full price back. */
  function drop({ quiet = false } = {}) {
    const had = Boolean(hidden.value);
    hidden.value = "";
    if (applied) applied.hidden = true;
    if (clear) clear.hidden = true;
    box.dataset.discountState = "empty";
    input.disabled = false;
    apply.hidden = false;
    const full = fullAmount();
    if (full) paintTotals(full, "");
    if (!quiet && had) say("The code was removed. The full price is back.", "");
    else if (!quiet) say("");
  }

  /* ── Showing the box at all ────────────────────────────────────────────── */

  function chosenMethod() {
    const picked = form.querySelector('input[name="payment_method"]:checked');
    return picked ? picked.value : "";
  }

  function refreshVisibility() {
    const method = chosenMethod();
    const wanted = takesCode.has(method);
    box.hidden = !wanted;
    if (!wanted && hidden.value) {
      // Somebody applied a code and then switched to a way of paying that cannot use it.
      // Keeping it would send them to a payment page for a price the box no longer shows.
      drop({ quiet: true });
      say("");
    }
  }

  for (const radio of form.querySelectorAll('input[name="payment_method"]')) {
    radio.addEventListener("change", refreshVisibility);
  }
  // The plan can change under the box on the two popup screens, and a code priced for
  // one plan is not priced for another.
  const planInput = form.querySelector('input[name="plan_code"]');
  if (planInput) {
    const planWatcher = new MutationObserver(() => drop({ quiet: true }));
    planWatcher.observe(planInput, { attributes: true, attributeFilter: ["value"] });
  }

  /* ── Applying ──────────────────────────────────────────────────────────── */

  async function submitCode() {
    if (busy) return;
    const typed = input.value.replace(/\s+/g, "").toUpperCase();
    if (!typed) {
      say("Write your code in the box first.", "danger");
      input.focus();
      return;
    }
    if (!CODE_SHAPE.test(typed)) {
      say("That does not look like a code. A code is letters and numbers, like HILAL25.", "danger");
      input.focus();
      return;
    }
    input.value = typed;
    busy = true;
    apply.disabled = true;
    const label = apply.querySelector("[data-discount-apply-label]") || apply;
    const wording = label.textContent;
    label.textContent = "Checking…";
    say("Checking your code.");
    const body = new FormData();
    body.set("csrf_token", form.querySelector('input[name="csrf_token"]')?.value || "");
    body.set("plan_code", planInput?.value || form.dataset.planCode || "");
    body.set("payment_method", chosenMethod());
    body.set("discount_code", typed);
    try {
      const response = await window.fetch(ENDPOINT, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload || typeof payload.now !== "string") {
        throw new Error(
          payload?.error?.message ||
            "We could not check that code just now. Please try again in a moment.",
        );
      }
      hidden.value = payload.code;
      box.dataset.discountState = "applied";
      if (appliedCode) appliedCode.textContent = payload.code;
      if (applied) applied.hidden = false;
      if (clear) clear.hidden = false;
      input.disabled = true;
      apply.hidden = true;
      paintTotals(payload.now, payload.was);
      say(
        `Code ${payload.code} is on. You save ${money(payload.saving)}, so you pay ` +
          `${money(payload.now)} instead of ${money(payload.was)}.`,
        "good",
      );
    } catch (error) {
      drop({ quiet: true });
      say(
        error instanceof Error
          ? error.message
          : "We could not check that code just now. Please try again in a moment.",
        "danger",
      );
      input.focus();
    } finally {
      busy = false;
      apply.disabled = false;
      label.textContent = wording;
    }
  }

  apply.addEventListener("click", (event) => {
    event.preventDefault();
    void submitCode();
  });
  clear?.addEventListener("click", (event) => {
    event.preventDefault();
    drop();
    input.value = "";
    input.focus();
  });
  // Enter inside the code box applies the code. Without this it submits the whole form,
  // which opens a payment page at the full price the moment somebody finishes typing.
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    void submitCode();
  });
  input.addEventListener("input", () => {
    if (said && said.dataset.tone === "danger") say("");
  });

  refreshVisibility();
}
