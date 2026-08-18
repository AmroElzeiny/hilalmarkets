/**
 * A window that opens over the page, and behaves the way a window must.
 *
 * Every one of these rules exists because a dialog without it strands somebody:
 *
 * - **Focus moves in.** Otherwise a keyboard user opens a window and is still tabbing
 *   through the page behind it, unable to reach the buttons they just asked for.
 * - **Focus stays in.** Tab from the last control returns to the first, so nothing
 *   outside can be reached while a decision is pending.
 * - **Escape closes it**, and so does a click on the ground behind it.
 * - **Focus returns** to whatever opened it, so the page does not lose the person's
 *   place.
 * - **The page behind cannot scroll**, so closing the window puts them back where
 *   they were rather than somewhere further down.
 * - **The rest of the page is hidden from screen readers** while it is open.
 *
 * The opening movement goes through `motion.ts`, so a person who asked for less motion
 * gets the window without the movement rather than no window at all.
 */
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { DURATION, move, prefersReducedMotion } from '../motion'
import { Icon } from './Icon'

/** Everything that can hold focus, in the order a Tab key walks it. */
const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  labelledBy,
  size = 'base',
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  labelledBy?: string
  size?: 'base' | 'wide'
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)
  const generatedId = useId()
  const titleId = labelledBy ?? `${generatedId}-title`
  const descriptionId = `${generatedId}-description`

  const close = useCallback(() => onClose(), [onClose])

  useEffect(() => {
    if (!open) return
    openerRef.current = document.activeElement as HTMLElement | null

    const panel = panelRef.current
    // The first thing a person needs is the heading, not the close button — reading
    // starts at the top. The panel takes focus itself and the heading is announced.
    panel?.focus()
    void move(
      panel,
      { opacity: [0, 1], transform: ['translateY(14px) scale(0.985)', 'translateY(0) scale(1)'] },
      { duration: DURATION.base },
    )

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const root = document.getElementById('root')
    root?.setAttribute('aria-hidden', 'true')

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close()
        return
      }
      if (event.key !== 'Tab' || !panel) return
      const stops = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (node) => node.offsetParent !== null || node === document.activeElement,
      )
      if (stops.length === 0) {
        event.preventDefault()
        return
      }
      const first = stops[0]
      const last = stops[stops.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = previousOverflow
      root?.removeAttribute('aria-hidden')
      // Put the person back where they were. Without this the page starts them at the
      // top again and they have to find their place a second time.
      openerRef.current?.focus?.()
    }
  }, [close, open])

  if (!open) return null

  // Rendered outside the application's own root, not inside it.
  //
  // Hiding the page behind means putting `aria-hidden` on `#root`. React renders this
  // component where it was written, which is *inside* `#root` — so the window would
  // have been hidden along with the page it opened over, and a screen reader would
  // have found nothing at all on the screen. Moving it to the body keeps the two
  // apart, which is the only way both halves of that rule can be true at once.
  return createPortal(
    <div className="hm-dialog-ground" onMouseDown={(event) => {
      if (event.target === event.currentTarget) close()
    }}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={`hm-dialog-panel ${size === 'wide' ? 'hm-dialog-panel--wide' : ''}`}
        style={prefersReducedMotion() ? undefined : { opacity: 0 }}
      >
        <div className="hm-dialog-head">
          <div>
            <h2 id={titleId} className="hm-dialog-title">{title}</h2>
            {description && (
              <p id={descriptionId} className="hm-dialog-description">{description}</p>
            )}
          </div>
          <button type="button" onClick={close} className="hm-dialog-close" aria-label="Close">
            <Icon name="close" className="size-5" />
          </button>
        </div>
        <div className="hm-dialog-body">{children}</div>
        {footer && <div className="hm-dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}
