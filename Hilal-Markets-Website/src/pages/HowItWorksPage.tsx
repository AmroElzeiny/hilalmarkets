/**
 * How it works.
 *
 * The page this replaces was six numbered cards and four sections of prose, written for
 * somebody who already knew the product: "deterministic rule objects", "canonical
 * strategy hash", "provider-blocked requirements", "policy exclusions separated from
 * technical failures". The audience is beginners. None of those phrases survive.
 *
 * The rebuild is one idea: **a rule has a life, and you are in charge of it at every
 * point.** So the page is a journey you can step through rather than a list you scroll
 * past. Picking a step changes the panel beside it, and the panel shows the thing
 * itself — a screened coin, blocks on a canvas, a result, a live monitor — not a
 * paragraph describing it.
 *
 * Why a stepper and not an animation that plays itself: a person reads at their own
 * speed, and a beginner re-reads. Anything that moves on its own has to be caught.
 * Keyboard arrows move between steps, the panel is a live region, and with reduced
 * motion asked for nothing transitions at all — the content simply changes.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Icon, IconBadge, type IconName } from '../components/Icon'
import { useTilt } from '../components/interactions'
import { Reveal } from '../components/Reveal'
import { BackToTop, dashboardEntryHref, SiteFooter, SiteNav } from '../components/SiteChrome'
import { TrackedCta } from '../components/Tracking'
import { DURATION, move, moveEach, whenSeen } from '../motion'

/* -------------------------------------------------------------------------- */
/*  The five steps                                                             */
/* -------------------------------------------------------------------------- */
type Step = {
  id: string
  icon: IconName
  short: string
  title: string
  line: string
  /** The one thing a person controls at this point. Never more than a sentence. */
  control: string
  visual: 'screen' | 'canvas' | 'check' | 'approve' | 'watch'
}

const STEPS: Step[] = [
  {
    id: 'choose',
    icon: 'market',
    short: 'Choose',
    title: 'Start from screened assets',
    line: 'Every coin in the list already carries its Shariah status, the methodology behind it, and the date it was reviewed.',
    control: 'You pick which assets to work with.',
    visual: 'screen',
  },
  {
    id: 'build',
    icon: 'workflow',
    short: 'Build',
    title: 'Build the rule on a canvas',
    line: 'Drag the conditions together and watch the rule take shape. You can also describe it in plain words, or start from a template.',
    control: 'You decide every condition.',
    visual: 'canvas',
  },
  {
    id: 'check',
    icon: 'scan',
    short: 'Check',
    title: 'Test it once, before anything is watched',
    line: 'Run the rule against the market a single time. You see what matches now, what is close, and what is ruled out.',
    control: 'You see the result before you commit.',
    visual: 'check',
  },
  {
    id: 'approve',
    icon: 'shield_check',
    short: 'Approve',
    title: 'Nothing runs until you approve it',
    line: 'The exact rule is shown back to you in plain language. Monitoring only starts when you say yes.',
    control: 'You are the only one who can approve.',
    visual: 'approve',
  },
  {
    id: 'watch',
    icon: 'radar',
    short: 'Watch',
    title: 'It watches, and it shows its work',
    line: 'When conditions start matching you can see which ones did, which are still missing, and why an alert did or did not arrive.',
    control: 'You can pause or change it at any time.',
    visual: 'watch',
  },
]

/* -------------------------------------------------------------------------- */
/*  The panel visuals                                                          */
/* -------------------------------------------------------------------------- */
function Row({
  mark,
  label,
  value,
  tone = 'plain',
}: {
  mark?: IconName
  label: string
  value?: string
  tone?: 'plain' | 'good' | 'wait' | 'off'
}) {
  return (
    <div className="hm-vrow" data-tone={tone}>
      {mark && <Icon name={mark} className="size-[15px] shrink-0" />}
      <span className="hm-vrow-label">{label}</span>
      {value && <span className="hm-vrow-value tnum">{value}</span>}
    </div>
  )
}

function StepVisual({ visual }: { visual: Step['visual'] }) {
  if (visual === 'screen') {
    return (
      <div className="hm-vstack">
        <Row mark="check" label="BTC/USDT" value="Halal" tone="good" />
        <Row mark="check" label="LTC/USDT" value="Halal" tone="good" />
        <Row mark="clock" label="ETH/USDT" value="Under review" tone="wait" />
        <p className="hm-vnote">
          <Icon name="passport" className="size-[15px] shrink-0 text-[#55712a]" />
          Open any coin to read the sources and the review date.
        </p>
      </div>
    )
  }
  if (visual === 'canvas') {
    return (
      <div className="hm-vstack">
        <div className="hm-vcanvas">
          <span className="hm-vcanvas-dots" aria-hidden="true" />
          <div className="hm-vblock" data-end="true">
            <Icon name="market" className="size-[15px]" />
            BTC/USDT
          </div>
          <span className="hm-vwire" aria-hidden="true" />
          <div className="hm-vblock">
            <Icon name="chart" className="size-[15px]" />
            Dips below yesterday’s low
          </div>
          <span className="hm-vwire" aria-hidden="true" />
          <div className="hm-vblock">
            <Icon name="refresh" className="size-[15px]" />
            Then recovers
            <span className="hm-vpill tnum">5%</span>
          </div>
        </div>
        <p className="hm-vnote">
          <Icon name="edit" className="size-[15px] shrink-0 text-[#55712a]" />
          Change any block and the rule updates with it.
        </p>
      </div>
    )
  }
  if (visual === 'check') {
    return (
      <div className="hm-vstack">
        <Row mark="check" label="2 coins match right now" tone="good" />
        <Row mark="clock" label="1 is close — 2.3% of 5%" tone="wait" />
        <Row mark="close" label="4 do not match" tone="off" />
        <p className="hm-vnote">
          <Icon name="info" className="size-[15px] shrink-0 text-[#55712a]" />
          Nothing is being monitored yet. This is a single check.
        </p>
      </div>
    )
  }
  if (visual === 'approve') {
    return (
      <div className="hm-vstack">
        <div className="hm-vsummary">
          <p>Watch <strong>BTC/USDT</strong>.</p>
          <p>Alert me when it dips below yesterday’s low, then recovers <strong>5%</strong>.</p>
        </div>
        <div className="hm-vapprove">
          <span className="hm-vapprove-btn">
            <Icon name="check" className="size-4" />
            Approve and start
          </span>
          <span className="hm-vapprove-note">You can stop it at any time.</span>
        </div>
      </div>
    )
  }
  return (
    <div className="hm-vstack">
      <Row mark="check" label="Dipped below yesterday’s low" value="Matched" tone="good" />
      <Row mark="clock" label="Recovering — 2.3% of 5%" value="Forming" tone="wait" />
      <div className="hm-vbar" role="img" aria-label="Recovery is 46 percent of the way to 5 percent">
        <span style={{ width: '46%' }} />
      </div>
      <p className="hm-vnote">
        <Icon name="bell" className="size-[15px] shrink-0 text-[#55712a]" />
        You get told the moment the last condition matches.
      </p>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  The journey                                                                */
/* -------------------------------------------------------------------------- */
function Journey() {
  const [index, setIndex] = useState(0)
  const panelRef = useRef<HTMLDivElement>(null)
  const step = STEPS[index]

  // The panel fades its content when the step changes, so the eye is told something
  // was replaced. Without it the text simply becomes different text and the change is
  // easy to miss. `move` puts it straight into the end state under reduced motion.
  useEffect(() => {
    void move(
      panelRef.current,
      { opacity: [0, 1], transform: ['translateY(8px)', 'translateY(0)'] },
      { duration: DURATION.base },
    )
  }, [index])

  // Left and right arrows move between steps, which is what a tablist is expected to
  // do. Without this the only way through is a pointer.
  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
    event.preventDefault()
    setIndex((current) => {
      const next =
        event.key === 'ArrowRight'
          ? (current + 1) % STEPS.length
          : (current - 1 + STEPS.length) % STEPS.length
      // The tab that gains selection has to gain focus too, or the keyboard is left
      // pointing at a tab that is no longer the selected one.
      window.setTimeout(() => {
        document.getElementById(`step-tab-${STEPS[next].id}`)?.focus()
      }, 0)
      return next
    })
  }, [])

  return (
    <div className="hm-journey">
      <div
        className="hm-journey-tabs"
        role="tablist"
        aria-label="The five steps"
        onKeyDown={onKeyDown}
      >
        {STEPS.map((item, position) => {
          const selected = position === index
          return (
            <button
              key={item.id}
              id={`step-tab-${item.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls="step-panel"
              tabIndex={selected ? 0 : -1}
              className="hm-journey-tab"
              data-selected={selected}
              onClick={() => setIndex(position)}
            >
              <span className="hm-journey-tab-index tnum">{position + 1}</span>
              <span className="hm-journey-tab-mark">
                <Icon name={item.icon} className="size-[17px]" />
              </span>
              <span className="hm-journey-tab-label">{item.short}</span>
            </button>
          )
        })}
      </div>

      <div
        id="step-panel"
        role="tabpanel"
        aria-labelledby={`step-tab-${step.id}`}
        className="hm-journey-panel"
      >
        <div ref={panelRef} className="hm-journey-panel-inner">
          <div className="hm-journey-copy">
            <p className="hm-journey-count tnum">
              Step {index + 1} of {STEPS.length}
            </p>
            <h3>{step.title}</h3>
            <p className="hm-journey-line">{step.line}</p>
            <p className="hm-journey-control">
              <Icon name="hand" className="size-[17px] shrink-0" />
              {step.control}
            </p>
          </div>
          <div className="hm-journey-visual">
            <StepVisual visual={step.visual} />
          </div>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Boundaries                                                                 */
/* -------------------------------------------------------------------------- */
const NEVER: Array<{ icon: IconName; label: string }> = [
  { icon: 'close', label: 'Place a trade for you' },
  { icon: 'close', label: 'Hold your money' },
  { icon: 'close', label: 'Ask for exchange trading keys' },
  { icon: 'close', label: 'Tell you what to buy or sell' },
  { icon: 'close', label: 'Promise a return' },
  { icon: 'close', label: 'Change an approved rule on its own' },
]

function Boundaries() {
  const listRef = useRef<HTMLUListElement>(null)
  useEffect(
    () =>
      whenSeen(listRef.current, () => {
        void moveEach(
          Array.from(listRef.current?.querySelectorAll<HTMLElement>('li') ?? []),
          { opacity: [0, 1], transform: ['translateY(10px)', 'translateY(0)'] },
          { duration: DURATION.base, stagger: 0.06 },
        )
      }),
    [],
  )
  return (
    <section className="hm-boundaries">
      <div className="hm-shell">
        <Reveal>
          <div className="hm-boundaries-head">
            <p className="hm-eyebrow">
              <Icon name="shield" className="size-4" />
              Where the line is
            </p>
            <h2>What Hilal Markets will never do</h2>
          </div>
        </Reveal>
        <ul ref={listRef} className="hm-boundaries-list">
          {NEVER.map((item) => (
            <li key={item.label}>
              <span className="hm-boundaries-mark">
                <Icon name={item.icon} className="size-4" strokeWidth={2.2} />
              </span>
              {item.label}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */
export default function HowItWorksPage() {
  const entry = dashboardEntryHref()
  const heroTilt = useTilt<HTMLDivElement>()

  return (
    <div className="hm-page min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main id="top" tabIndex={-1} className="outline-none">
        {/* ---- Opening ---------------------------------------------------- */}
        <section className="hm-hero">
          <div className="hm-aura hm-aura--apple left-1/2 top-[-60px] h-[460px] w-[820px] -translate-x-1/2" aria-hidden="true" />
          <div className="hm-shell">
            <div className="hm-hero-grid">
              <Reveal>
                <p className="hm-eyebrow">
                  <Icon name="workflow" className="size-4" />
                  How it works
                </p>
                <h1 className="hm-h1">
                  From an idea to a rule that watches the market for you.
                </h1>
                <p className="hm-lede">
                  Five steps. You are in charge at every one of them, and nothing starts
                  watching until you say so.
                </p>
                <div className="hm-hero-actions">
                  <TrackedCta
                    href={entry}
                    analyticsName="open_dashboard"
                    analyticsLocation="how_it_works_hero"
                    className="hm-btn hm-btn--primary"
                  >
                    Start free
                    <Icon name="arrow" className="size-4" />
                  </TrackedCta>
                  <TrackedCta
                    href="/features"
                    analyticsName="features"
                    analyticsLocation="how_it_works_hero"
                    className="hm-btn hm-btn--quiet"
                  >
                    See every feature
                  </TrackedCta>
                </div>
              </Reveal>

              <Reveal delay={120}>
                <div
                  ref={heroTilt.ref}
                  onPointerMove={heroTilt.onPointerMove}
                  onPointerLeave={heroTilt.onPointerLeave}
                  className="hm-tilt hm-card hm-hero-card"
                >
                  <div className="hm-hero-card-head">
                    <IconBadge name="radar" tone="apple" size="sm" />
                    <div>
                      <p className="hm-hero-card-title">One rule, watched</p>
                      <p className="hm-hero-card-note tnum">BTC/USDT · checked every minute</p>
                    </div>
                  </div>
                  <div className="hm-vstack">
                    <Row mark="check" label="Dipped below yesterday’s low" value="Matched" tone="good" />
                    <Row mark="clock" label="Recovering" value="2.3% of 5%" tone="wait" />
                    <div className="hm-vbar" role="img" aria-label="Recovery is 46 percent of the way to 5 percent">
                      <span style={{ width: '46%' }} />
                    </div>
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ---- The journey ------------------------------------------------ */}
        <section className="hm-section">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-section-head">
                <h2>Step through it</h2>
                <p>Pick any step to see what it looks like.</p>
              </div>
            </Reveal>
            <Reveal delay={80}>
              <Journey />
            </Reveal>
          </div>
        </section>

        {/* ---- What stays yours ------------------------------------------- */}
        <section className="hm-section hm-section--tint">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-section-head">
                <h2>Three things stay yours</h2>
              </div>
            </Reveal>
            <div className="hm-trio">
              {[
                {
                  icon: 'edit' as IconName,
                  title: 'The rules',
                  note: 'You write them, you change them, and every change makes a new version you approve.',
                },
                {
                  icon: 'passport' as IconName,
                  title: 'The evidence',
                  note: 'Every status shows its methodology, its sources and the date it was reviewed.',
                },
                {
                  icon: 'lock' as IconName,
                  title: 'The privacy',
                  note: 'Your strategies and watchlists are yours. They are never published or shared.',
                },
              ].map((card, position) => (
                <Reveal key={card.title} delay={position * 70}>
                  <article className="hm-card hm-lift hm-trio-card">
                    <IconBadge name={card.icon} tone="apple" />
                    <h3>{card.title}</h3>
                    <p>{card.note}</p>
                  </article>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <Boundaries />

        {/* ---- Closing ---------------------------------------------------- */}
        <section className="hm-section">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-closer">
                <h2>Build your first rule today</h2>
                <p>The free plan includes the canvas, the screened list and one live monitor.</p>
                <div className="hm-hero-actions hm-hero-actions--center">
                  <TrackedCta
                    href={entry}
                    analyticsName="open_dashboard"
                    analyticsLocation="how_it_works_closer"
                    className="hm-btn hm-btn--primary"
                  >
                    Start free
                    <Icon name="arrow" className="size-4" />
                  </TrackedCta>
                  <TrackedCta
                    href="/#pricing"
                    analyticsName="pricing"
                    analyticsLocation="how_it_works_closer"
                    className="hm-btn hm-btn--quiet"
                  >
                    Compare plans
                  </TrackedCta>
                </div>
              </div>
            </Reveal>
          </div>
        </section>
      </main>

      <BackToTop />

      <SiteFooter />
    </div>
  )
}
