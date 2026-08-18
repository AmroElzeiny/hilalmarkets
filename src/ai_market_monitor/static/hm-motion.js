/* The motion vocabulary for the redesigned dashboard pages.
 *
 * One owner for durations, easings and the reduced-motion decision. Components ask
 * for a *named* movement ("settle", "lift", "reveal") instead of writing their own
 * numbers, so the whole path moves at one speed and a single change retunes it.
 *
 * Built on Motion One, vendored at /static/vendor/motion.min.js. Motion One drives
 * the Web Animations API, so anything it starts runs off the main thread and never
 * blocks a click.
 *
 * `brand guide.md` section 15: motion explains relationships and progress. It never
 * loops for decoration, never flashes, and always has a reduced-motion form. That is
 * enforced here rather than left to each component: when a person asks for less
 * motion, `animate()` below jumps straight to the final state.
 */

import { animate as motionAnimate, inView, stagger } from "./vendor/motion.min.js";

const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

export const prefersReducedMotion = () => reduceQuery.matches;

/* Durations, in seconds. Short enough that nothing ever feels like waiting. */
export const DURATION = Object.freeze({
  instant: 0.12,
  quick: 0.2,
  base: 0.32,
  slow: 0.48,
});

/* One easing family, taken from `--hm-motion` in hilalmarkets-brand.css so CSS
   transitions and scripted animation cannot drift apart.

   Both entries are cubic-bezier control points, which is the only easing shape the
   vendored bundle accepts. A spring has to be passed as its own generator, not as an
   easing object, so there is deliberately no spring constant here waiting to be used
   as one and silently doing nothing.

   The option is `ease`. It used to be written as `easing`, which is the name the older
   Motion One API used; the vendored bundle is Motion 11, which ignores an option it
   does not know. Nothing failed and nothing warned — every scripted animation in the
   product simply ran on the library's default curve while this file said it did not.
   `EASE_OPTION` is the one place the name is written, so it cannot be got wrong twice. */
export const EASE = Object.freeze({
  standard: [0.2, 0.75, 0.25, 1],
  exit: [0.4, 0, 1, 1],
});

const EASE_OPTION = "ease";

/* One more thing this bundle does quietly, written here because it cost a visible bug.
 *
 * **Everything in the keyframes object is a value to travel to, including the ones that
 * only describe how a transform is drawn.** The side menu's flyout name asked for
 * `transformPerspective: 700` beside its `rotateY`, meaning "draw the turn as if the
 * viewer were 700px away". The bundle read it as a keyframe like any other, found no
 * perspective on the element to start from, and travelled up from nothing — so for the
 * first frames of every flyout the perspective was a few pixels, which magnifies
 * anything leaning towards the viewer enormously. The name shot sideways across the
 * page and then shrank back as the number climbed.
 *
 * A setting is not a keyframe. If a setting is ever needed, write it into the element's
 * own style before animating, never into the keyframes. And nothing in this product
 * animates in three dimensions at all now: `rotateX`, `rotateY` and `transformPerspective`
 * are forbidden by `test_no_animation_in_this_product_is_three_dimensional`, because a
 * turn in space has never once explained a relationship here — it only decorates, which
 * `brand guide.md` section 15 rules out. */

/** Options for the vendored bundle, with our curve unless the caller named another. */
function withEase(options = {}, fallback = EASE.standard) {
  const { easing, ...rest } = options;
  return { [EASE_OPTION]: easing || rest[EASE_OPTION] || fallback, ...rest };
}

/**
 * Animate, unless this person asked for less motion.
 *
 * With reduced motion the element is placed at its final keyframe immediately. The
 * result is the same interface, reached without movement — never a missing element.
 */
export function animate(element, keyframes, options = {}) {
  if (!element) return null;
  if (prefersReducedMotion()) {
    const settled = {};
    for (const [property, value] of Object.entries(keyframes)) {
      settled[property] = Array.isArray(value) ? value[value.length - 1] : value;
    }
    return motionAnimate(element, settled, { duration: 0 });
  }
  return motionAnimate(element, keyframes, withEase(options));
}

/** Rows and cards arriving: each one settles just after the one before it. */
export function settleIn(elements, { delayStep = 0.028, from = 10 } = {}) {
  const list = Array.from(elements || []);
  if (!list.length) return null;
  if (prefersReducedMotion()) {
    list.forEach((node) => {
      node.style.opacity = "1";
      node.style.transform = "none";
    });
    return null;
  }
  return motionAnimate(
    list,
    { opacity: [0, 1], transform: [`translateY(${from}px)`, "translateY(0px)"] },
    withEase({ duration: DURATION.base, delay: stagger(delayStep) }),
  );
}

/** A panel opening: scale and fade together so it reads as one surface arriving. */
export function reveal(element, { from = 0.97 } = {}) {
  return animate(
    element,
    { opacity: [0, 1], transform: [`scale(${from})`, "scale(1)"] },
    { duration: DURATION.quick },
  );
}

/** A panel closing. Faster than opening, because nobody wants to wait to leave. */
export function dismiss(element, { to = 0.98 } = {}) {
  if (!element) return Promise.resolve();
  if (prefersReducedMotion()) return Promise.resolve();
  return motionAnimate(
    element,
    { opacity: [1, 0], transform: ["scale(1)", `scale(${to})`] },
    withEase({ duration: DURATION.instant }, EASE.exit),
  ).finished.catch(() => undefined);
}

/**
 * Count a number up to its new value.
 *
 * Only used where the number is a count of things the person can see, so the motion
 * says "this many". Never used for a price: a price that visibly travels through
 * values it never had would be inventing market data.
 *
 * Driven by `animate(from, to, { onUpdate })`. It used to pass a callback as the first
 * argument, which is the older Motion One shape; Motion 11 accepts an element, a number
 * pair or a motion value there, and does nothing at all with a function. It did not
 * throw, so nothing anywhere reported it: every counted number in the product — the
 * market page's totals and the subscription page's allowances — sat frozen at the value
 * this function had just written into it, which was zero.
 *
 * The final value is written directly at the end rather than left to the last frame, so
 * the number a person reads is exactly the number that was asked for and never a
 * rounding of the frame the animation happened to stop on.
 */
export function countTo(node, value, { duration = DURATION.slow, format } = {}) {
  const render = format || ((current) => String(Math.round(current)));
  const from = Number.parseFloat(node.dataset.countValue || "0") || 0;
  node.dataset.countValue = String(value);
  if (prefersReducedMotion() || from === value) {
    node.textContent = render(value);
    return null;
  }
  const controls = motionAnimate(
    from,
    value,
    withEase({ duration, onUpdate: (current) => { node.textContent = render(current); } }),
  );
  controls?.finished?.then(
    () => { node.textContent = render(value); },
    () => { node.textContent = render(value); },
  );
  return controls;
}

/**
 * A connector drawing itself from its parent towards its child.
 *
 * The movement is the relationship: the line travels the way the rule flows, so a
 * person sees *which* card was just joined to *which*, instead of a line appearing
 * fully formed and leaving them to work it out.
 */
export function drawPath(path) {
  if (!path || typeof path.getTotalLength !== "function") return null;
  let length = 0;
  try {
    length = path.getTotalLength();
  } catch {
    return null;
  }
  if (!length || prefersReducedMotion()) return null;
  path.style.strokeDasharray = `${length}`;
  return motionAnimate(
    path,
    { strokeDashoffset: [length, 0] },
    withEase({ duration: DURATION.base }),
  ).finished.then(
    () => {
      path.style.strokeDasharray = "";
      path.style.strokeDashoffset = "";
    },
    () => undefined,
  );
}

/**
 * Point at one card, once.
 *
 * Used when a person asks "show me" about a problem: the board scrolls the card into
 * view and this says which one. A single pulse, never a loop — `brand guide.md`
 * section 15 rules out movement that repeats without meaning.
 */
export function attention(element) {
  if (!element || prefersReducedMotion()) return null;
  return motionAnimate(
    element,
    { transform: ["scale(1)", "scale(1.04)", "scale(1)"] },
    withEase({ duration: DURATION.slow }),
  );
}

/** Run something once the element has actually been scrolled into view. */
export function whenSeen(element, run, options = {}) {
  if (!element) return () => {};
  if (prefersReducedMotion()) {
    run();
    return () => {};
  }
  return inView(element, () => { run(); }, { amount: 0.2, ...options });
}

export { stagger };
