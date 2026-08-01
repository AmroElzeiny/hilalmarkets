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
  comingSoonLabel?: string
}

/** Server default, used only when the page is opened without runtime config. */
const DEFAULT_PROMOTION_ENDS_AT = '2026-09-01T00:00:00+00:00'
const DEFAULT_COMING_SOON_LABEL = 'Soon'

const PLANS: Plan[] = [
  {
    code: 'demo',
    name: 'Explore',
    monthlyPrice: 0,
    annualPrice: 0,
    monthlyAvailable: true,
    annualAvailable: false,
    description:
      'For traders who want to explore assets listed as Halal under a selected methodology, inspect the evidence, and follow changes to favorite coins.',
    button: 'Start free',
    highlightedFeature: 'Halal assets, methodologies, and evidence reports',
    visibleFeatures: [
      'Halal assets, methodologies, and evidence reports',
      'Full Evidence Passports',
      'Methodology reasons, sources, versions, and review dates',
      'Methodology comparison when available',
      'Favorite coins',
      'In-app Halal status-change alerts for favorites',
      'Telegram Halal status-change alerts for favorites',
    ],
    additionalFeatures: [
      'Published compliance-status changes',
      'Standard email support',
    ],
  },
  {
    code: 'trader',
    name: 'Monitor',
    monthlyPrice: 8,
    originalMonthlyPrice: 12,
    annualPrice: 120,
    monthlyAvailable: true,
    annualAvailable: false,
    description:
      'For regular traders who want AI-assisted market monitoring and clear evidence behind every alert.',
    button: 'Try Monitor for 7 days',
    badge: 'Most Popular',
    trialNote: 'No charge for seven days. Cancel before the first payment.',
    highlightedFeature: 'AI assistant for creating market monitors',
    visibleFeatures: [
      'Everything in Explore',
      'AI assistant for creating market monitors',
      '2 active market monitors',
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
  ['AI assistant', 'Not included', 'Included', 'Included'],
  ['Active market monitors', 'Not included', '2', '10'],
  ['Quick scans per month', 'Not included', '10', '100'],
  ['Monitor alerts per day', 'Not included', 'Up to 50', 'Unlimited'],
  ['Condition proof', 'Not included', 'Full', 'Full'],
  ['Opportunity Journeys', 'Not included', 'Complete', 'Complete'],
  ['Missed-alert investigations', 'Not included', 'Included', 'Included'],
  ['Telegram monitor delivery', 'Not included', 'Included', 'Included'],
  ['WhatsApp', 'Not included', 'Not included', 'Coming soon'],
  ['Monitor trial', 'Not included', '7 days, no payment', 'Not included'],
] as const

/** Can this plan be bought on this interval today? Missing means "yes", for older data. */
function isAvailable(plan: Plan, interval: BillingInterval) {
  const flag = interval === 'annual' ? plan.annualAvailable : plan.monthlyAvailable
  return flag !== false
}

type Price =
  | { kind: 'coming_soon'; label: string }
  | { kind: 'price'; amount: string; period: string; original?: string }

function priceLabel(plan: Plan, interval: BillingInterval): Price {
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
  return {
    kind: 'price',
    amount: `$${amount}`,
    period: 'per month',
    original:
      plan.originalMonthlyPrice && plan.originalMonthlyPrice > amount
        ? `$${plan.originalMonthlyPrice}`
        : undefined,
  }
}

function checkoutHref(planCode: PublicPlanCode, interval: BillingInterval) {
  return `/subscribe?plan_code=${encodeURIComponent(planCode)}&billing_interval=${interval}`
}

type Remaining = { days: number; hours: number; minutes: number } | null

function remainingUntil(endsAt: string, now: number): Remaining {
  const end = Date.parse(endsAt)
  if (Number.isNaN(end)) return null
  const ms = end - now
  if (ms <= 0) return null
  const minutes = Math.floor(ms / 60_000)
  return {
    days: Math.floor(minutes / 1440),
    hours: Math.floor((minutes % 1440) / 60),
    minutes: minutes % 60,
  }
}

/**
 * How long the launch price lasts, in days, hours and minutes.
 *
 * Deliberately quiet: it ticks once a minute rather than once a second, it does not
 * animate, and it says what it is next to the price instead of shouting a deadline. The
 * brand rules ask for calm, not urgency, so this states a fact and stops there.
 */
function OfferCountdown({ endsAt }: { endsAt: string }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  const left = remainingUntil(endsAt, now)
  if (!left) return null
  const parts: Array<[number, string]> = [
    [left.days, left.days === 1 ? 'day' : 'days'],
    [left.hours, left.hours === 1 ? 'hour' : 'hours'],
    [left.minutes, left.minutes === 1 ? 'minute' : 'minutes'],
  ]
  return (
    <div className="offer-countdown" role="group" aria-label="Time left at this price">
      <span className="offer-countdown-label">Launch price ends in</span>
      <span className="offer-countdown-parts tnum">
        {parts.map(([value, unit]) => (
          <span className="offer-countdown-part" key={unit}>
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
          // not exist. The toggle still switches, so a visitor can see what annual will
          // look like, and every card then says the same thing: soon.
          const anyAvailable = plans.some((plan) => isAvailable(plan, value))
          return (
            <label key={value} className={interval === value ? 'is-selected' : ''}>
              <input
                type="radio"
                name="billing-interval"
                value={value}
                checked={interval === value}
                onChange={() => setBillingInterval(value)}
              />
              <span>{value === 'monthly' ? 'Monthly' : 'Annual'}</span>
              {value === 'annual' && (
                <small>{anyAvailable ? 'Save up to $44' : DEFAULT_COMING_SOON_LABEL}</small>
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
          const price = priceLabel(plan, interval)
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
              {price.kind === 'price' && price.original && (
                <OfferCountdown endsAt={promotionEndsAt} />
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
              {plan.code === 'trader' && interval === 'monthly' && available && (
                <p className="trial-payment-note">
                  {plan.trialNote ?? 'No charge for seven days. Cancel before the first payment.'}
                </p>
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
            <caption className="sr-only">Explore, Monitor, and Pro plan comparison</caption>
            <thead>
              <tr>
                <th scope="col">Feature</th>
                <th scope="col">Explore</th>
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
          {(['Explore', 'Monitor', 'Pro'] as const).map((planName, planIndex) => (
            <article key={planName}>
              <h4>{planName}</h4>
              <dl>
                {comparisonRows.map((row) => (
                  <div key={row[0]}>
                    <dt>{row[0]}</dt>
                    <dd>
                      {row[0] === 'WhatsApp' && planName === 'Pro' && whatsappOperational
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
