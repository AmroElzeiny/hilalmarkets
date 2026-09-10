/**
 * The Hilal Markets Methodology, in public and in full.
 *
 * This page exists because the product now publishes a standard that **no scholar has
 * reviewed**. Every surface that shows one of its results carries a short warning and a
 * link to here; this is the long version those warnings point at, and it has to be
 * readable by somebody who has never heard the word "methodology".
 *
 * Three decisions shape it.
 *
 * **The warning is the hero, not a footnote.** A page that opened with the impressive
 * number — eighty conditions, a hundred and forty-eight proofs — and put "still under
 * development, no Shariah advisor" at the bottom would be an advertisement wearing a
 * disclosure. The first thing on the page is what it is not.
 *
 * **Nothing here is written down twice.** Every figure, every condition, every coin
 * comes from `window.HilalMarketsRuntimeConfig.methodology`, which the server builds
 * from the same register the screen applies. A page that hard-coded "68 approved" would
 * be correct on the day it shipped and quietly wrong on the day the owner approved the
 * sixty-ninth — and the website is the version a reader would believe.
 *
 * **Three illustrations, and only three.** The crawl, the two doors, and the arc of what
 * is applied against what is skipped. Those are the three things a person gets wrong
 * about this standard. Everything else is type, space and the product's own icons, per
 * the brand rule that an illustration explains a flow rather than decorating a section.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { MethodologyRuntimeConfig } from '../analytics'
import { Icon, IconBadge, type IconName } from '../components/Icon'
import { Reveal } from '../components/Reveal'
import { BackToTop, dashboardEntryHref, SiteFooter, SiteNav } from '../components/SiteChrome'
import { TrackedCta } from '../components/Tracking'
import { countTo, whenSeen } from '../motion'

/* -------------------------------------------------------------------------- */
/*  What the server hands this page                                            */
/* -------------------------------------------------------------------------- */
/* The shape is declared once, beside the rest of the runtime config, so this page
   and the `Window` interface can never describe it differently. */
type Coin = MethodologyRuntimeConfig['coins'][number]
type Condition = MethodologyRuntimeConfig['families'][number]['conditions'][number]

/**
 * The payload, or nothing.
 *
 * A page that threw here would render the whole React site blank — every page, not just
 * this one — because one throwing module at the top level takes the bundle down. So a
 * missing payload becomes an honest empty state instead.
 */
function useMethodology(): MethodologyRuntimeConfig | null {
  return useMemo(() => window.HilalMarketsRuntimeConfig?.methodology ?? null, [])
}

/* -------------------------------------------------------------------------- */
/*  Names a beginner can read                                                  */
/* -------------------------------------------------------------------------- */
/** Each chapter of the rules, in English, with the mark that stands for it. */
const FAMILY_LABEL: Record<string, { title: string; line: string; icon: IconName }> = {
  riba: {
    title: 'Interest',
    line: 'Money earned for lending money, rather than for doing work.',
    icon: 'billing',
  },
  maysir: {
    title: 'Gambling',
    line: 'A prize decided by chance, where one side loses what the other wins.',
    icon: 'spark',
  },
  gharar: {
    title: 'Deep uncertainty',
    line: 'Selling something nobody can describe, or does not yet own.',
    icon: 'eye',
  },
  prohibited: {
    title: 'Forbidden trades',
    line: 'Businesses built on things Islam forbids outright.',
    icon: 'close',
  },
  deceit: { title: 'Deception', line: 'Making a market look like something it is not.', icon: 'warning' },
  wrongful_gain: {
    title: 'Taking without right',
    line: "Profit that comes out of somebody else's pocket for nothing.",
    icon: 'hand',
  },
  contract_form: {
    title: 'The shape of the deal',
    line: 'Agreements Islam refuses because of how they are put together.',
    icon: 'file_text',
  },
  exchange: {
    title: 'Money for money',
    line: 'Swapping one currency for another without both sides handing over at once.',
    icon: 'refresh',
  },
  ratio: {
    title: 'The numbers',
    line: 'How much of a business runs on debt, or earns from something forbidden.',
    icon: 'chart',
  },
  harm: { title: 'Harm', line: 'Businesses that work by hurting their own users.', icon: 'shield' },
}

const AGREEMENT_LABEL: Record<Condition['agreement'], string> = {
  unanimous: 'Agreed by all',
  majority: 'Held by most',
  disputed: 'Scholars differ',
}

const EVIDENCE_LABEL: Record<string, string> = {
  quran: "Qur'an",
  sunnah: 'Hadith',
  ijma: 'Consensus',
  qaida: 'Legal maxim',
  standard: 'Standard',
}

const OUTCOME: Record<
  Coin['outcome'],
  { label: string; line: string; icon: IconName; tone: string }
> = {
  admitted: {
    label: 'Looks clean',
    line: 'Nothing in the approved conditions refused it.',
    icon: 'check',
    tone: 'hm-m-tone--good',
  },
  refused: {
    label: 'Has a problem',
    line: "The project's own words describe a business a condition refuses.",
    icon: 'close',
    tone: 'hm-m-tone--bad',
  },
  not_enough_data: {
    label: 'Not judged',
    line: 'Too little of what the project writes about itself could be read.',
    icon: 'clock',
    tone: 'hm-m-tone--wait',
  },
}

const SECTIONS = [
  { id: 'what', label: 'What it is' },
  { id: 'how', label: 'How it reads' },
  { id: 'rules', label: 'The conditions' },
  { id: 'skipped', label: 'What it skips' },
  { id: 'answers', label: 'The three answers' },
  { id: 'coins', label: 'Coins judged' },
  { id: 'others', label: 'Other standards' },
  { id: 'limits', label: 'What it never does' },
]

/* -------------------------------------------------------------------------- */
/*  A number that counts up the first time you see it                          */
/* -------------------------------------------------------------------------- */
function Figure({ value, label, hint }: { value: number; label: string; hint?: string }) {
  const ref = useRef<HTMLSpanElement | null>(null)
  useEffect(() => whenSeen(ref.current, () => void countTo(ref.current, value)), [value])
  return (
    <div className="hm-m-figure">
      {/* The true value is the element's text from the first paint, so a reader with
          motion switched off — and any crawler — sees the real number, never a zero. */}
      <span className="hm-m-figure-value tnum" ref={ref}>
        {value}
      </span>
      <span className="hm-m-figure-label">{label}</span>
      {hint && <span className="hm-m-figure-hint">{hint}</span>}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Illustration 1 — what "up to eighty pages" means                           */
/* -------------------------------------------------------------------------- */
/**
 * A project's own site, being read.
 *
 * The one thing people misunderstand first: this standard does not consult a scholar, a
 * database or a rating. It opens the project's own website and reads it. The beam
 * sweeps, pages tick over, and the counter stops at the budget — which is the boundary,
 * not a speed setting.
 */
function CrawlArt({ budget }: { budget: number }) {
  return (
    <div className="hm-m-art" aria-hidden="true">
      <svg viewBox="0 0 420 260" className="hm-m-art-svg" role="presentation">
        <defs>
          <linearGradient id="hm-m-beam" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#cbfa4d" stopOpacity="0" />
            <stop offset="50%" stopColor="#cbfa4d" stopOpacity="0.85" />
            <stop offset="100%" stopColor="#cbfa4d" stopOpacity="0" />
          </linearGradient>
          <clipPath id="hm-m-clip">
            <rect x="24" y="20" width="372" height="220" rx="18" />
          </clipPath>
        </defs>

        <rect x="24" y="20" width="372" height="220" rx="18" fill="#ffffff" stroke="#e1e5ea" />
        <rect x="24" y="20" width="372" height="34" rx="18" fill="#f5f8fb" />
        <rect x="24" y="46" width="372" height="8" fill="#f5f8fb" />
        <circle cx="44" cy="37" r="4" fill="#e1e5ea" />
        <circle cx="58" cy="37" r="4" fill="#e1e5ea" />
        <circle cx="72" cy="37" r="4" fill="#e1e5ea" />
        <rect x="92" y="31" width="150" height="12" rx="6" fill="#eef1f4" />

        <g clipPath="url(#hm-m-clip)">
          {/* Nine page cards. Each lights in turn: the reading is sequential and a
              reader should feel that it is finite. */}
          {Array.from({ length: 9 }).map((_, index) => {
            const column = index % 3
            const row = Math.floor(index / 3)
            return (
              <g
                key={index}
                className="hm-m-page"
                style={{ '--i': index } as React.CSSProperties}
                transform={`translate(${44 + column * 118} ${70 + row * 56})`}
              >
                <rect width="102" height="42" rx="10" fill="#f7f9fb" stroke="#e4e9ee" />
                <rect className="hm-m-page-bar" x="12" y="12" width="52" height="6" rx="3" />
                <rect x="12" y="24" width="72" height="5" rx="2.5" fill="#e4e9ee" />
                <circle className="hm-m-page-tick" cx="86" cy="15" r="5" />
              </g>
            )
          })}
          <rect className="hm-m-beam" x="24" y="54" width="372" height="46" fill="url(#hm-m-beam)" />
        </g>
      </svg>
      <div className="hm-m-art-caption">
        <Icon name="scan" className="size-4" />
        <span>
          Reads up to <b className="tnum">{budget}</b> pages the project publishes about itself
        </span>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Illustration 2 — the two ways in                                           */
/* -------------------------------------------------------------------------- */
function DoorsArt({
  regulatorCoins,
  machineCoins,
}: {
  regulatorCoins: number
  machineCoins: number
}) {
  return (
    <div className="hm-m-doors">
      <div className="hm-m-door">
        <IconBadge name="scale" tone="ink" />
        <h3>A regulator already said yes</h3>
        <p>
          The Shariah Advisory Council of the Securities Commission of Malaysia publishes a
          list of digital assets it calls Shariah-compliant. Those coins come straight in,
          and this standard is not allowed to refuse them.
        </p>
        <span className="hm-m-door-count tnum">{regulatorCoins} coins</span>
      </div>

      <div className="hm-m-door-link" aria-hidden="true">
        <svg viewBox="0 0 120 40" className="hm-m-door-svg">
          <path className="hm-m-door-path" d="M4 20h50" />
          <path className="hm-m-door-path hm-m-door-path--b" d="M66 20h50" />
          <circle className="hm-m-door-dot" cx="60" cy="20" r="7" />
        </svg>
      </div>

      <div className="hm-m-door">
        <IconBadge name="scan" tone="apple" />
        <h3>Or the machine read the website</h3>
        <p>
          For a coin no authority has ruled on, the screen opens the project's own pages and
          applies every approved condition it can settle from them. If nothing refuses it,
          it comes in — with the reading attached.
        </p>
        <span className="hm-m-door-count tnum">{machineCoins} coins</span>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Illustration 3 — applied against skipped                                   */
/* -------------------------------------------------------------------------- */
/**
 * The share of approved conditions the automatic reading can actually settle.
 *
 * Drawn as an arc rather than said in a sentence because the point is proportional: most
 * of the rules run, a minority cannot, and the minority is not small enough to ignore.
 */
function ReachArt({ applied, skipped }: { applied: number; skipped: number }) {
  const total = Math.max(1, applied + skipped)
  const circumference = 2 * Math.PI * 52
  const filled = (applied / total) * circumference
  return (
    <div className="hm-m-reach">
      <svg viewBox="0 0 140 140" className="hm-m-reach-svg" role="img"
        aria-label={`${applied} of ${total} approved conditions can be settled by reading a website`}>
        <circle cx="70" cy="70" r="52" fill="none" stroke="#e4e9ee" strokeWidth="14" />
        <circle
          className="hm-m-reach-arc"
          cx="70"
          cy="70"
          r="52"
          fill="none"
          stroke="#55712a"
          strokeWidth="14"
          strokeLinecap="round"
          transform="rotate(-90 70 70)"
          style={
            {
              strokeDasharray: `${circumference} ${circumference}`,
              '--arc-offset': `${circumference - filled}`,
            } as React.CSSProperties
          }
        />
      </svg>
      <div className="hm-m-reach-mid">
        <b className="tnum">{applied}</b>
        <span>run</span>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */
export default function MethodologyPage() {
  const data = useMethodology()
  const [family, setFamily] = useState<string>('all')
  const [outcome, setOutcome] = useState<string>('all')
  const [active, setActive] = useState<string>(SECTIONS[0].id)
  const entry = dashboardEntryHref()

  // Which section the reader is in, for the rail. One observer for the page, not one
  // per section, so the rail cannot disagree with itself.
  useEffect(() => {
    if (!data) return
    const nodes = SECTIONS.map((item) => document.getElementById(item.id)).filter(
      (node): node is HTMLElement => node !== null,
    )
    if (nodes.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        const seen = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (seen) setActive(seen.target.id)
      },
      { rootMargin: '-45% 0px -50% 0px', threshold: 0 },
    )
    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [data])

  if (!data) {
    return (
      <div className="hm-page min-h-screen bg-canvas text-ink">
        <SiteNav />
        <main className="hm-shell hm-shell--narrow py-40">
          <h1 className="font-display text-[32px] leading-[1.1] tracking-[-0.03em]">
            The methodology could not be loaded
          </h1>
          <p className="mt-4 text-[16px] leading-[1.6] text-[#4a505a]">
            Please reload the page. Nothing is missing from the product itself — only this
            description of it failed to arrive.
          </p>
        </main>
        <SiteFooter />
      </div>
    )
  }

  const { counts } = data
  const admitted = data.coins.filter((coin) => coin.outcome === 'admitted')
  const regulatorCoins = admitted.filter((coin) => coin.admission === 'regulator_floor')
  const machineCoins = admitted.filter((coin) => coin.admission === 'automated_screen')
  const shownFamilies =
    family === 'all' ? data.families : data.families.filter((item) => item.key === family)
  const shownCoins =
    outcome === 'all' ? data.coins : data.coins.filter((item) => item.outcome === outcome)

  return (
    <div className="hm-page hm-methodology min-h-screen bg-canvas text-ink">
      <SiteNav />

      <main>
        {/* ---- Hero -------------------------------------------------------- */}
        <section id="top" className="hm-m-hero">
          <div className="hm-aura hm-aura--apple hm-m-hero-aura" aria-hidden="true" />
          <div className="hm-shell">
            <div className="hm-m-hero-grid">
              <div>
                <Reveal delay={60}>
                  <p className="hm-eyebrow">
                    <Icon name="methodology" className="size-4" />
                    {data.name} · v{data.version}
                  </p>
                </Reveal>

                <Reveal delay={120}>
                  <h1 className="hm-m-title">
                    Our own screening standard, and{' '}
                    <span className="hm-m-mark">everything it cannot do</span>.
                  </h1>
                </Reveal>

                <Reveal delay={180}>
                  <p className="hm-m-lede">
                    It reads what a project writes about itself and applies{' '}
                    <b className="tnum">{counts.approved}</b> conditions, each carrying the
                    verse, hadith or standard behind it. It is a machine reading a website —
                    not a ruling, and not a person's judgement.
                  </p>
                </Reveal>

                {/* The warning is the first solid block on the page, above the numbers
                    and above the fold. It is the single most important sentence here. */}
                <Reveal delay={240}>
                  <div className="hm-m-warning" role="note">
                    <Icon name="alert" className="size-5 shrink-0" title="Important" />
                    <div>
                      <strong>Still under development. No Shariah advisor stands behind it.</strong>
                      <p>
                        {data.developmentNotice.replace(
                          'This standard is still under development. It is applied by machine and no Shariah advisor stands behind it. ',
                          '',
                        )}{' '}
                        For a result a named authority decided, choose one of the reviewed
                        standards inside the product.
                      </p>
                    </div>
                  </div>
                </Reveal>

                <Reveal delay={300}>
                  <div className="hm-m-hero-actions">
                    <a className="hm-btn hm-btn--primary" href="#rules">
                      Read every condition
                      <Icon name="arrow" className="size-4" />
                    </a>
                    <TrackedCta
                      href={entry}
                      analyticsName="open_dashboard"
                      analyticsLocation="methodology_hero"
                      className="hm-btn hm-btn--quiet"
                    >
                      Open the product
                    </TrackedCta>
                  </div>
                </Reveal>
              </div>

              <Reveal delay={220}>
                <CrawlArt budget={data.pageBudget} />
              </Reveal>
            </div>

            <Reveal delay={340}>
              <div className="hm-m-figures">
                <Figure value={counts.total} label="conditions written" />
                <Figure value={counts.approved} label="approved and live" hint="the rest change nothing" />
                <Figure value={counts.applied} label="a website can settle" hint={`${counts.skipped} cannot`} />
                <Figure value={counts.evidence} label="proofs cited" hint="Qur'an, hadith, standards" />
              </div>
            </Reveal>
          </div>
        </section>

        {/* ---- Rail + sections --------------------------------------------- */}
        <div className="hm-shell hm-m-body">
          <nav className="hm-m-rail" aria-label="Sections of this page">
            <ol>
              {SECTIONS.map((item) => (
                <li key={item.id}>
                  <a
                    href={`#${item.id}`}
                    aria-current={active === item.id ? 'true' : undefined}
                    className={active === item.id ? 'is-active' : ''}
                  >
                    <span aria-hidden="true" />
                    {item.label}
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          <div className="hm-m-content">
            {/* ---- What it is --------------------------------------------- */}
            <section id="what" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="info" className="size-4" />
                  What it is
                </p>
                <h2 className="hm-m-h2">A rule you can argue with, applied the same way every time.</h2>
                <p className="hm-m-p">
                  Most screening you will meet is a label with nothing behind it. This one is
                  the opposite: every condition is written down, every condition carries its
                  proof, and the owner has to sign a separate file before any of them is
                  allowed to refuse a single coin. Writing a rule and switching it on are two
                  different acts, on purpose.
                </p>
              </Reveal>

              <Reveal delay={80}>
                <div className="hm-m-cards">
                  {[
                    {
                      icon: 'book' as IconName,
                      title: 'Written with its proof',
                      line: `Each of the ${counts.total} conditions names the verse, the hadith, the point of consensus or the published standard it rests on. ${counts.evidence} citations in total.`,
                    },
                    {
                      icon: 'shield_check' as IconName,
                      title: 'Switched on one by one',
                      line: `${counts.approved} are approved and live. The other ${counts.proposed} are read on every coin so their effect can be seen — and they change nothing at all until they are signed.`,
                    },
                    {
                      icon: 'history' as IconName,
                      title: 'Recorded, so it can be checked',
                      line: 'Who decided, on what date, and against which version of the rules. A result you cannot re-open is an assertion, not evidence.',
                    },
                  ].map((card) => (
                    <article key={card.title} className="hm-card hm-lift hm-m-card">
                      <IconBadge name={card.icon} tone="neutral" />
                      <h3>{card.title}</h3>
                      <p>{card.line}</p>
                    </article>
                  ))}
                </div>
              </Reveal>
            </section>

            {/* ---- How it reads ------------------------------------------- */}
            <section id="how" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="scan" className="size-4" />
                  How it reads
                </p>
                <h2 className="hm-m-h2">Two ways a coin gets in, and they are never blurred.</h2>
              </Reveal>

              <Reveal delay={80}>
                <DoorsArt
                  regulatorCoins={regulatorCoins.length}
                  machineCoins={machineCoins.length}
                />
              </Reveal>

              <Reveal delay={120}>
                <ol className="hm-m-steps">
                  {[
                    {
                      icon: 'link' as IconName,
                      title: 'Find where the project publishes',
                      line: 'Its website, its documentation, its whitepaper and its code, taken from a market data provider rather than from a search box.',
                    },
                    {
                      icon: 'file_text' as IconName,
                      title: `Read up to ${data.pageBudget} of its own pages`,
                      line: "Only pages the project wrote about itself. A newsroom writing about the whole market is not the project describing its own business.",
                    },
                    {
                      icon: 'scale' as IconName,
                      title: 'Apply every approved condition',
                      line: 'A condition only counts when the project says it about itself, on enough of its own pages that it is a description rather than a passing mention.',
                    },
                    {
                      icon: 'flag' as IconName,
                      title: 'Answer, and show the sentence',
                      line: 'Every reason links to the page and the exact line it came from, so you can disagree with it.',
                    },
                  ].map((step, index) => (
                    <li key={step.title} className="hm-m-step">
                      <span className="hm-m-step-n tnum" aria-hidden="true">
                        {index + 1}
                      </span>
                      <div>
                        <h3>
                          <Icon name={step.icon} className="size-[17px]" />
                          {step.title}
                        </h3>
                        <p>{step.line}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </Reveal>
            </section>

            {/* ---- The conditions ----------------------------------------- */}
            <section id="rules" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="list" className="size-4" />
                  The conditions
                </p>
                <h2 className="hm-m-h2">
                  All {counts.applied} rules the reading applies, grouped by what they forbid.
                </h2>
                <p className="hm-m-p">
                  These are the ones a website can settle. Open a chapter to read its rules,
                  or pick one above to see it on its own. Each rule shows what it refuses
                  and the proof it stands on.
                </p>
              </Reveal>

              <Reveal delay={60}>
                <div className="hm-m-filters" role="group" aria-label="Filter the conditions">
                  <button
                    type="button"
                    className={`hm-m-chip ${family === 'all' ? 'is-on' : ''}`}
                    aria-pressed={family === 'all'}
                    onClick={() => setFamily('all')}
                  >
                    <Icon name="layers" className="size-4" />
                    Everything
                    <span className="tnum">{counts.applied}</span>
                  </button>
                  {data.families.map((item) => {
                    const label = FAMILY_LABEL[item.key]
                    return (
                      <button
                        key={item.key}
                        type="button"
                        className={`hm-m-chip ${family === item.key ? 'is-on' : ''}`}
                        aria-pressed={family === item.key}
                        onClick={() => setFamily(item.key)}
                      >
                        <Icon name={label?.icon ?? 'scale'} className="size-4" />
                        {label?.title ?? item.key}
                        <span className="tnum">{item.count}</span>
                      </button>
                    )
                  })}
                </div>
              </Reveal>

              {/* Each chapter is a real `<details>`, not a div with a click handler.
                  Fifty-six rules laid out flat made this section two thirds of the page
                  and unreadable; collapsed, a person sees the ten chapters at once and
                  opens the one they came for. Native, so it is keyboard-operable, and
                  every rule stays in the document for search and for print.

                  Open when the reader has narrowed to a single chapter, and the first
                  one is open by default so the section is never a row of closed boxes
                  with nothing to show what is inside them. */}
              <div className="hm-m-families">
                {shownFamilies.map((block, index) => {
                  const label = FAMILY_LABEL[block.key]
                  return (
                    <Reveal key={block.key} delay={40}>
                      <details className="hm-m-family" open={family !== 'all' || index === 0}>
                        <summary>
                          <IconBadge name={label?.icon ?? 'scale'} tone="apple" size="sm" />
                          <div>
                            <h3>
                              {label?.title ?? block.key}
                              <span className="hm-m-family-ar" lang="ar" dir="rtl">
                                {block.titleAr}
                              </span>
                            </h3>
                            <p>{label?.line}</p>
                          </div>
                          <span className="hm-m-family-count tnum">{block.count}</span>
                          <Icon name="chevron" className="hm-m-family-mark size-[18px]" />
                        </summary>
                        <ul>
                          {block.conditions.map((condition) => (
                            <li key={condition.code} className="hm-m-rule">
                              <div className="hm-m-rule-head">
                                <code>{condition.code}</code>
                                <span
                                  className="hm-m-rule-ar"
                                  lang="ar"
                                  dir="rtl"
                                  title={condition.titleAr}
                                >
                                  {condition.titleAr}
                                </span>
                                {condition.agreement === 'disputed' && (
                                  <span className="hm-m-flag">
                                    <Icon name="warning" className="size-[13px]" />
                                    {AGREEMENT_LABEL.disputed}
                                  </span>
                                )}
                              </div>
                              <p>{condition.reason}</p>
                              {condition.evidence.length > 0 && (
                                <ul className="hm-m-proofs">
                                  {condition.evidence.slice(0, 3).map((proof) => (
                                    <li key={`${condition.code}-${proof.reference}`}>
                                      <Icon name="book" className="size-[13px]" />
                                      <span>{EVIDENCE_LABEL[proof.kind] ?? proof.kind}</span>
                                      <b>{proof.reference}</b>
                                    </li>
                                  ))}
                                  {condition.evidence.length > 3 && (
                                    <li className="hm-m-proofs-more tnum">
                                      +{condition.evidence.length - 3}
                                    </li>
                                  )}
                                </ul>
                              )}
                            </li>
                          ))}
                        </ul>
                      </details>
                    </Reveal>
                  )
                })}
              </div>
            </section>

            {/* ---- What it skips ------------------------------------------ */}
            <section id="skipped" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="minus" className="size-4" />
                  What it skips
                </p>
                <h2 className="hm-m-h2">Skipping is not passing. It means we did not look.</h2>
              </Reveal>

              <Reveal delay={60}>
                <div className="hm-m-reach-row">
                  <ReachArt applied={counts.applied} skipped={counts.skipped} />
                  <div>
                    <p className="hm-m-p">
                      Some questions cannot be answered by reading a website. How much
                      interest-bearing debt a company carries. Whether a swap is unequal in the
                      way Islam forbids. Those need a person, or figures nobody publishes.
                    </p>
                    <p className="hm-m-p">
                      This standard <b>skips</b> them rather than guessing, and skipping is
                      recorded rather than hidden. A skipped rule contributes nothing in either
                      direction: it cannot refuse a coin, and it never counts as a coin having
                      passed.
                    </p>
                  </div>
                </div>
              </Reveal>

              <Reveal delay={100}>
                <ul className="hm-m-skips">
                  {data.skipped.map((item) => (
                    <li key={item.code}>
                      <code>{item.code}</code>
                      <span className="hm-m-skip-ar" lang="ar" dir="rtl">
                        {item.titleAr}
                      </span>
                      <span className="hm-m-skip-why">{item.why}</span>
                    </li>
                  ))}
                </ul>
              </Reveal>
            </section>

            {/* ---- The three answers -------------------------------------- */}
            <section id="answers" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="flag" className="size-4" />
                  The three answers
                </p>
                <h2 className="hm-m-h2">Three words, and none of them is "halal".</h2>
              </Reveal>
              <Reveal delay={60}>
                <div className="hm-m-answers">
                  {(Object.keys(OUTCOME) as Array<Coin['outcome']>).map((key) => (
                    <article key={key} className={`hm-card hm-lift hm-m-answer ${OUTCOME[key].tone}`}>
                      <span className="hm-m-answer-mark">
                        <Icon name={OUTCOME[key].icon} className="size-[18px]" />
                      </span>
                      <h3>{OUTCOME[key].label}</h3>
                      <p>{OUTCOME[key].line}</p>
                    </article>
                  ))}
                </div>
              </Reveal>
              <Reveal delay={100}>
                <p className="hm-m-note">
                  <Icon name="info" className="size-4 shrink-0" />
                  <span>
                    "Not judged" is a request for more research — never a quiet no. A coin
                    nobody could read has not been refused, and the product never shows it as
                    though it had been.
                  </span>
                </p>
              </Reveal>
            </section>

            {/* ---- Coins judged ------------------------------------------- */}
            <section id="coins" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="market" className="size-4" />
                  Coins judged
                </p>
                <h2 className="hm-m-h2">
                  Every coin this standard has an answer for — including the ones it refused.
                </h2>
                <p className="hm-m-p">
                  A list of only the coins that passed cannot be checked: you would have no way
                  to tell a coin that was refused from a coin nobody looked at. Both are here.
                </p>
              </Reveal>

              <Reveal delay={60}>
                <div className="hm-m-filters" role="group" aria-label="Filter the coins">
                  <button
                    type="button"
                    className={`hm-m-chip ${outcome === 'all' ? 'is-on' : ''}`}
                    aria-pressed={outcome === 'all'}
                    onClick={() => setOutcome('all')}
                  >
                    <Icon name="layers" className="size-4" />
                    All
                    <span className="tnum">{data.coins.length}</span>
                  </button>
                  {(Object.keys(OUTCOME) as Array<Coin['outcome']>).map((key) => (
                    <button
                      key={key}
                      type="button"
                      className={`hm-m-chip ${outcome === key ? 'is-on' : ''}`}
                      aria-pressed={outcome === key}
                      onClick={() => setOutcome(key)}
                    >
                      <Icon name={OUTCOME[key].icon} className="size-4" />
                      {OUTCOME[key].label}
                      <span className="tnum">
                        {data.coins.filter((coin) => coin.outcome === key).length}
                      </span>
                    </button>
                  ))}
                </div>
              </Reveal>

              <Reveal delay={90}>
                <div className="hm-m-table-wrap">
                  <table className="hm-m-table">
                    <caption className="sr-only">
                      Coins the Hilal Markets Methodology has judged, with how it decided
                    </caption>
                    <thead>
                      <tr>
                        <th scope="col">Coin</th>
                        <th scope="col">Answer</th>
                        <th scope="col">How</th>
                        <th scope="col" className="hm-m-num">Pages read</th>
                        <th scope="col">Decided</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownCoins.map((coin) => (
                        <tr key={coin.symbol}>
                          <th scope="row">
                            <b>{coin.symbol}</b>
                            <span>{coin.name}</span>
                          </th>
                          <td>
                            <span className={`hm-m-pill ${OUTCOME[coin.outcome].tone}`}>
                              <Icon name={OUTCOME[coin.outcome].icon} className="size-[13px]" />
                              {OUTCOME[coin.outcome].label}
                            </span>
                          </td>
                          <td className="hm-m-how">
                            {coin.admission === 'regulator_floor'
                              ? 'A regulator published it'
                              : 'The machine read its site'}
                          </td>
                          <td className="hm-m-num tnum">
                            {coin.admission === 'regulator_floor' ? '—' : coin.pagesRead}
                          </td>
                          <td className="hm-m-when tnum">{coin.decidedOn}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Reveal>
            </section>

            {/* ---- Other standards ---------------------------------------- */}
            <section id="others" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="globe" className="size-4" />
                  Other standards
                </p>
                <h2 className="hm-m-h2">Whose answers we take, and whose we do not.</h2>
                <p className="hm-m-p">
                  Only one outside list is admitted automatically, and the reason is that it is
                  the only financial regulator among them. The other two are used differently,
                  and neither of them puts a coin into this standard.
                </p>
              </Reveal>
              <Reveal delay={60}>
                <div className="hm-m-others">
                  {data.otherMethodologies.map((item) => (
                    <article
                      key={item.code}
                      className={`hm-card hm-m-other ${item.admitted ? 'is-admitted' : ''}`}
                    >
                      <div className="hm-m-other-head">
                        <IconBadge
                          name={item.admitted ? 'shield_check' : 'scale'}
                          tone={item.admitted ? 'apple' : 'neutral'}
                          size="sm"
                        />
                        <div>
                          <h3>{item.name}</h3>
                          <p className="hm-m-other-auth">{item.authority}</p>
                        </div>
                        <span className={item.admitted ? 'hm-chip-live' : 'hm-chip-next'}>
                          {item.admitted && <span aria-hidden="true" />}
                          {item.admitted ? `Admitted · ${item.coins} coins` : 'Not admitted'}
                        </span>
                      </div>
                      <p>{item.why}</p>
                      <a className="hm-m-other-link" href={item.url} target="_blank" rel="noreferrer noopener">
                        Open the source
                        <Icon name="external" className="size-[14px]" />
                      </a>
                    </article>
                  ))}
                </div>
              </Reveal>
            </section>

            {/* ---- Limits ------------------------------------------------- */}
            <section id="limits" className="hm-m-section">
              <Reveal>
                <p className="hm-m-kicker">
                  <Icon name="lock" className="size-4" />
                  What it never does
                </p>
                <h2 className="hm-m-h2">The boundaries, stated plainly.</h2>
              </Reveal>
              <Reveal delay={60}>
                <ul className="hm-m-limits">
                  {[
                    'It never calls a coin halal or haram. It says what it read and what refused it.',
                    'It is never mixed with an authority’s decision into one score. It publishes under its own name or not at all.',
                    'It is never chosen for you. You have to pick it deliberately, and every result carries this warning.',
                    'It never guesses at a question it cannot answer. It records the question as skipped.',
                    'It never gives buy or sell advice, and Hilal Markets never places a trade.',
                  ].map((line) => (
                    <li key={line}>
                      <Icon name="check" className="size-[16px] shrink-0" />
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
              </Reveal>

              <Reveal delay={100}>
                <div className="hm-m-close">
                  <div>
                    <h3>Want a decision a named authority made?</h3>
                    <p>
                      The product carries reviewed standards alongside this one. Choose one of
                      those and you get an authority's own published result, with its citation.
                    </p>
                  </div>
                  <TrackedCta
                    href={entry}
                    analyticsName="open_dashboard"
                    analyticsLocation="methodology_close"
                    className="hm-btn hm-btn--primary"
                  >
                    Open the product
                    <Icon name="arrow" className="size-4" />
                  </TrackedCta>
                </div>
              </Reveal>
            </section>
          </div>
        </div>
      </main>

      <BackToTop />
      <SiteFooter />
    </div>
  )
}
