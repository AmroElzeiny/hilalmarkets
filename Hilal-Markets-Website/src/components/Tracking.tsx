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
          (visibilityMode === 'entry' || entry.intersectionRatio >= threshold),
        )
        if (timer !== null) window.clearTimeout(timer)
        timer = visible && !completed ? window.setTimeout(attempt, dwellMs) : null
      },
      visibilityMode === 'entry'
        ? { rootMargin: '0px 0px -20% 0px', threshold: 0 }
        : { threshold: [0, threshold, 1] },
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
