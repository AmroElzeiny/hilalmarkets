/**
 * How long the launch price lasts, in days, hours, minutes and seconds.
 *
 * The server renders the price and the deadline; this only counts down to the deadline
 * the server gave it. It counts live, one step per second, so a visitor sitting on the
 * page watches the number fall. It stays quiet while it does: no animation, no colour
 * change, no alarm. The brand rules ask for calm, so this states a fact beside the price
 * and stops there.
 *
 * When the deadline passes it removes itself and hides the crossed-out price, so a page
 * left open overnight cannot keep showing an offer that has ended.
 *
 * The markup is built once and only the numbers change afterwards. Rewriting the whole
 * box every second would throw away the label and the units a screen reader is reading.
 */
(function () {
  "use strict";

  var SECOND = 1000;
  var UNITS = ["day", "hour", "minute", "second"];

  function remaining(endsAt) {
    var end = Date.parse(endsAt);
    if (isNaN(end)) return null;
    var ms = end - Date.now();
    if (ms <= 0) return null;
    var seconds = Math.floor(ms / SECOND);
    return [
      Math.floor(seconds / 86400),
      Math.floor((seconds % 86400) / 3600),
      Math.floor((seconds % 3600) / 60),
      seconds % 60,
    ];
  }

  function build(node) {
    var label = document.createElement("span");
    label.className = "offer-countdown-label";
    label.textContent = "Launch price ends in";
    var parts = document.createElement("span");
    parts.className = "offer-countdown-parts tnum";
    var cells = UNITS.map(function () {
      var part = document.createElement("span");
      part.className = "offer-countdown-part";
      var value = document.createElement("strong");
      var unit = document.createElement("small");
      part.appendChild(value);
      part.appendChild(unit);
      parts.appendChild(part);
      return { value: value, unit: unit };
    });
    node.textContent = "";
    node.appendChild(label);
    node.appendChild(parts);
    return cells;
  }

  function expire(node) {
    // The offer is over. The timer and the crossed-out price go together, so the page
    // never shows a discount it is no longer honouring. The card is marked once and the
    // stylesheet does the hiding, so this never fights the billing script over the same
    // element: this script owns "is the offer still running", that one owns "does this
    // billing interval have an offer at all".
    node.removeAttribute("data-offer-live");
    var card = node.closest("[data-offer-card]");
    if (card) card.setAttribute("data-offer-ended", "");
  }

  function tickOne(entry) {
    var left = remaining(entry.endsAt);
    if (!left) {
      expire(entry.node);
      return false;
    }
    for (var index = 0; index < UNITS.length; index += 1) {
      var count = left[index];
      var text = String(count);
      if (entry.cells[index].value.textContent !== text) {
        entry.cells[index].value.textContent = text;
      }
      var unit = count === 1 ? UNITS[index] : UNITS[index] + "s";
      if (entry.cells[index].unit.textContent !== unit) {
        entry.cells[index].unit.textContent = unit;
      }
    }
    // The server renders the box hidden so an empty frame never flashes. Once it holds a
    // real count it is handed over to the stylesheet.
    entry.node.removeAttribute("hidden");
    entry.node.setAttribute("data-offer-live", "");
    return true;
  }

  function start() {
    var entries = Array.prototype.slice
      .call(document.querySelectorAll("[data-offer-countdown]"))
      .map(function (node) {
        return {
          node: node,
          endsAt: node.getAttribute("data-offer-countdown") || "",
          cells: build(node),
        };
      });
    if (!entries.length) return;
    var timer = window.setInterval(function () {
      entries = entries.filter(tickOne);
      if (!entries.length) window.clearInterval(timer);
    }, SECOND);
    entries = entries.filter(tickOne);
    if (!entries.length) window.clearInterval(timer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
