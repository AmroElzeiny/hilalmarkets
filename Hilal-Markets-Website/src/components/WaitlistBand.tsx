import { Reveal } from './Reveal'
import { TrackedCta } from './Tracking'

/**
 * The pre-launch closing section for the React pages that are not the landing page.
 *
 * Contact, Privacy and Terms are rendered by this app; Features, How It Works and the
 * rest are rendered by the server's own templates. Both halves close on the same
 * message, and both take the words from the server, so the site cannot end up half
 * pre-launch and half open.
 *
 * The form itself stays on the landing page. A second copy of it here would be a second
 * place to keep the consent box, the idempotency key and the error wording correct, and
 * one of the two would fall behind. This band links back to the one form instead.
 *
 * Brand rule 8 keeps the trading-bars pattern for the landing page's waitlist section,
 * so nothing here repeats it.
 */
export function WaitlistBand({ location }: { location: string }) {
  const config = window.HilalMarketsRuntimeConfig?.waitlist
  if (!config?.mode) return null

  const href = config.href || '/#waitlist'
  const ctaLabel = config.ctaLabel || 'Join the waitlist'

  return (
    <Reveal as="section" className="mx-auto max-w-[1200px] px-5 pb-24">
      <div className="rounded-[32px] bg-apple px-8 py-14 text-center sm:px-16">
        <div className="mx-auto max-w-[620px]">
          {config.eyebrow && (
            <p className="text-[13px] font-semibold uppercase tracking-[0.13em] text-[#2b2e35]/70">
              {config.eyebrow}
            </p>
          )}
          <h2 className="pt-3 font-display text-[26px] leading-[1.1] tracking-[-0.03em] text-ink sm:text-[34px]">
            {config.headline}
          </h2>
          {config.body && (
            <p className="mx-auto max-w-[520px] pt-4 text-[17px] leading-[1.6] text-[#2b2e35]">
              {config.body}
            </p>
          )}
          <TrackedCta
            href={href}
            analyticsName="join_waitlist"
            analyticsLocation={location}
            className="mt-8 inline-flex items-center justify-center rounded-full bg-[#2b2e35] px-8 py-4 text-[15px] font-semibold text-white transition-transform hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-ink"
          >
            {ctaLabel}
          </TrackedCta>
        </div>
      </div>
    </Reveal>
  )
}
