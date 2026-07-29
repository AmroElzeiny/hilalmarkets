export type PublicFormErrorType =
  | 'invalid_email'
  | 'required_field'
  | 'duplicate_email'
  | 'rate_limited'
  | 'network_error'
  | 'server_error'
  | 'unknown_error'

type Bootstrap = {
  csrf_token: string
  contact_endpoint: string
}

type ContactResponse = {
  status: 'sent' | 'queued'
  message: string
}

let bootstrapPromise: Promise<Bootstrap> | null = null

export class PublicFormError extends Error {
  readonly category: PublicFormErrorType

  constructor(category: PublicFormErrorType) {
    super('The form could not be submitted.')
    this.category = category
  }
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
    if (response.status === 429) throw new PublicFormError('rate_limited')
    if (response.status >= 500) throw new PublicFormError('server_error')
    throw new PublicFormError('unknown_error')
  }
  return response.json() as Promise<T>
}

function idempotency(prefix: string): string {
  const random = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}:${random}`
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

export function newContactIdempotencyKey(): string {
  return idempotency('contact')
}
