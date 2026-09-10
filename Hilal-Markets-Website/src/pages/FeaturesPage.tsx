/**
 * Features.
 *
 * The page this replaces listed fifteen cards in five sections, all the same shape, all
 * the same weight, written in the product's internal vocabulary — "policy exclusions
 * separated from technical failures", "one approved compiler", "provider-backed
 * evaluation". A beginner could not tell which of the fifteen mattered, and would not
 * have understood the words on any of them.
 *
 * Three things changed.
 *
 * **The list is grouped by what you are trying to do**, not by which part of the system
 * provides it — Find, Build, Watch, Understand. That is the order a person meets them
 * in, and it is the order they are useful in.
 *
 * **You can filter it.** Fifteen items is more than anybody reads. Picking a group
 * narrows the grid to that group, and the count is announced, so somebody who only
 * wants to know what happens after an alert can get there in one click.
 *
 * **Each card shows the thing.** A name, then a small piece of the real interface — a
 * status row, a rule block, a progress bar. It carried a sentence of description between
 * the two; the picture already says it, and the sentence turned a grid you can scan into
 * a grid you have to read.
 *
 * What is not built yet is in its own group, marked as such, and never mixed into the
 * others. Listing a plan beside a working feature is how a features page becomes a lie.
 */
import { useMemo, useRef, useState } from 'react'
import { Icon, IconBadge, type IconName } from '../components/Icon'
import { useTilt } from '../components/interactions'
import { Reveal } from '../components/Reveal'
import { BackToTop, dashboardEntryHref, SiteFooter, SiteNav } from '../components/SiteChrome'
import { TrackedCta } from '../components/Tracking'
import { DURATION, moveEach } from '../motion'

/* -------------------------------------------------------------------------- */
/*  The features                                                               */
/* -------------------------------------------------------------------------- */
type Group = 'find' | 'build' | 'watch' | 'understand' | 'next'

type Feature = {
  id: string
  group: Group
  icon: IconName
  title: string
  visual: React.ReactNode
}

const GROUPS: Array<{ id: Group | 'all'; label: string; icon: IconName }> = [
  { id: 'all', label: 'Everything', icon: 'layers' },
  { id: 'find', label: 'Find', icon: 'market' },
  { id: 'build', label: 'Build', icon: 'workflow' },
  { id: 'watch', label: 'Watch', icon: 'radar' },
  { id: 'understand', label: 'Understand', icon: 'eye' },
  { id: 'next', label: 'Coming next', icon: 'spark' },
]

function VRow({
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

function Block({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <div className="hm-vblock">
      <Icon name={icon} className="size-[15px]" />
      {children}
    </div>
  )
}

const FEATURES: Feature[] = [
  {
    id: 'listings',
    group: 'find',
    icon: 'market',
    title: 'Halal listings',
    visual: (
      <div className="hm-vstack">
        <VRow mark="check" label="BTC/USDT" value="Halal" tone="good" />
        <VRow mark="check" label="LTC/USDT" value="Halal" tone="good" />
        <VRow mark="clock" label="ETH/USDT" value="Under review" tone="wait" />
      </div>
    ),
  },
  {
    id: 'passport',
    group: 'find',
    icon: 'passport',
    title: 'Evidence Passport',
    visual: (
      <div className="hm-vstack">
        <VRow mark="file_text" label="Methodology" value="AAOIFI-based" />
        <VRow mark="link" label="Source" value="SC Malaysia SAC" />
        <VRow mark="calendar" label="Reviewed" value="20 Jul 2026" />
      </div>
    ),
  },
  {
    id: 'watchlist',
    group: 'find',
    icon: 'star',
    title: 'Your own shortlist',
    visual: (
      <div className="hm-vstack">
        <VRow mark="star" label="4 coins on your list" tone="good" />
        <VRow mark="bell" label="Told when a status changes" />
      </div>
    ),
  },
  {
    id: 'canvas',
    group: 'build',
    icon: 'workflow',
    title: 'Visual canvas',
    visual: (
      <div className="hm-vcanvas">
        <span className="hm-vcanvas-dots" aria-hidden="true" />
        <Block icon="chart">Dips below yesterday’s low</Block>
        <span className="hm-vwire" aria-hidden="true" />
        <Block icon="refresh">
          Then recovers
          <span className="hm-vpill tnum">5%</span>
        </Block>
      </div>
    ),
  },
  {
    id: 'monitors',
    group: 'watch',
    icon: 'radar',
    title: 'Continuous monitoring',
    visual: (
      <div className="hm-vstack">
        <VRow mark="check" label="Dipped below the low" value="Matched" tone="good" />
        <VRow mark="clock" label="Recovering" value="2.3% of 5%" tone="wait" />
        <div className="hm-vbar" role="img" aria-label="Recovery is 46 percent of the way to 5 percent">
          <span style={{ width: '46%' }} />
        </div>
      </div>
    ),
  },
  {
    id: 'alerts',
    group: 'watch',
    icon: 'bell',
    title: 'Alerts where you already are',
    visual: (
      <div className="hm-vstack">
        <VRow mark="telegram" label="Telegram" value="Ready" tone="good" />
        <VRow mark="bell" label="In the app" value="Ready" tone="good" />
        <VRow mark="clock" label="WhatsApp" value="Coming" tone="wait" />
      </div>
    ),
  },
  {
    id: 'status-change',
    group: 'watch',
    icon: 'compliance',
    title: 'If a status changes',
    visual: (
      <div className="hm-vstack">
        <VRow mark="warning" label="ETH/USDT moved to under review" tone="wait" />
        <VRow mark="hand" label="Your choice: pause the monitor" />
      </div>
    ),
  },
  {
    id: 'journey',
    group: 'understand',
    icon: 'history',
    title: 'The whole story of a setup',
    visual: (
      <div className="hm-vstack">
        <VRow mark="clock" label="Forming" value="09:14" tone="wait" />
        <VRow mark="check" label="Confirmed" value="11:02" tone="good" />
        <VRow mark="send" label="Sent to you" value="11:02" tone="good" />
      </div>
    ),
  },
  {
    id: 'proof',
    group: 'understand',
    icon: 'eye',
    title: 'The numbers behind an alert',
    visual: (
      <div className="hm-vstack">
        <VRow mark="chart" label="Recovery" value="5.4% ≥ 5%" tone="good" />
        <VRow mark="clock" label="Measured" value="11:02:14" />
      </div>
    ),
  },
  {
    id: 'why-not',
    group: 'understand',
    icon: 'search',
    title: 'Why was I not alerted?',
    visual: (
      <div className="hm-vstack">
        <VRow mark="close" label="Recovery reached 4.1%, needed 5%" tone="off" />
        <VRow mark="info" label="So no alert was sent" />
      </div>
    ),
  },
  {
    id: 'agent',
    group: 'next',
    icon: 'spark',
    title: 'Hilal agent on the canvas',
    visual: (
      <div className="hm-vstack">
        <div className="hm-vquote">“Your rule has no time limit. Add one?”</div>
      </div>
    ),
  },
  {
    id: 'research',
    group: 'next',
    icon: 'search',
    title: 'Deeper asset research',
    visual: (
      <div className="hm-vstack">
        <VRow mark="search" label="Ask about any listed coin" />
        <VRow mark="link" label="Answer carries its sources" />
      </div>
    ),
  },
  {
    id: 'markets',
    group: 'next',
    icon: 'globe',
    title: 'More markets',
    visual: (
      <div className="hm-vstack">
        <VRow mark="check" label="Crypto spot" value="Live" tone="good" />
        <VRow mark="clock" label="Other classes" value="Planned" tone="wait" />
      </div>
    ),
  },
]

/* -------------------------------------------------------------------------- */
function FeatureCard({ feature }: { feature: Feature }) {
  const tilt = useTilt<HTMLElement>()
  const planned = feature.group === 'next'
  return (
    <article
      ref={tilt.ref}
      onPointerMove={tilt.onPointerMove}
      onPointerLeave={tilt.onPointerLeave}
      className="hm-tilt hm-card hm-feature-card"
      data-planned={planned}
    >
      <div className="hm-feature-head">
        <IconBadge name={feature.icon} tone={planned ? 'neutral' : 'apple'} size="sm" />
        <h3>{feature.title}</h3>
        {planned && <span className="hm-chip-next">In development</span>}
      </div>
      <div className="hm-feature-visual">{feature.visual}</div>
    </article>
  )
}

/* -------------------------------------------------------------------------- */
export default function FeaturesPage() {
  const entry = dashboardEntryHref()
  const [group, setGroup] = useState<Group | 'all'>('all')
  const gridRef = useRef<HTMLDivElement>(null)

  const shown = useMemo(
    () => (group === 'all' ? FEATURES : FEATURES.filter((item) => item.group === group)),
    [group],
  )

  function choose(next: Group | 'all') {
    setGroup(next)
    // The grid re-enters after a filter so the change is visible. Without it the cards
    // are simply different cards and somebody can miss that anything happened.
    window.setTimeout(() => {
      void moveEach(
        Array.from(gridRef.current?.querySelectorAll<HTMLElement>('article') ?? []),
        { opacity: [0, 1], transform: ['translateY(10px)', 'translateY(0)'] },
        { duration: DURATION.base, stagger: 0.04 },
      )
    }, 0)
  }

  return (
    <div className="hm-page min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main id="top" tabIndex={-1} className="outline-none">
        {/* ---- Opening ---------------------------------------------------- */}
        <section className="hm-hero">
          <div className="hm-aura hm-aura--apple left-1/2 top-[-60px] h-[460px] w-[820px] -translate-x-1/2" aria-hidden="true" />
          <div className="hm-shell">
            <Reveal>
              <div className="hm-hero-centre">
                <p className="hm-eyebrow">
                  <Icon name="layers" className="size-4" />
                  Features
                </p>
                <h1 className="hm-h1">Everything you need to watch the market properly.</h1>
                <p className="hm-lede">
                  Screened assets, a canvas to build your rules on, and monitoring that
                  shows its work.
                </p>
                <div className="hm-hero-actions hm-hero-actions--center">
                  <TrackedCta
                    href={entry}
                    analyticsName="open_dashboard"
                    analyticsLocation="features_hero"
                    className="hm-btn hm-btn--primary"
                  >
                    Start free
                    <Icon name="arrow" className="size-4" />
                  </TrackedCta>
                  <TrackedCta
                    href="/how-it-works"
                    analyticsName="how_it_works"
                    analyticsLocation="features_hero"
                    className="hm-btn hm-btn--quiet"
                  >
                    See how it works
                  </TrackedCta>
                </div>
              </div>
            </Reveal>
          </div>
        </section>

        {/* ---- The grid --------------------------------------------------- */}
        <section className="hm-section hm-section--tight">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-filters" role="group" aria-label="Show features by what you want to do">
                {GROUPS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="hm-filter"
                    data-selected={group === item.id}
                    aria-pressed={group === item.id}
                    onClick={() => choose(item.id)}
                  >
                    <Icon name={item.icon} className="size-4" />
                    {item.label}
                  </button>
                ))}
              </div>
            </Reveal>

            <p className="sr-only" role="status" aria-live="polite">
              Showing {shown.length} {shown.length === 1 ? 'feature' : 'features'}.
            </p>

            <div ref={gridRef} className="hm-feature-grid">
              {shown.map((feature, position) => (
                <Reveal key={feature.id} delay={Math.min(position * 45, 220)}>
                  <FeatureCard feature={feature} />
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ---- The promise ------------------------------------------------ */}
        <section className="hm-section hm-section--tint">
          <div className="hm-shell">
            <div className="hm-trio">
              {[
                {
                  icon: 'shield_check' as IconName,
                  title: 'Spot only, no leverage',
                  note: 'No margin, no futures, no borrowing.',
                },
                {
                  icon: 'hand' as IconName,
                  title: 'You approve everything',
                  note: 'No rule ever runs that you have not read and agreed to.',
                },
                {
                  icon: 'scale' as IconName,
                  title: 'Screening is governed',
                  note: 'Status comes from a published methodology and qualified review — never from a chatbot.',
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

        {/* ---- Closing ---------------------------------------------------- */}
        <section className="hm-section">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-closer">
                <h2>Try it on your own idea</h2>
                <p>The free plan includes the canvas, the screened list and one live monitor.</p>
                <div className="hm-hero-actions hm-hero-actions--center">
                  <TrackedCta
                    href={entry}
                    analyticsName="open_dashboard"
                    analyticsLocation="features_closer"
                    className="hm-btn hm-btn--primary"
                  >
                    Start free
                    <Icon name="arrow" className="size-4" />
                  </TrackedCta>
                  <TrackedCta
                    href="/#pricing"
                    analyticsName="pricing"
                    analyticsLocation="features_closer"
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
