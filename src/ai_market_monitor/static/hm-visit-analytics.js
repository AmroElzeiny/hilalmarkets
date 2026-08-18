/**
 * What the public site reports about one page view.
 *
 * Three facts, and nothing else: this page was opened, it was really in front of
 * somebody for this many seconds, and it was closed. Who the person is never leaves the
 * browser — no identifier is written to this device, and the server turns the calling
 * address into a one-way hash that changes every day.
 *
 * Deliberately not part of the React bundle. The landing page ships as a built file that
 * has to be rebuilt and copied by hand, so measurement written inside it would stop the
 * day somebody edited a component and forgot. Here it is one script the page shell
 * loads, and it works the same on the React pages and on the server-rendered ones.
 */
(function () {
  "use strict";

  var COLLECT = "/api/v1/site-analytics/collect";
  var PING_MS = 15000;
  // Never claim more than half an hour for a single page. A browser that keeps a page
  // in front of somebody longer than that is not reading it.
  var MAX_ACTIVE_MS = 30 * 60 * 1000;

  if (window.__hmVisitMeasured) return;
  window.__hmVisitMeasured = true;

  function sessionKey() {
    // Held in this page's memory only. Closing the tab ends the visit and the key.
    var random = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID().replace(/-/g, "")
      : String(Date.now()) + Math.random().toString(16).slice(2).padEnd(16, "0");
    return random.slice(0, 32).toLowerCase();
  }

  function campaign() {
    try {
      var value = new URLSearchParams(window.location.search).get("utm_source");
      return value ? value.trim().slice(0, 120) : null;
    } catch (error) {
      return null;
    }
  }

  var key = sessionKey();
  var activeSince = document.visibilityState === "visible" ? Date.now() : null;
  var activeMs = 0;
  var closed = false;
  var timer = null;

  function measuredMs() {
    var running = activeSince === null ? 0 : Date.now() - activeSince;
    return Math.min(activeMs + running, MAX_ACTIVE_MS);
  }

  function send(event, extra) {
    var body = {
      event: event,
      session_key: key,
      path: window.location.pathname || "/",
      active_ms: measuredMs()
    };
    if (event === "open") {
      body.referrer = document.referrer ? String(document.referrer).slice(0, 500) : null;
      body.campaign = campaign();
    }
    if (extra) {
      for (var name in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, name)) body[name] = extra[name];
      }
    }
    var payload = JSON.stringify(body);
    // `sendBeacon` is the only send that survives the page being closed. `fetch` with
    // `keepalive` is the fallback for browsers that refuse the beacon's content type.
    try {
      var blob = new Blob([payload], { type: "application/json" });
      if (navigator.sendBeacon && navigator.sendBeacon(COLLECT, blob)) return;
    } catch (error) {
      // Fall through to fetch.
    }
    try {
      fetch(COLLECT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
        credentials: "same-origin"
      }).catch(function () {});
    } catch (error) {
      // Measurement is never allowed to break the page it measures.
    }
  }

  function pause() {
    if (activeSince === null) return;
    activeMs = Math.min(activeMs + (Date.now() - activeSince), MAX_ACTIVE_MS);
    activeSince = null;
  }

  function resume() {
    if (activeSince === null) activeSince = Date.now();
  }

  function finish() {
    if (closed) return;
    closed = true;
    pause();
    if (timer) window.clearInterval(timer);
    send("close");
  }

  function beginVisit() {
    closed = false;
    activeMs = 0;
    activeSince = document.visibilityState === "visible" ? Date.now() : null;
    send("open");
    if (timer) window.clearInterval(timer);
    timer = window.setInterval(function () {
      if (document.visibilityState !== "visible" || closed) return;
      send("ping");
    }, PING_MS);
  }

  beginVisit();

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      resume();
      return;
    }
    pause();
    // A phone that switches away is usually never coming back to this page, and
    // `pagehide` does not always fire there. The running total is reported now.
    send("ping");
  });
  // `pagehide` only. A `beforeunload` listener stops some browsers from keeping the page
  // in their back-forward cache, which would make the visitor's own Back button slower —
  // measuring the site must never be a reason the site is worse to use. `pagehide` fires
  // for a normal close and for a page going into that cache, so nothing is missed.
  window.addEventListener("pagehide", finish);

  // Opening the assistant is the one thing a person can do here that is not a new page,
  // so it is the one action the page has to report itself.
  document.addEventListener("click", function (event) {
    var target = event.target && event.target.closest
      ? event.target.closest("[data-public-chat-launcher], [data-hm-chat-open]")
      : null;
    if (!target) return;
    send("action", { action: "chat", action_detail: window.location.pathname || "/" });
  }, true);

  // A single-page site changes address without loading anything, so the visit that just
  // ended has to be closed and the new one opened by hand. Without this the whole React
  // site would be one visit for ever and the journey report would have nothing to read.
  var lastPath = window.location.pathname || "/";
  function routed() {
    var path = window.location.pathname || "/";
    if (path === lastPath) return;
    lastPath = path;
    finish();
    key = sessionKey();
    beginVisit();
  }

  window.addEventListener("popstate", function () { window.setTimeout(routed, 0); });
  var pushState = window.history.pushState;
  window.history.pushState = function () {
    pushState.apply(window.history, arguments);
    window.setTimeout(routed, 0);
  };
  var replaceState = window.history.replaceState;
  window.history.replaceState = function () {
    replaceState.apply(window.history, arguments);
    window.setTimeout(routed, 0);
  };
})();
