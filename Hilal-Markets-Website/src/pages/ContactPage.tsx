import { useRef, useState, type FormEvent } from 'react'
import { trackCtaClick } from '../analytics'
import { CheckIcon } from '../components/brand'
import { Reveal } from '../components/Reveal'
import { SiteFooter, SiteNav } from '../components/SiteChrome'
import {
  newContactIdempotencyKey,
  PublicFormError,
  submitContact,
} from '../publicForms'

type ContactStatus = 'idle' | 'submitting' | 'success' | 'error'

/**
 * Contact is the form and the address, and nothing else.
 *
 * The page used to carry a three-step "A clear route to the team" diagram and, under it,
 * the private-beta waitlist band. Neither helped somebody who came here to write a
 * message: the diagram described our own inbox handling, and the band asked for a second,
 * unrelated action at the moment the person was filling in a form. Both were removed.
 * The band still closes the Privacy and Terms pages, where there is nothing else to do.
 */
export default function ContactPage() {
  const [status, setStatus] = useState<ContactStatus>('idle')
  const idempotencyKey = useRef(newContactIdempotencyKey())

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (status === 'submitting') return
    const form = event.currentTarget
    if (!form.reportValidity()) return
    const data = new FormData(form)
    setStatus('submitting')
    trackCtaClick('submit_contact', 'contact_form')
    try {
      await submitContact(
        {
          title: String(data.get('title') ?? ''),
          email: String(data.get('email') ?? ''),
          description: String(data.get('description') ?? ''),
        },
        idempotencyKey.current,
      )
      setStatus('success')
      form.reset()
      idempotencyKey.current = newContactIdempotencyKey()
    } catch (error) {
      if (!(error instanceof PublicFormError)) console.error('Contact form unavailable')
      setStatus('error')
    }
  }

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <SiteNav />
      <main id="top" className="overflow-hidden pt-36">
        <section className="relative mx-auto max-w-[1200px] px-5 pb-24">
          <div className="pointer-events-none absolute left-1/2 top-0 -z-10 h-[420px] w-[760px] -translate-x-1/2 rounded-full bg-white opacity-80 blur-3xl" aria-hidden="true" />
          <div className="grid items-start gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
            <Reveal>
              <div className="pt-5">
                <p className="text-[13px] font-medium text-apple-deep">Contact Hilal Markets</p>
                <h1 className="mt-4 font-display text-[38px] font-medium leading-[1.04] tracking-[-0.035em] text-[#2b2e35] sm:text-[48px]">
                  How can we help?
                </h1>
                <p className="max-w-[520px] pt-4 text-[17px] leading-[1.65] text-[#2b2e35]">
                  Share a product question, private-beta note, partnership idea, or support request. Please do not include passwords, access codes, wallet secrets, or API keys.
                </p>
                <p className="pt-4 text-[15px] text-[#2b2e35]">
                  Or email us at{' '}
                  <a
                    href="mailto:office@hilalmarkets.com"
                    className="rounded-sm bg-apple/45 px-1.5 py-0.5 font-semibold text-[#2b2e35] underline decoration-dotted decoration-2 decoration-[#63716c] underline-offset-4 transition-colors hover:bg-apple/70 hover:decoration-[#2b2e35] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
                  >
                    office@hilalmarkets.com
                  </a>
                  .
                </p>
              </div>
            </Reveal>

            <Reveal delay={100}>
              <form
                onSubmit={handleSubmit}
                className="rounded-[30px] border border-hairline bg-white p-6 shadow-[0_30px_80px_-56px_rgba(43,46,53,0.65)] sm:p-9"
                data-contact-form
              >
                <div className="mb-8">
                  <h2 className="font-display text-[26px] font-medium tracking-[-0.02em] text-ink">Send a message</h2>
                  <p className="mt-2 text-[14px] leading-relaxed text-ink-soft">All fields are required. Your message is delivered once to the Hilal Markets office inbox.</p>
                </div>

                <div className="space-y-5">
                  <label className="block">
                    <span className="mb-2 block text-[13px] font-medium text-ink">Title</span>
                    <input
                      name="title"
                      required
                      minLength={3}
                      maxLength={180}
                      autoComplete="off"
                      className="w-full rounded-[16px] border border-hairline bg-[#f8fafb] px-4 py-3.5 text-[15px] text-ink outline-none transition focus:border-apple-deep focus:bg-white focus:ring-4 focus:ring-apple/20"
                      placeholder="How can we help?"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-2 block text-[13px] font-medium text-ink">Email</span>
                    <input
                      name="email"
                      type="email"
                      required
                      maxLength={320}
                      autoComplete="email"
                      className="w-full rounded-[16px] border border-hairline bg-[#f8fafb] px-4 py-3.5 text-[15px] text-ink outline-none transition focus:border-apple-deep focus:bg-white focus:ring-4 focus:ring-apple/20"
                      placeholder="you@email.com"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-2 block text-[13px] font-medium text-ink">Description</span>
                    <textarea
                      name="description"
                      required
                      minLength={10}
                      maxLength={5000}
                      rows={7}
                      className="w-full resize-y rounded-[16px] border border-hairline bg-[#f8fafb] px-4 py-3.5 text-[15px] leading-relaxed text-ink outline-none transition focus:border-apple-deep focus:bg-white focus:ring-4 focus:ring-apple/20"
                      placeholder="Add the context that will help us understand your message."
                    />
                  </label>
                </div>

                {status === 'success' && (
                  <div className="mt-6 flex items-start gap-3 rounded-[18px] border border-[#badb76] bg-[#f1fadf] px-4 py-3.5 text-[#35451d]" role="status" data-contact-success>
                    <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-apple">
                      <CheckIcon className="size-4" />
                    </span>
                    <div>
                      <strong className="block text-[14px] font-medium">Your message was sent successfully.</strong>
                      <span className="mt-0.5 block text-[13px] leading-relaxed">The Hilal Markets team has received one copy.</span>
                    </div>
                  </div>
                )}
                {status === 'error' && (
                  <div className="mt-6 rounded-[18px] border border-[#e4b8b2] bg-[#fff5f3] px-4 py-3.5 text-[#702c25]" role="alert" data-contact-error>
                    <strong className="block text-[14px] font-medium">We could not send your message.</strong>
                    <span className="mt-0.5 block text-[13px]">Please check your connection and try again.</span>
                  </div>
                )}

                <button
                  type="submit"
                  disabled={status === 'submitting'}
                  aria-busy={status === 'submitting'}
                  className="mt-6 inline-flex w-full items-center justify-center rounded-full bg-apple px-7 py-4 text-[15px] font-semibold text-ink shadow-[0_14px_32px_-20px_rgba(120,170,40,0.8)] transition-transform hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-ink disabled:cursor-wait disabled:opacity-70"
                >
                  {status === 'submitting' ? 'Sending...' : 'Submit'}
                </button>
              </form>
            </Reveal>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
