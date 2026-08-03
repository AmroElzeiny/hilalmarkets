import type { ReactNode } from 'react'
import { Reveal } from '../components/Reveal'
import { SiteFooter, SiteNav } from '../components/SiteChrome'

type LegalKind = 'privacy' | 'terms'

type LegalSection = {
  id: string
  title: string
  content: ReactNode
}

const FALLBACK_EMAIL = 'office@hilalmarkets.com'

function BulletList({ children }: { children: ReactNode }) {
  return <ul className="mt-4 grid gap-3 text-[15px] leading-[1.7] text-[#50555e]">{children}</ul>
}

function Bullet({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-[#63716c]" aria-hidden="true" />
      <span>{children}</span>
    </li>
  )
}

function Paragraph({ children }: { children: ReactNode }) {
  return <p className="mt-4 text-[15px] leading-[1.75] text-[#50555e]">{children}</p>
}

function PrivacyContact({ email }: { email: string }) {
  return (
    <a
      className="font-medium text-[#2b2e35] underline decoration-[#63716c]/40 underline-offset-4"
      href={`mailto:${email}`}
    >
      {email}
    </a>
  )
}

function privacySections(privacyEmail: string): LegalSection[] {
  return [
    {
      id: 'scope',
      title: '1. Scope and status',
      content: (
        <>
          <Paragraph>
            This Privacy Policy explains how Hilal Markets handles personal information when you visit our public pages, join the waitlist, contact us, create or use an account, build Watchlists, review Shariah-screening evidence, receive alerts, or ask for support.
          </Paragraph>
          <Paragraph>
            The service is currently being prepared for private beta. Features and data practices may develop as the beta progresses. Material changes will be reflected in an updated policy and, where required, brought to your attention before they take effect.
          </Paragraph>
        </>
      ),
    },
    {
      id: 'controller',
      title: '2. Who is responsible',
      content: (
        <Paragraph>
          Hilal Markets is responsible for the personal information handled through this service.
          Privacy questions and rights requests may be sent to <PrivacyContact email={privacyEmail} />.
        </Paragraph>
      ),
    },
    {
      id: 'information',
      title: '3. Information we collect',
      content: (
        <BulletList>
          <Bullet><strong>Waitlist and contact details:</strong> email address, message title, message content, submission time, source page, and optional first-touch campaign details. Country may be recorded from a trusted edge-provider country header; we do not ask your browser to disclose precise location.</Bullet>
          <Bullet><strong>Account and security information:</strong> name, email, password hash, verification and recovery records, session data, consent choices, security events, and audit records.</Bullet>
          <Bullet><strong>Product information:</strong> Screened Watchlist selections, Watchlist descriptions and approved rules, strategy versions, scans, setup lifecycles, alert evidence, settings, and notification preferences.</Bullet>
          <Bullet><strong>Shariah-evidence interactions:</strong> methodologies and Passport versions viewed or attached to evaluations. AI may support factual research, but it does not issue Shariah decisions.</Bullet>
          <Bullet><strong>Communications:</strong> support requests, public-assistant feedback, delivery status, and connected-channel identifiers needed to send requested notifications.</Bullet>
          <Bullet><strong>Technical information:</strong> timestamps, page paths, browser or device category, security logs, provider errors, and limited diagnostics needed to operate and protect the service.</Bullet>
          <Bullet><strong>Optional analytics and advertising measurement:</strong> consented page views, section views, CTA interactions, normalized form outcomes, referrer, and campaign parameters. We do not send your email, password, message text, strategy text, or other form values to Google Analytics, Meta Pixel, or X Pixel.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'sources',
      title: '4. Where information comes from',
      content: (
        <Paragraph>
          Most information comes directly from you. We may also receive limited records from authentication, email, analytics, messaging, market-data, infrastructure, and security providers when they perform services on our behalf. Market and Shariah evidence can come from published third-party sources, but these records are not treated as your personal financial holdings.
        </Paragraph>
      ),
    },
    {
      id: 'purposes',
      title: '5. Why we use information',
      content: (
        <BulletList>
          <Bullet>Provide accounts, approved Watchlists, market monitoring, evidence Passports, lifecycle records, alerts, and support.</Bullet>
          <Bullet>Verify identity, preserve immutable approvals and evidence, prevent abuse, investigate incidents, and maintain service integrity.</Bullet>
          <Bullet>Respond to waitlist and contact requests and deliver communications you asked to receive.</Bullet>
          <Bullet>Understand consented public-site usage and improve product reliability and usability.</Bullet>
          <Bullet>Meet legal, regulatory, accounting, dispute, and security obligations that apply to us.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'legal-bases',
      title: '6. Legal bases',
      content: (
        <Paragraph>
          Depending on your location and the activity, processing may rely on your consent, steps taken at your request, performance of our agreement, legitimate interests in operating and securing the service, or compliance with law. Optional analytics and marketing technologies remain off until the relevant consent is granted, and consent can be changed later.
        </Paragraph>
      ),
    },
    {
      id: 'ai',
      title: '7. AI-assisted features',
      content: (
        <>
          <Paragraph>
            AI helps interpret user-authored monitoring ideas, explain product information, and support bounded factual research. Executable strategy logic is validated by deterministic services, and you must review and approve material strategy changes. AI cannot activate a Watchlist, execute a trade, or issue a Shariah ruling on its own.
          </Paragraph>
          <Paragraph>
            Relevant conversation context and structured product state may be sent to configured AI providers when an AI feature is used. We limit tools and data to the current task, apply ownership checks, and do not intentionally send passwords, authentication codes, exchange credentials, or unrelated account data.
          </Paragraph>
        </>
      ),
    },
    {
      id: 'cookies',
      title: '8. Cookies, analytics, and marketing',
      content: (
        <>
          <Paragraph>
            Essential storage supports security and core page behavior. Functional storage may remember preferences you choose. Google Analytics can load only after Analytics consent. Meta Pixel and X Pixel can load only after Marketing consent. Denied consent is the default where supported.
          </Paragraph>
          <Paragraph>
            Waitlist submission works independently of optional analytics. A successful server-confirmed signup may produce a consented lead event without the submitted email or other personal form content. Failed or duplicate submissions do not create a new conversion event.
          </Paragraph>
        </>
      ),
    },
    {
      id: 'sharing',
      title: '9. Sharing and service providers',
      content: (
        <Paragraph>
          We may share only the information needed with providers that support hosting, databases, email, authentication, AI, market data, analytics, security, and connected notification channels. We may also disclose information when required by law, to protect users or the service, or as part of a properly governed business reorganization. We do not sell personal information. Provider access does not authorize them to use private product data for unrelated purposes.
        </Paragraph>
      ),
    },
    {
      id: 'transfers',
      title: '10. International transfers',
      content: (
        <Paragraph>
          Service providers may process information in countries different from your own. Where transfer safeguards are legally required, we use an appropriate mechanism and assess relevant provider protections. You may contact us for more information about safeguards relevant to your personal information.
        </Paragraph>
      ),
    },
    {
      id: 'retention',
      title: '11. Retention',
      content: (
        <Paragraph>
          We keep information only as long as needed for the purpose collected, account operation, immutable strategy and alert evidence, security, support, legal obligations, and dispute handling. Retention periods differ by record. Bounded technical logs and detailed evaluation data may expire sooner, while approved strategy versions, Passport history, alert proof, and related audit records may be retained longer to preserve an accurate historical record. Data may be de-identified or deleted when no longer needed.
        </Paragraph>
      ),
    },
    {
      id: 'security',
      title: '12. Security',
      content: (
        <Paragraph>
          We use access controls, authentication, server-side authorization, encryption in transit, audit logging, redaction, rate limiting, idempotency, and fail-closed product boundaries appropriate to the service. No method is completely secure. Please use a unique password and never submit passwords, wallet secrets, access codes, or exchange API keys through public forms or chat.
        </Paragraph>
      ),
    },
    {
      id: 'choices',
      title: '13. Your choices and rights',
      content: (
        <BulletList>
          <Bullet>Change optional cookie and analytics choices through the preference controls.</Bullet>
          <Bullet>Update available account and notification settings or disconnect supported channels.</Bullet>
          <Bullet>Ask to access, correct, delete, restrict, or export personal information where applicable.</Bullet>
          <Bullet>Object to certain processing or withdraw consent without affecting processing already completed lawfully.</Bullet>
          <Bullet>Complain to a competent data-protection authority where that right applies.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'children',
      title: '14. Children',
      content: <Paragraph>Hilal Markets is not intended for children. Do not use the service if you are under 18 or below the minimum age required to enter a binding agreement where you live.</Paragraph>,
    },
    {
      id: 'contact',
      title: '15. Contact and updates',
      content: (
        <Paragraph>
          For privacy questions or rights requests, email <PrivacyContact email={privacyEmail} />. We may ask for information needed to verify a request and protect the account. The Last updated date changes when this policy is revised.
        </Paragraph>
      ),
    },
  ]
}

function termsSections(supportEmail: string): LegalSection[] {
  return [
    {
      id: 'agreement',
      title: '1. Agreement and provider',
      content: (
        <>
          <Paragraph>These Terms govern access to Hilal Markets and its public pages, private-beta product, research tools, Watchlists, evidence Passports, monitoring, alerts, and related services. By creating an account or using the service, you agree to these Terms and the related Privacy Policy, Cookie Policy, and Risk Disclosure.</Paragraph>
          <Paragraph>Hilal Markets provides the service described in these Terms. Questions about the service or these Terms may be sent to <PrivacyContact email={supportEmail} />.</Paragraph>
        </>
      ),
    },
    {
      id: 'eligibility',
      title: '2. Eligibility and accounts',
      content: (
        <BulletList>
          <Bullet>You must be at least 18 and legally able to enter this agreement.</Bullet>
          <Bullet>Account information must be accurate and kept current.</Bullet>
          <Bullet>You are responsible for protecting credentials and promptly reporting suspected unauthorized access.</Bullet>
          <Bullet>You may not share an account to avoid security, access, or entitlement limits.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'service',
      title: '3. What Hilal Markets provides',
      content: <Paragraph>Hilal Markets is an explainable crypto spot research and monitoring platform. It can help you review Shariah-screening evidence, translate your own setup into measurable rules, run a one-time market check, continuously monitor an approved Watchlist, follow setup lifecycles, and receive evidence-backed notifications. Private-beta scope, supported assets, providers, and channels may be limited.</Paragraph>,
    },
    {
      id: 'boundaries',
      title: '4. Important product boundaries',
      content: (
        <BulletList>
          <Bullet>Hilal Markets is not an exchange, broker, custodian, investment adviser, portfolio manager, or trade-execution service.</Bullet>
          <Bullet>It does not hold funds, connect exchange trading credentials, or place trades.</Bullet>
          <Bullet>Monitoring results and alerts are not personalized buy or sell recommendations and do not promise profit or prevent loss.</Bullet>
          <Bullet>AI can assist with explanation and structure, but cannot silently approve rules, activate Watchlists, or make authoritative market or Shariah decisions.</Bullet>
          <Bullet>You remain responsible for reviewing every strategy, decision, transaction, and legal or religious obligation relevant to you.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'shariah',
      title: '5. Shariah screening and evidence',
      content: <Paragraph>Shariah statuses are based on the identified methodology, reviewed evidence, asset identity, scope, qualifications, and published Passport version. Status can change as evidence or methodology changes. A status is not a personal fatwa, does not guarantee agreement among scholars, and does not automatically cover every possible use of an asset. Qualifications, disputed matters, stale evidence, market availability, and methodology limits should be reviewed before relying on a status.</Paragraph>,
    },
    {
      id: 'plans',
      title: '6. Watchlists, Scanner, and AI',
      content: (
        <>
          <Paragraph>You provide the monitoring idea. Hilal Markets may ask clarifying questions, match approved capabilities, and compile a deterministic rule set for your review. You must resolve critical ambiguity and explicitly approve executable mechanics before activation. An approved live version is not silently changed; revisions create traceable versions and require renewed approval.</Paragraph>
          <Paragraph>Check the Market Now is a one-time scan and does not create persistent alerts unless you convert and approve the tested configuration as a Watchlist. Custom capabilities remain bounded by provider, validation, certification, quarantine, and approval controls.</Paragraph>
        </>
      ),
    },
    {
      id: 'data',
      title: '7. Market data and evidence limitations',
      content: <Paragraph>Prices, candles, indicators, asset mappings, source documents, and provider status may be delayed, corrected, incomplete, stale, unavailable, or inconsistent. Hilal Markets uses provider attribution and fail-closed controls where required, but cannot guarantee uninterrupted or error-free third-party data. Historical previews and examples do not predict future behavior or performance.</Paragraph>,
    },
    {
      id: 'alerts',
      title: '8. Alerts and availability',
      content: <Paragraph>Alerts depend on approved rules, market data, worker availability, provider limits, cooldowns, exclusions, account settings, and connected notification channels. Alerts can be delayed, suppressed, duplicated by external providers, or fail to arrive. The dashboard and immutable proof records are the authoritative record where available. You should not rely on Hilal Markets for urgent, safety-critical, or time-guaranteed communications.</Paragraph>,
    },
    {
      id: 'acceptable-use',
      title: '9. Acceptable use',
      content: (
        <BulletList>
          <Bullet>Do not probe other accounts, evade access controls or limits, scrape protected data, or interfere with service operation.</Bullet>
          <Bullet>Do not submit malware, prompt injection intended to escape product boundaries, unlawful content, or credentials and secrets the service does not request.</Bullet>
          <Bullet>Do not misrepresent alerts, Passports, evidence, or Hilal Markets branding as guaranteed advice, a personal ruling, or an endorsement.</Bullet>
          <Bullet>Do not use the service to violate law, third-party rights, exchange terms, sanctions, or market-integrity rules.</Bullet>
        </BulletList>
      ),
    },
    {
      id: 'content',
      title: '10. Your content and privacy',
      content: <Paragraph>You retain responsibility for setup descriptions, Watchlist names, rules, support messages, and other content you submit. You grant us the limited permission needed to host, process, validate, display, and transmit that content to provide and secure the service. We do not acquire ownership of your private strategies. Personal information is handled under the Privacy Policy.</Paragraph>,
    },
    {
      id: 'ownership',
      title: '11. Hilal Markets materials',
      content: <Paragraph>The product, software, branding, design, documentation, curated methodology presentation, and non-user content are owned by Hilal Markets or its licensors and protected by applicable law. These Terms provide a limited, revocable, non-transferable right to use the service; they do not transfer intellectual-property ownership.</Paragraph>,
    },
    {
      id: 'third-parties',
      title: '12. Third-party services',
      content: <Paragraph>The service may depend on exchanges, market-data providers, official source sites, AI providers, messaging channels, analytics providers, cloud infrastructure, and email services. Their own terms and privacy practices may apply. A link, integration, or cited source does not imply control or endorsement by Hilal Markets.</Paragraph>,
    },
    {
      id: 'beta',
      title: '13. Private-beta changes',
      content: <Paragraph>During private beta, functionality may be introduced, restricted, repaired, or withdrawn as reliability and safety are evaluated. We may set participation limits, suspend unstable capabilities, reset non-essential test data, or end beta access. We will not use a product change to silently alter an approved strategy version or historical evidence record.</Paragraph>,
    },
    {
      id: 'billing',
      title: '14. Plans and billing',
      content: <Paragraph>Billing is disabled for the initial private beta unless a separately presented offer expressly states otherwise. If paid access is introduced, the checkout must disclose the price, currency, access period, renewal behavior, plan limits, cancellation behavior, and applicable refund terms before payment. Provider capability and verified payment state control entitlement; these Terms do not invent automatic renewal.</Paragraph>,
    },
    {
      id: 'suspension',
      title: '15. Suspension and termination',
      content: <Paragraph>You may stop using the service at any time. We may restrict or suspend access to protect users, evidence integrity, providers, security, or legal compliance, or when these Terms are materially breached. Where practical, we will explain the reason and available next step. Records that must remain immutable for security, evidence, legal, or dispute purposes may be retained as described in the Privacy Policy.</Paragraph>,
    },
    {
      id: 'risk',
      title: '16. Risk and disclaimers',
      content: <Paragraph>Crypto assets are volatile and can lose substantial value. Screening eligibility, historical behavior, lifecycle readiness, condition matches, alerts, AI explanations, and provider data do not establish suitability or future results. To the extent permitted by law, the service is provided on an as-available basis without guarantees of uninterrupted availability, market accuracy, alert timing, profitability, or agreement among Shariah authorities.</Paragraph>,
    },
    {
      id: 'liability',
      title: '17. Responsibility and liability',
      content: <Paragraph>You are responsible for decisions made using the service and for verifying information appropriate to your circumstances. To the extent permitted by applicable law, Hilal Markets is not responsible for indirect, incidental, special, or consequential loss arising from use of, or inability to use, the service or third-party data and delivery providers. Nothing in these Terms excludes rights or liability that cannot lawfully be excluded.</Paragraph>,
    },
    {
      id: 'law',
      title: '18. Disputes and mandatory rights',
      content: <Paragraph>If a concern arises, please contact us first at <PrivacyContact email={supportEmail} /> so we can try to resolve it directly. Any mandatory consumer protections, statutory rights, and jurisdictional rules that apply to you remain unaffected by these Terms.</Paragraph>,
    },
    {
      id: 'changes',
      title: '19. Changes and contact',
      content: <Paragraph>We may update these Terms as the private beta and legal framework develop. Material changes will be communicated where required. Questions can be sent to <PrivacyContact email={supportEmail} />.</Paragraph>,
    },
  ]
}

export default function LegalPage({ kind }: { kind: LegalKind }) {
  const config = window.HilalMarketsRuntimeConfig?.legal ?? {}
  const email = (kind === 'privacy' ? config.privacyEmail : config.supportEmail) || FALLBACK_EMAIL
  const isPrivacy = kind === 'privacy'
  const sections = isPrivacy
    ? privacySections(email)
    : termsSections(email)
  const highlights = isPrivacy
    ? ['Consent before optional analytics', 'No form content in ad analytics', 'Clear choices and requests']
    : ['Research and monitoring only', 'No trade execution', 'You approve executable changes']

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main className="overflow-hidden pt-32 sm:pt-36">
        <section className="relative mx-auto max-w-[1200px] px-5 pb-14 pt-6 sm:pb-20">
          <div className="pointer-events-none absolute left-1/2 top-[-100px] -z-10 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-white/80 blur-3xl" aria-hidden="true" />
          <Reveal>
            <div className="max-w-[840px]">
              <p className="text-[13px] font-medium text-[#55712a]">Legal and product transparency</p>
              <h1 className="mt-4 font-display text-[38px] font-medium leading-[1.04] tracking-[-0.035em] text-[#2b2e35] sm:text-[54px]">
                {isPrivacy ? 'Privacy Policy' : 'Terms of Use'}
              </h1>
              <p className="max-w-[720px] pt-4 text-[17px] leading-[1.7] text-[#50555e]">
                {isPrivacy
                  ? 'A plain-language record of what Hilal Markets collects, why it is needed, and the choices that remain with you.'
                  : 'The practical rules and boundaries for using Hilal Markets during private beta.'}
              </p>
              <div className="mt-7 flex flex-wrap gap-2">
                {highlights.map((item) => (
                  <span key={item} className="rounded-full border border-[#d9dfe4] bg-white px-4 py-2 text-[13px] text-[#2b2e35] shadow-[0_12px_28px_-24px_rgba(43,46,53,0.55)]">
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </Reveal>
        </section>

        <section className="mx-auto grid max-w-[1200px] gap-8 px-5 py-14 lg:grid-cols-[280px_minmax(0,1fr)] lg:items-start lg:py-20">
          <Reveal className="lg:sticky lg:top-28">
            <nav aria-label={`${isPrivacy ? 'Privacy Policy' : 'Terms of Use'} sections`} className="rounded-[24px] border border-[#e1e5ea] bg-white p-4 shadow-[0_26px_60px_-50px_rgba(43,46,53,0.6)]">
              <p className="px-3 pb-3 text-[12px] font-medium text-[#63716c]">On this page</p>
              <div className="max-h-[52vh] space-y-1 overflow-y-auto pr-1 lg:max-h-[62vh]">
                {sections.map((section) => (
                  <a key={section.id} href={`#${section.id}`} className="block rounded-[12px] px-3 py-2.5 text-[13px] leading-snug text-[#50555e] transition-colors hover:bg-[#f5f8fb] hover:text-[#2b2e35] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#63716c]">
                    {section.title}
                  </a>
                ))}
              </div>
            </nav>
          </Reveal>

          <div className="space-y-5">
            {sections.map((section, index) => (
              <Reveal key={section.id} delay={Math.min(index * 25, 175)}>
                <article id={section.id} className="scroll-mt-28 rounded-[24px] border border-[#e1e5ea] bg-white p-6 shadow-[0_24px_60px_-50px_rgba(43,46,53,0.55)] sm:p-8">
                  <h2 className="font-display text-[23px] font-medium tracking-[-0.02em] text-[#2b2e35] sm:text-[27px]">{section.title}</h2>
                  {section.content}
                </article>
              </Reveal>
            ))}
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
