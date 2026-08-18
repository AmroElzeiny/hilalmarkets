/**
 * Contact — a route to an answer, not a blank box.
 *
 * The page this replaces was a heading, an address and three unlabelled fields. It
 * worked, and it left every hard part to the person writing:
 *
 * - nothing said how long a reply takes, so a message was posted into silence;
 * - nothing helped somebody whose question already has an answer, so the common ones
 *   arrived one at a time and waited their turn behind each other;
 * - the fields had a length limit and no counter, so a long message hit a silent wall;
 * - the field borders measured 1.21:1 against their own fill — an edge nobody could
 *   see — and the helper text measured 3.98:1, under the 4.5:1 body text needs;
 * - a refused message said "we could not send your message", whatever had happened.
 *
 * So the page is now three steps in the order a person actually needs them: **find the
 * answer**, **choose the subject**, **write the message** — with a review window
 * before anything is sent, and a plain statement of the limit before it is met rather
 * than after.
 */
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { trackCtaClick } from '../analytics'
import { Dialog } from '../components/Dialog'
import { Icon, IconBadge, type IconName } from '../components/Icon'
import { useTilt } from '../components/interactions'
import { Reveal } from '../components/Reveal'
import { BackToTop, SiteFooter, SiteNav } from '../components/SiteChrome'
import { DURATION, move } from '../motion'
import {
  contactLimits,
  newContactIdempotencyKey,
  PublicFormError,
  submitContact,
  type ContactLimits,
} from '../publicForms'

type Status = 'idle' | 'submitting' | 'success' | 'error' | 'blocked'

/** What the server accepts. Longer than this is refused, so the page must not offer it. */
const SERVER_TITLE_LIMIT = 180
const DESCRIPTION_LIMIT = 5000

/* -------------------------------------------------------------------------- */
/*  What a message can be about                                               */
/* -------------------------------------------------------------------------- */
/**
 * The subjects, and what each one changes.
 *
 * Choosing a subject is not a decoration on the form: it changes the hint under the
 * message box to the two or three things we will need from that particular person, so
 * the first reply can answer instead of asking. The value travels with the message
 * title, so the inbox can tell them apart without a second field.
 */
type Topic = {
  id: string
  label: string
  icon: IconName
  /** What to ask for, so the first reply can be the answer. */
  prompt: string
}

const TOPICS: Topic[] = [
  {
    id: 'product',
    label: 'How the product works',
    icon: 'guide',
    prompt: 'Tell us what you were trying to do, and what you expected to see.',
  },
  {
    id: 'screening',
    label: 'Shariah screening',
    icon: 'compliance',
    prompt: 'Name the asset, and say which part of the screening you want explained.',
  },
  {
    id: 'account',
    label: 'Account and access',
    icon: 'user',
    prompt: 'Give the email address on the account. Never send us your password.',
  },
  {
    id: 'billing',
    label: 'Plans and payment',
    icon: 'billing',
    prompt: 'Give the date and the amount. Never send us full card details.',
  },
  {
    id: 'problem',
    label: 'Something is broken',
    icon: 'warning',
    prompt: 'Say what happened, roughly when, and what you saw on screen.',
  },
  {
    id: 'privacy',
    label: 'Privacy and data',
    icon: 'shield',
    prompt: 'Say which right you want to use: access, correction, deletion or export.',
  },
]

/* -------------------------------------------------------------------------- */
/*  Answers that save a message                                               */
/* -------------------------------------------------------------------------- */
/**
 * The questions we are asked most, with their answers on the page.
 *
 * This is the largest single improvement to the page and the least visible one: most
 * messages are a question that already has an answer. Answering here is faster for the
 * person than any reply we could send, and it keeps the queue for the messages that
 * genuinely need us.
 */
const ANSWERS: { id: string; question: string; answer: string; topic: string }[] = [
  {
    id: 'reply-time',
    topic: 'product',
    question: 'How long will a reply take?',
    answer:
      'We answer in the order messages arrive, on working days. A message about a charge, a missing alert, or something broken is looked at first.',
  },
  {
    id: 'is-broker',
    topic: 'product',
    question: 'Does Hilal Markets place trades for me?',
    answer:
      'No. Hilal Markets watches the market against rules you wrote and approved, and tells you when they are met. It holds no money, connects no trading keys, and places no orders. You keep using your own exchange.',
  },
  {
    id: 'status-change',
    topic: 'screening',
    question: 'Why did the Shariah status of an asset change?',
    answer:
      'A status follows the published methodology and the evidence behind it. When a source is updated or a review is repeated, the status can change. The Evidence Passport for that asset shows the methodology, the sources, the review date and the full history.',
  },
  {
    id: 'ruling',
    topic: 'screening',
    question: 'Is a screening result the same as a religious ruling?',
    answer:
      'No. It is the result of a published methodology applied to reviewed evidence. It is not a personal fatwa, and scholars can differ. Where a case is disputed or the evidence is thin, we say so on the asset itself.',
  },
  {
    id: 'no-alert',
    topic: 'problem',
    question: 'An alert did not reach me. What should I check?',
    answer:
      'Open the alert in your dashboard first: it records whether the rule was met and whether the message was sent. If it was sent but did not arrive, the problem is usually the channel — check that Telegram or email is still connected in your settings.',
  },
  {
    id: 'secrets',
    topic: 'account',
    question: 'What should I never put in a message?',
    answer:
      'Passwords, recovery codes, wallet seed phrases, private keys, exchange API keys, and full card numbers. We never ask for any of them, and we cannot use them. If you have already sent one somewhere, change it now.',
  },
  {
    id: 'delete',
    topic: 'privacy',
    question: 'How do I ask for my data to be deleted?',
    answer:
      'Send a message using the Privacy and data subject below, from the address on the account. We may ask you to confirm it is you before we act, because the alternative is acting on somebody else’s word about your account.',
  },
  {
    id: 'refund',
    topic: 'billing',
    question: 'How do I ask about a charge?',
    answer:
      'Send the date and the amount. Your billing page also lists every payment with its receipt, which is usually faster than waiting for us.',
  },
]

/* -------------------------------------------------------------------------- */
/*  The secret guard                                                          */
/* -------------------------------------------------------------------------- */
/**
 * Things that must never be sent to us, recognised before the message leaves.
 *
 * A warning, never a block: a person writing about their API key is not the same as a
 * person pasting one, and refusing to send a message because it contains a word would
 * be worse than the risk. What it does is stop the paste that somebody did not think
 * about — checked in the browser, so a value that should not be sent is caught before
 * it is sent rather than after we have stored it.
 */
const SECRET_PATTERNS: { id: string; label: string; test: RegExp }[] = [
  {
    id: 'seed',
    label: 'a wallet recovery phrase',
    // Twelve or more lower-case words in a row is what a seed phrase looks like and
    // what ordinary writing does not: sentences carry punctuation and capitals.
    test: /(?:^|\s)(?:[a-z]{3,8}\s+){11,}[a-z]{3,8}(?:\s|$)/,
  },
  {
    id: 'private-key',
    label: 'a private key',
    test: /(?:0x[a-fA-F0-9]{40,}|-{5}BEGIN [A-Z ]*PRIVATE KEY-{5})/,
  },
  {
    id: 'api-key',
    label: 'an API key or access token',
    test: /\b(?:sk|pk|api|token|secret|bearer)[-_ ]?(?:key|token)?\s*[:=]\s*\S{12,}/i,
  },
  {
    id: 'card',
    label: 'a full card number',
    test: /\b(?:\d[ -]?){15,18}\d\b/,
  },
  {
    id: 'password',
    label: 'a password',
    test: /\bpassword\s*(?:is|:|=)\s*\S+/i,
  },
]

function foundSecrets(text: string): string[] {
  return SECRET_PATTERNS.filter((rule) => rule.test.test(text)).map((rule) => rule.label)
}

/**
 * How long a title the person may actually write.
 *
 * The subject travels to our inbox as a prefix on the title, so the two together have
 * to fit inside what the server accepts. Offering the full 180 characters let somebody
 * fill the box and then have the message refused for a reason no part of the page had
 * mentioned — the counter said there was room, and there was not.
 *
 * Worked out from the longest subject rather than from whichever one is selected, so
 * the number under the box never changes while somebody is typing into it.
 */
const TITLE_LIMIT =
  SERVER_TITLE_LIMIT -
  Math.max(...TOPICS.map((topic) => `[${topic.label}] `.length))

/* -------------------------------------------------------------------------- */
/*  Small parts                                                                */
/* -------------------------------------------------------------------------- */
function Counter({ value, limit }: { value: number; limit: number }) {
  const left = limit - value
  const share = value / limit
  const state = share >= 1 ? 'full' : share >= 0.9 ? 'near' : 'fine'
  return (
    <span className="hm-counter" data-state={state}>
      {/* The number and the words together: a count alone is a colour change nobody
          reading with a screen reader would ever hear. */}
      {state === 'fine'
        ? `${value} of ${limit}`
        : state === 'near'
          ? `${left} characters left`
          : 'No characters left'}
    </span>
  )
}

function TopicCard({
  topic,
  selected,
  onSelect,
  name,
}: {
  topic: Topic
  selected: boolean
  onSelect: () => void
  name: string
}) {
  const tilt = useTilt<HTMLLabelElement>()
  return (
    <label
      ref={tilt.ref}
      onPointerMove={tilt.onPointerMove}
      onPointerLeave={tilt.onPointerLeave}
      className={`hm-tilt hm-choice group relative flex cursor-pointer gap-3 rounded-[20px] border p-4 ${
        selected
          ? 'border-[#55712a] bg-[#f6fbec]'
          : 'border-[#dbe1e7] bg-white hover:bg-[#fbfcfd]'
      }`}
    >
      <input
        type="radio"
        name={name}
        value={topic.id}
        checked={selected}
        onChange={onSelect}
        className="sr-only"
      />
      <IconBadge name={topic.icon} tone={selected ? 'apple' : 'neutral'} size="sm" />
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 text-[14.5px] font-semibold text-ink">
          {topic.label}
          {/* The chosen subject is marked with a tick as well as a colour, so the
              choice is not carried by colour alone. */}
          {selected && <Icon name="check" className="size-4 text-[#55712a]" />}
        </span>
      </span>
    </label>
  )
}

function AnswerRow({
  item,
  open,
  onToggle,
}: {
  item: (typeof ANSWERS)[number]
  open: boolean
  onToggle: () => void
}) {
  const panelId = `answer-${item.id}`
  return (
    <div className="border-b border-[#e4e9ee] last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full items-center gap-3 px-4 py-4 text-left transition-colors hover:bg-[#f7f9fb] sm:px-5"
      >
        <Icon
          name={open ? 'minus' : 'plus'}
          className="size-4 shrink-0 text-[#55712a] transition-transform duration-200"
        />
        <span className="flex-1 text-[15px] font-semibold text-ink">{item.question}</span>
      </button>
      <div id={panelId} className="hm-collapse" data-open={open} role="region">
        <div>
          <p className="px-4 pb-4 pl-11 text-[14.5px] leading-[1.65] text-[#4a505a] sm:px-5 sm:pl-12">
            {item.answer}
          </p>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  The page                                                                   */
/* -------------------------------------------------------------------------- */
export default function ContactPage() {
  const [status, setStatus] = useState<Status>('idle')
  const [topic, setTopic] = useState(TOPICS[0].id)
  const [title, setTitle] = useState('')
  const [email, setEmail] = useState('')
  const [description, setDescription] = useState('')
  const [query, setQuery] = useState('')
  const [openAnswer, setOpenAnswer] = useState<string | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<{ title: string; detail: string } | null>(null)
  const [limits, setLimits] = useState<ContactLimits | null>(null)
  const [remaining, setRemaining] = useState<number | null>(null)
  const [reference, setReference] = useState('')

  const idempotencyKey = useRef(newContactIdempotencyKey())
  const formRef = useRef<HTMLFormElement>(null)
  const resultRef = useRef<HTMLDivElement>(null)
  const fieldId = useId()

  const chosen = TOPICS.find((item) => item.id === topic) ?? TOPICS[0]
  const secrets = useMemo(() => foundSecrets(description), [description])

  // The limit is read from the server, so the page can never advertise a number
  // different from the one being enforced.
  useEffect(() => {
    let alive = true
    contactLimits()
      .then((value) => alive && setLimits(value))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  const visibleAnswers = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return ANSWERS
    return ANSWERS.filter(
      (item) =>
        item.question.toLowerCase().includes(needle) ||
        item.answer.toLowerCase().includes(needle),
    )
  }, [query])

  const validate = useCallback((): Record<string, string> => {
    const found: Record<string, string> = {}
    if (title.trim().length < 3) {
      found.title = 'Give the message a short title, at least 3 characters.'
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email.trim())) {
      found.email = 'Enter an email address we can reply to, for example you@email.com.'
    }
    if (description.trim().length < 10) {
      found.description = 'Add a little more detail, at least 10 characters.'
    }
    return found
  }, [description, email, title])

  function openReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (status === 'submitting') return
    const found = validate()
    setErrors(found)
    if (Object.keys(found).length > 0) {
      // Focus the first field that needs attention, so the person is taken to the
      // problem rather than told there is one.
      const first = ['title', 'email', 'description'].find((name) => found[name])
      const node = formRef.current?.querySelector<HTMLElement>(`[data-field="${first}"]`) ?? null
      node?.focus()
      void move(node, { transform: ['translateX(0)', 'translateX(-4px)', 'translateX(0)'] }, {
        duration: DURATION.quick,
      })
      return
    }
    trackCtaClick('review_contact', 'contact_form')
    setReviewing(true)
  }

  async function send() {
    setReviewing(false)
    setStatus('submitting')
    setFailure(null)
    trackCtaClick('submit_contact', 'contact_form')
    try {
      const result = await submitContact(
        {
          // The subject travels in the title, so the inbox can sort without a second
          // field and the person can still see exactly what will be sent.
          title: `[${chosen.label}] ${title.trim()}`,
          email: email.trim(),
          description: description.trim(),
        },
        idempotencyKey.current,
      )
      setReference(idempotencyKey.current.split(':')[1]?.slice(0, 8).toUpperCase() ?? '')
      setRemaining(result.remaining_messages)
      setStatus('success')
      idempotencyKey.current = newContactIdempotencyKey()
    } catch (error) {
      const category = error instanceof PublicFormError ? error.category : 'unknown_error'
      if (category === 'rate_limited') {
        setStatus('blocked')
        setFailure({
          title: 'You have reached the message limit',
          detail:
            error instanceof PublicFormError && error.detail
              ? error.detail
              : 'Please wait a little while before sending another message.',
        })
        return
      }
      setStatus('error')
      setFailure({
        title:
          category === 'network_error'
            ? 'Your message did not leave this device'
            : 'We could not send your message',
        detail:
          category === 'network_error'
            ? 'Check your internet connection and press Send again. Nothing was sent, so nothing is duplicated.'
            : 'Something went wrong on our side. Please try again in a moment, or email us directly at office@hilalmarkets.com.',
      })
    }
  }

  // A result the person cannot see is a result they did not get. Focus moves to it,
  // and the panel is announced.
  useEffect(() => {
    if (status !== 'success' && status !== 'blocked') return
    const node = resultRef.current
    node?.focus()
    void move(node, { opacity: [0, 1], transform: ['translateY(10px)', 'translateY(0)'] })
  }, [status])

  const perPerson = limits ? Math.min(limits.per_email, limits.per_client) : null

  return (
    <div className="hm-page min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main id="top">
        {/* ---- Opening ---------------------------------------------------- */}
        <section className="relative overflow-hidden pt-32 pb-10 sm:pt-36">
          <div className="hm-aura hm-aura--apple left-1/2 top-4 h-[380px] w-[720px] -translate-x-1/2" aria-hidden="true" />
          <div className="hm-shell">
            <Reveal>
              <p className="hm-eyebrow">
                <Icon name="chat" className="size-4" />
                Contact Hilal Markets
              </p>
              <h1 className="mt-5 max-w-[15ch] font-display text-[38px] leading-[1.04] tracking-[-0.035em] text-ink sm:text-[52px]">
                Tell us what you need.
              </h1>
            </Reveal>
          </div>
        </section>

        {/* ---- Answers first ---------------------------------------------- */}
        <section className="pb-12" aria-labelledby="answers-heading">
          <div className="hm-shell">
            <Reveal>
              <div className="hm-card overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#e4e9ee] p-4 sm:p-5">
                  <div className="flex items-center gap-3">
                    <IconBadge name="spark" tone="apple" size="sm" />
                    <div>
                      <h2 id="answers-heading" className="font-display text-[21px] font-medium tracking-[-0.02em] text-ink">
                        Answers, before you write
                      </h2>
                      <p className="mt-0.5 text-[13.5px] text-ink-soft">
                        This is usually faster than waiting for a reply.
                      </p>
                    </div>
                  </div>
                  <div className="relative w-full sm:w-[300px]">
                    <label className="sr-only" htmlFor={`${fieldId}-search`}>
                      Search the answers
                    </label>
                    <Icon
                      name="search"
                      className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-[#5c646e]"
                    />
                    <input
                      id={`${fieldId}-search`}
                      type="search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Search, e.g. alert"
                      className="hm-input !py-2.5 !pl-11 !text-[14.5px]"
                    />
                  </div>
                </div>

                <div aria-live="polite" className="sr-only">
                  {query.trim()
                    ? `${visibleAnswers.length} answer${visibleAnswers.length === 1 ? '' : 's'} match your search.`
                    : ''}
                </div>

                {visibleAnswers.length > 0 ? (
                  <div>
                    {visibleAnswers.map((item) => (
                      <AnswerRow
                        key={item.id}
                        item={item}
                        open={openAnswer === item.id}
                        onToggle={() => setOpenAnswer(openAnswer === item.id ? null : item.id)}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="flex items-center gap-3 p-5">
                    <IconBadge name="search" size="sm" />
                    <p className="text-[14.5px] text-[#4a505a]">
                      Nothing here matches &ldquo;{query.trim()}&rdquo;. Write to us below and we
                      will answer it.
                    </p>
                  </div>
                )}
              </div>
            </Reveal>
          </div>
        </section>

        {/* ---- The form ---------------------------------------------------- */}
        <section className="pb-16" aria-labelledby="write-heading">
          <div className="hm-shell">
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
              <Reveal>
                <div className="hm-card p-5 sm:p-7">
                  <div className="flex items-start gap-3">
                    <IconBadge name="send" tone="ink" />
                    <div>
                      <h2 id="write-heading" className="font-display text-[25px] font-medium tracking-[-0.02em] text-ink">
                        Write to us
                      </h2>
                    </div>
                  </div>

                  {(status === 'success' || status === 'blocked') && (
                    <div
                      ref={resultRef}
                      tabIndex={-1}
                      role="status"
                      className="mt-6 outline-none"
                      data-contact-result
                    >
                      {status === 'success' ? (
                        <div className="rounded-[20px] border border-[#97b95f] bg-[#f4faea] p-5">
                          <div className="flex items-start gap-3">
                            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-apple text-ink">
                              <Icon name="check" className="size-5" />
                            </span>
                            <div className="min-w-0">
                              <p className="font-display text-[19px] text-[#2b3a15]" data-contact-success>
                                Your message was sent.
                              </p>
                              <p className="mt-1 text-[14px] leading-[1.6] text-[#3d4d22]">
                                A person will read it and reply to{' '}
                                <strong className="font-semibold">{email.trim()}</strong>. Please
                                keep an eye on your spam folder as well.
                              </p>
                              {reference && (
                                <p className="mt-3 text-[13px] text-[#3d4d22]">
                                  Your reference:{' '}
                                  <span className="rounded-md bg-white/80 px-2 py-0.5 font-semibold tracking-wide tnum">
                                    {reference}
                                  </span>
                                </p>
                              )}
                              {remaining !== null && perPerson !== null && (
                                <p className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-[#3d4d22]">
                                  <span className="hm-quota" aria-hidden="true">
                                    {Array.from({ length: perPerson }, (_, index) => (
                                      <span
                                        key={index}
                                        className="hm-quota-pip"
                                        data-used={index >= remaining}
                                      />
                                    ))}
                                  </span>
                                  {remaining > 0
                                    ? `You can send ${remaining} more message${remaining === 1 ? '' : 's'} in the next hour.`
                                    : 'That was your last message for this hour. Reply to our email if you need to add anything.'}
                                </p>
                              )}
                              <button
                                type="button"
                                className="hm-btn hm-btn--quiet hm-btn--sm mt-4"
                                onClick={() => {
                                  setStatus('idle')
                                  setTitle('')
                                  setDescription('')
                                  setErrors({})
                                }}
                              >
                                <Icon name="edit" className="size-4" />
                                Write another message
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="hm-notice hm-notice--warn">
                          <Icon name="clock" className="mt-0.5 size-5 shrink-0" />
                          <span>
                            <strong>{failure?.title}</strong>
                            {failure?.detail}
                            <a
                              href="mailto:office@hilalmarkets.com"
                              className="mt-2 inline-flex items-center gap-1.5 font-semibold text-[#5a3f0b] underline underline-offset-4"
                            >
                              <Icon name="mail" className="size-4" />
                              Email us instead
                            </a>
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  {status !== 'success' && (
                    <form
                      ref={formRef}
                      onSubmit={openReview}
                      className="mt-6"
                      noValidate
                      data-contact-form
                    >
                      {/* -- Subject -- */}
                      <fieldset>
                        <legend className="mb-1 text-[14px] font-semibold text-ink">
                          What is it about?
                        </legend>
                        <p className="mb-3 text-[13px] text-ink-soft">
                          This decides who reads it first.
                        </p>
                        <div className="grid gap-2.5 sm:grid-cols-2">
                          {TOPICS.map((item) => (
                            <TopicCard
                              key={item.id}
                              topic={item}
                              name={`${fieldId}-topic`}
                              selected={topic === item.id}
                              onSelect={() => setTopic(item.id)}
                            />
                          ))}
                        </div>
                      </fieldset>

                      <div className="mt-6 grid gap-5">
                        {/* -- Title -- */}
                        <div className="hm-field">
                          <div className="hm-field-label">
                            <span>
                              <label htmlFor={`${fieldId}-title`}>Title</label>
                            </span>
                            <Counter value={title.length} limit={TITLE_LIMIT} />
                          </div>
                          <input
                            id={`${fieldId}-title`}
                            data-field="title"
                            className="hm-input"
                            value={title}
                            maxLength={TITLE_LIMIT}
                            autoComplete="off"
                            aria-invalid={errors.title ? true : undefined}
                            aria-describedby={errors.title ? `${fieldId}-title-error` : undefined}
                            onChange={(event) => {
                              setTitle(event.target.value)
                              if (errors.title) setErrors(({ title: _, ...rest }) => rest)
                            }}
                            placeholder="One line about your message"
                          />
                          {errors.title && (
                            <p id={`${fieldId}-title-error`} className="mt-2 flex items-center gap-1.5 text-[13px] font-semibold text-[#8d3029]">
                              <Icon name="warning" className="size-4" />
                              {errors.title}
                            </p>
                          )}
                        </div>

                        {/* -- Email -- */}
                        <div className="hm-field">
                          <div className="hm-field-label">
                            <span>
                              <label htmlFor={`${fieldId}-email`}>Your email</label>
                            </span>
                          </div>
                          <input
                            id={`${fieldId}-email`}
                            data-field="email"
                            type="email"
                            className="hm-input"
                            value={email}
                            maxLength={320}
                            autoComplete="email"
                            aria-invalid={errors.email ? true : undefined}
                            aria-describedby={errors.email ? `${fieldId}-email-error` : undefined}
                            onChange={(event) => {
                              setEmail(event.target.value)
                              if (errors.email) setErrors(({ email: _, ...rest }) => rest)
                            }}
                            placeholder="you@email.com"
                          />
                          {errors.email && (
                            <p id={`${fieldId}-email-error`} className="mt-2 flex items-center gap-1.5 text-[13px] font-semibold text-[#8d3029]">
                              <Icon name="warning" className="size-4" />
                              {errors.email}
                            </p>
                          )}
                        </div>

                        {/* -- Message -- */}
                        <div className="hm-field">
                          <div className="hm-field-label">
                            <span>
                              <label htmlFor={`${fieldId}-description`}>Your message</label>
                            </span>
                            <Counter value={description.length} limit={DESCRIPTION_LIMIT} />
                          </div>
                          <textarea
                            id={`${fieldId}-description`}
                            data-field="description"
                            className="hm-textarea"
                            value={description}
                            maxLength={DESCRIPTION_LIMIT}
                            aria-invalid={errors.description ? true : undefined}
                            aria-describedby={errors.description ? `${fieldId}-description-error` : undefined}
                            onChange={(event) => {
                              setDescription(event.target.value)
                              if (errors.description) {
                                setErrors(({ description: _, ...rest }) => rest)
                              }
                            }}
                            placeholder={chosen.prompt}
                          />
                          {errors.description && (
                            <p id={`${fieldId}-description-error`} className="mt-2 flex items-center gap-1.5 text-[13px] font-semibold text-[#8d3029]">
                              <Icon name="warning" className="size-4" />
                              {errors.description}
                            </p>
                          )}
                        </div>
                      </div>

                      {/* -- The secret guard -- */}
                      <div aria-live="polite">
                        {secrets.length > 0 && (
                          <div className="hm-notice hm-notice--stop mt-5" data-secret-warning>
                            <Icon name="shield" className="mt-0.5 size-5 shrink-0" />
                            <span>
                              <strong>This looks like it contains {secrets[0]}.</strong>
                              Please take it out before sending. We never need it, we cannot use
                              it, and anything you send is stored in our inbox. Nothing has left
                              your device yet.
                            </span>
                          </div>
                        )}
                      </div>

                      {status === 'error' && failure && (
                        <div className="hm-notice hm-notice--stop mt-5" role="alert" data-contact-error>
                          <Icon name="warning" className="mt-0.5 size-5 shrink-0" />
                          <span>
                            <strong>{failure.title}</strong>
                            {failure.detail}
                          </span>
                        </div>
                      )}

                      <div className="mt-6 flex flex-wrap items-center gap-3">
                        <button
                          type="submit"
                          className="hm-btn hm-btn--primary"
                          disabled={status === 'submitting'}
                          aria-busy={status === 'submitting'}
                        >
                          {status === 'submitting' ? (
                            <>
                              <Icon name="refresh" className="size-4 animate-spin" />
                              Sending
                            </>
                          ) : (
                            <>
                              <Icon name="eye" className="size-4" />
                              Check and send
                            </>
                          )}
                        </button>
                        <p className="text-[13px] text-ink-soft">
                          Nothing is sent until you press Send in the next step.
                        </p>
                      </div>
                    </form>
                  )}
                </div>
              </Reveal>

              {/* ---- Side rail ---------------------------------------------- */}
              <Reveal delay={110} className="lg:sticky lg:top-28">
                <div className="grid gap-4">
                  <div className="hm-card p-5">
                    <div className="flex items-center gap-2.5">
                      <IconBadge name="mail" size="sm" />
                      <h2 className="font-display text-[18px] font-medium text-ink">
                        Other ways to reach us
                      </h2>
                    </div>
                    <ul className="mt-4 grid gap-2">
                      {[
                        {
                          icon: 'mail' as IconName,
                          label: 'office@hilalmarkets.com',
                          note: 'General questions and partnerships',
                          href: 'mailto:office@hilalmarkets.com',
                        },
                        {
                          icon: 'shield' as IconName,
                          label: 'Privacy requests',
                          note: 'Access, correction, deletion or export',
                          href: '/privacy#your-choices',
                        },
                        // The Help Center row is deliberately not here for now.
                      ].map((route) => (
                        <li key={route.label}>
                          <a
                            href={route.href}
                            className="hm-tilt flex items-center gap-3 rounded-[16px] border border-[#dbe1e7] bg-white p-3 hover:bg-[#fbfcfd]"
                          >
                            <Icon name={route.icon} className="size-5 shrink-0 text-[#55712a]" />
                            <span className="min-w-0">
                              <span className="block truncate text-[14px] font-semibold text-ink">
                                {route.label}
                              </span>
                              <span className="block text-[12.5px] text-ink-soft">{route.note}</span>
                            </span>
                            <Icon name="chevron_right" className="ml-auto size-4 shrink-0 text-[#5c646e]" />
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* The limit, stated before it is met. */}
                  {limits && perPerson !== null && (
                    <div className="hm-card p-5">
                      <div className="flex items-center gap-2.5">
                        <IconBadge name="scale" size="sm" />
                        <h2 className="font-display text-[18px] font-medium text-ink">
                          How many messages
                        </h2>
                      </div>
                      <p className="mt-3 text-[14px] leading-[1.6] text-[#4a505a]">
                        You can send{' '}
                        <strong className="font-semibold text-ink">
                          {perPerson} message{perPerson === 1 ? '' : 's'}
                        </strong>{' '}
                        every {limits.window_hours === 1 ? 'hour' : `${limits.window_hours} hours`}.
                        This keeps the queue short enough that everybody gets a real reply.
                      </p>
                      <p className="mt-2.5 text-[13px] leading-[1.6] text-ink-soft">
                        Need to add something to a message you already sent? Reply to our email
                        instead of writing a new one — it stays in the same conversation.
                      </p>
                    </div>
                  )}

                  <div className="hm-card p-5">
                    <div className="flex items-center gap-2.5">
                      <IconBadge name="lock" size="sm" />
                      <h2 className="font-display text-[18px] font-medium text-ink">
                        What never to send
                      </h2>
                    </div>
                    <ul className="mt-3 grid gap-2">
                      {[
                        'Passwords or recovery codes',
                        'Wallet seed phrases or private keys',
                        'Exchange API keys',
                        'Full card numbers',
                      ].map((item) => (
                        <li key={item} className="flex items-start gap-2.5 text-[14px] text-[#4a505a]">
                          <Icon name="close" className="mt-0.5 size-4 shrink-0 text-[#8d3029]" />
                          {item}
                        </li>
                      ))}
                    </ul>
                    <p className="mt-3 text-[13px] leading-[1.55] text-ink-soft">
                      We will never ask you for any of these, in a message or anywhere else.
                    </p>
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>
      </main>

      {/* ---- Review before sending ---------------------------------------- */}
      <Dialog
        open={reviewing}
        onClose={() => setReviewing(false)}
        title="This is what will be sent"
        description="Check it once. After you press Send it goes straight to our inbox."
        footer={
          <>
            <button type="button" className="hm-btn hm-btn--quiet" onClick={() => setReviewing(false)}>
              <Icon name="chevron_left" className="size-4" />
              Keep editing
            </button>
            <button type="button" className="hm-btn hm-btn--primary" onClick={send}>
              <Icon name="send" className="size-4" />
              Send message
            </button>
          </>
        }
      >
        <dl className="grid gap-4">
          {[
            { label: 'Subject', value: chosen.label, icon: chosen.icon },
            { label: 'Title', value: title.trim(), icon: 'file_text' as IconName },
            { label: 'Reply to', value: email.trim(), icon: 'mail' as IconName },
          ].map((row) => (
            <div key={row.label} className="flex items-start gap-3">
              <IconBadge name={row.icon} size="sm" />
              <div className="min-w-0">
                <dt className="text-[12.5px] font-semibold uppercase tracking-[0.06em] text-ink-soft">
                  {row.label}
                </dt>
                <dd className="mt-0.5 break-words text-[15px] text-ink">{row.value}</dd>
              </div>
            </div>
          ))}
          <div className="flex items-start gap-3">
            <IconBadge name="chat" size="sm" />
            <div className="min-w-0 flex-1">
              <dt className="text-[12.5px] font-semibold uppercase tracking-[0.06em] text-ink-soft">
                Message
              </dt>
              <dd className="mt-1 max-h-[220px] overflow-y-auto whitespace-pre-wrap rounded-[14px] border border-[#e1e5ea] bg-[#fafbfc] p-3 text-[14.5px] leading-[1.65] text-[#4a505a]">
                {description.trim()}
              </dd>
            </div>
          </div>
        </dl>
        {secrets.length > 0 && (
          <div className="hm-notice hm-notice--stop mt-5">
            <Icon name="shield" className="mt-0.5 size-5 shrink-0" />
            <span>
              <strong>This still looks like it contains {secrets[0]}.</strong>
              Go back and remove it. Once it is sent we cannot unsend it.
            </span>
          </div>
        )}
      </Dialog>

      <BackToTop />

      <SiteFooter />
    </div>
  )
}
