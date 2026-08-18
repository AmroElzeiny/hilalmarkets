(() => {
  "use strict";

  const config = window.HilalMarketsConsentConfig || {};
  const version = Number(config.version || 1);
  const storageKey = `hm-cookie-consent-v${version}`;
  const cookieName = "hm_cookie_consent";
  const defaults = Object.freeze({
    essential: true,
    analytics: false,
    functional: false,
    marketing: false,
  });
  let previousFocus = null;
  let googleLoaded = false;
  let xLoaded = false;

  const elements = () => ({
    banner: document.querySelector("[data-cookie-banner]"),
    modal: document.querySelector("[data-cookie-modal]"),
    analytics: document.querySelector("input[data-consent-analytics]"),
    functional: document.querySelector("input[data-consent-functional]"),
    marketing: document.querySelector("input[data-consent-marketing]"),
  });

  function parse(value) {
    if (!value) return null;
    try {
      const parsed = JSON.parse(value);
      return parsed.version === version ? parsed : null;
    } catch {
      return null;
    }
  }

  function readCookie() {
    const prefix = `${cookieName}=`;
    const pair = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith(prefix));
    if (!pair) return null;
    try {
      return parse(decodeURIComponent(pair.slice(prefix.length)));
    } catch {
      return null;
    }
  }

  function read() {
    try {
      return parse(window.localStorage.getItem(storageKey)) || readCookie();
    } catch {
      return readCookie();
    }
  }

  function loadGoogle() {
    const containerId = String(config.gtmContainerId || "").trim();
    if (googleLoaded || !config.analyticsEnabled) return;
    if (!/^GTM-[A-Z0-9]+$/.test(containerId)) return;

    const loadedContainer = Array.from(document.scripts).some((item) => {
      try {
        const source = new URL(item.src);
        return source.hostname === "www.googletagmanager.com"
          && source.pathname === "/gtm.js"
          && source.searchParams.get("id") === containerId;
      } catch {
        return false;
      }
    });
    if (loadedContainer) {
      googleLoaded = true;
      return;
    }

    const script = document.createElement("script");
    script.async = true;
    script.dataset.hmOptionalAnalytics = "true";
    googleLoaded = true;
    window.dataLayer.push({ "gtm.start": Date.now(), event: "gtm.js" });
    script.src = `https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(containerId)}`;
    script.dataset.hmProvider = "google-tag-manager";
    document.head.appendChild(script);
  }

  function loadX() {
    const pixelId = String(config.xPixelId || "").trim();
    if (xLoaded || !config.xPixelEnabled) return;
    if (!/^[A-Za-z0-9]{3,32}$/.test(pixelId)) return;

    const configuredPixels = window.__hmXConfiguredPixels instanceof Set
      ? window.__hmXConfiguredPixels
      : new Set();
    window.__hmXConfiguredPixels = configuredPixels;
    if (configuredPixels.has(pixelId)) {
      xLoaded = true;
      return;
    }

    window.twq = window.twq || function () {
      window.twq.exe
        ? window.twq.exe.apply(window.twq, arguments)
        : window.twq.queue.push(arguments);
    };
    window.twq.version = "1.1";
    window.twq.queue = window.twq.queue || [];
    window.twq.integration = "gtm-ad-manager";

    const loadedScript = Array.from(document.scripts).some((item) => {
      try {
        const source = new URL(item.src);
        return source.hostname === "static.ads-twitter.com"
          && source.pathname === "/uwt.js";
      } catch {
        return false;
      }
    });
    if (!loadedScript) {
      const script = document.createElement("script");
      script.async = true;
      script.src = "https://static.ads-twitter.com/uwt.js";
      script.dataset.hmProvider = "x-pixel";
      script.addEventListener("error", () => {
        xLoaded = false;
        configuredPixels.delete(pixelId);
      }, { once: true });
      document.head.appendChild(script);
    }

    configuredPixels.add(pixelId);
    xLoaded = true;
    window.twq("config", pixelId);
  }

  function apply(choice) {
    const value = { ...defaults, ...(choice || {}) };
    const marketing = Boolean(config.marketingEnabled && value.marketing);
    window.gtag("consent", "update", {
      ad_storage: marketing ? "granted" : "denied",
      analytics_storage: value.analytics ? "granted" : "denied",
      ad_user_data: marketing ? "granted" : "denied",
      ad_personalization: marketing ? "granted" : "denied",
      functionality_storage: value.functional ? "granted" : "denied",
      personalization_storage: value.functional ? "granted" : "denied",
      security_storage: "granted",
    });
    document.documentElement.dataset.consentAnalytics = value.analytics ? "granted" : "denied";
    document.documentElement.dataset.consentFunctional = value.functional ? "granted" : "denied";
    document.documentElement.dataset.consentMarketing = marketing ? "granted" : "denied";
    if (value.analytics) loadGoogle();
    if (marketing) loadX();
    window.dispatchEvent(new CustomEvent("hm:consent-updated", {
      detail: {
        version,
        analytics: Boolean(value.analytics),
        functional: Boolean(value.functional),
        marketing,
      },
    }));
  }

  function save(choice) {
    const value = {
      ...defaults,
      ...choice,
      marketing: Boolean(config.marketingEnabled && choice.marketing),
      version,
      updatedAt: new Date().toISOString(),
    };
    const serialized = JSON.stringify(value);
    try {
      window.localStorage.setItem(storageKey, serialized);
    } catch {
      // The first-party cookie remains the persistence fallback.
    }
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${cookieName}=${encodeURIComponent(serialized)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
    apply(value);
  }

  /* How much room the banner is taking, published for the page to keep clear.
   *
   * The banner is fixed to the bottom of the window and nothing reserved space for it,
   * so on every page it covered the last part of the content until somebody answered
   * it — on the dashboard home that was the whole "no alert proof yet" panel, and on a
   * phone it was most of a screen. A page cannot know the height in a stylesheet
   * because the text wraps differently at every width, so it is measured and published
   * here, where the banner is shown and hidden.
   */
  function publishBannerHeight() {
    const banner = elements().banner;
    const showing = Boolean(banner && banner.classList.contains("is-visible"));
    const height = showing ? Math.ceil(banner.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty(
      "--hm-cookie-banner-height",
      height ? `${height}px` : "0px",
    );
  }

  function showBanner() {
    elements().banner?.classList.add("is-visible");
    publishBannerHeight();
  }

  function hideBanner() {
    elements().banner?.classList.remove("is-visible");
    publishBannerHeight();
  }

  function showModal() {
    const current = read() || defaults;
    const view = elements();
    previousFocus = document.activeElement;
    if (view.analytics) view.analytics.checked = Boolean(current.analytics);
    if (view.functional) view.functional.checked = Boolean(current.functional);
    if (view.marketing) view.marketing.checked = Boolean(current.marketing);
    view.modal?.classList.add("is-visible");
    view.modal?.setAttribute("aria-hidden", "false");
    document.body.classList.add("no-scroll");
    view.modal?.querySelector("[data-cookie-close]")?.focus();
  }

  function hideModal() {
    const modal = elements().modal;
    modal?.classList.remove("is-visible");
    modal?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("no-scroll");
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  function saveAndClose(choice) {
    save(choice);
    hideBanner();
    hideModal();
  }

  /* Every consent control, listened for on the document rather than collected once.
   *
   * This used to be five `querySelectorAll` loops inside `DOMContentLoaded`, attaching a
   * handler to each control that existed **at that moment**. It worked, and it worked by
   * luck: half of this site is drawn by React, from a deferred module script, and the
   * footer only happens to exist by the time DOMContentLoaded fires because deferred
   * modules run just before it. Nothing in this codebase asks for that ordering, nothing
   * documents it, and nothing would report it if it changed — a control that is on the
   * page and attached to nothing throws no error and logs nothing. It would simply stop
   * responding, and the first person to find out would be a customer.
   *
   * One listener on the document removes the dependency entirely. A control drawn by
   * React, by a later script, or by a page that has not been written yet is answered the
   * same way as one that was in the original HTML, because the click is read where it
   * lands rather than where the element was when the page loaded.
   */
  function onDocumentClick(event) {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const opener = target.closest("[data-cookie-customize], [data-cookie-settings]");
    if (opener) {
      // The footer control is a real link with a real address, so the default has to be
      // stopped when the panel opens in place. Without a script it follows that address
      // instead, which lands on the Cookie Policy with `settings=1` and opens the same
      // panel from the branch at the bottom of this file.
      event.preventDefault();
      showModal();
      return;
    }
    if (target.closest("[data-cookie-essential]")) {
      saveAndClose(defaults);
      return;
    }
    if (target.closest("[data-cookie-accept-analytics]")) {
      saveAndClose({ ...defaults, analytics: true });
      return;
    }
    if (target.closest("[data-cookie-close]")) {
      hideModal();
      return;
    }
    if (target.closest("[data-cookie-save]")) {
      const view = elements();
      saveAndClose({
        essential: true,
        analytics: Boolean(view.analytics?.checked),
        functional: Boolean(view.functional?.checked),
        marketing: Boolean(view.marketing?.checked),
      });
      return;
    }
    // A click on the panel's own backdrop, and not on the panel, means "outside".
    if (target === elements().modal) hideModal();
  }

  document.addEventListener("click", onDocumentClick);

  document.addEventListener("DOMContentLoaded", () => {
    const current = read();
    if (current) apply(current);
    else showBanner();
    // The banner rewraps as the window changes, so what it covers changes with it.
    window.addEventListener("resize", publishBannerHeight, { passive: true });

    if (new URLSearchParams(window.location.search).get("settings") === "1") {
      showModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    const modal = elements().modal;
    if (!modal?.classList.contains("is-visible")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      hideModal();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...modal.querySelectorAll("button, input, a[href]")].filter((item) => !item.disabled);
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
  });
})();
