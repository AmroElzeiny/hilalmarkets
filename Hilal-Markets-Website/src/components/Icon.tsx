/**
 * One icon, drawn from the product's own set.
 *
 * The geometry comes from `static/hilalmarkets-icons.js` — the same 101 outline icons
 * every dashboard and Jinja page already uses. Nothing is redrawn here, because a
 * second set of icons is a second set to keep matching, and they never do for long.
 *
 * An icon is decorative by default: it repeats something the text beside it already
 * says, so a screen reader that announced it would say everything twice. Passing a
 * `title` makes it meaningful instead, and then it is announced — for the rare case
 * where the mark is the only thing carrying the message.
 */
import { useEffect, useState } from 'react'

export type IconName =
  | 'alert'
  | 'archive'
  | 'arrow'
  | 'bell'
  | 'billing'
  | 'book'
  | 'calendar'
  | 'chart'
  | 'chat'
  | 'check'
  | 'chevron'
  | 'chevron_left'
  | 'chevron_right'
  | 'clock'
  | 'close'
  | 'compliance'
  | 'cookie'
  | 'copy'
  | 'database'
  | 'download'
  | 'edit'
  | 'external'
  | 'eye'
  | 'file_text'
  | 'filter'
  | 'flag'
  | 'globe'
  | 'guide'
  | 'hand'
  | 'history'
  | 'info'
  | 'integrations'
  | 'keyboard'
  | 'layers'
  | 'link'
  | 'list'
  | 'lock'
  | 'mail'
  | 'market'
  | 'methodology'
  | 'minus'
  | 'moon'
  | 'passport'
  | 'plus'
  | 'printer'
  | 'radar'
  | 'refresh'
  | 'scale'
  | 'scan'
  | 'search'
  | 'send'
  | 'settings'
  | 'shield'
  | 'shield_check'
  | 'sliders'
  | 'spark'
  | 'star'
  | 'support'
  | 'telegram'
  | 'user'
  | 'version'
  | 'warning'
  | 'watchlist'
  | 'workflow'

/** A visible mark for a name the set does not know, so a typo is never invisible. */
const FALLBACK = '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16h.01"/>'

/**
 * The drawing for one name, and whether it is the real one.
 *
 * The flag matters because the fallback cannot be told apart by looking at it: several
 * real icons in the set — `info`, `radar`, `methodology` — are also a circle of radius
 * nine, so a test that recognised the fallback by its shape reported four failures on a
 * page where every icon was correct. The component says which it used instead of
 * leaving anybody to guess.
 */
function iconBody(name: IconName): { body: string; missing: boolean } {
  if (typeof window === 'undefined' || typeof window.iconBody !== 'function') {
    return { body: FALLBACK, missing: true }
  }
  const body = window.iconBody(name)
  return body ? { body, missing: false } : { body: FALLBACK, missing: true }
}

export function Icon({
  name,
  className = 'size-5',
  title,
  strokeWidth = 1.75,
}: {
  name: IconName
  className?: string
  /** Give this only when the icon is the whole message. It is then announced. */
  title?: string
  strokeWidth?: number
}) {
  // The set is loaded by a separate script tag, which may land after React mounts.
  // Reading it once at render time would leave every icon on the fallback forever, so
  // the first paint is re-run on the next frame once the script has had its chance.
  const [drawing, setDrawing] = useState(() => iconBody(name))
  useEffect(() => {
    setDrawing(iconBody(name))
    if (typeof window.iconBody === 'function') return
    const timer = window.setTimeout(() => setDrawing(iconBody(name)), 0)
    return () => window.clearTimeout(timer)
  }, [name])

  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}
      focusable="false"
      data-icon={name}
      data-icon-missing={drawing.missing ? 'true' : undefined}
      dangerouslySetInnerHTML={{
        __html: title ? `<title>${title}</title>${drawing.body}` : drawing.body,
      }}
    />
  )
}

/**
 * An icon inside a rounded container — the brand's own way of giving a mark weight
 * without making it look like a button. The tones are the palette's, never a new one.
 */
export function IconBadge({
  name,
  tone = 'neutral',
  className = '',
  size = 'base',
}: {
  name: IconName
  tone?: 'apple' | 'neutral' | 'ink' | 'blue'
  className?: string
  size?: 'sm' | 'base' | 'lg'
}) {
  const tones = {
    apple: 'bg-[#e8fbbf] text-[#3f5219]',
    neutral: 'bg-[#eef1f4] text-[#454b55]',
    ink: 'bg-ink text-white',
    blue: 'bg-[#e2f1f9] text-[#1c5f80]',
  }
  const sizes = {
    sm: 'size-8 rounded-[10px]',
    base: 'size-11 rounded-[14px]',
    lg: 'size-14 rounded-[18px]',
  }
  const marks = { sm: 'size-4', base: 'size-5', lg: 'size-6' }
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center ${sizes[size]} ${tones[tone]} ${className}`}
      aria-hidden="true"
    >
      <Icon name={name} className={marks[size]} />
    </span>
  )
}
