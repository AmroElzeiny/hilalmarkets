/**
 * Privacy and Terms — one page that reads two documents.
 *
 * The page this replaces was nineteen white cards of unbroken legal prose with a list
 * of links beside them. Everything that was wrong with it comes down to one thing: it
 * was written to be complete and not to be read.
 *
 * What changed, and why each change is not decoration:
 *
 * - **Two reading modes.** *Plain summary* shows one or two sentences per section, and
 *   is what opens first. *Full text* opens every clause. A beginner gets a page they can
 *   finish; a lawyer gets every word; neither is asked to be the other.
 * - **The list beside it now knows where you are.** It marks the section being read,
 *   with a bar and a weight change as well as a colour, and a line shows how much of the
 *   document is left. Before this, twenty links all looked identical however far you had
 *   read.
 * - **You can search it.** Nineteen sections is more than anybody scans.
 * - **The date is on the page.** The old text said "the Last updated date changes when
 *   this policy is revised" — and no date was shown anywhere. It pointed at something
 *   that did not exist.
 * - **It prints.** A legal document people cannot keep a copy of is a worse legal
 *   document. Printing opens every clause, whatever was open on screen.
 * - **The waitlist band is gone.** Asking somebody to sign up for something else, in the
 *   middle of reading what they are agreeing to, is the wrong thing to do on this page.
 *   Where it stood there are now links to the documents that go with this one.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Icon, IconBadge } from '../components/Icon'
import {
  jumpTo,
  useReadingProgress,
  useScrollSpy,
  useTilt,
} from '../components/interactions'
import { Reveal } from '../components/Reveal'
import { BackToTop, SiteFooter, SiteNav } from '../components/SiteChrome'
import {
  cookiesDocument,
  privacyDocument,
  termsDocument,
  type LegalDocument,
  type LegalKind,
  type LegalSection,
} from '../legal/documents'
import { DURATION, move, moveEach, whenSeen } from '../motion'

const FALLBACK_EMAIL = 'office@hilalmarkets.com'

/**
 * Marks the section a jump landed on.
 *
 * After the page moves, twenty identical white cards look the same and the eye has to
 * find the right one. A brief ring says "this is the one you asked for" and then gets
 * out of the way. It is the only movement on this page that is not an entrance, and it
 * exists because losing your place is the actual problem with a long document.
 */
function markArrival(id: string): void {
  const node = document.getElementById(id)
  if (!node) return
  void move(
    node,
    { boxShadow: ['0 0 0 0 rgba(85,113,42,0)', '0 0 0 4px rgba(85,113,42,0.32)', '0 0 0 0 rgba(85,113,42,0)'] },
    { duration: DURATION.slow * 2 },
  ).then(() => node.style.removeProperty('box-shadow'))
}

/** Roughly how long the full text takes to read, from its own word count. */
function readingMinutes(document_: LegalDocument): number {
  const words = document_.sections.reduce(
    (total, section) => total + section.short.split(/\s+/).length + 110,
    0,
  )
  return Math.max(2, Math.round(words / 200))
}

/* -------------------------------------------------------------------------- */
/*  One section                                                                */
/* -------------------------------------------------------------------------- */
function Section({
  section,
  index,
  expanded,
  onToggle,
}: {
  section: LegalSection
  index: number
  expanded: boolean
  onToggle: () => void
}) {
  const bodyId = `${section.id}-full`
  return (
    <article id={section.id} className="hm-legal-section hm-card p-5 sm:p-7">
      <div className="flex items-start gap-3.5">
        <IconBadge name={section.icon} />
        <div className="min-w-0 flex-1">
          <p className="text-[12px] font-semibold uppercase tracking-[0.09em] text-ink-soft tnum">
            Section {index + 1}
          </p>
          <h2 className="mt-1 font-display text-[22px] font-medium leading-[1.2] tracking-[-0.02em] text-ink outline-none sm:text-[25px]">
            {section.title}
          </h2>
        </div>
      </div>

      {/* The plain sentence comes first, always. Somebody can read only these and
          still know what they agreed to. */}
      <div className="hm-inshort mt-4">
        <Icon name="spark" className="mt-0.5 size-4 shrink-0 text-[#55712a]" />
        <span>
          <strong className="font-semibold">In short: </strong>
          {section.short}
        </span>
      </div>

      <button
        type="button"
        className="hm-disclose mt-3"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        {expanded ? 'Hide the full wording' : 'Read the full wording'}
        <Icon name="chevron" className="size-4" />
      </button>

      <div id={bodyId} className="hm-collapse" data-open={expanded} role="region" aria-label={`${section.title}, full wording`}>
        <div>
          <div className="hm-legal-body pt-2">{section.body}</div>
        </div>
      </div>
    </article>
  )
}

/* -------------------------------------------------------------------------- */
/*  The page                                                                   */
/* -------------------------------------------------------------------------- */
export default function LegalPage({ kind }: { kind: LegalKind }) {
  const config = window.HilalMarketsRuntimeConfig?.legal ?? {}
  // Privacy and cookies are both about what is held about a person, so both go to the
  // privacy address; the Terms are about the agreement, so they go to support.
  const email =
    (kind === 'terms' ? config.supportEmail : config.privacyEmail) || FALLBACK_EMAIL
  const document_ = useMemo(() => {
    if (kind === 'privacy') return privacyDocument(email)
    if (kind === 'cookies') return cookiesDocument(email)
    return termsDocument(email)
  }, [email, kind])

  const [mode, setMode] = useState<'summary' | 'full'>('summary')
  const [openSections, setOpenSections] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [copied, setCopied] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const highlightsRef = useRef<HTMLUListElement>(null)

  const ids = useMemo(() => document_.sections.map((section) => section.id), [document_])
  const active = useScrollSpy(ids)
  const progress = useReadingProgress()

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return document_.sections
    return document_.sections.filter(
      (section) =>
        section.title.toLowerCase().includes(needle) ||
        section.short.toLowerCase().includes(needle),
    )
  }, [document_, query])

  // Switching to Full text opens every clause; switching back closes them. The two
  // modes are one control, so they must not leave half the page in the other state.
  const setReadingMode = useCallback(
    (next: 'summary' | 'full') => {
      setMode(next)
      setOpenSections(next === 'full' ? new Set(ids) : new Set())
    },
    [ids],
  )

  // A deep link must land on an open clause. Somebody sent /terms#billing is being
  // sent to the wording, not to the summary of it.
  useEffect(() => {
    const target = window.location.hash.slice(1)
    if (!target || !ids.includes(target)) return
    setOpenSections((current) => new Set(current).add(target))
    window.setTimeout(() => {
      jumpTo(target)
      markArrival(target)
    }, 60)
  }, [ids])

  // The four summary cards arrive one after another, which is the brand's own reading
  // of motion: a calm sequence that says "these belong together" rather than four
  // things appearing at once. `stagger` comes from the animation library.
  useEffect(
    () =>
      whenSeen(highlightsRef.current, () => {
        const cards = Array.from(
          highlightsRef.current?.querySelectorAll<HTMLElement>('li') ?? [],
        )
        void moveEach(
          cards,
          { opacity: [0, 1], transform: ['translateY(12px)', 'translateY(0)'] },
          { duration: DURATION.base, stagger: 0.07 },
        )
      }),
    [],
  )

  // The rail scrolls itself, so the section being read stays in view rather than
  // sliding off the top of a list twenty items long.
  useEffect(() => {
    const link = listRef.current?.querySelector<HTMLElement>(`[data-rail="${active}"]`)
    link?.scrollIntoView({ block: 'nearest' })
  }, [active])

  function toggle(id: string) {
    setOpenSections((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function copyLink() {
    const address = `${window.location.origin}${window.location.pathname}${active ? `#${active}` : ''}`
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2200)
    } catch {
      // Copying can be refused by the browser. Saying nothing would look like it
      // worked, so the address goes in the bar instead and the person can copy it.
      window.location.hash = active
    }
  }

  const minutes = readingMinutes(document_)

  return (
    <div className="hm-page min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main id="top" tabIndex={-1} className="outline-none">
        {/* ---- Opening ---------------------------------------------------- */}
        <section className="relative overflow-hidden pt-32 pb-8 sm:pt-36">
          <div className="hm-aura hm-aura--cool left-1/2 top-0 h-[420px] w-[860px] -translate-x-1/2" aria-hidden="true" />
          <div className="hm-shell">
            <Reveal>
              <p className="hm-eyebrow">
                <Icon name={kind === 'cookies' ? 'cookie' : 'scale'} className="size-4" />
                {kind === 'privacy' ? 'Your data' : kind === 'cookies' ? 'Your browser' : 'The rules'}
              </p>
              <h1 className="mt-5 font-display text-[38px] leading-[1.04] tracking-[-0.035em] text-ink sm:text-[52px]">
                {document_.title}
              </h1>
              <p className="mt-4 max-w-[62ch] text-[17px] leading-[1.65] text-[#4a505a]">
                {document_.lede}
              </p>

              {/* Facts about the document itself, which the old page did not show at
                  all — including the date its own text refers to. */}
              <dl className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13.5px] text-ink-soft">
                {[
                  { icon: 'calendar' as const, label: 'Last updated', value: document_.updated },
                  { icon: 'version' as const, label: 'Version', value: document_.version },
                  { icon: 'clock' as const, label: 'Reading time', value: `about ${minutes} minutes` },
                  {
                    icon: 'list' as const,
                    label: 'Sections',
                    value: String(document_.sections.length),
                  },
                ].map((fact) => (
                  <div key={fact.label} className="flex items-center gap-1.5">
                    <Icon name={fact.icon} className="size-4 text-[#5c646e]" />
                    <dt className="sr-only">{fact.label}</dt>
                    <dd>
                      <span className="text-ink-soft">{fact.label}: </span>
                      <span className="font-semibold text-ink tnum">{fact.value}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </Reveal>

            {/* ---- The whole document in four lines ------------------------ */}
            <ul ref={highlightsRef} className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {document_.highlights.map((item) => (
                <HighlightCard key={item.label} {...item} />
              ))}
            </ul>
          </div>
        </section>

        {/* ---- Reading controls -------------------------------------------- */}
        <section className="hm-no-print pb-6">
          <div className="hm-shell">
            <Reveal delay={40}>
              <div className="hm-card flex flex-wrap items-center gap-3 p-3 sm:p-4">
                <div className="hm-segment" role="group" aria-label="How much to show">
                  <button
                    type="button"
                    aria-pressed={mode === 'summary'}
                    onClick={() => setReadingMode('summary')}
                  >
                    <Icon name="list" className="size-4" />
                    Plain summary
                  </button>
                  <button
                    type="button"
                    aria-pressed={mode === 'full'}
                    onClick={() => setReadingMode('full')}
                  >
                    <Icon name="file_text" className="size-4" />
                    Full text
                  </button>
                </div>

                <div className="relative min-w-[200px] flex-1">
                  <label className="sr-only" htmlFor="legal-search">
                    Search this document
                  </label>
                  <Icon
                    name="search"
                    className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-[#5c646e]"
                  />
                  <input
                    id="legal-search"
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search, e.g. delete my data"
                    className="hm-input !py-2.5 !pl-11 !text-[14.5px]"
                  />
                </div>

                <div className="flex gap-2">
                  <button type="button" className="hm-btn hm-btn--quiet hm-btn--sm" onClick={copyLink}>
                    <Icon name={copied ? 'check' : 'link'} className="size-4" />
                    {copied ? 'Link copied' : 'Copy link'}
                  </button>
                  <button
                    type="button"
                    className="hm-btn hm-btn--quiet hm-btn--sm"
                    onClick={() => window.print()}
                  >
                    <Icon name="printer" className="size-4" />
                    Print
                  </button>
                </div>
              </div>
            </Reveal>

            <p className="mt-3 flex items-start gap-2 text-[13px] leading-[1.55] text-ink-soft">
              <Icon name="info" className="mt-0.5 size-4 shrink-0 text-[#55712a]" />
              <span>
                The <strong className="font-semibold text-ink">In short</strong> line is a
                plain summary to help you read. The full wording under it is the part that
                applies.
              </span>
            </p>
          </div>
        </section>

        {/* ---- Rail and sections ------------------------------------------- */}
        <section className="pb-16">
          <div className="hm-shell">
            <div className="grid gap-7 lg:grid-cols-[264px_minmax(0,1fr)] lg:items-start">
              {/* -- The rail -- */}
              <nav className="hm-rail hm-no-print hidden lg:flex" aria-label={`${document_.title} sections`}>
                <div className="hm-card flex min-h-0 flex-col p-3">
                  <div className="px-2 pb-3">
                    <p className="text-[12px] font-semibold uppercase tracking-[0.08em] text-ink-soft">
                      On this page
                    </p>
                    <div className="mt-2.5 flex items-center gap-2">
                      <div className="hm-rail-progress flex-1">
                        <span style={{ width: `${Math.round(progress * 100)}%` }} />
                      </div>
                      <span className="text-[12px] font-semibold text-ink-soft tnum">
                        {Math.round(progress * 100)}%
                      </span>
                    </div>
                  </div>
                  <div ref={listRef} className="hm-rail-list">
                    {document_.sections.map((section, index) => (
                      <a
                        key={section.id}
                        href={`#${section.id}`}
                        data-rail={section.id}
                        aria-current={active === section.id ? 'true' : undefined}
                        className="hm-rail-link"
                        onClick={(event) => {
                          event.preventDefault()
                          setOpenSections((current) => new Set(current).add(section.id))
                          window.history.replaceState(null, '', `#${section.id}`)
                          jumpTo(section.id)
                          markArrival(section.id)
                        }}
                      >
                        <span className="w-4 shrink-0 text-[12px] font-semibold text-ink-soft tnum">
                          {index + 1}
                        </span>
                        <span className="min-w-0">{section.title}</span>
                      </a>
                    ))}
                  </div>
                </div>
              </nav>

              {/* -- The sections -- */}
              <div>
                <div aria-live="polite" className="sr-only">
                  {query.trim()
                    ? `${visible.length} section${visible.length === 1 ? '' : 's'} match your search.`
                    : ''}
                </div>

                {visible.length === 0 ? (
                  <div className="hm-card flex items-center gap-3 p-6">
                    <IconBadge name="search" size="sm" />
                    <p className="text-[15px] text-[#4a505a]">
                      No section mentions &ldquo;{query.trim()}&rdquo;. Try a simpler word, or{' '}
                      <a href="/contact" className="font-semibold text-ink underline underline-offset-4">
                        ask us directly
                      </a>
                      .
                    </p>
                  </div>
                ) : (
                  <div className="grid gap-4">
                    {visible.map((section) => (
                      <Reveal key={section.id}>
                        <Section
                          section={section}
                          index={document_.sections.indexOf(section)}
                          expanded={openSections.has(section.id)}
                          onToggle={() => toggle(section.id)}
                        />
                      </Reveal>
                    ))}
                  </div>
                )}

                {/* -- Where the waitlist band used to be -- */}
                <Reveal>
                  <div className="hm-card mt-6 p-5 sm:p-7">
                    <div className="flex items-center gap-3">
                      <IconBadge name="layers" tone="apple" />
                      <h2 className="font-display text-[21px] font-medium tracking-[-0.02em] text-ink">
                        The rest of the paperwork
                      </h2>
                    </div>
                    <p className="mt-2 text-[14.5px] leading-[1.6] text-[#4a505a]">
                      These four documents go together. Each one is short and says one thing.
                    </p>
                    <ul className="mt-4 grid gap-2.5 sm:grid-cols-2">
                      {[
                        {
                          href: '/privacy',
                          icon: 'shield' as const,
                          label: 'Privacy Policy',
                          note: 'What we hold, and what you can ask for.',
                        },
                        {
                          href: '/terms',
                          icon: 'scale' as const,
                          label: 'Terms of Use',
                          note: 'The rules for using the service.',
                        },
                        {
                          href: '/cookies',
                          icon: 'cookie' as const,
                          label: 'Cookie Policy',
                          note: 'What is stored in your browser, and why.',
                        },
                        {
                          href: '/risk-disclosure',
                          icon: 'alert' as const,
                          label: 'Risk Disclosure',
                          note: 'What can go wrong in crypto markets.',
                        },
                      ]
                        .filter((item) => item.href !== window.location.pathname)
                        .map((item) => (
                          <RelatedCard key={item.href} {...item} />
                        ))}
                    </ul>
                    <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-[#e4e9ee] pt-5">
                      <a href="/contact" className="hm-btn hm-btn--primary hm-btn--sm">
                        <Icon name="chat" className="size-4" />
                        Ask us about this
                      </a>
                      <p className="text-[13.5px] text-ink-soft">
                        If any part of this page is unclear, that is our problem to fix.
                      </p>
                    </div>
                  </div>
                </Reveal>
              </div>
            </div>
          </div>
        </section>
      </main>

      <BackToTop />

      <SiteFooter />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
function HighlightCard({
  icon,
  label,
  note,
}: {
  icon: LegalDocument['highlights'][number]['icon']
  label: string
  note: string
}) {
  const tilt = useTilt<HTMLLIElement>()
  return (
    <li
      ref={tilt.ref}
      onPointerMove={tilt.onPointerMove}
      onPointerLeave={tilt.onPointerLeave}
      className="hm-tilt hm-card flex gap-3 p-4"
    >
      <IconBadge name={icon} tone="apple" size="sm" />
      <div className="min-w-0">
        <p className="text-[14.5px] font-semibold leading-[1.35] text-ink">{label}</p>
        <p className="mt-1 text-[13px] leading-[1.5] text-ink-soft">{note}</p>
      </div>
    </li>
  )
}

function RelatedCard({
  href,
  icon,
  label,
  note,
}: {
  href: string
  icon: LegalDocument['highlights'][number]['icon']
  label: string
  note: string
}) {
  const tilt = useTilt<HTMLAnchorElement>()
  return (
    <li>
      <a
        ref={tilt.ref}
        href={href}
        onPointerMove={tilt.onPointerMove}
        onPointerLeave={tilt.onPointerLeave}
        className="hm-tilt flex items-center gap-3 rounded-[16px] border border-[#dbe1e7] bg-white p-3.5 hover:bg-[#fbfcfd]"
      >
        <Icon name={icon} className="size-5 shrink-0 text-[#55712a]" />
        <span className="min-w-0 flex-1">
          <span className="block text-[14.5px] font-semibold text-ink">{label}</span>
          <span className="block text-[12.5px] leading-[1.45] text-ink-soft">{note}</span>
        </span>
        <Icon name="chevron_right" className="size-4 shrink-0 text-[#5c646e]" />
      </a>
    </li>
  )
}
