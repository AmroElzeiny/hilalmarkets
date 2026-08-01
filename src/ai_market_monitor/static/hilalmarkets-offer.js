/**
 * How long the launch price lasts, in days, hours and minutes.
 *
 * The server renders the price and the deadline; this only counts down to the deadline
 * the server gave it. Deliberately quiet, per the brand rules: it ticks once a minute
 * rather than once a second, it does not animate, and it states a fact next to the price
 * instead of shouting a deadline.
 *
 * When the deadline passes it removes itself and hides the crossed-out price, so a page
 * left open overnight cannot keep showing an offer that has ended.
 */
(function () {
  "use strict";

  var MINUTE = 60000;

  function remaining(endsAt) {
    var end = Date.parse(endsAt);
    if (isNaN(end)) return null;
    var ms = end - Date.now();
    if (ms <= 0) return null;
    var minutes = Math.floor(ms / MINUTE);
    return {
      days: Math.floor(minutes / 1440),
      hours: Math.floor((minutes % 1440) / 60),
      minutes: minutes % 60,
    };
  }

  function plural(value, unit) {
    return value === 1 ? unit : unit + "s";
  }

  function render(node) {
    var endsAt = node.getAttribute("data-offer-countdown") || "";
    var left = remaining(endsAt);
    if (!left) {
      // The offer is over. Remove the timer and the crossed-out price together, so the
      // page never shows a discount it is no longer honouring.
      node.hidden = true;
      var card = node.closest("[data-offer-card]") || document;
      card.querySelectorAll("[data-offer-original-price]").forEach(function (old) {
        old.hidden = true;
      });
      return false;
    }
    var parts = [
      [left.days, plural(left.days, "day")],
      [left.hours, plural(left.hours, "hour")],
      [left.minutes, plural(left.minutes, "minute")],
    ];
    node.hidden = false;
    node.innerHTML =
      '<span class="offer-countdown-label">Launch price ends in</span>' +
      '<span class="offer-countdown-parts tnum">' +
      parts
        .map(function (part) {
          return (
            '<span class="offer-countdown-part"><strong>' +
            part[0] +
            "</strong><small>" +
            part[1] +
            "</small></span>"
          );
        })
        .join("") +
      "</span>";
    return true;
  }

  function start() {
    var nodes = Array.prototype.slice.call(
      document.querySelectorAll("[data-offer-countdown]")
    );
    if (!nodes.length) return;
    var tick = function () {
      var live = nodes.filter(render);
      if (!live.length && timer) window.clearInterval(timer);
    };
    var timer = window.setInterval(tick, MINUTE);
    tick();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
