/* How long this product waits for its own server. One place, every request.
 *
 * A page that reads something over the network has two honest endings: it draws the
 * answer, or it says it could not. `fetch` has a third — it waits for ever. A request
 * that is accepted and then never answered never resolves and never rejects, so the code
 * after it never runs and no error handler is ever reached.
 *
 * That is what left the monitor canvas sitting on "Reading what this platform can watch
 * for…" with no error, no message and no way out. The canvas had an error banner and a
 * "Try again" button ready; neither could ever be shown, because nothing told the page
 * the read had failed.
 *
 * Giving that one page a time limit would fix that one page. Every request this product
 * makes had the same hole — forty-odd of them, across the dashboard, the public site and
 * the assistants — and a helper each page has to remember to import is the same hole
 * waiting to reopen the next time somebody writes `fetch(`. So the limit is applied here,
 * once, to `fetch` itself. Every request the product sends to its own server ends,
 * whether the code that sent it has heard of this file or not, and a `fetch` written
 * tomorrow is covered on the day it is written.
 *
 * Deliberately a classic script, not a module, and deliberately the first script on the
 * page: module scripts are deferred, so a module owner would be installed after every
 * classic script had already run and after some of them had already sent a request.
 *
 * Only requests to this product's own server are touched. A third party's script keeps
 * its own behaviour, because this file cannot know what that behaviour is meant to be.
 */

(() => {
  "use strict";

  const native = window.fetch;
  // Wrapping twice would stack two limits on one request. The shorter would win, and the
  // number a reader sees here would not be the number that fires.
  if (typeof native !== "function" || window.hmWait) return;

  /** How long a person waits before being told it did not work, in milliseconds.
   *
   *  Two numbers, and which one applies is decided by the request itself rather than by
   *  a list of addresses somebody has to keep up to date. A list is the thing this
   *  codebase keeps paying for: one misspelt path, or one endpoint added and not added
   *  here, and the wrong limit fires with nothing to say so.
   *
   *  `reading` is for a request that only asks a question — a list, a form's saved
   *  values, the contract the canvas draws from. Twenty seconds is already far longer
   *  than any of them take when the server is healthy, so a wait that reaches it is a
   *  failure and not slowness. Nothing was changed, so trying again is always safe.
   *
   *  `changing` is for a request that makes something happen — switching a monitor on,
   *  sending a test message, starting a payment, asking the assistant for an answer.
   *  Giving up early on one of those is worse than waiting: the server may have already
   *  done the thing, and the page would be telling a person it failed. Two minutes is
   *  well past every limit the server puts on its own slow work, and it still ends. */
  const WAIT = Object.freeze({ reading: 20_000, changing: 120_000 });

  /** Which of the two applies. `GET` and `HEAD` are the methods that only ask. */
  function waitFor(method) {
    const asked = String(method || "GET").toUpperCase();
    return asked === "GET" || asked === "HEAD" ? WAIT.reading : WAIT.changing;
  }

  /** The address a `fetch` call is really going to, or null if it cannot be read. */
  function addressOf(input) {
    const href =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input && typeof input.url === "string"
            ? input.url
            : null;
    if (href === null) return null;
    try {
      return new URL(href, window.location.href);
    } catch {
      return null;
    }
  }

  /** The method a `fetch` call will really use. */
  function methodOf(input, init) {
    if (init && init.method) return init.method;
    if (input && typeof input.method === "string") return input.method;
    return "GET";
  }

  /** The sentence a person reads when we stop waiting.
   *
   *  Written here rather than left to the browser. `AbortSignal.timeout` gives up with
   *  the words "signal timed out", and about twenty places in this product put
   *  `error.message` straight in front of somebody — so a beginner was going to be shown
   *  a phrase about signals. This is the reason the browser's own timer is not used:
   *  its message cannot be changed, and one plain sentence here is better than twenty
   *  pages each rewriting it. */
  const TOO_LONG = "The server did not answer in time.";

  /** A signal that gives up after `ms`.
   *
   *  The timer deliberately runs for the whole window rather than being cleared when
   *  the response arrives. `fetch` resolves as soon as the headers are in, and the body
   *  can still be streaming for a long time after that — which is a second way to wait
   *  for ever. Aborting a request that has already finished does nothing at all. */
  function afterMilliseconds(ms) {
    const controller = new AbortController();
    window.setTimeout(
      () => controller.abort(new DOMException(TOO_LONG, "TimeoutError")),
      ms,
    );
    return controller.signal;
  }

  /** One signal that fires when either of two does.
   *
   *  Several pages already pass a signal of their own, to drop a request the person has
   *  replaced by typing again. Overwriting it would break that; leaving it alone would
   *  leave the hole. Both have to be able to end the request. */
  function either(existing, added) {
    if (!existing) return added;
    if (typeof AbortSignal.any === "function") return AbortSignal.any([existing, added]);
    const controller = new AbortController();
    const stop = (signal) => controller.abort(signal.reason);
    if (existing.aborted) stop(existing);
    else if (added.aborted) stop(added);
    else {
      existing.addEventListener("abort", () => stop(existing), { once: true });
      added.addEventListener("abort", () => stop(added), { once: true });
    }
    return controller.signal;
  }

  window.fetch = function hmFetch(input, init) {
    const address = addressOf(input);
    // Somebody else's server. Its own script decides how long it waits for it.
    if (!address || address.origin !== window.location.origin) {
      return native.call(window, input, init);
    }
    const options = init ? { ...init } : {};
    const own = options.signal || (input && input.signal) || null;
    options.signal = either(own, afterMilliseconds(waitFor(methodOf(input, init))));
    return native.call(window, input, options);
  };

  /** Read-only, for the pages that want to say the number out loud and for the tests
   *  that check this file is really in front of every request. */
  window.hmWait = Object.freeze({
    reading: WAIT.reading,
    changing: WAIT.changing,
    forMethod: (method) => waitFor(method),
  });

  /** Did this request end because we stopped waiting? Asked by any page that wants to
   *  say "it took too long" rather than "something went wrong", so the sentence a person
   *  reads matches what actually happened.
   *
   *  Only a timeout counts. A page that drops its own request because the person typed
   *  again aborts it deliberately, and that is not a failure to report. */
  window.hmWaitedTooLong = (error) => Boolean(error) && error.name === "TimeoutError";
})();
