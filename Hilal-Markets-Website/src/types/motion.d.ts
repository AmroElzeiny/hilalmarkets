/**
 * The shapes this site uses from the vendored Motion 11 bundle.
 *
 * The bundle is minified JavaScript with no types beside it, so the parts we call are
 * declared here. Only what is used is declared: an unused name typed loosely is an
 * invitation to call it wrongly.
 *
 * Two traps in this library are worth naming, because it ignores what it does not
 * understand instead of failing:
 *
 * - the easing option is `ease`, never `easing`;
 * - `animate` does not accept a function as its first argument.
 *
 * `src/motion.ts` is the only module allowed to import this. Everything else goes
 * through the helpers there, so neither trap can be walked into twice.
 */
declare module 'motion' {
  export type MotionKeyframes = Record<string, string | number | Array<string | number>>

  export type MotionOptions = {
    duration?: number
    delay?: number | ((index: number, total: number) => number)
    ease?: string | number[]
    repeat?: number
    times?: number[]
    at?: string | number
    type?: string
    stiffness?: number
    damping?: number
    mass?: number
    onUpdate?: (latest: number) => void
  }

  export type MotionPlayback = {
    finished: Promise<unknown>
    stop: () => void
    complete: () => void
  }

  export function animate(
    subject: Element | Element[] | NodeListOf<Element> | string,
    keyframes: MotionKeyframes,
    options?: MotionOptions,
  ): MotionPlayback

  export function animate(
    from: number,
    to: number,
    options?: MotionOptions,
  ): MotionPlayback

  export function inView(
    subject: Element | Element[] | NodeListOf<Element> | string,
    onStart: (entry: IntersectionObserverEntry) => void | (() => void),
    options?: { root?: Element | null; margin?: string; amount?: number | 'some' | 'all' },
  ): () => void

  export function scroll(
    onScroll: (progress: number) => void,
    options?: { target?: Element; offset?: Array<string | number> },
  ): () => void

  export function stagger(
    duration: number,
    options?: { start?: number; from?: number | 'first' | 'last' | 'center' },
  ): (index: number, total: number) => number

}
