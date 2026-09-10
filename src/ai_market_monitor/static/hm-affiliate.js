/* The affiliate page's two moving parts: the network list, and the time-log popups.
 *
 * **The network list follows the coin.** A coin and a network are not independent
 * choices. USDC does not exist on Litecoin, and a form that lets somebody pick that pair
 * sends money to a chain their wallet cannot see. The server refuses such a pair outright
 * — `affiliate_payout_options.network_for` returns nothing and the request is turned away
 * — and this makes the same rule visible before anybody presses the button, rather than
 * after.
 *
 * The options come from the page, not from a copy of the catalogue written here. A
 * second list in JavaScript is a list that drifts: a network removed on the server would
 * still be offered on screen, and the person choosing it would be told no for a reason
 * they could not have seen.
 *
 * With scripting off, the network select is empty and the form cannot be submitted with
 * a wrong pair; the server still holds the rule either way.
 *
 * **The popups open what each figure is made of.** Every row inside them is already in
 * the page's markup, drawn by the server — nothing is fetched and nothing is counted
 * here. That is deliberate: a popup that worked its own totals out would be a second
 * answer to the question the card already answered, and nobody could tell which was
 * right. Opening, closing, the focus trap and the movement are `hm-dialog.js`, shared
 * with every other popup in the product.
 */
import { manageDialog } from "./hm-dialog.js";

(() => {
  "use strict";

  /* Each popup is wired to the buttons that name it. A `<dialog>` is moved to the top
     layer and is not a descendant of the page, so its own close button is looked for
     inside it — which is what `closers` does. */
  for (const dialog of document.querySelectorAll("[data-hm-aff-log]")) {
    const popup = manageDialog(dialog, { closers: ["[data-hm-aff-log-close]"] });
    const selector = `[data-hm-aff-log-open="${dialog.id}"]`;
    for (const trigger of document.querySelectorAll(selector)) {
      trigger.addEventListener("click", () => popup.open(trigger));
    }
  }

  const form = document.querySelector("[data-hm-affiliate-payout]");
  if (!form) return;

  const currencySelect = form.querySelector("[data-hm-payout-currency]");
  const networkSelect = form.querySelector("[data-hm-payout-network]");
  const hint = form.querySelector("[data-hm-payout-hint]");
  if (!currencySelect || !networkSelect) return;

  let catalogue = [];
  try {
    const raw = document.getElementById("hm-payout-options");
    catalogue = raw ? JSON.parse(raw.textContent || "[]") : [];
  } catch (error) {
    // A catalogue that cannot be read leaves the network list empty rather than half
    // filled. An empty required select cannot be submitted, which is the safe end.
    catalogue = [];
  }

  const byKey = new Map(catalogue.map((item) => [item.key, item]));

  function renderNetworks() {
    const currency = byKey.get(currencySelect.value);
    networkSelect.textContent = "";
    if (!currency || !currency.networks.length) {
      networkSelect.disabled = true;
      if (hint) hint.textContent = "Pick a coin first.";
      return;
    }
    networkSelect.disabled = false;
    currency.networks.forEach((network) => {
      const option = document.createElement("option");
      option.value = network.key;
      option.textContent = network.label;
      networkSelect.appendChild(option);
    });
    describeNetwork();
  }

  function describeNetwork() {
    if (!hint) return;
    const currency = byKey.get(currencySelect.value);
    if (!currency) return;
    const network = currency.networks.find((item) => item.key === networkSelect.value);
    if (!network) return;
    // The fee is said out loud because it comes out of the payout. Somebody choosing
    // between two networks is choosing between two amounts arriving.
    hint.textContent = `${network.address_hint}. Sending costs about $${network.typical_fee_usd}.`;
  }

  currencySelect.addEventListener("change", renderNetworks);
  networkSelect.addEventListener("change", describeNetwork);
  renderNetworks();
})();
