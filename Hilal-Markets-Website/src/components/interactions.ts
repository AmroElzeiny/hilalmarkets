/**
 * The behaviours the rebuilt public pages share.
 *
 * Each one exists to answer a question a person would otherwise have to work out for
 * themselves: which card is under my pointer, which section am I reading, how much is
 * left, where is the top. None of them is decoration, and every one of them stops when
 * the person has asked for less motion.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { onReducedMotionChange, prefersReducedMotion } from '../motion'

/** How far a card turns, in degrees, at the very edge. Small on purpose. */
const TILT_LIMIT = 4

/**
 * Turns an element slightly towards the pointer.
 *
 * Real 3D: the element rotates about its own X and Y axes inside a perspective, so it
 * reads as a physical card being tipped rather than a rectangle changing colour. The
 * two angles are written as custom properties and the transform lives in CSS, so the
 * hover lift and the tilt compose instead of overwriting each other.
 *
 * Switched off entirely for a coarse pointer — a finger has no hover, and a card that
 * tips on touch only delays the tap.
 */
export function useTilt<T extends HTMLElement>() {
  const ref = useRef<T>(null)

  const reset = useCallback(() => {
    const node = ref.current
    if (!node) return
    node.style.setProperty('--tilt-x', '0')
    node.style.setProperty('--tilt-y', '0')
  }, [])

  const onPointerMove = useCallback(
    (event: React.PointerEvent<T>) => {
      const node = ref.current
      if (!node || event.pointerType !== 'mouse' || prefersReducedMotion()) return
      const box = node.getBoundingClientRect()
      if (box.width === 0 || box.height === 0) return
      // -0.5 … 0.5 from the centre of the card, in each direction.
      const across = (event.clientX - box.left) / box.width - 0.5
      const down = (event.clientY - box.top) / box.height - 0.5
      // Moving the pointer down tips the top of the card away, so the sign is flipped
      // on the X axis. Without that the card leans the wrong way and feels broken.
      node.style.setProperty('--tilt-x', (-down * TILT_LIMIT).toFixed(2))
      node.style.setProperty('--tilt-y', (across * TILT_LIMIT).toFixed(2))
    },
    [],
  )

  return { ref, onPointerMove, onPointerLeave: reset, onBlur: reset }
}

/**
 * Which section of a long document is being read.
 *
 * Not "the topmost visible section" — that flickers at every boundary. The section
 * whose start is nearest above a line a third of the way down the window is the one a
 * person is actually looking at, and it changes once per section rather than twice.
 */
export function useScrollSpy(ids: string[], offset = 140): string {
  const [active, setActive] = useState(ids[0] ?? '')

  useEffect(() => {
    if (ids.length === 0) return
    let frame = 0

    const measure = () => {
      frame = 0
      const line = offset + window.innerHeight / 3
      let current = ids[0]
      for (const id of ids) {
        const node = document.getElementById(id)
        if (!node) continue
        if (node.getBoundingClientRect().top <= line) current = id
      }
      // At the very bottom nothing new can scroll past the line, so the last section
      // would never be marked. Reaching the end means reading the end.
      const atBottom =
        window.innerHeight + window.scrollY >= document.body.scrollHeight - 2
      setActive(atBottom ? ids[ids.length - 1] : current)
    }

    const onScroll = () => {
      if (frame) return
      frame = window.requestAnimationFrame(measure)
    }

    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame) window.cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [ids, offset])

  return active
}

/**
 * How far through the page a person has read, from 0 to 1.
 *
 * Measured against what can actually be scrolled, so a document shorter than the
 * window reports 1 rather than dividing by zero and reporting nothing.
 */
export function useReadingProgress(): number {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    let frame = 0
    const measure = () => {
      frame = 0
      const scrollable = document.body.scrollHeight - window.innerHeight
      setProgress(scrollable <= 0 ? 1 : Math.min(1, Math.max(0, window.scrollY / scrollable)))
    }
    const onScroll = () => {
      if (frame) return
      frame = window.requestAnimationFrame(measure)
    }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll, { passive: true })
    return () => {
      if (frame) window.cancelAnimationFrame(frame)
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
    }
  }, [])

  return progress
}

/** Whether the page has been scrolled far enough for "back to top" to be useful. */
export function useScrolledPast(distance = 600): boolean {
  const [past, setPast] = useState(false)
  useEffect(() => {
    const onScroll = () => setPast(window.scrollY > distance)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [distance])
  return past
}

/** Live answer to "has this person asked for less motion", updated if they change it. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => onReducedMotionChange(setReduced), [])
  return reduced
}

/**
 * Moves the page to an element the way the person asked for it.
 *
 * A jump is instant when motion is reduced and smooth otherwise, and either way focus
 * lands on the heading — so a keyboard user's next Tab continues from the section they
 * asked for rather than from the link they pressed.
 */
export function jumpTo(id: string): void {
  const node = document.getElementById(id)
  if (!node) return
  node.scrollIntoView({
    behavior: prefersReducedMotion() ? 'auto' : 'smooth',
    block: 'start',
  })
  const heading = node.querySelector<HTMLElement>('h2, h3') ?? node
  const restore = heading.getAttribute('tabindex')
  heading.setAttribute('tabindex', '-1')
  heading.focus({ preventScroll: true })
  if (restore === null) {
    heading.addEventListener('blur', () => heading.removeAttribute('tabindex'), { once: true })
  }
}
