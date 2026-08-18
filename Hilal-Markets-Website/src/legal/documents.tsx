/**
 * The Privacy Policy and the Terms of Use.
 *
 * Rewritten in two ways at once.
 *
 * **Plain language.** The audience for this product is beginners, and the old text was
 * written for lawyers: ninety-word sentences, and words like "notwithstanding" and
 * "de-identified". Every section now opens with **In short** — one or two sentences a
 * person can read and stop at — and the full clause sits under it for anyone who needs
 * the precise wording. The clause is still the agreement; the summary is a way in, not
 * a replacement, and the page says so.
 *
 * **Live behaviour.** The old text described a closed private beta: accounts by
 * invitation only, and no paid plan of any kind. That is no longer what the product
 * does, and a policy that describes something else is not a smaller problem than no
 * policy. Both documents now describe the service as it runs, including the parts that
 * are genuinely limited, said plainly rather than dressed up.
 *
 * Nothing here decides how open the product is. That is one server setting,
 * `LAUNCH_STAGE`, and the header, the footer and the assistant all read it. These
 * documents describe the rules of using the service, which are the same rules whoever
 * is let in — so a change of stage never leaves the legal text behind.
 */
import type { ReactNode } from 'react'
import type { IconName } from '../components/Icon'

export type LegalKind = 'privacy' | 'terms' | 'cookies'

export type LegalSection = {
  id: string
  title: string
  icon: IconName
  /** One or two plain sentences. What a beginner needs and nothing more. */
  short: string
  body: ReactNode
}

export type LegalDocument = {
  kind: LegalKind
  title: string
  lede: string
  /** The date this wording took effect. Shown, because the text refers to it. */
  updated: string
  version: string
  highlights: { icon: IconName; label: string; note: string }[]
  sections: LegalSection[]
}

const UPDATED = '17 August 2026'

/* -------------------------------------------------------------------------- */
/*  Shared pieces                                                             */
/* -------------------------------------------------------------------------- */
function P({ children }: { children: ReactNode }) {
  return <p>{children}</p>
}

function Mail({ address }: { address: string }) {
  return (
    <a
      href={`mailto:${address}`}
      className="font-semibold text-ink underline decoration-[#828b96] underline-offset-4 hover:decoration-ink"
    >
      {address}
    </a>
  )
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      className="font-semibold text-ink underline decoration-[#828b96] underline-offset-4 hover:decoration-ink"
    >
      {children}
    </a>
  )
}

/* -------------------------------------------------------------------------- */
/*  Privacy                                                                    */
/* -------------------------------------------------------------------------- */
export function privacyDocument(email: string): LegalDocument {
  return {
    kind: 'privacy',
    title: 'Privacy Policy',
    lede:
      'What we collect, why we need it, how long we keep it, and what you can ask us to do with it. Written to be read, not to be survived.',
    updated: UPDATED,
    version: '2.0',
    highlights: [
      {
        icon: 'shield',
        label: 'We never sell your data',
        note: 'Not to anyone, for any price.',
      },
      {
        icon: 'cookie',
        label: 'Tracking is off until you allow it',
        note: 'Analytics and advertising tools stay switched off by default.',
      },
      {
        icon: 'lock',
        label: 'Your strategies stay yours',
        note: 'They are private and are never published or shared.',
      },
      {
        icon: 'user',
        label: 'You can ask us to delete it',
        note: 'And to show you, correct it, or send you a copy.',
      },
    ],
    sections: [
      {
        id: 'what-this-covers',
        title: 'What this policy covers',
        icon: 'book',
        short:
          'This explains how we handle your personal information when you use Hilal Markets — the public website, an account, and anything you send us.',
        body: (
          <>
            <P>
              This policy applies to the Hilal Markets website, to an account and everything
              you do with it, and to messages you send us. That includes building
              Watchlists, reading Shariah screening evidence, receiving alerts, and asking
              for help.
            </P>
            <P>
              If we change something important, we will update this page and, where the law
              requires it, tell you before the change takes effect. The date at the top
              always shows when this wording began.
            </P>
          </>
        ),
      },
      {
        id: 'who-we-are',
        title: 'Who is responsible',
        icon: 'user',
        short:
          'Hilal Markets is responsible for your information. Write to us and a person will answer.',
        body: (
          <P>
            Hilal Markets decides how and why your personal information is handled, which
            in data-protection law makes us the controller of it. Questions and requests go
            to <Mail address={email} />, or through the{' '}
            <Link href="/contact">contact page</Link>.
          </P>
        ),
      },
      {
        id: 'what-we-collect',
        title: 'What we collect',
        icon: 'list',
        short:
          'Your email and account details, the Watchlists and rules you create, the alerts we send you, messages you write to us, and basic technical records needed to run and protect the service.',
        body: (
          <>
            <P>
              <strong>Your account.</strong> Name, email address, an encrypted form of your
              password that cannot be turned back into the password, sign-in records,
              security events, and the choices you have made in your settings.
            </P>
            <P>
              <strong>What you build.</strong> The Watchlists you create, the rules you
              wrote and approved, the versions of them over time, the scans that ran, the
              setups that formed, and the evidence attached to every alert.
            </P>
            <P>
              <strong>What you read.</strong> Which screening methodologies and Evidence
              Passport versions you opened or attached to an evaluation.
            </P>
            <P>
              <strong>Messages.</strong> What you write to support, when you wrote it, and
              the address to reply to. We also keep a short scrambled record of your address
              and your browser session so we can count how many messages have been sent and
              stop one machine flooding the queue. That record cannot be turned back into an
              address.
            </P>
            <P>
              <strong>Delivery.</strong> Where you asked alerts to go — email, Telegram —
              and whether each one arrived.
            </P>
            <P>
              <strong>Technical records.</strong> Times, page addresses, the general kind of
              browser or device, security logs, and errors from the services we depend on.
              We may record the country your request came from, when our hosting provider
              tells us. We never ask your browser for your exact location.
            </P>
            <P>
              <strong>Optional measurement.</strong> Only if you allow it: page views,
              which sections you saw, which buttons you pressed, and where you arrived from.
              We never send your email address, your password, the words of your message, or
              your strategy text to Google Analytics, Meta Pixel or X Pixel.
            </P>
          </>
        ),
      },
      {
        id: 'never-send',
        title: 'What we never ask for',
        icon: 'lock',
        short:
          'Never send us a password, a wallet recovery phrase, a private key, an exchange API key, or a full card number. We do not need any of them.',
        body: (
          <>
            <P>
              Hilal Markets does not connect to your exchange account, does not hold your
              money, and does not place orders. So there is no situation in which we need a
              trading key or a wallet secret, and nobody from Hilal Markets will ever ask
              you for one.
            </P>
            <P>
              If a message asking for one appears to come from us, it did not. Do not reply
              to it, and tell us at <Mail address={email} />. If you have already sent a
              secret to anyone, change it now.
            </P>
          </>
        ),
      },
      {
        id: 'why-we-use-it',
        title: 'Why we use it',
        icon: 'compliance',
        short:
          'To run your account and your monitoring, to answer you, to keep the service safe, and to meet the law. Nothing else.',
        body: (
          <>
            <P>We use your information to:</P>
            <ul className="hm-legal-list">
              {[
                'Run your account, your Watchlists, the monitoring, the evidence and the alerts.',
                'Send the notifications you asked for, to the channels you chose.',
                'Answer your messages and keep a record of what was asked and answered.',
                'Keep an honest history: an approved rule and the proof behind an alert must not change afterwards.',
                'Spot and stop abuse, fraud and attacks on the service.',
                'Meet legal, accounting and dispute obligations that apply to us.',
                'Understand how the public website is used, but only if you allowed measurement.',
              ].map((item) => (
                <li key={item}>
                  <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m5 12 5 5L20 7" />
                  </svg>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </>
        ),
      },
      {
        id: 'legal-basis',
        title: 'What allows us to use it',
        icon: 'scale',
        short:
          'Either you asked for it, or we need it to give you the service, or the law requires it, or we have a fair reason such as keeping the service safe.',
        body: (
          <>
            <P>
              Depending on where you live and what is happening, we rely on one of these:
              your agreement to a service you signed up for, a step you asked us to take,
              your consent, a legal duty, or a fair interest in running and protecting the
              service.
            </P>
            <P>
              Optional measurement and advertising tools rely on your consent alone. They
              stay off until you allow them, and you can change your mind at any time
              without losing anything else.
            </P>
          </>
        ),
      },
      {
        id: 'ai',
        title: 'How AI is used',
        icon: 'workflow',
        short:
          'AI helps turn your words into clear rules and explains things. It cannot approve a rule, start monitoring, place a trade, or decide a Shariah status.',
        body: (
          <>
            <P>
              When you describe a setup, AI helps put it into measurable rules and asks you
              a question when something is unclear. You read the result and approve it. The
              rules that actually run are checked by our own code, not by the model.
            </P>
            <P>
              <strong>AI is never the authority.</strong> It cannot switch a Watchlist on,
              change a rule you already approved, buy or sell anything, or decide whether an
              asset is acceptable under Shariah. Those decisions belong to you, to our own
              code, and to our review process.
            </P>
            <P>
              When you use an AI feature, the part of the conversation it needs and the
              related product information are sent to the AI provider we have configured. We
              limit what is sent to the task at hand. We do not send passwords, sign-in
              codes, exchange keys, or data from other people&rsquo;s accounts.
            </P>
          </>
        ),
      },
      {
        id: 'cookies',
        title: 'Cookies and measurement',
        icon: 'cookie',
        short:
          'Some storage is needed to keep you signed in and safe. Everything else is off until you say yes, and you can change your answer whenever you like.',
        body: (
          <>
            <P>
              <strong>Necessary storage</strong> keeps you signed in and protects the site
              from attacks. It cannot be switched off, because without it the site does not
              work.
            </P>
            <P>
              <strong>Optional storage</strong> remembers preferences you chose. Google
              Analytics loads only after you allow analytics. Meta Pixel and X Pixel load
              only after you allow marketing. Until then they are not loaded at all.
            </P>
            <P>
              Joining a waitlist or sending us a message works exactly the same whether or
              not you allowed measurement. If you did allow it, a successful signup may
              record that a signup happened — never your email address or the words you
              wrote. The <Link href="/cookies">Cookie Policy</Link> lists what each one does.
            </P>
          </>
        ),
      },
      {
        id: 'sharing',
        title: 'Who else sees it',
        icon: 'integrations',
        short:
          'Only the companies that help us run the service, and only the part they need. We never sell your information.',
        body: (
          <>
            <P>
              We use other companies for hosting, databases, email, sign-in, AI, market
              data, measurement, security, and the channels you asked alerts to go to. Each
              one gets only what it needs to do its job, and none of them is allowed to use
              it for anything else.
            </P>
            <P>
              We may also share information when the law requires it, when it is needed to
              protect people or the service, or if the business is ever reorganised — and in
              that last case, the protections in this policy travel with it.
            </P>
            <P>
              <strong>We do not sell your personal information</strong>, and we do not trade
              it for advertising.
            </P>
          </>
        ),
      },
      {
        id: 'where',
        title: 'Where it is kept',
        icon: 'globe',
        short:
          'Some of the companies we use are in other countries. When information moves, we use the legal protections required for that move.',
        body: (
          <P>
            The companies that help us run the service may handle information in a country
            different from your own. Where the law requires a safeguard for that, we put one
            in place and check the protections that provider offers. Ask us and we will tell
            you which safeguards apply to you.
          </P>
        ),
      },
      {
        id: 'how-long',
        title: 'How long we keep it',
        icon: 'clock',
        short:
          'As long as your account is open, and then only what we still need. Some records — an approved rule, the proof behind an alert — are kept longer because changing them would make our own history untrue.',
        body: (
          <>
            <P>
              <strong>While you have an account,</strong> we keep what the account needs.
            </P>
            <P>
              <strong>After you close it,</strong> we delete or scramble what we no longer
              need. We keep what we must for security, accounting, and any dispute — and we
              keep it for no longer than that requires.
            </P>
            <P>
              <strong>Some records are deliberately permanent.</strong> The rule you
              approved, the version history of it, and the evidence attached to an alert
              cannot be edited afterwards. That is the point of them: a record that can be
              changed later is not proof of anything. They are kept for as long as the
              account history needs them.
            </P>
            <P>
              <strong>Short-lived records</strong> — technical logs, detailed evaluation
              data, the counts that limit support messages — expire on their own, usually in
              days or weeks.
            </P>
          </>
        ),
      },
      {
        id: 'security',
        title: 'How we protect it',
        icon: 'shield_check',
        short:
          'Encrypted connections, checks on every request, records of who did what, and limits that stop one machine flooding the service. No system is perfect, and we say so.',
        body: (
          <>
            <P>
              We use encrypted connections, check on the server that you are allowed to see
              what you asked for, keep an audit record of important actions, hide sensitive
              values from our own logs, and limit how often an action can be repeated. Where
              a check cannot be made safely, the product refuses rather than guesses.
            </P>
            <P>
              No method of protection is perfect and nobody can promise otherwise. Please
              use a password you use nowhere else, and turn on any extra sign-in protection
              we offer.
            </P>
            <P>
              <strong>If something goes wrong,</strong> and personal information is exposed
              in a way that puts you at risk, we will tell the relevant authority and the
              people affected within the time the law allows, and we will say what happened
              and what to do about it.
            </P>
          </>
        ),
      },
      {
        id: 'your-choices',
        title: 'What you can ask us to do',
        icon: 'hand',
        short:
          'Show you what we hold, correct it, delete it, send you a copy, or stop using it. Ask through the contact page and we will act.',
        body: (
          <>
            <P>Depending on where you live, you can ask us to:</P>
            <ul className="hm-legal-list">
              {[
                'Show you the personal information we hold about you.',
                'Correct anything that is wrong.',
                'Delete it, where we do not have to keep it.',
                'Send you a copy in a form you can take elsewhere.',
                'Stop or limit a particular use of it.',
                'Withdraw a consent you gave, without losing anything you already have.',
              ].map((item) => (
                <li key={item}>
                  <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m5 12 5 5L20 7" />
                  </svg>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <P>
              Ask through the <Link href="/contact">contact page</Link> or at{' '}
              <Mail address={email} />. We may need to check it really is you before we act
              — acting on somebody else&rsquo;s word about your account would be the larger
              risk. You can also complain to the data-protection authority where you live.
            </P>
          </>
        ),
      },
      {
        id: 'children',
        title: 'Age',
        icon: 'flag',
        short: 'Hilal Markets is for adults. Do not use it if you are under 18.',
        body: (
          <P>
            This service is not intended for children. Do not use it if you are under 18, or
            under the age at which you can enter a binding agreement where you live. If we
            learn that an account belongs to a child, we will close it and delete what we
            hold.
          </P>
        ),
      },
      {
        id: 'contact-privacy',
        title: 'Getting in touch',
        icon: 'mail',
        short: 'Write to us with any question about this policy, and a person will answer.',
        body: (
          <P>
            Email <Mail address={email} /> or use the{' '}
            <Link href="/contact">contact page</Link>. Please do not include passwords,
            recovery codes, wallet secrets or exchange keys in your message — see{' '}
            <Link href="/privacy#never-send">What we never ask for</Link> above.
          </P>
        ),
      },
    ],
  }
}

/* -------------------------------------------------------------------------- */
/*  Terms                                                                      */
/* -------------------------------------------------------------------------- */
export function termsDocument(email: string): LegalDocument {
  return {
    kind: 'terms',
    title: 'Terms of Use',
    lede:
      'The rules for using Hilal Markets: what it does, what it will never do, and what each of us is responsible for.',
    updated: UPDATED,
    version: '2.0',
    highlights: [
      {
        icon: 'market',
        label: 'We never place trades',
        note: 'No orders, no money held, no exchange keys.',
      },
      {
        icon: 'check',
        label: 'You approve every rule',
        note: 'Nothing runs until you have read it and said yes.',
      },
      {
        icon: 'compliance',
        label: 'Screening is evidence, not a fatwa',
        note: 'A published method applied to reviewed sources.',
      },
      {
        icon: 'billing',
        label: 'The price is shown before you pay',
        note: 'Every charge, and how to stop it, in plain words.',
      },
    ],
    sections: [
      {
        id: 'agreement',
        title: 'This agreement',
        icon: 'file_text',
        short:
          'By using Hilal Markets you agree to these rules, and to the Privacy Policy, the Cookie Policy and the Risk Disclosure.',
        body: (
          <>
            <P>
              These Terms cover the Hilal Markets website and the product: research tools,
              Watchlists, Evidence Passports, monitoring, and alerts. Using the site or an
              account means you accept them, together with the{' '}
              <Link href="/privacy">Privacy Policy</Link>, the{' '}
              <Link href="/cookies">Cookie Policy</Link> and the{' '}
              <Link href="/risk-disclosure">Risk Disclosure</Link>.
            </P>
            <P>
              If you do not agree with them, please do not use the service. Questions go to{' '}
              <Mail address={email} />.
            </P>
          </>
        ),
      },
      {
        id: 'who-can-use',
        title: 'Who can use it',
        icon: 'user',
        short:
          'You must be 18 or older. Keep your account details accurate, keep your password to yourself, and tell us if somebody else gets in.',
        body: (
          <ul className="hm-legal-list">
            {[
              'You must be at least 18 and legally able to enter this agreement.',
              'The details on your account must be true and kept up to date.',
              'Your password is yours alone. Do not share it, and do not reuse it elsewhere.',
              'Tell us straight away if you think somebody else has reached your account.',
              'Do not share one account between people to get around a limit.',
            ].map((item) => (
              <li key={item}>
                <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m5 12 5 5L20 7" />
                </svg>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ),
      },
      {
        id: 'what-it-does',
        title: 'What Hilal Markets does',
        icon: 'radar',
        short:
          'It turns your own setup into clear rules, watches the market against them, and tells you with the evidence when they are met.',
        body: (
          <>
            <P>
              Hilal Markets is a research and monitoring tool for crypto spot markets. You
              describe a setup. We help you turn it into measurable rules, you read them and
              approve them, and then we watch the market and tell you when they are met —
              with the numbers and the sources behind the alert.
            </P>
            <P>
              You can also read the Shariah screening evidence for an asset, run a one-off
              check on the market now, and follow a setup as it forms.
            </P>
          </>
        ),
      },
      {
        id: 'boundaries',
        title: 'What it will never do',
        icon: 'hand',
        short:
          'It is not a broker or an adviser. It holds no money, connects no trading keys, places no orders, and gives no personal buy or sell advice.',
        body: (
          <>
            <ul className="hm-legal-list">
              {[
                'Hilal Markets is not an exchange, a broker, a custodian, an investment adviser, or a portfolio manager.',
                'It does not hold your money and never connects to your exchange trading keys.',
                'It does not place, cancel or manage any order.',
                'An alert says a rule you wrote was met. It is not advice to buy or sell, and it promises no profit and prevents no loss.',
                'AI can explain and organise. It cannot approve a rule, switch monitoring on, or decide a Shariah status.',
                'Every decision about your own money stays yours.',
              ].map((item) => (
                <li key={item}>
                  <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m5 12 5 5L20 7" />
                  </svg>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <P>
              These are not disclaimers written to protect us. They describe what the
              software can actually do: there is no code in this product that can reach an
              exchange and move money.
            </P>
          </>
        ),
      },
      {
        id: 'screening',
        title: 'Shariah screening',
        icon: 'compliance',
        short:
          'A status is a published method applied to reviewed evidence, with the date and the sources shown. It is not a personal religious ruling, and scholars can disagree.',
        body: (
          <>
            <P>
              Every screening result names the methodology it followed, the evidence it
              used, the date it was reviewed, and what it does and does not cover. You can
              read all of it in the Evidence Passport for that asset.
            </P>
            <P>
              A status can change when the evidence or the methodology changes, and the
              history of every change stays visible.
            </P>
            <P>
              <strong>A status is not a fatwa.</strong> It does not guarantee that every
              scholar would reach the same conclusion, and it does not automatically cover
              every possible use of an asset. Where a case is disputed, where the evidence
              is thin, or where a source has not been reviewed recently, we say so on the
              asset itself. Please read those notes before relying on a status.
            </P>
          </>
        ),
      },
      {
        id: 'your-rules',
        title: 'Your rules and your approval',
        icon: 'check',
        short:
          'You approve every rule before it runs. An approved rule is never changed quietly — a change makes a new version, and you approve that too.',
        body: (
          <>
            <P>
              You provide the idea. We may ask you questions, match it to what the product
              can actually measure, and produce a rule set for you to read. Where the
              meaning is unclear, we ask rather than guess — and where we cannot express
              what you meant, we say so instead of monitoring something close to it.
            </P>
            <P>
              <strong>Nothing runs until you approve it.</strong> A live version is never
              edited underneath you. Any change creates a new version with its own record,
              and needs your approval again.
            </P>
            <P>
              A one-off market check is exactly that: it looks now and reports. It creates
              no ongoing alerts unless you turn the tested setup into a Watchlist and
              approve it.
            </P>
          </>
        ),
      },
      {
        id: 'market-data',
        title: 'Market data has limits',
        icon: 'chart',
        short:
          'Prices and indicators come from other companies. They can be late, wrong, incomplete or unavailable, and we cannot promise otherwise.',
        body: (
          <>
            <P>
              Prices, candles, indicators and source documents come from third parties. Any
              of them can be delayed, corrected afterwards, incomplete, out of date, or
              simply unavailable. We show where a figure came from and when, and we refuse
              to act on data we know is stale rather than acting on it quietly.
            </P>
            <P>
              What the market did before does not tell you what it will do next. Historical
              previews and examples are there to explain a rule, not to predict anything.
            </P>
          </>
        ),
      },
      {
        id: 'alerts',
        title: 'Alerts can be late or missed',
        icon: 'bell',
        short:
          'Alerts depend on your rules, on market data, and on email or Telegram. Do not rely on them for anything urgent or safety-critical.',
        body: (
          <>
            <P>
              An alert has to travel through market data, our own workers, and whichever
              channel you chose. Any of those can be slow or fail. A message can arrive
              late, arrive twice because the channel repeated it, or not arrive at all.
            </P>
            <P>
              Your dashboard holds the record that matters: it shows whether the rule was
              met, and whether the message was sent. If the two disagree, the dashboard is
              right and the channel is the problem.
            </P>
            <P>
              <strong>Do not depend on Hilal Markets for anything urgent</strong>, anything
              time-guaranteed, or anything where a missed message would be dangerous.
            </P>
          </>
        ),
      },
      {
        id: 'fair-use',
        title: 'Fair use of support',
        icon: 'support',
        short:
          'There is a limit on how many support messages one person can send in an hour, so the queue stays short enough for everybody to get a real answer.',
        body: (
          <>
            <P>
              Support messages — from the contact page and from inside your account — are
              limited per email address and per device over a rolling period, and there is
              an overall limit on how many the service accepts per hour. The current numbers
              are shown on the <Link href="/contact">contact page</Link> before you write.
            </P>
            <P>
              This is not there to keep you out. It is there because a person reads every
              message, and an unlimited queue is an unanswered queue. If you reach the
              limit, we tell you when it clears. To add something to a message you already
              sent, reply to our email instead of writing a new one.
            </P>
          </>
        ),
      },
      {
        id: 'acceptable-use',
        title: 'What you must not do',
        icon: 'warning',
        short:
          'Do not attack the service, reach into other people’s accounts, scrape it, or pretend our alerts are guaranteed advice.',
        body: (
          <ul className="hm-legal-list">
            {[
              'Do not try to reach another person’s account or data.',
              'Do not get around access controls, security checks or usage limits.',
              'Do not scrape, copy in bulk, or resell what the service shows you.',
              'Do not interfere with the service, overload it, or disrupt it for others.',
              'Do not send malware, or text designed to make the AI break its own rules.',
              'Do not send us credentials or secrets — we never ask for them.',
              'Do not present our alerts, Passports or branding as guaranteed advice, a personal religious ruling, or an endorsement.',
              'Do not use the service to break the law, sanctions, exchange rules, or somebody else’s rights.',
            ].map((item) => (
              <li key={item}>
                <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m5 12 5 5L20 7" />
                </svg>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ),
      },
      {
        id: 'your-content',
        title: 'What you write stays yours',
        icon: 'lock',
        short:
          'Your setups, rules and Watchlists belong to you. We only get permission to store and run them so the service works.',
        body: (
          <>
            <P>
              You keep ownership of what you write: your setup descriptions, your Watchlist
              names, your rules, and your messages. You give us only the permission we need
              to store it, check it, show it back to you, and act on it — which is what
              makes the product work at all.
            </P>
            <P>
              <strong>We do not acquire your strategies.</strong> They are not published,
              not shared with other users, and not sold. Personal information inside them is
              handled under the <Link href="/privacy">Privacy Policy</Link>.
            </P>
          </>
        ),
      },
      {
        id: 'our-content',
        title: 'What belongs to us',
        icon: 'layers',
        short:
          'The software, the design, the brand and our written methodology are ours. You get the right to use the service, not to own it.',
        body: (
          <P>
            The product, its software, design, brand, documentation and the way we present
            our methodology belong to Hilal Markets or to the people who licensed them to
            us. These Terms give you a limited right to use the service, which we can
            withdraw. They do not transfer ownership of anything.
          </P>
        ),
      },
      {
        id: 'third-parties',
        title: 'Other companies we depend on',
        icon: 'integrations',
        short:
          'Exchanges, data providers, AI providers, messaging and hosting all have their own terms. A link or an integration is not an endorsement.',
        body: (
          <P>
            The service depends on exchanges, market-data providers, official source
            websites, AI providers, messaging channels, measurement providers, hosting and
            email services. Their own terms and privacy practices may also apply to you.
            Linking to a source or integrating with a channel does not mean we control it or
            endorse it.
          </P>
        ),
      },
      {
        id: 'changes-to-service',
        title: 'The service will change',
        icon: 'version',
        short:
          'We add, repair and sometimes withdraw features. We will never use a change to quietly alter a rule you approved or a record of what happened.',
        body: (
          <>
            <P>
              Features are added, improved, and occasionally removed. Where a change removes
              something you were relying on, we will tell you in advance where we reasonably
              can.
            </P>
            <P>
              <strong>One thing never changes with a release:</strong> a strategy version
              you approved, and the evidence recorded for an alert that already happened. A
              product update must not rewrite either. If it needs to change what runs, it
              creates a new version and asks you.
            </P>
          </>
        ),
      },
      {
        id: 'billing',
        title: 'Plans, payment and refunds',
        icon: 'billing',
        short:
          'Before you pay, the checkout shows the price, what you get, how long it lasts, whether it renews, and how to cancel. Nothing is charged without that.',
        body: (
          <>
            <P>
              <strong>Before any payment,</strong> the checkout shows the price and currency,
              what the plan includes and its limits, how long the access lasts, whether it
              renews by itself, how to cancel, and what happens if you do. If any of that is
              not shown, no charge may be taken.
            </P>
            <P>
              <strong>Renewal.</strong> A plan renews automatically only where the checkout
              said so and only where the payment provider actually supports it. Where it does
              not, access simply ends at the end of the period you paid for and nothing
              further is taken.
            </P>
            <P>
              <strong>Cancelling.</strong> You can cancel at any time from your billing page.
              Cancelling stops the next payment; it does not shorten the period you have
              already paid for, so you keep the access you bought until it ends.
            </P>
            <P>
              <strong>Refunds.</strong> Where a refund is offered, the terms are shown at
              checkout before you pay, and they are the terms that apply. Any refund right
              you have under the law of your own country applies as well, and nothing here
              reduces it.
            </P>
            <P>
              <strong>Free access.</strong> Where a free plan or trial exists, its limits are
              shown with it. We do not turn a free plan into a paid one without asking.
            </P>
          </>
        ),
      },
      {
        id: 'ending',
        title: 'Ending it',
        icon: 'close',
        short:
          'You can stop at any time. We can restrict an account to protect people or the service, and we will explain why where we can.',
        body: (
          <>
            <P>You can stop using the service, or close your account, whenever you like.</P>
            <P>
              We may limit or suspend access where it is needed to protect users, the
              integrity of the evidence, our providers, or security — or where these Terms
              have been seriously broken. Where it is practical and lawful, we will tell you
              why and what you can do next.
            </P>
            <P>
              Records we have to keep for security, evidence, legal or dispute reasons stay
              as described in the <Link href="/privacy">Privacy Policy</Link>.
            </P>
          </>
        ),
      },
      {
        id: 'risk',
        title: 'Risk, and what we do not promise',
        icon: 'alert',
        short:
          'Crypto can lose a lot of value. We do not promise the service is always available, always right, or profitable in any way.',
        body: (
          <>
            <P>
              Crypto assets move sharply and can lose most or all of their value. Nothing in
              this product — a screening result, past behaviour, a rule being met, an alert,
              or an AI explanation — makes an asset suitable for you or tells you what will
              happen next.
            </P>
            <P>
              So far as the law allows, the service is provided as it is and as it is
              available. We do not promise uninterrupted availability, perfectly accurate
              market data, alerts at a guaranteed time, any financial outcome, or agreement
              between Shariah authorities.
            </P>
          </>
        ),
      },
      {
        id: 'liability',
        title: 'Who is responsible for what',
        icon: 'scale',
        short:
          'You are responsible for your own decisions. We are not liable for indirect losses — and nothing here removes a right you have by law.',
        body: (
          <>
            <P>
              You are responsible for the decisions you make using the service, and for
              checking anything that matters to your own situation.
            </P>
            <P>
              So far as the law allows, Hilal Markets is not responsible for indirect,
              incidental or consequential loss arising from using the service, from being
              unable to use it, or from third-party data and delivery providers.
            </P>
            <P>
              <strong>Nothing in these Terms removes a right you cannot lawfully be asked
              to give up.</strong> Consumer protections where you live apply whatever this
              page says.
            </P>
          </>
        ),
      },
      {
        id: 'disputes',
        title: 'If something goes wrong between us',
        icon: 'chat',
        short:
          'Tell us first and we will try to sort it out. Your legal rights where you live are unaffected.',
        body: (
          <P>
            If you are unhappy, please write to <Mail address={email} /> first so we can try
            to put it right directly. Whatever happens, the consumer protections and legal
            rights that apply where you live are not changed by these Terms.
          </P>
        ),
      },
      {
        id: 'updates',
        title: 'Changes to these Terms',
        icon: 'history',
        short:
          'We may update this page. When a change matters, we will tell you before it takes effect.',
        body: (
          <P>
            These Terms will be updated as the product and the law develop. The date at the
            top shows when the current wording began. Where a change materially affects you,
            we will tell you before it takes effect, in the way the law requires. Questions
            go to <Mail address={email} />.
          </P>
        ),
      },
    ],
  }
}

/* -------------------------------------------------------------------------- */
/*  Cookies                                                                    */
/* -------------------------------------------------------------------------- */
/**
 * The Cookie Policy, in the same shape as the other two.
 *
 * It was a separate page with its own template — a single column of headings, no
 * summary line, no search, no reading progress, and its own idea of what a legal page
 * looks like. There is no reason for the third document about the same service to be
 * read differently from the first two, so it is the same component now and only the
 * words differ.
 *
 * The consent categories below are the ones the banner actually offers. If a category is
 * ever added to `hilalmarkets-consent.js`, it has to be added here too — a policy that
 * lists three categories while the banner offers four is worse than no list at all.
 */
export function cookiesDocument(email: string): LegalDocument {
  return {
    kind: 'cookies',
    title: 'Cookie Policy',
    lede:
      'What gets stored in your browser, which parts are optional, and how to change your mind at any time.',
    updated: UPDATED,
    version: '2.0',
    highlights: [
      {
        icon: 'shield_check',
        label: 'Optional cookies start off',
        note: 'Nothing optional loads until you say yes.',
      },
      {
        icon: 'hand',
        label: 'You can change your mind',
        note: 'Cookie settings reopens the choice on any page.',
      },
      {
        icon: 'lock',
        label: 'No strategy data is shared',
        note: 'Measurement tools never receive what you type or build.',
      },
      {
        icon: 'eye',
        label: 'Nothing is sold',
        note: 'We do not sell or rent what these tools measure.',
      },
    ],
    sections: [
      {
        id: 'what',
        title: 'What a cookie is here',
        icon: 'cookie',
        short:
          'Small pieces of data your browser keeps for us. Some are needed to sign you in; the rest are optional.',
        body: (
          <>
            <P>
              A cookie is a small piece of data a website asks your browser to keep. Hilal
              Markets also uses local storage, which works the same way for this purpose. We
              use both to remember that you are signed in, to keep your session secure, to
              hold your preferences, and to record the cookie choice you made.
            </P>
            <P>
              Some of this is required for the service to work at all. Everything else is
              optional and stays switched off until you turn it on.
            </P>
          </>
        ),
      },
      {
        id: 'categories',
        title: 'The four categories',
        icon: 'list',
        short:
          'Essential is always on because the site cannot work without it. Analytics, Functional and Marketing are all off until you allow them.',
        body: (
          <>
            <P>
              <strong>Essential.</strong> Signing in, keeping your session secure, remembering
              your cookie choice, and the behaviour you directly asked for. These stay on. If
              they were switched off you could not sign in, so there is no choice to offer.
            </P>
            <P>
              <strong>Analytics.</strong> Optional. Counts how the public pages are used, in
              aggregate, so we can tell which pages are confusing. It stays denied until you
              allow it, and it only loads at all when a Google Tag Manager container has been
              configured for the deployment.
            </P>
            <P>
              <strong>Functional.</strong> Optional. Remembers preferences that go beyond the
              core service. Denied until you allow it.
            </P>
            <P>
              <strong>Marketing.</strong> Optional, and off unless you enable it yourself
              under Customize. When it is on and configured, an advertising pixel may measure
              that a visit happened. It never receives what you typed into a form, your
              account credentials, or anything about your strategies.
            </P>
          </>
        ),
      },
      {
        id: 'choice',
        title: 'The choice you are offered',
        icon: 'check',
        short:
          'On your first visit the banner offers Essential only, Customize, or Accept analytics. Marketing is never included unless you pick it yourself.',
        body: (
          <>
            <P>
              The first time you open a public page, a banner offers three options:{' '}
              <strong>Essential only</strong>, <strong>Customize</strong>, and{' '}
              <strong>Accept analytics</strong>. Accepting analytics does not turn on
              marketing — that one can only be enabled from Customize, deliberately, so it is
              never something you agreed to by accident.
            </P>
            <P>
              Your choice is stored in your own browser with a version number and the time you
              made it. Keeping the version means that if the categories ever change, we can
              tell that your answer was given about the old list and ask you again rather than
              assuming it still applies.
            </P>
          </>
        ),
      },
      {
        id: 'google',
        title: 'Google Consent Mode',
        icon: 'settings',
        short:
          'Every optional Google signal starts denied. Only your choice changes that.',
        body: (
          <P>
            Before any optional Google tag is allowed to load, ad storage, analytics storage,
            ad user data, ad personalization, functionality storage and personalization
            storage are all set to denied. Security storage is granted, because it is what
            keeps the session safe. Those defaults are set in the page itself, before any tag
            runs, and they are only updated after you choose.
          </P>
        ),
      },
      {
        id: 'change',
        title: 'Changing or withdrawing your choice',
        icon: 'refresh',
        short:
          'Use Cookie settings in the footer, on any page, at any time.',
        body: (
          <>
            <P>
              <strong>Cookie settings</strong> is in the footer of every page. Opening it
              brings back the same choice and replaces whatever you chose before. Withdrawing
              consent sends a denied signal immediately.
            </P>
            <P>
              One honest limitation: a script that has already loaded into the current page
              cannot be pulled back out of the browser's memory. What withdrawal does is stop
              any optional provider being started again — on the next page you open, and every
              one after it, nothing optional loads while consent is denied.
            </P>
          </>
        ),
      },
      {
        id: 'contact',
        title: 'Questions',
        icon: 'mail',
        short: 'Ask us. If this page did not answer it, that is ours to fix.',
        body: (
          <P>
            Questions about this policy go to <Mail address={email} />. What we hold about you
            more generally, and what you can ask us to do with it, is in the{' '}
            <Link href="/privacy">Privacy Policy</Link>.
          </P>
        ),
      },
    ],
  }
}
