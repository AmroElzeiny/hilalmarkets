import type { FirstTouchAttribution, WaitlistErrorType } from './analytics'

/**
 * Why a public form could not be submitted. One list, owned by `analytics.ts`, so the
 * reason shown to the visitor and the reason reported to analytics can never drift
 * apart into two slightly different vocabularies.
 */
export type PublicFormErrorType = WaitlistErrorType

/**
 * How many messages one person may send, and over how long.
 *
 * Sent by the server rather than written here, because the numbers are settings the
 * operator can change. A page that carried its own copy would go on promising a limit
 * that stopped being the one enforced.
 */
export type ContactLimits = {
  per_email: number
  per_client: number
  window_hours: number
}

type Bootstrap = {
  csrf_token: string
  waitlist_endpoint: string
  contact_endpoint: string
  contact_limits?: ContactLimits
}

type WaitlistResponse = {
  status: 'created' | 'already_registered'
  created: boolean
  code: 'waitlist_created' | 'duplicate_email'
  sheet_delivery_status: 'sent' | 'queued' | 'retrying' | 'not_configured'
  message: string
}

type ContactResponse = {
  status: 'sent' | 'queued'
  message: string
  remaining_messages: number
  window_hours: number
}

let bootstrapPromise: Promise<Bootstrap> | null = null

export class PublicFormError extends Error {
  readonly category: PublicFormErrorType
  /**
   * What the server said, when it said something a person can act on.
   *
   * A refused message is the one case where the server knows more than the page: it
   * knows which limit was reached and when it clears. Showing our own guess instead
   * would tell somebody to wait when they cannot, or to retry when they should not.
   */
  readonly detail: string

  constructor(category: PublicFormErrorType, detail = '') {
    super('The form could not be submitted.')
    this.category = category
    this.detail = detail
  }
}

/** The message a server refusal carried, if it carried one a person can read. */
async function refusalDetail(response: Response): Promise<string> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail.message === 'string') return detail.message
  } catch {
    // A refusal with no readable body is still a refusal. The caller has a default.
  }
  return ''
}

async function bootstrap(): Promise<Bootstrap> {
  bootstrapPromise = bootstrapPromise ?? fetch('/api/v1/public-forms/bootstrap', {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  }).then(async (response) => {
    if (!response.ok) throw new PublicFormError('server_error')
    return response.json() as Promise<Bootstrap>
  }).catch((error) => {
    bootstrapPromise = null
    throw error instanceof PublicFormError ? error : new PublicFormError('network_error')
  })
  return bootstrapPromise
}

async function post<T>(endpoint: string, csrfToken: string, payload: object): Promise<T> {
  let response: Response
  try {
    response = await fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken,
      },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new PublicFormError('network_error')
  }
  if (!response.ok) {
    const detail = await refusalDetail(response)
    if (response.status === 429) throw new PublicFormError('rate_limited', detail)
    if (response.status >= 500) throw new PublicFormError('server_error', detail)
    throw new PublicFormError('unknown_error', detail)
  }
  return response.json() as Promise<T>
}

/**
 * The message limit, from the server.
 *
 * Falls back to nothing rather than to a guessed number: a page that invents "2 per
 * hour" while the server enforces something else has told the visitor a lie, and no
 * number at all is better than a wrong one.
 */
export async function contactLimits(): Promise<ContactLimits | null> {
  const config = await bootstrap()
  return config.contact_limits ?? null
}

function idempotency(prefix: string): string {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}:${random}`
}

export async function submitWaitlist(
  values: { email: string },
  attribution: FirstTouchAttribution,
  idempotencyKey: string,
): Promise<WaitlistResponse> {
  const config = await bootstrap()
  return post<WaitlistResponse>(config.waitlist_endpoint, config.csrf_token, {
    email: values.email,
    source_page: window.location.pathname || '/',
    attribution,
    idempotency_key: idempotencyKey,
    company_website: '',
  })
}

export async function submitContact(
  values: { title: string; email: string; description: string },
  idempotencyKey: string,
): Promise<ContactResponse> {
  const config = await bootstrap()
  return post<ContactResponse>(config.contact_endpoint, config.csrf_token, {
    ...values,
    source_page: window.location.pathname || '/contact',
    idempotency_key: idempotencyKey,
    company_website: '',
  })
}

export function newWaitlistIdempotencyKey(): string {
  return idempotency('waitlist')
}

export function newContactIdempotencyKey(): string {
  return idempotency('contact')
}
