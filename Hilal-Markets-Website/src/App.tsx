import { useCallback, useRef, useState, type FormEvent } from 'react'
import { Reveal } from './components/Reveal'
import { HeroFlow } from './components/HeroFlow'
import Component03ProblemAndSolution from './imports/03ProblemAndSolution-1'
import Component04HowHilalMarketsWorks from './imports/04HowHilalMarketsWorks'
import Component06CoreFeatures from './imports/06CoreFeatures-1'
import Component07TrustAndControl from './imports/07TrustAndControl'
import {
  getFirstTouchAttribution,
  trackFaqOpen,
  trackWaitlistError,
  trackWaitlistFormStart,
  trackWaitlistFormView,
  trackWaitlistSubmitAttempt,
  trackWaitlistSuccess,
  type WaitlistErrorType,
} from './analytics'
import { SiteFooter, SiteNav } from './components/SiteChrome'
import { TrackedCta, TrackedSection, useVisibilityTracking } from './components/Tracking'
import ContactPage from './pages/ContactPage'
import LegalPage from './pages/LegalPage'
import {
  newWaitlistIdempotencyKey,
  PublicFormError,
  submitWaitlist,
} from './publicForms'

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
            href="#waitlist"
            analyticsName="join_waitlist"
            analyticsLocation="hero"
            className="inline-flex items-center justify-center rounded-full bg-[#cbfa4d] px-[49px] py-[19px] font-display text-[16px] leading-none text-[#2b2e35] shadow-[0_16px_36px_-18px_rgba(120,170,40,0.9)] transition-transform hover:-translate-y-0.5"
          >
            Join the waitlist
          </TrackedCta>
          <p className="text-[14px] text-ink/50">Be among the first to access Hilal Markets.</p>
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
/*  Waitlist                                                                    */
/* -------------------------------------------------------------------------- */
/**
 * The one place a visitor can act on the landing page while the product is invite-only.
 *
 * It sits where the pricing table used to sit, because that is the point in the page
 * where somebody who has read the product decides to do something about it. Nothing
 * here creates an account or reaches the dashboard: the private beta is entered by
 * invitation, so asking for a password would promise access the visitor does not have.
 *
 * The form asks for an email address and nothing else. It briefly carried a pre-ticked
 * "contact me about beta testing" box; a box that is already ticked is not a choice the
 * person made, so it was removed rather than left recording an answer nobody gave.
 */
function Waitlist() {
  const bars = [1, 0.62, 0.38, 0.22, 0.12, 0.06]
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'success' | 'duplicate' | 'error'>('idle')
  const [errorType, setErrorType] = useState<WaitlistErrorType>('unknown_error')
  const idempotencyKey = useRef(newWaitlistIdempotencyKey())
  const formVisible = useCallback(() => trackWaitlistFormView('landing_final'), [])
  const waitlistRef = useVisibilityTracking<HTMLDivElement>(formVisible, {
    visibilityMode: 'percentage',
    dwellMs: 1000,
    threshold: 0.5,
  })

  // One identifier stands for one exact submission. A network retry of the same values
  // must not create a second record, so the key survives a retry; changing a value makes
  // it a different submission, so the key is replaced.
  function resetIdempotency() {
    idempotencyKey.current = newWaitlistIdempotencyKey()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (status === 'submitting') return
    if (!event.currentTarget.reportValidity()) return
    trackWaitlistSubmitAttempt('landing_final')
    setStatus('submitting')

    try {
      const result = await submitWaitlist(
        { email },
        getFirstTouchAttribution(),
        idempotencyKey.current,
      )
      if (result.created) {
        trackWaitlistSuccess('landing_final', idempotencyKey.current)
        setStatus('success')
      } else {
        trackWaitlistError('duplicate_email', 'landing_final')
        setStatus('duplicate')
      }
      resetIdempotency()
    } catch (error) {
      const category = error instanceof PublicFormError ? error.category : 'unknown_error'
      setErrorType(category)
      trackWaitlistError(category, 'landing_final')
      setStatus('error')
    }
  }

  return (
    <Reveal as="section" className="mx-auto max-w-[1200px] px-5 py-24">
      <div
        id="waitlist"
        ref={waitlistRef}
        className="relative overflow-hidden rounded-[32px] bg-apple px-8 py-16 text-center sm:px-16"
      >
        {/* The trading-bars pattern. The brand rules name the waitlist section as one of
            the few places it belongs: first column most visible, each later one fainter,
            and the whole thing kept behind the message rather than competing with it.
            The values are the ones already tuned for this panel — near-black at 18% and
            below on apple green — so it reads as texture, not as market data. */}
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden items-end gap-3 pr-8 sm:flex" aria-hidden="true">
          {bars.map((o, i) => (
            <span
              key={i}
              className="w-8 rounded-t-lg bg-[#2b2e35]"
              style={{ height: `${88 - i * 8}%`, opacity: o * 0.18 }}
            />
          ))}
        </div>

        <div className="relative mx-auto max-w-[580px]">
          <h2 className="font-display text-[26px] leading-[1.08] tracking-[-0.03em] text-ink sm:text-[34px] md:text-[42px]">
            Be among the first to experience Hilal Markets
          </h2>
          <p className="mx-auto max-w-[480px] pt-4 text-[18px] leading-[24px] text-[#2b2e35]">
            Join the waitlist for early access and help shape a platform built around the real needs of Muslim traders.
          </p>
          {status === 'success' ? (
            <div className="mx-auto mt-8 max-w-[440px] rounded-[20px] border border-ink/10 bg-white/70 px-6 py-5" role="status">
              <p className="font-display text-[20px] text-ink">You are on the waitlist.</p>
              <p className="mt-1 text-[14px] text-ink/65">
                We will contact you as access becomes available.
              </p>
              <button
                type="button"
                className="mt-4 text-[13px] font-semibold text-ink underline decoration-ink/30 underline-offset-4"
                onClick={() => {
                  setEmail('')
                  setStatus('idle')
                  resetIdempotency()
                }}
              >
                Use another email
              </button>
            </div>
          ) : (
            // One row: the address and the button. Stacked on a narrow phone, side by
            // side from the small breakpoint up.
            <form
              className="mx-auto mt-8 flex max-w-[440px] flex-col gap-3 sm:flex-row"
              onSubmit={handleSubmit}
            >
              <label className="sr-only" htmlFor="waitlist-email">Email address</label>
              <input
                id="waitlist-email"
                type="email"
                required
                autoComplete="email"
                value={email}
                aria-invalid={status === 'duplicate' || status === 'error' ? true : undefined}
                aria-describedby={status === 'duplicate' || status === 'error' ? 'waitlist-email-error' : undefined}
                onFocus={() => trackWaitlistFormStart('landing_final')}
                onPaste={() => trackWaitlistFormStart('landing_final')}
                onChange={(event) => {
                  trackWaitlistFormStart('landing_final')
                  setEmail(event.target.value)
                  resetIdempotency()
                  if (status === 'duplicate' || status === 'error') setStatus('idle')
                }}
                placeholder="you@email.com"
                className={`w-full rounded-full border bg-white/70 px-5 py-3.5 text-[15px] text-ink outline-none transition-colors placeholder:text-ink/40 focus:bg-white ${
                  status === 'duplicate' || status === 'error'
                    ? 'border-[#8d3029] focus:border-[#8d3029] focus:ring-4 focus:ring-[#8d3029]/10'
                    : 'border-ink/15 focus:border-ink/50'
                }`}
              />
              <button
                type="submit"
                disabled={status === 'submitting'}
                aria-busy={status === 'submitting'}
                className="shrink-0 rounded-full bg-ink px-7 py-3.5 text-[15px] font-medium !text-[#fff] transition-transform hover:-translate-y-0.5 disabled:cursor-wait disabled:opacity-70"
              >
                {status === 'submitting' ? 'Joining...' : 'Join the waitlist'}
              </button>
            </form>
          )}
          {(status === 'duplicate' || status === 'error') && (
            <p id="waitlist-email-error" className="mt-3 text-[13px] font-semibold text-[#6c271f]" role="alert">
              {status === 'duplicate'
                ? 'This email is already on the waitlist. Please use a different email.'
                : errorType === 'rate_limited'
                  ? 'Please wait a moment before trying again.'
                  : 'We could not submit your email. Please try again.'}
            </p>
          )}
          <p className="pt-4 text-[14px] text-[#2b2e35]/55">
            We will contact selected waitlist members directly as access becomes available.
          </p>
        </div>
      </div>
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
        <TrackedSection analyticsName="waitlist"><Waitlist /></TrackedSection>
        <TrackedSection analyticsName="faq"><FAQ /></TrackedSection>
      </main>
      <SiteFooter />
    </div>
  )
}
