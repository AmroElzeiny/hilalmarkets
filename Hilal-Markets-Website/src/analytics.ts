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

export type BillingInterval = 'monthly' | 'annual'
export type PublicPlanCode = 'demo' | 'trader' | 'pro'

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

type CommerceRuntimeConfig = {
  billingEnabled?: boolean
  cardCheckoutAvailable?: boolean
  cryptoCheckoutAvailable?: boolean
  whatsappOperational?: boolean
  annualBillingSupported?: boolean
  plans?: Array<{
    code: PublicPlanCode
    name: string
    /** Null while the interval is not open for sale: there is no price to quote yet. */
    monthlyPrice: number | null
    annualPrice: number | null
    description: string
    button: string
    badge?: string | null
    trialNote?: string | null
    visibleFeatures: string[]
    additionalFeatures: string[]
    highlightedFeature?: string | null
    /** False while an interval is not open for sale yet. */
    monthlyAvailable?: boolean
    annualAvailable?: boolean
    /** The price before the launch discount, or null when there is no discount. */
    originalMonthlyPrice?: number | null
    comingSoonLabel?: string
  }>
  comparisonRows?: string[][]
  /** When the launch price stops, as an ISO instant. */
  promotionEndsAt?: string
  promotionActive?: boolean
}

export type LegalRuntimeConfig = {
  legalName?: string | null
  companyAddress?: string | null
  governingLaw?: string | null
  privacyEmail?: string | null
  supportEmail?: string | null
  reviewRequired?: boolean
}

/**
 * The pre-launch message, sent by the server rather than written again here.
 *
 * The Jinja pages and these React pages are two different renderers of the same site. If
 * each kept its own copy of the wording, one of them would still be describing an open
 * product after the other had been changed.
 */
export type WaitlistRuntimeConfig = {
  mode?: boolean
  eyebrow?: string
  headline?: string
  body?: string
  ctaLabel?: string
  href?: string
}

/**
 * The site menu and the social channels, sent by the server.
 *
 * The header and footer exist twice — once in Jinja for the server-rendered pages and
 * once in React for these — and a menu written by hand in both is a menu that disagrees
 * with itself the first time a page is added to one of them. Both read
 * `core/site_content.py` now: Jinja directly, React through this.
 *
 * Everything is optional because a page opened without the shell must still draw a
 * footer. `FALLBACK_FOOTER_GROUPS` in `SiteChrome.tsx` is what it draws then.
 */
export type ChromeRuntimeConfig = {
  footerGroups?: Array<{
    label: string
    items: Array<{ label: string; href: string }>
  }>
  social?: Array<{ label: string; handle: string; href: string }>
  /** Signed in goes to the dashboard, anyone else to sign-up. The server decides which.
   *  Absolute, on the product's own hostname, whenever it has one of its own. */
  dashboardEntryHref?: string
  /** Sign-in, on the product's own hostname when it has one. */
  signInHref?: string
  /** Where the footer's "Cookie settings" link goes when no script catches the click. */
  cookieSettingsHref?: string
  primaryCtaLabel?: string
}

declare global {
  interface Window {
    HilalMarketsRuntimeConfig?: {
      analytics?: AnalyticsRuntimeConfig
      legal?: LegalRuntimeConfig
      commerce?: CommerceRuntimeConfig
      waitlist?: WaitlistRuntimeConfig
      chrome?: ChromeRuntimeConfig
    }
    HilalAnalytics?: typeof publicAnalyticsApi
    /**
     * The product's one icon set, loaded by the page shell from
     * `static/hilalmarkets-icons.js`. It returns the paths for one name, without the
     * `<svg>` around them, so React can draw the element itself and still take the
     * geometry from the single owner every Jinja template already uses.
     */
    iconBody?: (name: string) => string
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

export type WaitlistErrorType =
  | 'invalid_email'
  | 'required_field'
  | 'duplicate_email'
  | 'rate_limited'
  | 'network_error'
  | 'server_error'
  | 'unknown_error'

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

/**
 * The data-layer event the GTM container turns into the GA4 waitlist conversion.
 *
 * Named once here so the site has a single spelling of the conversion. A second copy
 * typed into a component is how a container trigger and a page end up disagreeing by
 * one character and the conversion silently stops counting.
 */
export const WAITLIST_JOIN_EVENT = 'waitlist_join'

/**
 * A signup the server confirmed as new. This is the only place the site may report a
 * waitlist conversion.
 *
 * Every event here is scoped to `submissionId` - the idempotency key of that one
 * submission - so a re-render, a retried callback or a second call with the same key
 * reports nothing further. A genuinely new submission carries a new key and is counted
 * again, which is what a second person joining actually is.
 *
 * Nothing is emitted unless Analytics consent has been granted: `emitGoogle` returns
 * false without it, and `emitOnce` then keeps the key unused, so the event is dropped
 * rather than stored up and released later.
 */
export function trackWaitlistSuccess(
  formLocation: string,
  submissionId = '',
): boolean {
  const eventScope = submissionId.trim().slice(0, 128)
    || `${pagePath()}:${formLocation.slice(0, 80)}`
  const google = emitOnce(`waitlist-success:${eventScope}`, () =>
    emitGoogle('waitlist_signup_success', {}),
  )
  const join = emitOnce(`waitlist-join:${eventScope}`, () =>
    emitGoogle(WAITLIST_JOIN_EVENT, {}),
  )
  const meta = emitOnce(`meta-lead:${eventScope}`, () => emitMeta('Lead'))
  return google || join || meta
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

function validPlanCode(value: string): value is PublicPlanCode {
  return value === 'demo' || value === 'trader' || value === 'pro'
}

function validBillingInterval(value: string): value is BillingInterval {
  return value === 'monthly' || value === 'annual'
}

function commerceEvent(
  event: string,
  planCode?: string,
  billingInterval?: string,
  errorType?: string,
): boolean {
  const parameters: EventParameters = { page_path: pagePath() }
  if (planCode && validPlanCode(planCode)) parameters.plan_code = planCode
  if (billingInterval && validBillingInterval(billingInterval)) {
    parameters.billing_interval = billingInterval
  }
  if (errorType) parameters.error_type = errorType.replace(/[^a-z0-9_]/gi, '').slice(0, 60)
  return emitGoogle(event, parameters)
}

export function trackPricingSectionView(): boolean {
  return emitOnce(`pricing-view:${googlePageViewSequence}:${pagePath()}`, () =>
    commerceEvent('pricing_section_view'),
  )
}

export function trackBillingIntervalChanged(interval: BillingInterval): boolean {
  return commerceEvent('billing_interval_changed', undefined, interval)
}

export function trackPlanSelected(planCode: PublicPlanCode, interval: BillingInterval): boolean {
  return emitDebounced(`plan-selected:${planCode}:${interval}`, () =>
    commerceEvent('plan_selected', planCode, interval),
  )
}

export function trackCheckoutStarted(planCode: PublicPlanCode, interval: BillingInterval): boolean {
  return emitDebounced(`checkout-started:${planCode}:${interval}`, () =>
    commerceEvent('checkout_started', planCode, interval),
  )
}

export function trackCheckoutCompleted(planCode: PublicPlanCode, interval: BillingInterval): boolean {
  return emitOnce(`checkout-completed:${planCode}:${interval}`, () =>
    commerceEvent('checkout_completed', planCode, interval),
  )
}

export function trackCheckoutCancelled(planCode: PublicPlanCode, interval: BillingInterval): boolean {
  return commerceEvent('checkout_cancelled', planCode, interval)
}

export function trackCheckoutFailed(
  planCode: PublicPlanCode,
  interval: BillingInterval,
  errorType: string,
): boolean {
  return commerceEvent('checkout_failed', planCode, interval, errorType)
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
  trackPricingSectionView,
  trackBillingIntervalChanged,
  trackPlanSelected,
  trackCheckoutStarted,
  trackCheckoutCompleted,
  trackCheckoutCancelled,
  trackCheckoutFailed,
  captureFirstTouchAttribution,
  getFirstTouchAttribution,
}

if (typeof window !== 'undefined') {
  window.HilalAnalytics = publicAnalyticsApi
}
