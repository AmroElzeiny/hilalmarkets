import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from 'react'

type RevealProps = {
  children: ReactNode
  className?: string
  delay?: number
  cascade?: boolean
  as?: 'div' | 'section' | 'li' | 'article' | 'header' | 'footer'
}

/**
 * Calm, single-shot scroll reveal — a subtle rise + fade the first time the
 * element enters the viewport. Honours prefers-reduced-motion via CSS.
 */
export function Reveal({
  children,
  className = '',
  delay = 0,
  cascade = false,
  as = 'div',
}: RevealProps) {
  const ref = useRef<HTMLElement | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setVisible(true)
            observer.unobserve(entry.target)
          }
        })
      },
      { threshold: 0.18, rootMargin: '0px 0px -8% 0px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const Tag = as as any
  return (
    <Tag
      ref={ref}
      className={`reveal ${cascade ? 'reveal-cascade' : ''} ${visible ? 'is-visible' : ''} ${className}`}
      style={{ '--reveal-delay': `${delay}ms` } as CSSProperties}
    >
      {children}
    </Tag>
  )
}

/**
 * Adds the same reveal treatment to selected blocks inside a generated Figma
 * section. CSS `translate` is used instead of `transform`, so the animation
 * composes safely with Figma's positioning and scale transforms.
 */
export function useRevealElements(
  containerRef: RefObject<HTMLElement | null>,
  selector: string,
  stagger = 80,
) {
  useEffect(() => {
    const container = containerRef.current
    if (!container || !selector) return

    const nodes = Array.from(container.querySelectorAll<HTMLElement>(selector))
    if (nodes.length === 0) return

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    nodes.forEach((node, index) => {
      node.classList.add('scroll-reveal-block')
      node.style.setProperty('--reveal-delay', `${(index % 4) * stagger}ms`)
      if (reducedMotion) node.classList.add('is-visible')
    })

    if (reducedMotion) return

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          entry.target.classList.add('is-visible')
          observer.unobserve(entry.target)
        }
      },
      { threshold: 0.16, rootMargin: '0px 0px -6% 0px' },
    )

    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [containerRef, selector, stagger])
}
