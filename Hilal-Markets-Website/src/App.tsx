import { useState } from 'react'
import { Reveal } from './components/Reveal'
import { HeroFlow } from './components/HeroFlow'
import Component03ProblemAndSolution from './imports/03ProblemAndSolution-1'
import Component04HowHilalMarketsWorks from './imports/04HowHilalMarketsWorks'
import Component06CoreFeatures from './imports/06CoreFeatures-1'
import Component07TrustAndControl from './imports/07TrustAndControl'
import { trackFaqOpen } from './analytics'
import { SiteFooter, SiteNav } from './components/SiteChrome'
import { TrackedCta, TrackedSection } from './components/Tracking'
import Pricing from './components/Pricing'
import ContactPage from './pages/ContactPage'
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
function Hero() {
  return (
    <section id="top" className="relative overflow-hidden pt-36 pb-16 lg:pt-40">
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

      <div className="mx-auto w-full max-w-[780px] text-center">
        <Reveal delay={80}>
          <h1 className="mx-[15px] font-display text-[26px] leading-none tracking-[-0.03em] text-[#2b2e35] sm:text-[34px] md:text-[40px]">
            A better way for Muslim crypto traders to build and monitor their strategies.
          </h1>
        </Reveal>

        <Reveal delay={160}>
          <p className="mx-[15px] pt-3 text-[15px] leading-[1.4] text-[#2b2e35] sm:text-[18px]">
            Create your own rules, explore Shariah-screened assets, and follow every setup without
            constantly switching between charts, alerts, and halal screeners.
          </p>
        </Reveal>

        <Reveal delay={240} className="mt-8 flex flex-col items-center gap-3">
          <TrackedCta
            href="/subscribe?plan_code=demo&billing_interval=monthly"
            analyticsName="start_free"
            analyticsLocation="hero"
            className="inline-flex items-center justify-center rounded-full bg-[#cbfa4d] px-[49px] py-[19px] font-display text-[16px] leading-none text-[#2b2e35] shadow-[0_16px_36px_-18px_rgba(120,170,40,0.9)] transition-transform hover:-translate-y-0.5"
          >
            Get started
          </TrackedCta>
          <p className="text-[14px] text-ink/50">Explore screened assets with a free account.</p>
        </Reveal>
      </div>

      <Reveal delay={200} cascade className="mt-16 hidden sm:block">
        <HeroFlow />
      </Reveal>
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/*  Problem and Solution                                                        */
/* -------------------------------------------------------------------------- */
function ProblemSolution() {
  return (
    <Reveal>
      <ResponsiveSection
        bg="#ffffff"
      >
        <Component03ProblemAndSolution />
      </ResponsiveSection>
    </Reveal>
  )
}

/* -------------------------------------------------------------------------- */
/*  How it works                                                               */
/* -------------------------------------------------------------------------- */
function HowItWorks() {
  return (
    <Reveal>
      <ResponsiveSection
        id="how-it-works"
        bg="#f5f8fb"
      >
        <Component04HowHilalMarketsWorks />
      </ResponsiveSection>
    </Reveal>
  )
}

/* -------------------------------------------------------------------------- */
/*  Core Features                                                               */
/* -------------------------------------------------------------------------- */
function Features() {
  return (
    <Reveal>
      <ResponsiveSection
        id="features"
        bg="#ffffff"
      >
        <Component06CoreFeatures />
      </ResponsiveSection>
    </Reveal>
  )
}

/* -------------------------------------------------------------------------- */
/*  Trust and Control                                                           */
/* -------------------------------------------------------------------------- */
function TrustControl() {
  return (
    <Reveal>
      <ResponsiveSection
        bg="#f5f8fb"
      >
        <Component07TrustAndControl />
      </ResponsiveSection>
    </Reveal>
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
      a: 'Hilal Markets is a strategy-building and market-monitoring platform designed for Muslim traders. It helps users turn their own ideas into clear rules, explore Shariah-screened assets, monitor setups continuously, and understand the evidence behind every alert.',
    },
    {
      id: 'target_audience',
      q: 'Who is Hilal Markets designed for?',
      a: 'Hilal Markets is designed for Muslim traders who already have strategies, indicators, or market conditions they want to monitor but do not want to watch charts continuously. No coding experience is required.',
    },
    {
      id: 'is_broker',
      q: 'Is Hilal Markets a broker?',
      a: 'No. Hilal Markets does not provide brokerage accounts, hold funds, or execute transactions. Users continue to use their own broker or exchange separately.',
    },
    {
      id: 'provides_signals',
      q: 'Does Hilal Markets provide buy and sell signals?',
      a: 'Hilal Markets does not distribute generic buy or sell calls. Users define their own strategies and conditions, and the platform monitors the market according to the rules they have reviewed and approved.',
    },
    {
      id: 'supported_markets',
      q: 'Which markets and assets will be supported first?',
      a: 'Hilal Markets will initially focus on crypto spot markets and a selected universe of screened assets. Stocks, ETFs, and other asset classes may be added as the platform grows.',
    },
    {
      id: 'shariah_screening',
      q: 'How does Shariah screening work?',
      a: 'Each supported asset is evaluated under a published screening methodology. Users can review the status, screening criteria, supporting sources, review date, relevant restrictions, and status history.',
    },
    {
      id: 'halal_guarantee',
      q: 'Does Hilal Markets guarantee that every asset or strategy is halal?',
      a: 'Hilal Markets provides screening information based on its published methodology and qualified Shariah oversight. It does not replace a personal religious ruling or guarantee that every scholar will reach the same conclusion. Disputed or uncertain cases will be shown clearly.',
    },
    {
      id: 'ai_strategy_builder',
      q: 'How does the AI chatbot help build my strategy?',
      a: 'The AI chatbot helps translate your description into structured and measurable conditions and asks clarifying questions when something is incomplete or ambiguous. You review and approve the rules before monitoring begins. No coding is required.',
    },
    {
      id: 'ai_rule_changes',
      q: 'Can AI change my strategy automatically?',
      a: 'No. AI cannot silently change an approved strategy. Any change must be reviewed and confirmed before a new version becomes active.',
    },
    {
      id: 'alert_delivery',
      q: 'How will alerts be delivered?',
      a: 'Users can receive alerts without keeping the Hilal Markets dashboard open. Telegram and email are supported. WhatsApp delivery is coming soon and will be enabled only when the integration is operational.',
    },
    {
      id: 'strategy_privacy',
      q: 'Will my strategies remain private?',
      a: 'Yes. Your strategies, rules, watchlists, and monitoring history will remain private and will not be published or shared with other users without your permission.',
    },
  ]
  const [open, setOpen] = useState<number | null>(0)
  return (
    <Reveal>
      <section id="faq" className="mx-auto max-w-[820px] px-5 py-24">
        <div className="mb-12 text-center">
          <h2 className="font-display text-[34px] leading-[1.1] tracking-[-0.03em] text-ink sm:text-[40px]">
            FAQ
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
                    <p className="px-6 pb-5 text-[15.5px] leading-relaxed text-ink/70">{f.a}</p>
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
  if (window.location.pathname === '/contact') return <ContactPage />
  if (window.location.pathname === '/privacy') return <LegalPage kind="privacy" />
  if (window.location.pathname === '/terms') return <LegalPage kind="terms" />
  return (
    <div className="min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main>
        <TrackedSection analyticsName="hero"><Hero /></TrackedSection>
        <TrackedSection analyticsName="problem_solution"><ProblemSolution /></TrackedSection>
        <TrackedSection analyticsName="how_it_works"><HowItWorks /></TrackedSection>
        <Features />
        <TrackedSection analyticsName="trust_control"><TrustControl /></TrackedSection>
        <TrackedSection analyticsName="pricing"><Pricing /></TrackedSection>
        <TrackedSection analyticsName="faq"><FAQ /></TrackedSection>
      </main>
      <SiteFooter />
    </div>
  )
}
