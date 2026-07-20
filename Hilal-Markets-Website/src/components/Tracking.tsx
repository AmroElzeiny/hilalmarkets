import {
  useEffect,
  useCallback,
  useRef,
  type AnchorHTMLAttributes,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import { trackCtaClick, trackSectionView } from '../analytics'

export function useSectionTracking<T extends HTMLElement = HTMLElement>(name: string) {
  const send = useCallback(() => trackSectionView(name), [name])
  return useVisibilityTracking<T>(send)
}

export function useVisibilityTracking<T extends HTMLElement = HTMLElement>(send: () => boolean) {
  const ref = useRef<T>(null)

  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') return
    let timer: number | null = null
    let visible = false
    let completed = false
    const attempt = () => {
      if (!visible || completed) return
      completed = send()
      if (!completed) timer = window.setTimeout(attempt, 1000)
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        visible = Boolean(entry?.isIntersecting && entry.intersectionRatio >= 0.5)
        if (timer !== null) window.clearTimeout(timer)
        timer = visible && !completed ? window.setTimeout(attempt, 1000) : null
      },
      { threshold: [0, 0.5, 1] },
    )
    observer.observe(element)
    return () => {
      if (timer !== null) window.clearTimeout(timer)
      observer.disconnect()
    }
  }, [send])

  return ref
}

export function TrackedSection({
  analyticsName,
  children,
}: {
  analyticsName: string
  children: ReactNode
}) {
  const ref = useSectionTracking<HTMLDivElement>(analyticsName)
  return <div ref={ref}>{children}</div>
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
