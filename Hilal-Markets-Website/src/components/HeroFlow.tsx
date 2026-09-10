import { useEffect, useRef, useState } from 'react'
import { CheckIcon, StatusPill } from './brand'
import { Icon, type IconName } from './Icon'
import { DURATION, move, moveEach, whenSeen } from '../motion'

/* Coin glyphs (simple vector marks, not the exaggerated crypto clichés the
   brand forbids) */
function Coin({ symbol }: { symbol: 'BTC' | 'ETH' | 'LTC' }) {
  const map = {
    BTC: '#f7931a',
    ETH: '#627eea',
    LTC: '#345d9d',
  } as const
  const glyph = { BTC: '₿', ETH: 'Ξ', LTC: 'Ł' } as const
  return (
    <span
      className="flex size-7 shrink-0 items-center justify-center rounded-full text-[13px] font-bold text-white"
      style={{ backgroundColor: map[symbol] }}
    >
      {glyph[symbol]}
    </span>
  )
}

function Sparkline() {
  return (
    <svg viewBox="0 0 320 64" className="w-full" fill="none" preserveAspectRatio="none" aria-hidden="true">
      <path
        d="M0 50 C30 46 46 30 70 34 C96 38 108 20 140 24 C168 28 184 44 214 40 C244 36 262 14 300 10 L320 8"
        stroke="#a8d936"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <circle cx="300" cy="10" r="5" fill="#cbfa4d" stroke="#2b2e35" strokeWidth="1.4" />
    </svg>
  )
}

/* -------------------------------------------------------------------------- */
/*  The canvas                                                                 */
/* -------------------------------------------------------------------------- */
/**
 * How a rule is actually built in the product, shown as four connected blocks.
 *
 * This panel used to be a chat bubble: a sentence typed at an assistant, with a button
 * under it. That was the wrong picture in two ways. It showed the product as a place
 * you talk to rather than a place you build in — the canvas is how a rule is put
 * together — and a wall of prose is the one thing a beginner cannot read at a glance.
 *
 * So it is blocks now, and the rule reads top to bottom: which coin, what has to
 * happen, what has to happen after that, what you get. Four short lines, no sentence
 * longer than five words, and the numbers that matter — the 5% — carried on a pill of
 * their own rather than buried in prose.
 *
 * The connectors draw themselves once, when the panel is first seen, in the order the
 * rule is read. That is the brand's own use of motion: a left-to-right — here
 * top-to-bottom — product flow that explains a relationship. It runs once and stops.
 * With reduced motion asked for, every block and every line is simply already there.
 */
type Block = {
  id: string
  icon: IconName
  label: string
  value?: string
  tone: 'asset' | 'rule' | 'alert'
}

const BLOCKS: Block[] = [
  { id: 'asset', icon: 'market', label: 'BTC/USDT', value: 'Screened', tone: 'asset' },
  { id: 'sweep', icon: 'chart', label: 'Dips below yesterday’s low', tone: 'rule' },
  { id: 'recover', icon: 'refresh', label: 'Then recovers', value: '5%', tone: 'rule' },
  { id: 'alert', icon: 'bell', label: 'Tell me straight away', tone: 'alert' },
]

function StrategyCanvas() {
  const rootRef = useRef<HTMLDivElement>(null)
  const [active, setActive] = useState<string | null>(null)

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    return whenSeen(root, () => {
      const blocks = Array.from(root.querySelectorAll<HTMLElement>('[data-canvas-block]'))
      void moveEach(
        blocks,
        { opacity: [0, 1], transform: ['translateY(10px)', 'translateY(0)'] },
        { duration: DURATION.base, stagger: 0.11 },
      )
      const lines = Array.from(root.querySelectorAll<SVGPathElement>('[data-canvas-line]'))
      lines.forEach((line, index) => {
        // The line is drawn by shrinking its own dash gap to nothing. The length is
        // measured from the path rather than written down, so the value cannot fall
        // out of step with the geometry above it.
        const length = line.getTotalLength()
        line.style.strokeDasharray = String(length)
        void move(
          line as unknown as HTMLElement,
          { strokeDashoffset: [length, 0] },
          { duration: DURATION.slow, delay: 0.18 + index * 0.11 },
        )
      })
    })
  }, [])

  return (
    <div ref={rootRef} className="hm-canvas" data-name="Strategy canvas">
      {/* The canvas surface: a dotted field, which is what the builder itself looks
          like. It is a ground, so it is quiet and carries no meaning. */}
      <div className="hm-canvas-grid" aria-hidden="true" />

      <ol className="hm-canvas-flow">
        {BLOCKS.map((block, index) => (
          <li key={block.id}>
            <div
              data-canvas-block
              data-tone={block.tone}
              data-active={active === block.id}
              className="hm-canvas-block"
              onPointerEnter={() => setActive(block.id)}
              onPointerLeave={() => setActive(null)}
            >
              <span className="hm-canvas-block-mark">
                <Icon name={block.icon} className="size-[15px]" />
              </span>
              <span className="hm-canvas-block-label">{block.label}</span>
              {block.value && <span className="hm-canvas-block-value tnum">{block.value}</span>}
            </div>

            {index < BLOCKS.length - 1 && (
              <svg className="hm-canvas-link" viewBox="0 0 24 26" fill="none" aria-hidden="true">
                <path
                  data-canvas-line
                  d="M12 1v24"
                  stroke="#b8c2ce"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                />
              </svg>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/**
 * The hero illustration, at every screen size.
 *
 * It is the same three panels everywhere; only the shape of the grid changes:
 *
 *   phone   one column, page gutter matched to the hero text (15px)
 *   tablet  two columns — watchlist beside the canvas, the three states in a row below
 *   desktop the original three columns
 *
 * Nothing inside a panel is removed or swapped per size, so the phone shows the whole
 * story rather than a cut-down version of it.
 *
 * The two-column arrangement starts at 900px, not at Tailwind's 768px `md`, because the
 * rest of the page collapses to a single column below 900 — the
 * `@media (max-width: 899px)` block in index.css, which every other row obeys.
 */
export function HeroFlow() {
  return (
    <div className="relative mx-auto grid w-full max-w-[560px] items-stretch gap-3 px-[15px] sm:max-w-[820px] sm:gap-4 sm:px-5 min-[900px]:grid-cols-2 lg:max-w-[1228px] lg:grid-cols-[1fr_1.15fr_1fr] lg:gap-3 lg:px-6" data-name="Hero flow">
      {/* Panel 1 — screened watchlist */}
      <div className="rounded-[22px] border border-hairline bg-surface p-4 shadow-[0_18px_40px_-28px_rgba(43,46,53,0.35)]">
        <div className="space-y-2.5">
          {[
            { s: 'ETH', pair: 'ETH/USDT' },
            { s: 'LTC', pair: 'LTC/USDT' },
          ].map((r) => (
            <div
              key={r.pair}
              className="flex items-center justify-between rounded-2xl border border-hairline bg-[#fafbfc] px-3 py-2.5"
            >
              <div className="flex items-center gap-2.5">
                <Coin symbol={r.s as 'ETH' | 'LTC'} />
                <span className="text-[13px] font-bold text-ink tnum">{r.pair}</span>
              </div>
              <StatusPill tone="apple" dot>halal</StatusPill>
            </div>
          ))}

          <div className="rounded-2xl border border-[#d0d6de] bg-surface p-3.5 shadow-[0_10px_30px_-22px_rgba(43,46,53,0.5)]">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Coin symbol="BTC" />
                <span className="text-[16px] font-bold text-ink tnum">BTC/USDT</span>
              </div>
              <StatusPill tone="apple" dot>halal</StatusPill>
            </div>
            <div className="mt-3 flex items-start gap-2 border-t border-hairline pt-3">
              <span className="mt-0.5 flex size-4 items-center justify-center rounded-full bg-[#e7f5ce]">
                <svg viewBox="0 0 10 8" className="size-2.5" fill="none" aria-hidden="true">
                  <path d="M1 4.2 3.4 6.6 9 1" stroke="#55712a" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <div>
                <p className="text-[12px] font-bold text-ink">Evidence verified</p>
                <p className="text-[10.5px] text-ink-soft tnum">SC Malaysia SAC · 20 Jul 2020</p>
              </div>
            </div>
            <div className="mt-2">
              <Sparkline />
            </div>
          </div>
        </div>
      </div>

      {/* Panel 2 — the canvas a rule is built on */}
      <div className="rounded-[22px] border border-[#c8cdd5] bg-surface p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-apple text-ink">
            <Icon name="workflow" className="size-3.5" />
          </span>
          <p className="text-[13px] font-bold text-ink">Build it on the canvas</p>
          <span className="ml-auto shrink-0 rounded-full bg-[#edf1f4] px-2.5 py-1 text-[9px] font-bold text-[#5c646e]">
            screened only
          </span>
        </div>

        <StrategyCanvas />

        <p className="mt-3.5 flex items-center gap-1.5 text-[11.5px] text-[#5c646e]">
          <Icon name="check" className="size-3.5 shrink-0 text-[#55712a]" />
          You approve the rule before anything is watched.
        </p>
      </div>

      {/* Panel 3 — monitoring states. Three cards: stacked on a phone, a row across the
          full width on a tablet, back to a column beside the other panels on a desktop. */}
      <div className="flex flex-col gap-2.5 min-[900px]:col-span-2 min-[900px]:grid min-[900px]:grid-cols-3 min-[900px]:gap-3 lg:col-span-1 lg:flex lg:flex-col lg:gap-2.5">
        <div className="rounded-2xl border border-hairline bg-surface p-3.5">
          <StatusPill tone="blue" dot>forming</StatusPill>
          <p className="mt-2 text-[13px] font-bold text-ink tnum">BTC/USDT recovery: +2.3% of +5%</p>
        </div>
        <div className="rounded-2xl border-[1.5px] border-[#a9d83f] bg-surface p-3.5">
          <div className="flex items-center gap-2">
            <span className="flex size-5 items-center justify-center rounded-full bg-[#25d366] text-white">
              <CheckIcon className="size-3" />
            </span>
            <StatusPill tone="apple">ready for review</StatusPill>
          </div>
          <p className="mt-2 text-[14px] font-bold text-ink">LTC/USDT completed the plan</p>
          <p className="text-[10.5px] text-ink-soft tnum">Recovery from sweep low · +5.4%</p>
        </div>
        <div className="rounded-2xl border border-hairline bg-surface p-3.5">
          <StatusPill tone="review" dot>monitoring paused</StatusPill>
          <p className="mt-2 text-[13px] font-bold text-ink">ETH/USDT Shariah status is under review</p>
        </div>
      </div>
    </div>
  )
}
