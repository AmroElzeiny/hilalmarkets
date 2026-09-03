/**
 * Not rendered while the site is in waitlist mode.
 *
 * Hilal Markets is invite-only during the private beta, so the landing page shows the
 * waitlist form where this section used to sit. Nothing here is stale: the plans, the
 * comparison table and the launch-price countdown still read from the same server
 * values as the dashboard, so putting `<Pricing />` back into `App.tsx` restores the
 * section exactly as it shipped. Deleting it would mean rebuilding it from memory
 * later, and a rebuilt price is a price that can disagree with the server's.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  trackBillingIntervalChanged,
  trackPlanSelected,
  trackPricingSectionView,
  type BillingInterval,
  type PublicPlanCode,
} from '../analytics'
import { CheckIcon } from './brand'
import { useVisibilityTracking } from './Tracking'

type Plan = {
  code: PublicPlanCode
  name: string
  /**
   * What the plan costs today, already carrying the launch price when one is running.
   * Null while the interval is not open for sale — there is no price to quote yet, and
   * shipping the number anyway would put it in the page source for anyone to read.
   */
  monthlyPrice: number | null
  annualPrice: number | null
  description: string
  button: string
  visibleFeatures: string[]
  additionalFeatures: string[]
  highlightedFeature?: string | null
  badge?: string | null
  trialNote?: string | null
  /** False while an interval is not open yet: the card says "Soon" and shows no price. */
  monthlyAvailable?: boolean
  annualAvailable?: boolean
  /** The old price to cross out, or null when nothing changed. */
  originalMonthlyPrice?: number | null
  /**
   * What a checkout charges when nobody types a code. `monthlyPrice` above is the
   * headline, which already carries the launch-code discount, so the card needs both:
   * one to show, and one to say what the price is without the code.
   */
  fullMonthlyPrice?: number | null
  /** The code that unlocks the headline price, or null when there is no code running. */
  discountCode?: string | null
  discountPercent?: number | null
  comingSoonLabel?: string
}

/** Server default, used only when the page is opened without runtime config. */
const DEFAULT_PROMOTION_ENDS_AT = '2026-09-15T00:00:00+00:00'
const DEFAULT_COMING_SOON_LABEL = 'Soon'

const PLANS: Plan[] = [
  {
    code: 'demo',
    name: 'Basic',
    monthlyPrice: 0,
    annualPrice: 0,
    monthlyAvailable: true,
    annualAvailable: false,
    description:
      'For traders who want the AI assistant, screened-asset evidence, and a measured introduction to market monitoring.',
    button: 'Start free',
    highlightedFeature: 'AI assistant with Basic limits',
    visibleFeatures: [
      'AI assistant with Basic limits',
      'Approve 2 strategies per 30 days',
      '1 active market monitor',
      '2 monitor notifications per week across all monitors',
      '1 quick scan per week',
      'Halal assets, methodologies, and evidence reports',
      'Full Evidence Passports',
    ],
    additionalFeatures: [
      'Methodology reasons, sources, versions, and review dates',
      'Favorite coins and compliance-status changes',
      'In-app and Telegram notifications',
      'Why wasn\'t I alerted? available on Monitor',
      'Published compliance-status changes',
      'Standard email support',
    ],
  },
  {
    code: 'trader',
    name: 'Monitor',
    monthlyPrice: 15,
    originalMonthlyPrice: 20,
    fullMonthlyPrice: 20,
    discountCode: 'HILAL25',
    discountPercent: 25,
    annualPrice: 120,
    monthlyAvailable: true,
    annualAvailable: false,
    description:
      'For regular traders who want AI-assisted market monitoring and clear evidence behind every alert.',
    button: 'Choose Monitor monthly',
    trialNote: 'Cancel within 7 days of payment for a full refund.',
    highlightedFeature: 'AI assistant for creating market monitors',
    visibleFeatures: [
      'Everything in Basic',
      'AI assistant for creating market monitors',
      '5 active market monitors',
      '10 quick scans per month',
      'Up to 50 monitor alerts per day',
      'Full Why wasn\'t I alerted? explanations',
      'Complete Opportunity Journeys',
    ],
    additionalFeatures: [
      'Condition-level proof',
      'Missed-alert investigations',
      'In-app and Telegram monitor alerts',
    ],
  },
  {
    code: 'pro',
    name: 'Pro',
    monthlyPrice: 22,
    annualPrice: 220,
    monthlyAvailable: false,
    annualAvailable: false,
    description:
      'For active traders who need more simultaneous monitors, more quick scans, and unlimited monitor alerts.',
    button: 'Choose Pro',
    highlightedFeature: 'WhatsApp delivery',
    visibleFeatures: [
      'Everything in Monitor',
      '10 active market monitors',
      '100 quick scans per month',
      'Unlimited monitor alerts per day',
      'WhatsApp delivery',
    ],
    additionalFeatures: [
      'AI assistant for creating market monitors',
      'Condition-level proof',
      'Complete Opportunity Journeys',
      'Missed-alert investigations',
    ],
  },
]

const COMPARISON_ROWS = [
  ['Halal Assets market', 'Included', 'Included', 'Included'],
  ['Evidence Passports', 'Full', 'Full', 'Full'],
  ['Methodology reports', 'Full', 'Full', 'Full'],
  ['Favorite coins', 'Included', 'Included', 'Included'],
  [
    'Halal status-change alerts',
    'In-app + Telegram',
    'In-app + Telegram',
    'In-app + Telegram',
  ],
  ['AI assistant', 'Limited', 'Included', 'Included'],
  ['Strategy approvals', '2 per 30 days', 'Included', 'Included'],
  ['Active market monitors', '1', '5', '10'],
  ['Quick scans', '1 per week', '10 per month', '100 per month'],
  ['Monitor notifications', '2 per week', 'Up to 50 per day', 'Unlimited'],
  ['Condition proof', 'Not included', 'Full', 'Full'],
  ['Opportunity Journeys', 'Not included', 'Complete', 'Complete'],
  ['Why wasn\'t I alerted?', 'Not included', 'Included', 'Included'],
  ['Telegram monitor delivery', 'Included', 'Included', 'Included'],
  ['WhatsApp', 'Not included', 'Not included', 'Coming soon'],
  ['Money-back window', 'Not included', '7 days', 'Not included'],
] as const

/** Can this plan be bought on this interval today? Missing means "yes", for older data. */
function isAvailable(plan: Plan, interval: BillingInterval) {
  const flag = interval === 'annual' ? plan.annualAvailable : plan.monthlyAvailable
  return flag !== false
}

type Price =
  | { kind: 'coming_soon'; label: string }
  | { kind: 'price'; amount: string; period: string; original?: string }

function priceLabel(
  plan: Plan,
  interval: BillingInterval,
  promotionRunning: boolean,
): Price {
  const amount = interval === 'annual' ? plan.annualPrice : plan.monthlyPrice
  if (!isAvailable(plan, interval) || amount === null || amount === undefined) {
    // No price at all for something nobody can buy yet. A number next to "Soon" reads
    // as a charge the visitor is about to face.
    return { kind: 'coming_soon', label: plan.comingSoonLabel ?? DEFAULT_COMING_SOON_LABEL }
  }
  if (amount === 0) return { kind: 'price', amount: '$0', period: 'Free forever' }
  if (interval === 'annual') {
    return { kind: 'price', amount: `$${amount}`, period: 'per year' }
  }
  const discounted = Boolean(plan.originalMonthlyPrice && plan.originalMonthlyPrice > amount)
  if (discounted && !promotionRunning) {
    // The deadline passed while this page was open. The plan costs its normal price
    // again, so the page says so rather than holding an offer that has ended: the same
    // rule the server applies, applied to the copy the visitor is looking at.
    return { kind: 'price', amount: `$${plan.originalMonthlyPrice}`, period: 'per month' }
  }
  return {
    kind: 'price',
    amount: `$${amount}`,
    period: 'per month',
    original: discounted ? `$${plan.originalMonthlyPrice}` : undefined,
  }
}

function checkoutHref(planCode: PublicPlanCode, interval: BillingInterval) {
  return `/subscribe?plan_code=${encodeURIComponent(planCode)}&billing_interval=${interval}`
}

type Remaining = {
  days: number
  hours: number
  minutes: number
  seconds: number
} | null

const SECOND = 1_000

function remainingUntil(endsAt: string, now: number): Remaining {
  const end = Date.parse(endsAt)
  if (Number.isNaN(end)) return null
  const ms = end - now
  if (ms <= 0) return null
  const seconds = Math.floor(ms / SECOND)
  return {
    days: Math.floor(seconds / 86_400),
    hours: Math.floor((seconds % 86_400) / 3_600),
    minutes: Math.floor((seconds % 3_600) / 60),
    seconds: seconds % 60,
  }
}

/** One clock for the whole section, so the price and the countdown cannot disagree. */
function useNow(everyMs: number) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), everyMs)
    return () => window.clearInterval(timer)
  }, [everyMs])
  return now
}

/**
 * How long the launch price lasts, in days, hours, minutes and seconds.
 *
 * It counts down live, one step per second, so a visitor sitting on the page watches the
 * number fall. It stays quiet while it does: no animation, no colour change, no alarm —
 * the brand rules ask for calm, so this states a fact beside the price and stops there.
 *
 * When the deadline passes it removes itself, and the price beside it goes back to
 * normal in the same render, so a page left open overnight never shows an offer that has
 * ended.
 */
function OfferCountdown({ endsAt, now }: { endsAt: string; now: number }) {
  const left = remainingUntil(endsAt, now)
  if (!left) return null
  const parts: Array<[number, string]> = [
    [left.days, left.days === 1 ? 'day' : 'days'],
    [left.hours, left.hours === 1 ? 'hour' : 'hours'],
    [left.minutes, left.minutes === 1 ? 'minute' : 'minutes'],
    [left.seconds, left.seconds === 1 ? 'second' : 'seconds'],
  ]
  return (
    // `data-offer-live` is not decoration: the shared stylesheet hides every countdown
    // that does not carry it (`.offer-countdown:not([data-offer-live]){display:none}`),
    // because a box with no numbers in it yet must never flash on screen. The
    // server-rendered pages get the attribute from `hilalmarkets-offer.js` once their
    // first count is in. This one is built from a real count or not at all — the guard
    // above returns null when the deadline has passed — so it says so on render. Without
    // it the landing page drew the timer inside the card and the stylesheet hid it, on
    // every visit, for as long as the offer ran.
    <div
      className="offer-countdown"
      data-offer-live=""
      role="group"
      aria-label="Time left at this price"
    >
      <span className="offer-countdown-label">Launch price ends in</span>
      <span className="offer-countdown-parts tnum">
        {parts.map(([value, unit]) => (
          // Keyed on position, not on the unit word: "1 second" becomes "0 seconds" and
          // a key that changes with the wording would remount the element every minute.
          <span className="offer-countdown-part" key={unit.replace(/s$/, '')}>
            <strong>{value}</strong>
            <small>{unit}</small>
          </span>
        ))}
      </span>
    </div>
  )
}

export default function Pricing() {
  const [interval, setInterval] = useState<BillingInterval>('monthly')
  const [expandedPlans, setExpandedPlans] = useState<Set<PublicPlanCode>>(new Set())
  const pricingSeen = useCallback(() => trackPricingSectionView(), [])
  const sectionRef = useVisibilityTracking<HTMLElement>(pricingSeen, {
    visibilityMode: 'entry',
    dwellMs: 1000,
  })
  const commerce = window.HilalMarketsRuntimeConfig?.commerce
  const whatsappOperational = Boolean(commerce?.whatsappOperational)
  const plans: Plan[] =
    commerce?.plans?.length === PLANS.length ? commerce.plans : PLANS
  const comparisonRows =
    commerce?.comparisonRows?.length ? commerce.comparisonRows : COMPARISON_ROWS
  const promotionEndsAt = commerce?.promotionEndsAt ?? DEFAULT_PROMOTION_ENDS_AT
  const now = useNow(SECOND)
  const promotionEnd = Date.parse(promotionEndsAt)
  const promotionRunning = !Number.isNaN(promotionEnd) && promotionEnd > now

  function setBillingInterval(next: BillingInterval) {
    setInterval(next)
    trackBillingIntervalChanged(next)
  }

  function selectPlan(plan: Plan) {
    trackPlanSelected(plan.code, interval)
  }

  return (
    <section id="pricing" ref={sectionRef} className="pricing-section" aria-labelledby="pricing-title">
      <div className="pricing-heading">
        <span className="pricing-kicker">PRICING</span>
        <h2 id="pricing-title">Choose how deeply you want to monitor the market.</h2>
        <p>
          Start free, then upgrade when you need live market monitors, more quick scans,
          more alerts, and additional delivery options.
        </p>
      </div>

      <fieldset className="billing-toggle">
        <legend className="sr-only">Choose a billing interval</legend>
        {(['monthly', 'annual'] as const).map((value) => {
          // "Save up to $44" next to an interval nobody can buy is an offer that does
          // not exist. An unavailable interval remains visible for orientation but is
          // not an interactive control.
          const anyAvailable = plans.some((plan) => isAvailable(plan, value))
          // Computed from the prices beside it, never written out: a monthly price
          // changed on the server must not leave the toggle promising an old saving.
          const bestSaving = Math.max(
            0,
            ...plans
              .filter((plan) => isAvailable(plan, 'annual'))
              .map((plan) => (plan.monthlyPrice ?? 0) * 12 - (plan.annualPrice ?? 0)),
          )
          return (
            <label
              key={value}
              className={`${interval === value ? 'is-selected' : ''} ${
                anyAvailable ? '' : 'is-unavailable'
              }`}
            >
              <input
                type="radio"
                name="billing-interval"
                value={value}
                checked={interval === value}
                disabled={!anyAvailable}
                aria-disabled={!anyAvailable}
                onChange={() => setBillingInterval(value)}
              />
              <span>{value === 'monthly' ? 'Monthly' : 'Annual'}</span>
              {value === 'annual' && (
                <small>
                  {anyAvailable ? `Save up to $${bestSaving}` : DEFAULT_COMING_SOON_LABEL}
                </small>
              )}
            </label>
          )
        })}
      </fieldset>

      <p className="sr-only" role="status" aria-live="polite">
        Prices are shown for {interval === 'monthly' ? 'monthly' : 'annual'} billing.
      </p>
      <div className="pricing-grid">
        {plans.map((plan) => {
          const price = priceLabel(plan, interval, promotionRunning)
          const available = isAvailable(plan, interval)
          const expanded = expandedPlans.has(plan.code)
          const ctaLabel = !available
            ? `${plan.name} is coming soon`
            : plan.code === 'trader' && interval === 'annual'
              ? 'Choose Monitor'
              : plan.button
          const presentFeature = (feature: string) =>
            feature === 'WhatsApp delivery' && !whatsappOperational
              ? 'WhatsApp delivery - coming soon'
              : feature
          const additionalFeatures = plan.additionalFeatures.map(presentFeature)
          return (
            <article
              key={plan.code}
              // The apple-green accent marks the one plan to choose. A card that
              // cannot be bought is not that plan, so it loses the accent as well as
              // the badge.
              className={`pricing-card ${
                plan.code === 'trader' && available ? 'is-popular' : ''
              } ${available ? '' : 'is-coming-soon'}`}
              aria-labelledby={`plan-${plan.code}`}
            >
              <div className="plan-title-row">
                <h3 id={`plan-${plan.code}`}>{plan.name}</h3>
                {plan.badge && available && (
                  <span className="popular-badge">{plan.badge}</span>
                )}
                {!available && <span className="soon-badge">{price.kind === 'coming_soon' ? price.label : 'Soon'}</span>}
              </div>
              <div className="pricing-card-head">
                <p>{plan.description}</p>
              </div>
              {price.kind === 'coming_soon' ? (
                <div className="plan-price is-coming-soon">
                  <strong>{price.label}</strong>
                  <span>Not available yet</span>
                </div>
              ) : (
                <div className="plan-price tnum">
                  {price.original && (
                    <s className="plan-price-original" aria-label={`Was ${price.original} per month`}>
                      {price.original}
                    </s>
                  )}
                  <strong>{price.amount}</strong>
                  <span>{price.period}</span>
                </div>
              )}
              {/*
                Why the price above is the lower one. The launch price is not automatic:
                it is reached by typing this code, and without it the plan costs the
                crossed-out figure. This card is the only place on the public site where
                a visitor can learn that before they reach a payment page.

                Monthly only — the code is a monthly offer, and naming it under a yearly
                price would advertise a code that does nothing there.
              */}
              {price.kind === 'price' &&
                interval === 'monthly' &&
                promotionRunning &&
                plan.discountCode && (
                  <p className="price-code-note">
                    <span className="price-code-line">
                      Using code <code className="hm-code-chip">{plan.discountCode}</code>
                    </span>
                    <span>
                      Without it the price is ${plan.fullMonthlyPrice ?? plan.originalMonthlyPrice}{' '}
                      a month.
                    </span>
                  </p>
                )}
              {price.kind === 'price' && price.original && (
                <OfferCountdown endsAt={promotionEndsAt} now={now} />
              )}
              <p
                className={`annual-saving ${
                  interval === 'annual' && available && plan.annualPrice && plan.monthlyPrice
                    ? ''
                    : 'is-placeholder'
                }`}
                aria-hidden={interval !== 'annual' || !available || !plan.annualPrice}
              >
                {interval === 'annual' && available && plan.annualPrice && plan.monthlyPrice
                  ? `Save $${plan.monthlyPrice * 12 - plan.annualPrice} per year`
                  : '\u00a0'}
              </p>
              {plan.code === 'trader' && interval === 'monthly' && available && (
                <aside className="money-back-guarantee" aria-label="Refund policy">
                  <strong>7-day money-back guarantee</strong>
                  <span>
                    {plan.trialNote ?? 'Cancel within 7 days of payment for a full refund.'}
                  </span>
                </aside>
              )}
              {available ? (
                <a
                  href={checkoutHref(plan.code, interval)}
                  className="plan-cta"
                  data-plan={plan.code}
                  onClick={() => selectPlan(plan)}
                >
                  {ctaLabel}
                </a>
              ) : (
                <button type="button" className="plan-cta is-disabled" disabled data-plan={plan.code}>
                  {ctaLabel}
                </button>
              )}
              <ul className="plan-features">
                {plan.visibleFeatures.map((feature) => {
                  const presented = presentFeature(feature)
                  return (
                    <li
                      key={feature}
                      className={feature === plan.highlightedFeature ? 'is-highlighted' : ''}
                    >
                      <CheckIcon className="feature-check" />
                      <span>{presented}</span>
                    </li>
                  )
                })}
              </ul>
              <div id={`plan-${plan.code}-features`} hidden={!expanded}>
                <ul className="plan-features plan-features-extra">
                  {additionalFeatures.map((feature) => (
                    <li key={feature}>
                      <CheckIcon className="feature-check" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <button
                type="button"
                className="feature-expander"
                aria-expanded={expanded}
                aria-controls={`plan-${plan.code}-features`}
                onClick={() => {
                  setExpandedPlans((current) => {
                    const next = new Set(current)
                    if (next.has(plan.code)) next.delete(plan.code)
                    else next.add(plan.code)
                    return next
                  })
                }}
              >
                {expanded ? 'Show fewer features' : 'View all features'}
              </button>
            </article>
          )
        })}
      </div>

      <p className="pricing-trust">
        Every plan uses the same screening evidence. Paid plans add monitoring capacity,
        quick scans, alert volume, and delivery options - never greater religious certainty.
      </p>

      <section className="comparison-section" aria-labelledby="comparison-title">
        <div className="comparison-heading">
          <span className="pricing-kicker">COMPARE</span>
          <h3 id="comparison-title">Plan details at a glance</h3>
        </div>
        <div className="comparison-desktop">
          <table>
            <caption className="sr-only">Basic, Monitor, and Pro plan comparison</caption>
            <thead>
              <tr>
                <th scope="col">Feature</th>
                <th scope="col">Basic</th>
                <th scope="col">Monitor</th>
                <th scope="col">Pro</th>
              </tr>
            </thead>
            <tbody>
              {comparisonRows.map(([feature, explore, monitor, pro]) => (
                <tr key={feature}>
                  <th scope="row">{feature}</th>
                  <td>{explore}</td>
                  <td>{monitor}</td>
                  <td>{feature === 'WhatsApp' && whatsappOperational ? 'Included' : pro}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="comparison-mobile">
          {plans.map((plan, planIndex) => (
            <article key={plan.code}>
              <h4>{plan.name}</h4>
              <dl>
                {comparisonRows.map((row) => (
                  <div key={row[0]}>
                    <dt>{row[0]}</dt>
                    <dd>
                      {row[0] === 'WhatsApp' && plan.name === 'Pro' && whatsappOperational
                        ? 'Included'
                        : row[planIndex + 1]}
                    </dd>
                  </div>
                ))}
              </dl>
            </article>
          ))}
        </div>
      </section>
    </section>
  )
}
