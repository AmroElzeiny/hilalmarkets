import type { ReactNode } from 'react'

/* The Hilal Markets mark: a rounded container with a chamfered top-right
   corner (the brand signature) holding a "scanning" motif — a small and a
   large dot read as an asset being screened. Used once, at brand scale. */
export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path
        d="M8 1h13l10 10v13a7 7 0 0 1-7 7H8a7 7 0 0 1-7-7V8a7 7 0 0 1 7-7Z"
        fill="#2b2e35"
      />
      <path d="M21 1l10 10h-6a4 4 0 0 1-4-4V1Z" fill="#cbfa4d" />
      <circle cx="14.5" cy="12.5" r="3.2" fill="#cbfa4d" />
      <circle cx="17" cy="21.5" r="5" fill="#cbfa4d" />
    </svg>
  )
}

export function Wordmark() {
  return (
    <div className="flex items-center gap-2.5 select-none">
      <LogoMark />
      <span className="font-display text-[19px] tracking-[-0.5px] text-ink lowercase">
        hilal markets
      </span>
    </div>
  )
}

/* Status pill — colour is always paired with a text label + optional dot,
   never colour alone (brand rule 10). */
type Tone = 'apple' | 'blue' | 'neutral' | 'review'
const tones: Record<Tone, string> = {
  apple: 'bg-[#e8fbbf] text-[#46551b]',
  blue: 'bg-[#e2f1f9] text-[#1f6e97]',
  neutral: 'bg-[#eef1f4] text-[#5b626b]',
  review: 'bg-[#fdf2df] text-[#8a6316]',
}

export function StatusPill({
  tone = 'neutral',
  children,
  dot = false,
}: {
  tone?: Tone
  children: ReactNode
  dot?: boolean
}) {
  const dotColor =
    tone === 'apple'
      ? '#7ba428'
      : tone === 'blue'
        ? '#2a8fc3'
        : tone === 'review'
          ? '#c99320'
          : '#9aa0a8'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold leading-none ${tones[tone]}`}
    >
      {dot && (
        <span
          className="size-1.5 rounded-full"
          style={{ backgroundColor: dotColor }}
        />
      )}
      {children}
    </span>
  )
}

export function CheckIcon({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} fill="none" aria-hidden="true">
      <path
        d="M4.5 10.5l3.5 3.5 7.5-8"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
