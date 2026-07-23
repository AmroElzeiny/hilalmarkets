export type AnalyticsConsent = {
  analytics: boolean
  marketing: boolean
}

export type FirstTouchAttribution = {
  utm_source?: string
  utm_medium?: string
  utm_campaign?: string
  utm_content?: string
  utm_term?: string
  referrer?: string
  landing_page?: string
}

export type WaitlistErrorType =
  | 'invalid_email'
  | 'required_field'
  | 'duplicate_email'
  | 'rate_limited'
  | 'network_error'
  | 'server_error'
  | 'unknown_error'

type EventParameters = Record<string, string | number | boolean | undefined>

type AnalyticsRuntimeConfig = {
  enabled?: boolean
  gtmId?: string
  metaPixelId?: string
  metaPixelEnabled?: boolean
  xPixelId?: string
  xPixelEnabled?: boolean
  siteUrl?: string
  debug?: boolean
}

export type LegalRuntimeConfig = {
  legalName?: string | null
  companyAddress?: string | null
  governingLaw?: string | null
  privacyEmail?: string | null
  supportEmail?: string | null
  reviewRequired?: boolean
}

declare global {
  interface Window {
    HilalMarketsRuntimeConfig?: {
      analytics?: AnalyticsRuntimeConfig
      legal?: LegalRuntimeConfig
    }
    HilalAnalytics?: typeof publicAnalyticsApi
    dataLayer?: unknown[]
    gtag?: (...args: unknown[]) => void
    fbq?: MetaPixelFunction
    _fbq?: MetaPixelFunction
    twq?: XPixelFunction
    __hmXConfiguredPixels?: Set<string>
    __hmConsentDefaultSet?: boolean
    __hmAnalyticsInitialized?: boolean
  }
}

type MetaPixelFunction = ((...args: unknown[]) => void) & {
  callMethod?: (...args: unknown[]) => void
  queue?: unknown[][]
  loaded?: boolean
  version?: string
  push?: (...args: unknown[]) => void
}

type XPixelFunction = ((...args: unknown[]) => void) & {
  exe?: (...args: unknown[]) => void
  queue?: unknown[][]
  version?: string
  integration?: string
}

const ATTRIBUTION_KEY = 'hm-first-touch-attribution-v1'
const FORBIDDEN_PARAMETER_KEYS = new Set([
  'email',
  'email_address',
  'user_id',
  'password',
  'description',
  'message',
  'free_text',
  'ip',
  'ip_address',
])
const VALID_GTM = /^GTM-[A-Z0-9]+$/
const VALID_PIXEL = /^[0-9]{5,32}$/
const VALID_X_PIXEL = /^[A-Za-z0-9]{3,32}$/

let consent: AnalyticsConsent = { analytics: false, marketing: false }
let googleLoaded = false
let metaLoaded = false
let xLoaded = false
let volatileAttribution: FirstTouchAttribution | null = null
let lastGooglePageView = ''
let lastMetaPageView = ''
let googlePageViewSequence = 0
const onceEvents = new Set<string>()
const recentEvents = new Map<string, number>()

function runtimeConfig(): AnalyticsRuntimeConfig {
  if (typeof window === 'undefined') return {}
  return window.HilalMarketsRuntimeConfig?.analytics ?? {}
}

function debug(event: string, parameters?: EventParameters) {
  if (!runtimeConfig().debug) return
  // Parameters are filtered before debug output so private form values cannot leak.
  console.info('[Hilal analytics]', event, sanitizeParameters(parameters ?? {}))
}

function pagePath(): string {
  if (typeof window === 'undefined') return '/'
  return window.location.pathname || '/'
}

function safePageLocation(): string {
  if (typeof window === 'undefined') return ''
  return `${window.location.origin}${pagePath()}`
}

function sanitizeParameters(parameters: EventParameters): EventParameters {
  return Object.fromEntries(
    Object.entries(parameters).filter(
      ([key, value]) =>
        !FORBIDDEN_PARAMETER_KEYS.has(key.toLowerCase()) &&
        value !== undefined &&
        ['string', 'number', 'boolean'].includes(typeof value),
    ),
  )
}

function ensureConsentDefaults() {
  if (typeof window === 'undefined') return
  window.dataLayer = window.dataLayer || []
  window.gtag = window.gtag || function (...args: unknown[]) {
    window.dataLayer?.push(args)
  }
  if (window.__hmConsentDefaultSet) return
  window.gtag('consent', 'default', {
    ad_storage: 'denied',
    analytics_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    functionality_storage: 'denied',
    personalization_storage: 'denied',
    security_storage: 'granted',
    wait_for_update: 500,
  })
  window.__hmConsentDefaultSet = true
}

function injectScript(src: string, marker: string, onError?: () => void) {
  if (typeof document === 'undefined') return
  if (document.querySelector(`script[data-hm-provider="${marker}"]`)) return
  const script = document.createElement('script')
  script.async = true
  script.src = src
  script.dataset.hmProvider = marker
  if (onError) script.addEventListener('error', onError, { once: true })
  document.head.appendChild(script)
}

function loadGoogle() {
  const config = runtimeConfig()
  if (!config.enabled || googleLoaded || !consent.analytics) return
  const gtmId = String(config.gtmId ?? '').trim().toUpperCase()
  if (!VALID_GTM.test(gtmId)) return
  const loadedContainer = Array.from(document.scripts).some((item) => {
    try {
      const source = new URL(item.src)
      return source.hostname === 'www.googletagmanager.com'
        && source.pathname === '/gtm.js'
        && source.searchParams.get('id') === gtmId
    } catch {
      return false
    }
  })
  googleLoaded = true
  if (loadedContainer) return
  window.dataLayer?.push({ 'gtm.start': Date.now(), event: 'gtm.js' })
  injectScript(
    `https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(gtmId)}`,
    'google-tag-manager',
    () => debug('google_script_error'),
  )
}

function installMetaStub(): MetaPixelFunction {
  if (window.fbq) return window.fbq
  const fbq = function (...args: unknown[]) {
    if (fbq.callMethod) fbq.callMethod(...args)
    else fbq.queue?.push(args)
  } as MetaPixelFunction
  fbq.push = fbq
  fbq.loaded = true
  fbq.version = '2.0'
  fbq.queue = []
  window.fbq = fbq
  window._fbq = fbq
  return fbq
}

function loadMeta() {
  const config = runtimeConfig()
  const pixelId = String(config.metaPixelId ?? '').trim()
  if (
    !config.metaPixelEnabled ||
    metaLoaded ||
    !consent.marketing ||
    !VALID_PIXEL.test(pixelId)
  ) return
  metaLoaded = true
  const fbq = installMetaStub()
  fbq('consent', 'grant')
  fbq('init', pixelId)
  injectScript(
    'https://connect.facebook.net/en_US/fbevents.js',
    'meta-pixel',
    () => debug('meta_script_error'),
  )
}

function loadX() {
  const config = runtimeConfig()
  const pixelId = String(config.xPixelId ?? '').trim()
  if (
    !config.xPixelEnabled ||
    xLoaded ||
    !consent.marketing ||
    !VALID_X_PIXEL.test(pixelId)
  ) return

  const configuredPixels = window.__hmXConfiguredPixels instanceof Set
    ? window.__hmXConfiguredPixels
    : new Set<string>()
  window.__hmXConfiguredPixels = configuredPixels
  if (configuredPixels.has(pixelId)) {
    xLoaded = true
    return
  }

  const twq = window.twq || function (...args: unknown[]) {
    if (twq.exe) twq.exe(...args)
    else twq.queue?.push(args)
  } as XPixelFunction
  twq.version = '1.1'
  twq.queue = twq.queue || []
  twq.integration = 'gtm-ad-manager'
  window.twq = twq

  configuredPixels.add(pixelId)
  xLoaded = true
  twq('config', pixelId)
  injectScript(
    'https://static.ads-twitter.com/uwt.js',
    'x-pixel',
    () => {
      configuredPixels.delete(pixelId)
      xLoaded = false
      debug('x_pixel_script_error')
    },
  )
}

function emitGoogle(event: string, parameters: EventParameters): boolean {
  if (!runtimeConfig().enabled || !consent.analytics) return false
  loadGoogle()
  if (!googleLoaded) return false
  const clean = sanitizeParameters(parameters)
  if (runtimeConfig().debug) clean.debug_mode = true
  if (VALID_GTM.test(String(runtimeConfig().gtmId ?? '').trim().toUpperCase())) {
    window.dataLayer?.push({ event, ...clean })
  } else {
    window.gtag?.('event', event, clean)
  }
  debug(event, clean)
  return true
}

function emitMeta(event: 'PageView' | 'Lead'): boolean {
  if (!consent.marketing) return false
  loadMeta()
  if (!metaLoaded || !window.fbq) return false
  window.fbq('track', event)
  debug(`meta:${event}`)
  return true
}

function emitOnce(key: string, send: () => boolean): boolean {
  if (onceEvents.has(key)) return false
  if (!send()) return false
  onceEvents.add(key)
  return true
}

function emitDebounced(key: string, send: () => boolean): boolean {
  const now = Date.now()
  if (now - (recentEvents.get(key) ?? 0) < 750) return false
  if (!send()) return false
  recentEvents.set(key, now)
  return true
}

export function trackPageView(): boolean {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false
  const key = `${pagePath()}|${document.title}`
  const googleSent = lastGooglePageView !== key && emitGoogle('page_view', {
      page_location: safePageLocation(),
      page_path: pagePath(),
      page_title: document.title,
    })
  if (googleSent) {
    lastGooglePageView = key
    googlePageViewSequence += 1
  }
  const metaSent = lastMetaPageView !== key && emitMeta('PageView')
  if (metaSent) lastMetaPageView = key
  return googleSent || metaSent
}

export function trackSectionView(sectionName: string): boolean {
  const normalized = sectionName.trim().slice(0, 80)
  if (!normalized) return false
  return emitOnce(`section:${googlePageViewSequence}:${pagePath()}:${normalized}`, () =>
    emitGoogle('section_view', {
      section_name: normalized,
      page_path: pagePath(),
    }),
  )
}

export function trackFaqOpen(faqId: string): boolean {
  const normalized = faqId.trim().slice(0, 80)
  if (!normalized) return false
  return emitOnce(`faq:${googlePageViewSequence}:${pagePath()}:${normalized}`, () =>
    emitGoogle('faq_open', {
      faq_id: normalized,
      page_path: pagePath(),
    }),
  )
}

export function trackCtaClick(
  ctaName: string,
  ctaLocation: string,
  destination = '',
): boolean {
  const cleanName = ctaName.trim().slice(0, 80)
  const cleanLocation = ctaLocation.trim().slice(0, 80)
  const cleanDestination = destination.startsWith('/') || destination.startsWith('#')
    ? destination.slice(0, 240)
    : destination ? 'external' : ''
  return emitDebounced(
    `cta:${pagePath()}:${cleanName}:${cleanLocation}:${cleanDestination}`,
    () =>
      emitGoogle('cta_click', {
        cta_name: cleanName,
        cta_location: cleanLocation,
        destination: cleanDestination,
        page_path: pagePath(),
      }),
  )
}

export function trackWaitlistFormView(formLocation: string): boolean {
  return emitOnce(`waitlist-view:${pagePath()}:${formLocation}`, () =>
    emitGoogle('waitlist_form_view', {
      form_location: formLocation.slice(0, 80),
      page_path: pagePath(),
    }),
  )
}

export function trackWaitlistFormStart(formLocation: string): boolean {
  return emitOnce(`waitlist-start:${pagePath()}:${formLocation}`, () =>
    emitGoogle('waitlist_form_start', {
      form_location: formLocation.slice(0, 80),
      page_path: pagePath(),
    }),
  )
}

export function trackWaitlistSubmitAttempt(formLocation: string): boolean {
  return emitDebounced(`waitlist-attempt:${pagePath()}:${formLocation}`, () =>
    emitGoogle('waitlist_submit_attempt', {
      form_location: formLocation.slice(0, 80),
      page_path: pagePath(),
    }),
  )
}

export function trackWaitlistSuccess(
  formLocation: string,
  submissionId = '',
): boolean {
  const eventScope = submissionId.trim().slice(0, 128)
    || `${pagePath()}:${formLocation.slice(0, 80)}`
  const google = emitOnce(`waitlist-success:${eventScope}`, () =>
    emitGoogle('waitlist_signup_success', {}),
  )
  const meta = emitOnce(`meta-lead:${eventScope}`, () => emitMeta('Lead'))
  return google || meta
}

export function trackWaitlistError(
  errorType: WaitlistErrorType,
  formLocation: string,
): boolean {
  return emitGoogle('waitlist_form_error', {
    error_type: errorType,
    form_location: formLocation.slice(0, 80),
    page_path: pagePath(),
  })
}

function readStoredAttribution(): FirstTouchAttribution | null {
  if (!consent.analytics || typeof window === 'undefined') return null
  try {
    const parsed = JSON.parse(window.localStorage.getItem(ATTRIBUTION_KEY) ?? 'null')
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function currentAttribution(): FirstTouchAttribution {
  if (typeof window === 'undefined' || typeof document === 'undefined') return {}
  const query = new URLSearchParams(window.location.search)
  let referrer: string | undefined
  if (document.referrer) {
    try {
      const parsed = new URL(document.referrer)
      referrer = `${parsed.origin}${parsed.pathname}`
    } catch {
      referrer = undefined
    }
  }
  const result: FirstTouchAttribution = {
    referrer,
    landing_page: safePageLocation(),
  }
  for (const key of ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'] as const) {
    const value = query.get(key)?.trim()
    if (value) result[key] = value.slice(0, 120)
  }
  return result
}

export function captureFirstTouchAttribution(): FirstTouchAttribution {
  volatileAttribution = volatileAttribution ?? currentAttribution()
  const stored = readStoredAttribution()
  if (stored) return stored
  if (consent.analytics) {
    try {
      window.localStorage.setItem(ATTRIBUTION_KEY, JSON.stringify(volatileAttribution))
    } catch {
      // Attribution remains optional when first-party storage is unavailable.
    }
    return volatileAttribution
  }
  return {}
}

export function getFirstTouchAttribution(): FirstTouchAttribution {
  return readStoredAttribution() ?? captureFirstTouchAttribution()
}

function setConsent(next: Partial<AnalyticsConsent>) {
  consent = {
    analytics: Boolean(next.analytics),
    marketing: Boolean(next.marketing),
  }
  ensureConsentDefaults()
  window.gtag?.('consent', 'update', {
    analytics_storage: consent.analytics ? 'granted' : 'denied',
    ad_storage: consent.marketing ? 'granted' : 'denied',
    ad_user_data: consent.marketing ? 'granted' : 'denied',
    ad_personalization: consent.marketing ? 'granted' : 'denied',
  })
  if (consent.analytics) {
    captureFirstTouchAttribution()
    loadGoogle()
  }
  if (consent.marketing) {
    loadMeta()
    loadX()
  } else if (window.fbq) {
    window.fbq('consent', 'revoke')
  }
  trackPageView()
}

export function initializeAnalytics() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  if (window.__hmAnalyticsInitialized) return
  window.__hmAnalyticsInitialized = true
  ensureConsentDefaults()
  volatileAttribution = currentAttribution()
  setConsent({
    analytics: document.documentElement.dataset.consentAnalytics === 'granted',
    marketing: document.documentElement.dataset.consentMarketing === 'granted',
  })
  window.addEventListener('hm:consent-updated', (event) => {
    const detail = (event as CustomEvent<Partial<AnalyticsConsent>>).detail ?? {}
    setConsent(detail)
  })
  const notifyRoute = () => {
    window.setTimeout(trackPageView, 0)
  }
  window.addEventListener('popstate', notifyRoute)
  const originalPushState = window.history.pushState.bind(window.history)
  window.history.pushState = (data, unused, url) => {
    originalPushState(data, unused, url)
    notifyRoute()
  }
  const originalReplaceState = window.history.replaceState.bind(window.history)
  window.history.replaceState = (data, unused, url) => {
    originalReplaceState(data, unused, url)
    notifyRoute()
  }
  trackPageView()
}

const publicAnalyticsApi = {
  trackPageView,
  trackSectionView,
  trackFaqOpen,
  trackCtaClick,
  trackWaitlistFormView,
  trackWaitlistFormStart,
  trackWaitlistSubmitAttempt,
  trackWaitlistSuccess,
  trackWaitlistError,
  captureFirstTouchAttribution,
  getFirstTouchAttribution,
}

if (typeof window !== 'undefined') {
  window.HilalAnalytics = publicAnalyticsApi
}
