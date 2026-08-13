/* Contextual page guides for the authenticated HilalMarkets dashboard.
 *
 * One engine, one registry, no runtime AI and no tour library. The registry below was
 * chosen by reading the rendered templates: a step exists only where the feature is core
 * to the product, non-obvious, specific to HilalMarkets, part of a consequential
 * workflow, or carries a screening/evidence/approval consequence.
 *
 * Targeting is exact and fails closed. A step names one `data-hm-guide-target` marker.
 * If that marker is missing, or present more than once, the step is dropped, the counter
 * is recalculated, and a console warning names the page and the marker. The engine never
 * falls back to a parent, a sibling, a class name, a position or an nth-child — a guide
 * that guesses points at the wrong thing with full confidence.
 */
(() => {
  "use strict";

  const PAGE_ATTRIBUTE = "data-hm-guide-page";
  const TARGET_ATTRIBUTE = "data-hm-guide-target";
  const SPOTLIGHT_PADDING = 8;
  //: Softest corner the outline may have, even around a perfectly square element.
  const SPOTLIGHT_MIN_RADIUS = 14;
  //: Longest a smooth scroll is waited on before the popover is placed anyway.
  const SCROLL_REST_TIMEOUT_MS = 700;
  const READY_TIMEOUT_MS = 1200;
  //: The only page a guide may open by itself, and only for a genuinely new account.
  const HOME_PAGE_KEY = "dashboard-home";

  // ---------------------------------------------------------------------------
  // Registry. Page key -> guide. `version` is part of the completion key, so a
  // changed guide is offered again instead of being silently suppressed.
  // ---------------------------------------------------------------------------

  const GUIDES = {
    "dashboard-home": {
      id: "dashboard-home",
      version: 2,
      steps: [
        {
          target: "home-screened-scope",
          title: "Your screened market",
          body: "Every number here counts only assets that passed the published screening methodology. Open the market to see which ones and why.",
          placement: "bottom",
        },
        {
          target: "home-compliance-attention",
          title: "Screening can change",
          body: "An asset you already watch can stop being eligible. This shows when that happened so you can decide what to do about it.",
          placement: "bottom",
        },
        {
          target: "home-create-watchlist",
          title: "Build a Watchlist",
          body: "Describe the market behaviour you want followed. HilalMarkets turns it into exact rules you review before anything runs.",
          placement: "bottom",
        },
        // The last three point at the side menu. The engine opens the menu for a target
        // that lives inside it and puts it back exactly as it was when the guide ends.
        {
          target: "nav-halal-assets",
          title: "Where assets are listed",
          body: "Only assets that passed a published screening methodology appear here. Open one to read the evidence behind its status.",
          placement: "right",
        },
        {
          target: "nav-trading-assistant",
          title: "One check, right now",
          body: "Ask for a single market check in your own words. It runs once and stops, unlike a Watchlist that keeps watching.",
          placement: "right",
        },
        {
          target: "nav-opportunities",
          title: "Proof for every alert",
          body: "Matches, forming setups and screening changes are kept here with the evidence behind each one. Nothing appears without a real evaluation.",
          placement: "right",
        },
      ],
    },

    "screened-market": {
      id: "screened-market",
      version: 1,
      steps: [
        {
          target: "market-methodology-selector",
          title: "Which methodology applies",
          body: "Eligibility depends on the methodology in force. Switching it changes which assets are listed and the evidence behind each one.",
          placement: "bottom",
        },
        {
          target: "market-live-status",
          title: "Prices are never guessed",
          body: "When the market feed is unavailable this says so instead of showing an estimate. A missing value here means no value was known.",
          placement: "bottom",
        },
        {
          target: "market-saved-assets",
          title: "My Screened Watchlist",
          body: "Assets you save for reference. Saving one does not monitor it and does not override the current screening result.",
          placement: "bottom",
        },
      ],
    },

    "asset-passport": {
      id: "asset-passport",
      version: 1,
      steps: [
        {
          target: "passport-status",
          title: "Published screening status",
          body: "This status comes from the platform's governed review under a named methodology. It is a record, not an opinion or a forecast.",
          placement: "bottom",
        },
        {
          target: "passport-evidence",
          title: "The evidence behind it",
          body: "Each line is the source the reviewers used. Read it when you want to understand why an asset is eligible or excluded.",
          placement: "top",
        },
        {
          target: "passport-history",
          title: "When the status changed",
          body: "Screening results move over time. This history shows every change, so a past decision can always be traced.",
          placement: "top",
        },
      ],
    },

    "watch-plans": {
      id: "watch-plans",
      version: 1,
      steps: [
        {
          target: "plans-active-count",
          title: "Only approved lists run",
          body: "A Watchlist counts as active once you have approved it. Anything you built but did not approve is not watching the market.",
          placement: "bottom",
        },
        {
          target: "plans-create",
          title: "Start a new Watchlist",
          body: "Describe what you want watched in your own words. The rules are built for you and shown before anything is activated.",
          placement: "bottom",
        },
      ],
    },

    "strategy-builder-monitor": {
      id: "strategy-builder-monitor",
      version: 1,
      steps: [
        {
          target: "builder-setup-chat",
          title: "Describe it in words",
          body: "Write the market behaviour you want followed. Ask questions here too — the chat answers from your draft, not from guesses.",
          placement: "right",
        },
        {
          target: "builder-canvas",
          title: "Your rules, exactly",
          body: "Everything understood from your words appears here as exact rules. Check it matches before you go further.",
          placement: "left",
        },
        {
          target: "builder-open-canvas",
          title: "Edit it visually",
          body: "The same rules open in the Visual Canvas when you would rather arrange them yourself. Both views hold one set of rules.",
          placement: "bottom",
        },
      ],
    },

    "strategy-builder-scanner": {
      id: "strategy-builder-scanner",
      version: 1,
      steps: [
        {
          target: "builder-setup-chat",
          title: "Describe the scan",
          body: "A Scanner checks the market once, right now. Write what you want found and the rules are built from your words.",
          placement: "right",
        },
        {
          target: "builder-canvas",
          title: "Your rules, exactly",
          body: "The scan runs on exactly these rules and nothing else. Read them first, so the result answers the question you meant.",
          placement: "left",
        },
      ],
    },

    activity: {
      id: "activity",
      version: 1,
      steps: [
        {
          target: "activity-updates-panel",
          title: "Alerts and screening changes",
          body: "Matches, forming setups and screening changes each have their own view here. A screening change can affect what you may keep watching.",
          placement: "bottom",
        },
      ],
    },

    integrations: {
      id: "integrations",
      version: 1,
      steps: [
        {
          target: "integrations-telegram-connect",
          title: "Alerts on Telegram",
          body: "Connect Telegram to receive alerts outside the dashboard. The link is per account and can be removed at any time.",
          placement: "bottom",
        },
        {
          target: "integrations-plan-locked-channel",
          title: "Some channels need a plan",
          body: "A delivery channel your plan does not include stays locked here. Your rules still run; only this way of reaching you is unavailable.",
          placement: "top",
        },
      ],
    },

    billing: {
      id: "billing",
      version: 1,
      steps: [
        {
          target: "billing-plan-limits",
          title: "What your plan allows",
          body: "Your plan sets how many Watchlists can run at once. Reaching the limit stops new ones activating, not existing alerts.",
          placement: "bottom",
        },
      ],
    },

    settings: {
      id: "settings",
      version: 1,
      steps: [
        {
          target: "settings-notification-preferences",
          title: "Forming is not matched",
          body: "A forming setup is a rule part-way to completing, not a match. Turn these on only if you want the earlier, noisier signal.",
          placement: "bottom",
        },
        {
          target: "settings-compliance-behaviour",
          title: "If screening changes",
          body: "Screening updates always reach you in the app, so a change to an asset you watch cannot pass by unnoticed.",
          placement: "top",
        },
      ],
    },
  };

  // ---------------------------------------------------------------------------
  // Target resolution. Exactly one, connected, visible, measurable — or nothing.
  // ---------------------------------------------------------------------------

  function findTarget(pageKey, name) {
    const matches = document.querySelectorAll(`[${TARGET_ATTRIBUTE}="${CSS.escape(name)}"]`);
    if (matches.length === 0) {
      warn(pageKey, name, "no element carries this guide target");
      return null;
    }
    if (matches.length > 1) {
      warn(pageKey, name, `${matches.length} elements carry this guide target; it must be unique`);
      return null;
    }
    const element = matches[0];
    if (!element.isConnected) {
      warn(pageKey, name, "the target is not in the document");
      return null;
    }
    return element;
  }

  function isMeasurable(element) {
    if (!element || !element.isConnected) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
  }

  function warn(pageKey, name, reason) {
    // Deliberately console.warn rather than a thrown error: a missing marker must never
    // break the page the guide is describing.
    console.warn(`[hm-guide] ${pageKey}: target "${name}" skipped — ${reason}`);
  }

  /** Wait, briefly and boundedly, for a target that is still rendering. */
  function waitForTarget(pageKey, name) {
    return new Promise((resolve) => {
      const immediate = findTarget(pageKey, name);
      if (immediate && isMeasurable(immediate)) {
        resolve(immediate);
        return;
      }
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        window.clearTimeout(timer);
        resolve(value);
      };
      const observer = new MutationObserver(() => {
        const candidate = findTargetQuiet(name);
        if (candidate && isMeasurable(candidate)) finish(candidate);
      });
      observer.observe(document.body, { childList: true, subtree: true, attributes: true });
      const timer = window.setTimeout(() => {
        const candidate = findTarget(pageKey, name);
        finish(candidate && isMeasurable(candidate) ? candidate : null);
      }, READY_TIMEOUT_MS);
    });
  }

  function findTargetQuiet(name) {
    const matches = document.querySelectorAll(`[${TARGET_ATTRIBUTE}="${CSS.escape(name)}"]`);
    return matches.length === 1 ? matches[0] : null;
  }

  // ---------------------------------------------------------------------------
  // Sidebar state. A guide must never point at something the user cannot see, and it
  // must leave the sidebar exactly as it found it.
  // ---------------------------------------------------------------------------

  const sidebar = {
    element: () => document.querySelector("[data-hilal-sidebar], [data-sidebar]"),
    saved: null,
    reveal() {
      const element = this.element();
      if (!element) return;
      // Record the user's own menu state once. Three consecutive menu steps each call
      // reveal(); saving again on the second would record the state this guide itself
      // produced, and restore() would then leave the menu open forever.
      if (this.saved === null) {
        this.saved = {
          collapsed: document.body.classList.contains("sidebar-collapsed"),
          open: element.classList.contains("is-open"),
        };
      }
      document.body.classList.remove("sidebar-collapsed");
      element.classList.add("is-open");
    },
    restore() {
      const element = this.element();
      if (!element || !this.saved) return;
      document.body.classList.toggle("sidebar-collapsed", this.saved.collapsed);
      element.classList.toggle("is-open", this.saved.open);
      this.saved = null;
    },
  };

  function targetIsInSidebar(element) {
    const bar = sidebar.element();
    return Boolean(bar && bar.contains(element));
  }

  // ---------------------------------------------------------------------------
  // The engine.
  // ---------------------------------------------------------------------------

  class GuideEngine {
    constructor(pageKey, guide) {
      this.pageKey = pageKey;
      this.guide = guide;
      this.steps = guide.steps.slice();
      this.index = 0;
      this.active = false;
      this.target = null;
      this.launcher = null;
      this.frame = 0;
      this.observers = [];
      this.build();
    }

    build() {
      const root = document.createElement("div");
      root.className = "hm-guide-root";
      root.hidden = true;
      root.setAttribute("data-hm-guide-root", "");
      root.innerHTML = `
        <div class="hm-guide-panel" data-hm-guide-panel="top"></div>
        <div class="hm-guide-panel" data-hm-guide-panel="bottom"></div>
        <div class="hm-guide-panel" data-hm-guide-panel="left"></div>
        <div class="hm-guide-panel" data-hm-guide-panel="right"></div>
        <div class="hm-guide-spotlight" data-hm-guide-spotlight></div>
        <section class="hm-guide-popover" role="dialog" aria-modal="true"
                 aria-labelledby="hm-guide-title" data-hm-guide-popover tabindex="-1">
          <span class="hm-guide-popover-counter" data-hm-guide-counter aria-live="polite"></span>
          <h2 class="hm-guide-popover-title" id="hm-guide-title" data-hm-guide-title></h2>
          <p class="hm-guide-popover-body" data-hm-guide-body></p>
          <div class="hm-guide-popover-actions">
            <button type="button" class="hm-guide-btn hm-guide-btn-quiet" data-hm-guide-skip>Skip</button>
            <span class="hm-guide-spacer"></span>
            <button type="button" class="hm-guide-btn" data-hm-guide-back>Back</button>
            <button type="button" class="hm-guide-btn hm-guide-btn-primary" data-hm-guide-next>Next</button>
          </div>
        </section>`;
      document.body.appendChild(root);

      this.root = root;
      this.panels = {
        top: root.querySelector('[data-hm-guide-panel="top"]'),
        bottom: root.querySelector('[data-hm-guide-panel="bottom"]'),
        left: root.querySelector('[data-hm-guide-panel="left"]'),
        right: root.querySelector('[data-hm-guide-panel="right"]'),
      };
      this.spotlight = root.querySelector("[data-hm-guide-spotlight]");
      this.popover = root.querySelector("[data-hm-guide-popover]");
      this.counter = root.querySelector("[data-hm-guide-counter]");
      this.title = root.querySelector("[data-hm-guide-title]");
      this.body = root.querySelector("[data-hm-guide-body]");
      this.backButton = root.querySelector("[data-hm-guide-back]");
      this.nextButton = root.querySelector("[data-hm-guide-next]");
      this.skipButton = root.querySelector("[data-hm-guide-skip]");

      this.backButton.addEventListener("click", () => this.go(this.index - 1));
      this.nextButton.addEventListener("click", () => {
        if (this.index >= this.steps.length - 1) this.finish("done");
        else this.go(this.index + 1);
      });
      this.skipButton.addEventListener("click", () => this.finish("skipped"));
      Array.from(this.panels ? Object.values(this.panels) : []).forEach((panel) => {
        panel.addEventListener("click", () => this.finish("skipped"));
      });

      this.onKeyDown = (event) => this.handleKey(event);
      this.onReflow = () => this.scheduleReposition();
    }

    /** Drop every step whose target cannot be resolved exactly once. */
    async resolveSteps() {
      const usable = [];
      for (const step of this.guide.steps) {
        if (typeof step.prepare === "function") {
          try {
            await step.prepare();
          } catch (error) {
            warn(this.pageKey, step.target, `prepare() failed: ${error && error.message}`);
            continue;
          }
        }
        const element = findTarget(this.pageKey, step.target);
        if (element) usable.push(step);
      }
      this.steps = usable;
      return usable.length;
    }

    async start(launcher) {
      if (this.active) this.finish("skipped", { silent: true });
      const count = await this.resolveSteps();
      if (count === 0) {
        warn(this.pageKey, "(guide)", "no configured target resolved; nothing to show");
        return false;
      }
      this.launcher = launcher || document.querySelector("[data-hm-guide-launcher]");
      this.previousFocus = document.activeElement;
      this.active = true;
      this.index = 0;
      this.root.hidden = false;
      document.addEventListener("keydown", this.onKeyDown, true);
      window.addEventListener("scroll", this.onReflow, true);
      window.addEventListener("resize", this.onReflow);
      await this.go(0);
      return true;
    }

    async go(index) {
      if (!this.active) return;
      // Say out loud that a step is being prepared. Moving to a step can involve a
      // smooth scroll, and until it has stopped the outline is still drawn around the
      // previous step. Anything that reads the outline — a test, or a future feature
      // that waits for the guide — needs a way to know the difference between "not
      // moved yet" and "finished moving" that is not a guessed number of milliseconds.
      this.root.dataset.hmGuideBusy = "true";
      const bounded = Math.max(0, Math.min(index, this.steps.length - 1));
      const step = this.steps[bounded];
      if (typeof step.prepare === "function") {
        try {
          await step.prepare();
        } catch (error) {
          warn(this.pageKey, step.target, `prepare() failed: ${error && error.message}`);
        }
      }
      const element = await waitForTarget(this.pageKey, step.target);
      if (!element) {
        // Fail closed: drop the step, renumber, and move on rather than pointing at
        // nothing or guessing a nearby element.
        this.steps.splice(bounded, 1);
        if (this.steps.length === 0) {
          this.finish("skipped");
          return;
        }
        await this.go(Math.min(bounded, this.steps.length - 1));
        return;
      }

      this.index = bounded;
      this.target = element;
      // Open the menu for a menu step, and hand it back the moment the guide leaves the
      // menu again — including when the user presses Back.
      if (targetIsInSidebar(element)) sidebar.reveal();
      else sidebar.restore();

      const remaining = this.steps.length - bounded - 1;
      this.counter.textContent =
        `Step ${bounded + 1} of ${this.steps.length}` +
        (remaining > 0 ? ` · ${remaining} left` : "");
      this.title.textContent = step.title;
      this.body.textContent = step.body;
      this.backButton.disabled = bounded === 0;
      this.nextButton.textContent = remaining === 0 ? "Done" : "Next";
      this.skipButton.textContent = remaining === 0 ? "Close" : "Skip";

      if (this.scrollIntoSafeView(element)) await restAfterScrolling(element);
      await settle();
      this.watchTarget(element);
      this.reposition();
      this.popover.focus({ preventScroll: true });
      // The outline is now around this step's element and the page has stopped moving.
      this.root.dataset.hmGuideBusy = "false";
    }

    /** Scroll only when the target is not already comfortably on screen.
     *
     * Returns whether it scrolled. Moving the page when nothing needed moving makes the
     * popover slide across the screen under the reader's cursor, so its buttons are a
     * moving target for as long as the smooth scroll lasts.
     */
    scrollIntoSafeView(element) {
      const rect = element.getBoundingClientRect();
      const margin = SPOTLIGHT_PADDING * 2;
      const inView =
        rect.top >= margin &&
        rect.left >= 0 &&
        rect.bottom <= document.documentElement.clientHeight - margin &&
        rect.right <= document.documentElement.clientWidth;
      if (inView) return false;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      element.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "center",
        inline: "nearest",
      });
      return true;
    }

    watchTarget(element) {
      this.observers.forEach((observer) => observer.disconnect());
      this.observers = [];
      if (typeof ResizeObserver === "function") {
        const resize = new ResizeObserver(() => this.scheduleReposition());
        resize.observe(element);
        this.observers.push(resize);
      }
      // Repositioning writes inline styles on the panels and the outline. Those writes
      // are themselves attribute changes, so an observer that watched them would
      // schedule another reposition on every frame, for as long as the guide was open.
      // Only changes outside the guide's own markup can move the target.
      const mutations = new MutationObserver((records) => {
        if (records.every((record) => this.root.contains(record.target))) return;
        this.scheduleReposition();
      });
      mutations.observe(document.body, {
        attributes: true,
        attributeFilter: ["class", "style", "hidden"],
        subtree: true,
      });
      this.observers.push(mutations);
    }

    scheduleReposition() {
      if (!this.active || this.frame) return;
      this.frame = window.requestAnimationFrame(() => {
        this.frame = 0;
        this.reposition();
      });
    }

    reposition() {
      if (!this.active || !this.target || !this.target.isConnected) return;
      const rect = this.target.getBoundingClientRect();
      const pad = SPOTLIGHT_PADDING;
      // Not clamped to the viewport. The outline is a claim about where the element is,
      // and clamping it made that claim false the moment the reader scrolled the
      // element off the top: the element kept going and the outline stopped at zero,
      // so the guide pointed at a piece of the page that had nothing to do with the
      // step. The panels below take their size through `setBox`, which floors width and
      // height at zero, so a negative edge simply closes the panel on that side.
      const top = rect.top - pad;
      const left = rect.left - pad;
      const width = rect.width + pad * 2;
      const height = rect.height + pad * 2;
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = document.documentElement.clientHeight;

      // Four panels around a real hole. Nothing is drawn over the target itself.
      setBox(this.panels.top, { top: 0, left: 0, width: viewportWidth, height: top });
      setBox(this.panels.bottom, {
        top: top + height,
        left: 0,
        width: viewportWidth,
        height: Math.max(0, viewportHeight - top - height),
      });
      setBox(this.panels.left, { top, left: 0, width: left, height });
      setBox(this.panels.right, {
        top,
        left: left + width,
        width: Math.max(0, viewportWidth - left - width),
        height,
      });

      setBox(this.spotlight, { top, left, width, height });
      this.spotlight.style.borderRadius = spotlightRadius(this.target, pad, height);

      this.placePopover({ top, left, width, height }, viewportWidth, viewportHeight);
    }

    placePopover(hole, viewportWidth, viewportHeight) {
      const step = this.steps[this.index] || {};
      const preferred = step.placement || "bottom";
      const box = this.popover.getBoundingClientRect();
      const gap = 12;
      const order = [preferred, ...["bottom", "top", "right", "left"].filter((p) => p !== preferred)];

      let chosen = null;
      for (const placement of order) {
        const candidate = positionFor(placement, hole, box, gap);
        if (
          candidate.top >= gap &&
          candidate.left >= gap &&
          candidate.top + box.height <= viewportHeight - gap &&
          candidate.left + box.width <= viewportWidth - gap
        ) {
          chosen = candidate;
          break;
        }
      }
      if (!chosen) {
        // Nothing fits cleanly. Shift into the viewport rather than leaving the screen.
        const fallback = positionFor(preferred, hole, box, gap);
        chosen = {
          top: clamp(fallback.top, gap, viewportHeight - box.height - gap),
          left: clamp(fallback.left, gap, viewportWidth - box.width - gap),
        };
      }
      this.popover.style.top = `${Math.round(chosen.top)}px`;
      this.popover.style.left = `${Math.round(chosen.left)}px`;
    }

    handleKey(event) {
      if (!this.active) return;
      if (event.key === "Escape") {
        event.preventDefault();
        this.finish("skipped");
        return;
      }
      if (event.key === "Tab") {
        this.trapFocus(event);
        return;
      }
      if (isEditable(document.activeElement)) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        if (this.index < this.steps.length - 1) this.go(this.index + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (this.index > 0) this.go(this.index - 1);
      }
    }

    trapFocus(event) {
      const focusable = Array.from(
        this.popover.querySelectorAll("button:not([disabled])"),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;
      if (event.shiftKey && (current === first || current === this.popover)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && current === last) {
        event.preventDefault();
        first.focus();
      } else if (!this.popover.contains(current)) {
        event.preventDefault();
        first.focus();
      }
    }

    finish(reason, options = {}) {
      if (!this.active) return;
      this.active = false;
      this.root.hidden = true;
      document.removeEventListener("keydown", this.onKeyDown, true);
      window.removeEventListener("scroll", this.onReflow, true);
      window.removeEventListener("resize", this.onReflow);
      this.observers.forEach((observer) => observer.disconnect());
      this.observers = [];
      if (this.frame) {
        window.cancelAnimationFrame(this.frame);
        this.frame = 0;
      }
      sidebar.restore();
      this.target = null;
      if (!options.silent) {
        remember(this.guide, reason);
        const focusTarget = this.launcher || this.previousFocus;
        if (focusTarget && typeof focusTarget.focus === "function") {
          focusTarget.focus({ preventScroll: true });
        }
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers.
  // ---------------------------------------------------------------------------

  function setBox(element, box) {
    element.style.top = `${Math.round(box.top)}px`;
    element.style.left = `${Math.round(box.left)}px`;
    element.style.width = `${Math.max(0, Math.round(box.width))}px`;
    element.style.height = `${Math.max(0, Math.round(box.height))}px`;
  }

  /** Corner rounding for the outline drawn around a target.
   *
   * The outline sits `pad` outside the element on every side, so an outline that copied
   * the element's own radius would look tighter than the element it traces. Adding the
   * padding keeps the two curves concentric. `SPOTLIGHT_MIN_RADIUS` gives a square
   * element a soft corner instead of a hard one, and the result never exceeds half the
   * shorter side, which is the point where a rounded rectangle becomes a pill.
   */
  function spotlightRadius(target, pad, height) {
    const declared = Number.parseFloat(window.getComputedStyle(target).borderTopLeftRadius);
    const base = Number.isFinite(declared) ? declared : 0;
    const wanted = Math.max(base + pad, SPOTLIGHT_MIN_RADIUS);
    return `${Math.round(Math.min(wanted, height / 2))}px`;
  }

  function positionFor(placement, hole, box, gap) {
    const centreX = hole.left + hole.width / 2 - box.width / 2;
    const centreY = hole.top + hole.height / 2 - box.height / 2;
    if (placement === "top") return { top: hole.top - box.height - gap, left: centreX };
    if (placement === "left") return { top: centreY, left: hole.left - box.width - gap };
    if (placement === "right") return { top: centreY, left: hole.left + hole.width + gap };
    return { top: hole.top + hole.height + gap, left: centreX };
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(value, high));
  }

  function isEditable(element) {
    if (!element) return false;
    const tag = element.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      element.isContentEditable === true
    );
  }

  /** Two animation frames: layout has settled and rectangles are final. */
  function settle() {
    return new Promise((resolve) => {
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()));
    });
  }

  /** Wait for a smooth scroll to come to a stop, with a hard limit.
   *
   * Placing the popover while the page is still gliding leaves it chasing the target
   * across the screen, and a control that is still moving is a control the reader
   * cannot press.
   */
  function restAfterScrolling(element) {
    return new Promise((resolve) => {
      const deadline = performance.now() + SCROLL_REST_TIMEOUT_MS;
      let previous = element.getBoundingClientRect().top;
      let stillFrames = 0;
      // A scroll that has been asked for but has not begun looks exactly like a scroll
      // that has finished: nothing is moving. Counting still frames alone therefore
      // declared the page settled on the very first frame, before the browser had taken
      // a single step — the outline was drawn around where the element used to be, and
      // then the page slid out from under it. Waiting for real movement first is what
      // tells the two apart.
      let moved = false;
      let finished = false;
      const stop = () => {
        if (finished) return;
        finished = true;
        window.removeEventListener("scrollend", stop, true);
        resolve();
      };
      // Where the browser reports the end of its own scroll, believe it.
      window.addEventListener("scrollend", stop, true);
      const check = () => {
        if (finished) return;
        const current = element.getBoundingClientRect().top;
        const still = Math.abs(current - previous) < 0.5;
        if (!still) moved = true;
        stillFrames = still ? stillFrames + 1 : 0;
        previous = current;
        // The deadline is the safety net: a scroll that never starts, because the
        // element was already where it needed to be, must not hold the guide for ever.
        if ((moved && stillFrames >= 3) || performance.now() > deadline) stop();
        else window.requestAnimationFrame(check);
      };
      window.requestAnimationFrame(check);
    });
  }

  function completionKey(guide) {
    return `hm-guide:${guide.id}:v${guide.version}`;
  }

  function remember(guide, reason) {
    try {
      window.localStorage.setItem(completionKey(guide), reason);
    } catch (error) {
      // A blocked storage API must not break the guide; it only means the automatic
      // invitation may be offered again.
    }
  }

  function seen(guide) {
    try {
      return Boolean(window.localStorage.getItem(completionKey(guide)));
    } catch (error) {
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Boot.
  // ---------------------------------------------------------------------------

  function pageKey() {
    const holder = document.querySelector(`[${PAGE_ATTRIBUTE}]`);
    return holder ? holder.getAttribute(PAGE_ATTRIBUTE) : null;
  }

  function boot() {
    const key = pageKey();
    const launcher = document.querySelector("[data-hm-guide-launcher]");
    const guide = key ? GUIDES[key] : null;
    if (!guide) {
      if (launcher) launcher.hidden = true;
      return;
    }
    const engine = new GuideEngine(key, guide);
    window.HilalMarketsGuide = {
      registry: GUIDES,
      pageKey: key,
      start: () => engine.start(launcher),
      stop: () => engine.finish("skipped"),
      engine,
    };
    if (launcher) {
      launcher.addEventListener("click", () => engine.start(launcher));
      // Offer the launcher only when this page really has something to show. A page
      // whose markers are all absent — an unconfigured market, a section that did not
      // render — would otherwise advertise a guide and then produce nothing.
      const anyTargetPresent = () =>
        guide.steps.some((step) => findTargetQuiet(step.target) !== null);
      const syncLauncher = () => {
        const show = anyTargetPresent();
        // Assign only on a real change. Writing `hidden` unconditionally rewrites the
        // attribute even when the value is identical, and the observer below would see
        // that write as a change and call this again — a loop with no exit that starves
        // the page before it can finish loading.
        if (launcher.hidden === show) launcher.hidden = !show;
        return show;
      };
      if (!syncLauncher()) {
        // Content may still be rendering. Watch for elements arriving — not for attribute
        // changes, which this callback itself can cause. Then stop: an observer left
        // running for the life of the page is a cost paid on every DOM change.
        const observer = new MutationObserver(() => {
          if (syncLauncher()) observer.disconnect();
        });
        observer.observe(document.body, { childList: true, subtree: true });
        window.setTimeout(() => observer.disconnect(), READY_TIMEOUT_MS * 3);
      }
    }

    // Auto-start only on Home, and only on a server-owned signal. Emptiness, account
    // age and a missing Watchlist are not evidence that someone is new — and a guide
    // that opens itself on every page a new user visits is an obstacle, not help.
    if (key !== HOME_PAGE_KEY || seen(guide)) return;
    const isNew = document.body.getAttribute("data-hm-guide-new-user") === "true";
    if (isNew) {
      engine.start(launcher);
      return;
    }
    const invitation = document.querySelector("[data-hm-guide-invite]");
    if (invitation) {
      invitation.hidden = false;
      invitation.addEventListener("click", () => engine.start(launcher));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
