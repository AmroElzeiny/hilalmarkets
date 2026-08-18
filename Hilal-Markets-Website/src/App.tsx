import { useState } from 'react'
import { Reveal } from './components/Reveal'
import { HeroFlow } from './components/HeroFlow'
import Component03ProblemAndSolution from './imports/03ProblemAndSolution-1'
import Component04HowHilalMarketsWorks from './imports/04HowHilalMarketsWorks'
import Component06CoreFeatures from './imports/06CoreFeatures-1'
import Component07TrustAndControl from './imports/07TrustAndControl'
import { trackFaqOpen } from './analytics'
import { BackToTop, dashboardEntryHref, SiteFooter, SiteNav } from './components/SiteChrome'
import { Icon, IconBadge, type IconName } from './components/Icon'
import Pricing from './components/Pricing'
import { TrackedCta, TrackedSection } from './components/Tracking'
import ContactPage from './pages/ContactPage'
import FeaturesPage from './pages/FeaturesPage'
import HowItWorksPage from './pages/HowItWorksPage'
import LegalPage from './pages/LegalPage'

/* -------------------------------------------------------------------------- */
/*  Responsive imported-section wrapper                                       */
/* -------------------------------------------------------------------------- */
function ResponsiveSection({ id, bg, children }: {
  id?: string
  bg?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <section
        id={id}
        className="flex w-full justify-center overflow-hidden"
        style={{ backgroundColor: bg }}
      >
        <div className="prototype-frame mx-auto w-full max-w-[1440px]">
          {children}
        </div>
      </section>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Hero                                                                         */
/* -------------------------------------------------------------------------- */
/**
 * The opening.
 *
 * It used to end in a waitlist form, because the product was invite-only. It is open
 * now, so the one action here opens the product: `/dashboard-entry` sends a signed-in
 * person to their dashboard and everybody else to sign-up, which means the button does
 * not have to guess which of the two the visitor is.
 */
function Hero() {
  const entry = dashboardEntryHref()
  return (
    <section id="top" className="relative overflow-hidden pt-32 pb-16 lg:pt-40">
      <div
        className="pointer-events-none absolute left-1/2 top-24 -z-10 h-[560px] w-[860px] -translate-x-1/2 opacity-[0.45]"
        aria-hidden="true"
      >
        <svg viewBox="0 0 860 560" fill="none" className="h-full w-full" aria-hidden="true">
          <path
            d="M120 40h500l100 100v300a80 80 0 0 1-80 80H120a80 80 0 0 1-80-80V120a80 80 0 0 1 80-80Z"
            stroke="#e3e8ee"
            strokeWidth="1.5"
          />
        </svg>
      </div>

      <div className="mx-auto w-full max-w-[820px] text-center">
        <Reveal delay={80}>
          <h1 className="mx-[15px] font-display text-[28px] leading-[1.08] tracking-[-0.03em] text-[#2b2e35] sm:text-[38px] md:text-[46px]">
            A better way for Muslim crypto traders to build and monitor their strategies.
          </h1>
        </Reveal>

        <Reveal delay={160}>
          <p className="mx-auto mt-4 max-w-[640px] px-[15px] text-[16px] leading-[1.55] text-[#4a505a] sm:text-[18px]">
            Build your rules on a visual canvas, explore Shariah-screened assets, and follow
            every setup in one place.
          </p>
        </Reveal>

        <Reveal delay={240} className="mt-8 flex flex-col items-center gap-3">
          <div className="flex flex-col items-center gap-3 sm:flex-row">
            <TrackedCta
              href={entry}
              analyticsName="open_dashboard"
              analyticsLocation="hero"
              className="inline-flex items-center justify-center gap-2 rounded-full bg-[#cbfa4d] px-[42px] py-[18px] font-display text-[16px] leading-none text-[#2b2e35] shadow-[0_16px_36px_-18px_rgba(120,170,40,0.9)] transition-transform hover:-translate-y-0.5"
            >
              Start free
              <svg viewBox="0 0 12 12" className="size-3.5" fill="none" aria-hidden="true">
                <path d="M2 6h8M6 2l4 4-4 4" stroke="#2b2e35" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </TrackedCta>
            <TrackedCta
              href="/how-it-works"
              analyticsName="how_it_works"
              analyticsLocation="hero"
              className="inline-flex items-center justify-center gap-2 rounded-full border border-[#d3d9e0] bg-white px-[32px] py-[17px] text-[15.5px] font-medium text-ink transition-colors hover:bg-[#f4f7fa]"
            >
              See how it works
            </TrackedCta>
          </div>
          <p className="text-[14px] text-[#5c646e]">
            Free plan, no card. Not a broker — Hilal Markets never places a trade.
          </p>
        </Reveal>
      </div>

      {/* The illustration is part of the hero on a phone too. It was hidden below the
          small breakpoint, which left a phone with the headline and nothing to look at;
          HeroFlow now reshapes itself instead of disappearing. */}
      <Reveal delay={200} cascade className="mt-10 sm:mt-16">
        <HeroFlow />
      </Reveal>
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/*  Sections carried over from the design file                                  */
/* -------------------------------------------------------------------------- */
function ProblemSolution() {
  return (
    <Reveal>
      <ResponsiveSection bg="#ffffff">
        <Component03ProblemAndSolution />
      </ResponsiveSection>
    </Reveal>
  )
}

function HowItWorks() {
  return (
    <Reveal>
      <ResponsiveSection id="how-it-works" bg="#f5f8fb">
        <Component04HowHilalMarketsWorks />
      </ResponsiveSection>
    </Reveal>
  )
}

function Features() {
  return (
    <Reveal>
      <ResponsiveSection id="features" bg="#ffffff">
        <Component06CoreFeatures />
      </ResponsiveSection>
    </Reveal>
  )
}

function TrustControl() {
  return (
    <Reveal>
      <ResponsiveSection bg="#f5f8fb">
        <Component07TrustAndControl />
      </ResponsiveSection>
    </Reveal>
  )
}

/* -------------------------------------------------------------------------- */
/*  The ecosystem                                                               */
/* -------------------------------------------------------------------------- */
/**
 * What is here now, and what is being built next.
 *
 * The two halves are labelled and kept apart on purpose. Everything under "Working
 * today" is something a person can open an account and use this afternoon; everything
 * under "Being built" is not, and says so. Mixing the two — which is what a single
 * feature grid does — is how a marketing page ends up promising software that does not
 * exist yet, and it is the one thing the brand rules refuse outright.
 */
const LIVE_STEPS: Array<{ icon: IconName; title: string; note: string }> = [
  {
    icon: 'market',
    title: 'Halal listings',
    note: 'Every listed asset carries its Shariah status, the methodology behind it, and the date it was reviewed.',
  },
  {
    icon: 'passport',
    title: 'Evidence Passport',
    note: 'Open any asset to read the reasons, the sources, and the full history of status changes.',
  },
  {
    icon: 'workflow',
    title: 'Visual canvas',
    note: 'Drag your conditions together and see the rule take shape. Plain language and templates work too.',
  },
  {
    icon: 'radar',
    title: 'Continuous monitoring',
    note: 'Approve a rule and Hilal Markets watches for it, then tells you which conditions matched.',
  },
]

const NEXT_STEPS: Array<{ icon: IconName; title: string; note: string }> = [
  {
    icon: 'spark',
    title: 'Hilal agent on the canvas',
    note: 'The agent sits beside your canvas, suggests the missing condition, and explains what each block does.',
  },
  {
    icon: 'search',
    title: 'Deeper asset research',
    note: 'Ask about an asset and get its screening evidence gathered into one answer, with the sources attached.',
  },
  {
    icon: 'layers',
    title: 'More markets',
    note: 'Crypto spot first. Other asset classes follow once each one can be screened to the same standard.',
  },
]

function Ecosystem() {
  const entry = dashboardEntryHref()
  return (
    <section id="ecosystem" className="hm-page bg-white py-[clamp(72px,8vw,116px)]">
      <div className="hm-shell">
        <Reveal>
          <div className="mx-auto max-w-[720px] text-center">
            <p className="hm-eyebrow">
              <Icon name="layers" className="size-4" />
              The ecosystem
            </p>
            <h2 className="mt-5 font-display text-[30px] leading-[1.1] tracking-[-0.03em] text-ink sm:text-[40px]">
              One place for halal listings, your rules, and what happens next.
            </h2>
            <p className="mx-auto mt-4 max-w-[600px] text-[16.5px] leading-[1.6] text-[#4a505a]">
              Hilal Markets is being built in steps. These are the ones that work today.
            </p>
          </div>
        </Reveal>

        <ul className="mt-10 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
          {LIVE_STEPS.map((step, index) => (
            <Reveal key={step.title} delay={Math.min(index * 60, 240)}>
              <li className="hm-card hm-lift h-full p-5">
                <div className="flex items-center gap-2.5">
                  <IconBadge name={step.icon} tone="apple" size="sm" />
                  <span className="hm-chip-live">
                    <span aria-hidden="true" />
                    Working today
                  </span>
                </div>
                <h3 className="mt-3.5 font-display text-[18px] font-medium leading-[1.25] tracking-[-0.01em] text-ink">
                  {step.title}
                </h3>
                <p className="mt-2 text-[14px] leading-[1.55] text-[#4a505a]">{step.note}</p>
              </li>
            </Reveal>
          ))}
        </ul>

        {/* ---- What is being built ---------------------------------------- */}
        <Reveal>
          <div className="mt-12 rounded-[26px] border border-[#dbe1e7] bg-[#f7f9fb] p-5 sm:p-7">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <IconBadge name="workflow" size="sm" />
                <h3 className="font-display text-[20px] font-medium tracking-[-0.02em] text-ink">
                  Being built next
                </h3>
              </div>
              <p className="text-[13.5px] text-[#5c646e]">
                Not available yet. Listed so you know where this is going.
              </p>
            </div>
            <ul className="mt-5 grid gap-3 sm:grid-cols-3">
              {NEXT_STEPS.map((step) => (
                <li key={step.title} className="rounded-[18px] border border-[#e1e5ea] bg-white p-4">
                  <div className="flex items-center gap-2">
                    <Icon name={step.icon} className="size-[18px] text-[#55712a]" />
                    <span className="hm-chip-next">In development</span>
                  </div>
                  <h4 className="mt-3 text-[15px] font-semibold leading-[1.3] text-ink">
                    {step.title}
                  </h4>
                  <p className="mt-1.5 text-[13.5px] leading-[1.55] text-[#4a505a]">{step.note}</p>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>

        <Reveal>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <TrackedCta
              href={entry}
              analyticsName="open_dashboard"
              analyticsLocation="ecosystem"
              className="hm-btn hm-btn--primary"
            >
              Start free
              <Icon name="arrow" className="size-4" />
            </TrackedCta>
            <TrackedCta
              href="/features"
              analyticsName="features"
              analyticsLocation="ecosystem"
              className="hm-btn hm-btn--quiet"
            >
              See every feature
            </TrackedCta>
          </div>
        </Reveal>
      </div>
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/*  FAQ                                                                         */
/* -------------------------------------------------------------------------- */
function FAQ() {
  const faqs = [
    {
      id: 'what_is_hilal',
      q: 'What is Hilal Markets?',
      a: 'Hilal Markets is a strategy-building and market-monitoring platform for Muslim traders. You turn your own ideas into clear rules, explore Shariah-screened assets, follow setups as they develop, and see the evidence behind every alert.',
    },
    {
      id: 'target_audience',
      q: 'Who is Hilal Markets designed for?',
      a: 'Muslim traders who already have market conditions they want to watch but do not want to sit in front of charts all day. No coding experience is needed.',
    },
    {
      id: 'how_to_build',
      q: 'How do I build a strategy?',
      a: 'Three ways, and you can switch between them. Drag your conditions together on the visual canvas, describe what you want in plain language, or start from a ready-made template and change it. Whichever you choose, you see the exact rules and approve them before any monitoring starts.',
    },
    {
      id: 'is_broker',
      q: 'Is Hilal Markets a broker?',
      a: 'No. Hilal Markets does not open brokerage accounts, hold your money, or place trades. You keep using your own broker or exchange.',
    },
    {
      id: 'provides_signals',
      q: 'Does Hilal Markets provide buy and sell signals?',
      a: 'No. There are no generic buy or sell calls. You define the conditions, and the platform watches the market against the rules you approved.',
    },
    {
      id: 'supported_markets',
      q: 'Which markets are supported?',
      a: 'Crypto spot markets and a screened set of assets. Other asset classes will be added once each can be screened to the same standard.',
    },
    {
      id: 'halal_listings',
      q: 'What are the halal listings?',
      a: 'Every asset in the list carries its Shariah status, the methodology used to reach it, the sources, the review date, and any restrictions. You can open the full record for any asset and read the whole history of how that status changed.',
    },
    {
      id: 'shariah_screening',
      q: 'How does Shariah screening work?',
      a: 'Each asset is assessed under a published methodology by the platform’s own review process. The status, the criteria, the supporting sources, the review date and the status history are all shown to you.',
    },
    {
      id: 'halal_guarantee',
      q: 'Does Hilal Markets guarantee that every asset is halal?',
      a: 'No, and it does not claim to. It shows screening information based on a published methodology and qualified Shariah oversight. It does not replace a personal religious ruling, and scholars do not always agree. Where a case is disputed or uncertain, the platform says so.',
    },
    {
      id: 'ai_rule_changes',
      q: 'Can AI change my strategy on its own?',
      a: 'No. Nothing can quietly change a rule you approved. Any change creates a new version that you have to review and confirm before it becomes active.',
    },
    {
      id: 'alert_delivery',
      q: 'How are alerts delivered?',
      a: 'In the app and through Telegram, so you do not need the dashboard open. WhatsApp is being added and will be switched on only when it is working properly.',
    },
    {
      id: 'strategy_privacy',
      q: 'Will my strategies stay private?',
      a: 'Yes. Your strategies, rules, watchlists and monitoring history are private. They are not published or shared with other users.',
    },
  ]
  const [open, setOpen] = useState<number | null>(0)
  return (
    <Reveal>
      <section id="faq" className="mx-auto max-w-[820px] px-5 py-24">
        <div className="mb-12 text-center">
          <h2 className="font-display text-[34px] leading-[1.1] tracking-[-0.03em] text-ink sm:text-[40px]">
            Questions, answered
          </h2>
        </div>

        <div className="divide-y divide-hairline overflow-hidden rounded-[24px] border border-hairline bg-surface">
          {faqs.map((f, i) => {
            const isOpen = open === i
            return (
              <Reveal key={f.q} delay={Math.min(i * 45, 270)}>
                <button
                  id={`faq-button-${i}`}
                  type="button"
                  onClick={() => {
                    if (!isOpen) trackFaqOpen(f.id)
                    setOpen(isOpen ? null : i)
                  }}
                  className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition-colors hover:bg-[#fafbfc]"
                  aria-expanded={isOpen}
                  aria-controls={`faq-panel-${i}`}
                >
                  <span className="text-[16.5px] font-semibold text-ink">{f.q}</span>
                  <span
                    className={`flex size-7 shrink-0 items-center justify-center rounded-full border border-hairline transition-transform duration-300 ${
                      isOpen ? 'rotate-45 bg-apple' : 'bg-surface'
                    }`}
                  >
                    <svg viewBox="0 0 12 12" className="size-3" fill="none" aria-hidden="true">
                      <path d="M6 1v10M1 6h10" stroke="#2b2e35" strokeWidth="1.6" strokeLinecap="round" />
                    </svg>
                  </span>
                </button>
                <div
                  id={`faq-panel-${i}`}
                  role="region"
                  aria-labelledby={`faq-button-${i}`}
                  aria-hidden={!isOpen}
                  className="grid transition-all duration-300 ease-out"
                  style={{ gridTemplateRows: isOpen ? '1fr' : '0fr' }}
                >
                  <div className="overflow-hidden">
                    <p className="px-6 pb-5 text-[15.5px] leading-relaxed text-[#4a505a]">{f.a}</p>
                  </div>
                </div>
              </Reveal>
            )
          })}
        </div>
      </section>
    </Reveal>
  )
}

/* -------------------------------------------------------------------------- */
export default function App() {
  const path = window.location.pathname
  if (path === '/contact') return <ContactPage />
  if (path === '/privacy') return <LegalPage kind="privacy" />
  if (path === '/terms') return <LegalPage kind="terms" />
  if (path === '/cookies') return <LegalPage kind="cookies" />
  if (path === '/features') return <FeaturesPage />
  if (path === '/how-it-works') return <HowItWorksPage />
  return (
    <div className="hm-page min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main>
        <TrackedSection analyticsName="hero"><Hero /></TrackedSection>
        <TrackedSection analyticsName="problem_solution"><ProblemSolution /></TrackedSection>
        <TrackedSection analyticsName="how_it_works"><HowItWorks /></TrackedSection>
        <Features />
        <TrackedSection analyticsName="ecosystem"><Ecosystem /></TrackedSection>
        <TrackedSection analyticsName="trust_control"><TrustControl /></TrackedSection>
        <TrackedSection analyticsName="pricing"><Pricing /></TrackedSection>
        <TrackedSection analyticsName="faq"><FAQ /></TrackedSection>
      </main>
      <BackToTop />
      <SiteFooter />
    </div>
  )
}
