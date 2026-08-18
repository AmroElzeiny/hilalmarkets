/**
 * The one place this site talks to the animation library.
 *
 * Every animation on the public pages goes through a helper here. That is not tidiness
 * for its own sake — it is the only way three rules can be true everywhere at once:
 *
 * 1. **A person who asked for less motion gets none.** `prefers-reduced-motion` is
 *    checked here, live, so a change in the operating system takes effect without a
 *    reload. A helper called under that setting puts the element straight into its end
 *    state instead of moving it there.
 * 2. **The easing option is spelled correctly.** Motion 11 accepts `ease` and silently
 *    ignores `easing`, which is the older name. Written by hand, the mistake produces an
 *    animation that runs on the default curve and reports nothing. `EASE` below is the
 *    only curve any caller names.
 * 3. **A finished animation keeps its end state.** A Web Animations animation stops
 *    holding its values the moment it finishes, so anything that had an inline style
 *    before snaps back. Helpers here commit the final value.
 *
 * The library itself is Motion 11.18.2, vendored at
 * `src/ai_market_monitor/static/vendor/motion.min.js` and shared with the dashboard.
 */
import { animate, inView, stagger } from 'motion'
import type { MotionKeyframes, MotionOptions } from 'motion'

/** The product's motion curve. Named once; nothing else writes a curve. */
export const EASE: number[] = [0.22, 1, 0.36, 1]

/** How long things take. Calm, and short enough never to be in the way. */
export const DURATION = {
  instant: 0.12,
  quick: 0.2,
  base: 0.36,
  slow: 0.6,
} as const

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

/** Whether this person asked for less movement. Read at the moment it is needed. */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(REDUCED_MOTION_QUERY).matches
}

/** Tells you when that preference changes, so a page can settle without a reload. */
export function onReducedMotionChange(listener: (reduced: boolean) => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return () => {}
  }
  const query = window.matchMedia(REDUCED_MOTION_QUERY)
  const handle = (event: MediaQueryListEvent) => listener(event.matches)
  query.addEventListener('change', handle)
  return () => query.removeEventListener('change', handle)
}

/** The value an element should hold when the animation is over. */
function endState(keyframes: MotionKeyframes): Record<string, string> {
  const final: Record<string, string> = {}
  for (const [property, value] of Object.entries(keyframes)) {
    const last = Array.isArray(value) ? value[value.length - 1] : value
    if (last !== undefined) final[property] = String(last)
  }
  return final
}

function commit(element: HTMLElement, keyframes: MotionKeyframes): void {
  for (const [property, value] of Object.entries(endState(keyframes))) {
    element.style.setProperty(property, value)
  }
}

/**
 * Move one element, and leave it where it was moved to.
 *
 * Returns a promise that settles when the element is holding its end state, whether it
 * animated to get there or was placed there because motion is switched off.
 */
export function move(
  element: HTMLElement | null,
  keyframes: MotionKeyframes,
  options: MotionOptions = {},
): Promise<void> {
  if (!element) return Promise.resolve()
  if (prefersReducedMotion()) {
    commit(element, keyframes)
    return Promise.resolve()
  }
  const playback = animate(element, keyframes, {
    duration: DURATION.base,
    ease: EASE,
    ...options,
  })
  return playback.finished.then(() => commit(element, keyframes)).catch(() => {
    commit(element, keyframes)
  })
}

/**
 * Move a group of elements one after another.
 *
 * Used for lists that appear: the eye follows the sequence and understands the items
 * are one set, rather than meeting a block of text that was not there a moment ago.
 */
export function moveEach(
  elements: HTMLElement[],
  keyframes: MotionKeyframes,
  options: MotionOptions & { stagger?: number } = {},
): Promise<void> {
  if (elements.length === 0) return Promise.resolve()
  const { stagger: step = 0.05, ...rest } = options
  if (prefersReducedMotion()) {
    elements.forEach((element) => commit(element, keyframes))
    return Promise.resolve()
  }
  const playback = animate(elements, keyframes, {
    duration: DURATION.base,
    ease: EASE,
    delay: stagger(step),
    ...rest,
  })
  return playback.finished
    .then(() => elements.forEach((element) => commit(element, keyframes)))
    .catch(() => elements.forEach((element) => commit(element, keyframes)))
}

/**
 * Count a number up to its value.
 *
 * Only ever used on a number that is already true — the end value is written into the
 * element first, so a person with motion switched off, or a search engine, reads the
 * real figure rather than a zero that was going to grow.
 */
export function countTo(
  element: HTMLElement | null,
  to: number,
  format: (value: number) => string = (value) => String(Math.round(value)),
): Promise<void> {
  if (!element) return Promise.resolve()
  if (prefersReducedMotion()) {
    element.textContent = format(to)
    return Promise.resolve()
  }
  // A function is not a valid first argument to `animate`; a pair of numbers with an
  // `onUpdate` is. Written the other way it does nothing at all, quietly.
  const playback = animate(0, to, {
    duration: DURATION.slow,
    ease: EASE,
    onUpdate: (latest: number) => {
      element.textContent = format(latest)
    },
  })
  return playback.finished
    .then(() => {
      element.textContent = format(to)
    })
    .catch(() => {
      element.textContent = format(to)
    })
}

/**
 * Run something the first time an element is seen, and never again.
 *
 * With motion switched off the callback still runs, immediately — the point of a reveal
 * is that the content arrives, and only the movement is optional.
 */
export function whenSeen(
  element: HTMLElement | null,
  run: () => void,
  amount: number | 'some' | 'all' = 0.2,
): () => void {
  if (!element) return () => {}
  if (prefersReducedMotion() || typeof IntersectionObserver === 'undefined') {
    run()
    return () => {}
  }
  let done = false
  return inView(
    element,
    () => {
      if (done) return
      done = true
      run()
    },
    { amount },
  )
}

/* A note on `spring`, which is exported by this library and is not used here.
 *
 * It reads `options.keyframes[0]` the moment it is called, so calling it with only a
 * stiffness and a damping — the shape every example on the internet shows — throws
 * `Cannot read properties of undefined`. Written at the top level of this module, that
 * one line stopped the whole bundle loading, and every one of the three pages rendered
 * as a blank screen. Nothing reported it: the server returned 200, the file downloaded,
 * and the page was empty.
 *
 * If a spring is ever wanted, pass `type: 'spring'` in the options to `move` and let
 * the library build it, rather than building one here.
 */
