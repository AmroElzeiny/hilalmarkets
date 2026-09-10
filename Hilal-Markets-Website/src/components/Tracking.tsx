import {
  useEffect,
  useCallback,
  useRef,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { trackCtaClick, trackSectionView } from '../analytics'

export type VisibilityMode = 'entry' | 'percentage'

export type VisibilityTrackingOptions = {
  visibilityMode?: VisibilityMode
  dwellMs?: number
  threshold?: number
}

const DEFAULT_DWELL_MS = 1000
const DEFAULT_PERCENTAGE_THRESHOLD = 0.5
/**
 * How many steps the browser is asked to report while a section scrolls past.
 *
 * `intersectionRatio` is the share of the *element* on screen. A section taller than
 * the window can never reach a high share of itself, so with only `[0, threshold, 1]`
 * to watch, the browser reported once at the top edge and then went quiet for the whole
 * of the section. Regular steps mean the share below is recalculated as the visitor
 * scrolls.
 */
const PERCENTAGE_OBSERVER_STEPS = 20

/**
 * How much of the screen this section fills, as a number from 0 to 1.
 *
 * Measured against the element or the window, whichever is smaller. A short panel is
 * "seen" when half of the panel is showing; a section three windows tall is "seen" when
 * it fills half the window - there is no other half of it to wait for. Using
 * `intersectionRatio` alone meant a section taller than twice the window could never
 * reach the threshold, so it was never counted as seen. That is every long section, not
 * only the waitlist form the browser test measures.
 */
function visibleShare(entry: IntersectionObserverEntry): number {
  const windowHeight = entry.rootBounds?.height ?? 0
  const elementHeight = entry.boundingClientRect.height
  const reference = windowHeight > 0
    ? Math.min(elementHeight, windowHeight)
    : elementHeight
  if (reference <= 0) return 0
  return entry.intersectionRect.height / reference
}

function normalizedVisibilityOptions(options: VisibilityTrackingOptions) {
  return {
    visibilityMode: options.visibilityMode ?? 'entry',
    dwellMs: Math.max(0, options.dwellMs ?? DEFAULT_DWELL_MS),
    threshold: Math.min(1, Math.max(0, options.threshold ?? DEFAULT_PERCENTAGE_THRESHOLD)),
  }
}

export function useSectionTracking<T extends HTMLElement = HTMLElement>(
  name: string,
  options: VisibilityTrackingOptions = {},
) {
  const send = useCallback(() => trackSectionView(name), [name])
  return useVisibilityTracking<T>(send, options)
}

export function useVisibilityTracking<T extends HTMLElement = HTMLElement>(
  send: () => boolean,
  options: VisibilityTrackingOptions = {},
) {
  const ref = useRef<T>(null)
  const { visibilityMode, dwellMs, threshold } = normalizedVisibilityOptions(options)

  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') return
    let timer: number | null = null
    let visible = false
    let completed = false
    const attempt = () => {
      if (!visible || completed) return
      completed = send()
      if (!completed) timer = window.setTimeout(attempt, dwellMs)
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        visible = Boolean(
          entry?.isIntersecting &&
          (visibilityMode === 'entry' || visibleShare(entry) >= threshold),
        )
        if (timer !== null) window.clearTimeout(timer)
        timer = visible && !completed ? window.setTimeout(attempt, dwellMs) : null
      },
      visibilityMode === 'entry'
        ? { rootMargin: '0px 0px -20% 0px', threshold: 0 }
        : {
            threshold: Array.from(
              { length: PERCENTAGE_OBSERVER_STEPS + 1 },
              (_, step) => step / PERCENTAGE_OBSERVER_STEPS,
            ),
          },
    )
    observer.observe(element)
    return () => {
      if (timer !== null) window.clearTimeout(timer)
      observer.disconnect()
    }
  }, [dwellMs, send, threshold, visibilityMode])

  return ref
}

export function TrackedSection({
  analyticsName,
  visibilityMode = 'entry',
  dwellMs = DEFAULT_DWELL_MS,
  threshold = DEFAULT_PERCENTAGE_THRESHOLD,
  children,
}: {
  analyticsName: string
  visibilityMode?: VisibilityMode
  dwellMs?: number
  threshold?: number
  children: ReactNode
}) {
  const ref = useSectionTracking<HTMLDivElement>(analyticsName, {
    visibilityMode,
    dwellMs,
    threshold,
  })
  return <div ref={ref} data-analytics-section={analyticsName}>{children}</div>
}

type TrackedCtaProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  analyticsName: string
  analyticsLocation: string
  children: ReactNode
}

export function TrackedCta({
  analyticsName,
  analyticsLocation,
  onClick,
  href = '',
  children,
  ...props
}: TrackedCtaProps) {
  return (
    <a
      {...props}
      href={href}
      onClick={(event) => {
        trackCtaClick(analyticsName, analyticsLocation, href)
        onClick?.(event)
      }}
    >
      {children}
    </a>
  )
}

type TrackedButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  analyticsName: string
  analyticsLocation: string
  children: ReactNode
}

export function TrackedButton({
  analyticsName,
  analyticsLocation,
  onClick,
  children,
  ...props
}: TrackedButtonProps) {
  return (
    <button
      {...props}
      onClick={(event) => {
        trackCtaClick(analyticsName, analyticsLocation)
        onClick?.(event)
      }}
    >
      {children}
    </button>
  )
}
