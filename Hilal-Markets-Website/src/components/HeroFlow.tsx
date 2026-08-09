import { CheckIcon, StatusPill } from './brand'
import { TrackedCta } from './Tracking'

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

/**
 * The hero illustration, at every screen size.
 *
 * It used to be hidden below the small breakpoint, so a phone met the hero with words
 * only. It is the same three panels everywhere; only the shape of the grid changes:
 *
 *   phone   one column, page gutter matched to the hero text (15px)
 *   tablet  two columns — watchlist beside the prompt, the three states in a row below
 *   desktop the original three columns
 *
 * Nothing inside a panel is removed or swapped per size, so the phone shows the whole
 * story rather than a cut-down version of it.
 *
 * Every size keeps a side margin. The desktop row used to be a fixed 1180px box with no
 * padding, so on a screen between 1024 and 1180 wide the cards touched both edges. The
 * box is 1228 wide with a 24px gutter instead: the same 1180 of content on a wide screen,
 * and a margin that survives on a narrow laptop.
 */
export function HeroFlow() {
  return (
    <div className="relative mx-auto grid w-full max-w-[560px] items-stretch gap-3 px-[15px] sm:max-w-[820px] sm:gap-4 sm:px-5 md:grid-cols-2 lg:max-w-[1228px] lg:grid-cols-[1fr_1.15fr_1fr] lg:gap-3 lg:px-6" data-name="Hero flow">
{/* Panel 1 — screened watchlist */}
      <div className="rounded-[22px] border border-hairline bg-surface p-4 shadow-[0_18px_40px_-28px_rgba(43,46,53,0.35)]">
        <div className="space-y-2.5">
          {[
            { s: 'ETH', pair: 'ETH/USDT', muted: true },
            { s: 'LTC', pair: 'LTC/USDT', muted: false },
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

      {/* Panel 2 — describe what to watch */}
      <div className="rounded-[22px] border border-[#c8cdd5] bg-surface p-4 sm:p-5">
        {/* The mark and the pill keep their size on a narrow phone; only the sentence
            between them gives way and wraps, so the row never squashes the label. */}
        <div className="flex items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-apple text-ink">
            <svg viewBox="0 0 14 14" className="size-3.5" fill="none" aria-hidden="true">
              <path d="M7 1v12M1 7h12" stroke="#2b2e35" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </span>
          <p className="text-[13px] font-bold text-ink">Describe what you want to watch</p>
          <span className="ml-auto shrink-0 rounded-full bg-[#edf1f4] px-2.5 py-1 text-[9px] font-bold text-[#656b74]">
            screened only
          </span>
        </div>

        <div className="mt-4 rounded-2xl border border-hairline bg-[#f2f5f7] p-3.5 sm:p-4">
          <div className="flex gap-2.5">
            <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-ink text-[11px] font-bold text-white">
              you
            </span>
            <p className="text-[13.5px] font-semibold leading-relaxed text-ink">
              Alert me when a screened coin sweeps yesterday's low and recovers 5%.
            </p>
          </div>
        </div>

        {/* The illustration sits inside the hero, so its button is a real link like any
            other. It goes to the waitlist: while the product is invite-only, nothing on
            this page may open an account. */}
        <TrackedCta analyticsName="join_waitlist" analyticsLocation="hero_flow" href="#waitlist" className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-apple px-4 py-2 text-[12px] font-bold text-[#263018] transition-transform hover:-translate-y-0.5">
          Join the waitlist
          <svg viewBox="0 0 12 12" className="size-3" fill="none" aria-hidden="true">
            <path d="M2 6h8M6 2l4 4-4 4" stroke="#263018" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </TrackedCta>
      </div>

      {/* Panel 3 — monitoring states. Three cards: stacked on a phone, a row across the
          full width on a tablet, back to a column beside the other panels on a desktop. */}
      <div className="flex flex-col gap-2.5 md:col-span-2 md:grid md:grid-cols-3 md:gap-3 lg:col-span-1 lg:flex lg:flex-col lg:gap-2.5">
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
