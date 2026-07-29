import { useCallback, useState } from 'react'
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
  monthlyPrice: number
  annualPrice: number
  description: string
  button: string
  visibleFeatures: string[]
  additionalFeatures: string[]
  highlightedFeature?: string | null
  badge?: string | null
  trialNote?: string | null
}

const PLANS: Plan[] = [
  {
    code: 'demo',
    name: 'Explore',
    monthlyPrice: 0,
    annualPrice: 0,
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
    monthlyPrice: 12,
    annualPrice: 120,
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

function priceLabel(plan: Plan, interval: BillingInterval) {
  if (plan.monthlyPrice === 0) return { amount: '$0', period: 'Free forever' }
  return interval === 'annual'
    ? { amount: `$${plan.annualPrice}`, period: 'per year' }
    : { amount: `$${plan.monthlyPrice}`, period: 'per month' }
}

function checkoutHref(planCode: PublicPlanCode, interval: BillingInterval) {
  return `/subscribe?plan_code=${encodeURIComponent(planCode)}&billing_interval=${interval}`
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
        {(['monthly', 'annual'] as const).map((value) => (
          <label key={value} className={interval === value ? 'is-selected' : ''}>
            <input
              type="radio"
              name="billing-interval"
              value={value}
              checked={interval === value}
              onChange={() => setBillingInterval(value)}
            />
            <span>{value === 'monthly' ? 'Monthly' : 'Annual'}</span>
            {value === 'annual' && <small>Save up to $44</small>}
          </label>
        ))}
      </fieldset>

      <p className="sr-only" role="status" aria-live="polite">
        Prices are shown for {interval === 'monthly' ? 'monthly' : 'annual'} billing.
      </p>
      <div className="pricing-grid">
        {plans.map((plan) => {
          const price = priceLabel(plan, interval)
          const expanded = expandedPlans.has(plan.code)
          const ctaLabel =
            plan.code === 'trader' && interval === 'annual'
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
              className={`pricing-card ${plan.code === 'trader' ? 'is-popular' : ''}`}
              aria-labelledby={`plan-${plan.code}`}
            >
              <div className="plan-title-row">
                <h3 id={`plan-${plan.code}`}>{plan.name}</h3>
                {plan.badge && <span className="popular-badge">{plan.badge}</span>}
              </div>
              <div className="pricing-card-head">
                <p>{plan.description}</p>
              </div>
              <div className="plan-price tnum">
                <strong>{price.amount}</strong>
                <span>{price.period}</span>
              </div>
              <p
                className={`annual-saving ${
                  interval === 'annual' && plan.annualPrice > 0 ? '' : 'is-placeholder'
                }`}
                aria-hidden={interval !== 'annual' || plan.annualPrice === 0}
              >
                {interval === 'annual' && plan.annualPrice > 0
                  ? `Save $${plan.monthlyPrice * 12 - plan.annualPrice} per year`
                  : '\u00a0'}
              </p>
              <a
                href={checkoutHref(plan.code, interval)}
                className="plan-cta"
                data-plan={plan.code}
                onClick={() => selectPlan(plan)}
              >
                {ctaLabel}
              </a>
              {plan.code === 'trader' && interval === 'monthly' && (
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
