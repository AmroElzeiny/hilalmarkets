# Landing, Contact, and Analytics Implementation Report

Date: 2026-07-19

## 1. Outcome

The supplied `Hilal-Markets-Website/` React/Vite project is now the source of truth for the
public landing page. FastAPI serves a thin HTML shell at `/` and `/contact`, while the locked
frontend build supplies the original layout, responsive behavior, hover states, motion, fonts,
and assets. The old Jinja landing markup is no longer rendered inside either route.

The same frontend now includes a brand-matched contact page, a real waitlist submission flow,
and one reusable consent-aware analytics layer. Public forms persist before external delivery,
use idempotency and rate limits, and keep Google Sheet credentials and office-delivery details
on the server.

## 2. Files

### Supplied landing and contact frontend

- `Hilal-Markets-Website/` - supplied landing source, assets, fonts, and responsive styles.
- `Hilal-Markets-Website/src/App.tsx` - real waitlist API and tracked landing interactions.
- `Hilal-Markets-Website/src/main.tsx` - centralized analytics initialization.
- `Hilal-Markets-Website/src/analytics.ts` - typed provider-neutral analytics API.
- `Hilal-Markets-Website/src/publicForms.ts` - same-origin CSRF bootstrap and form client.
- `Hilal-Markets-Website/src/components/Tracking.tsx` - reusable section and CTA tracking.
- `Hilal-Markets-Website/src/components/SiteChrome.tsx` - working shared navigation/footer links.
- `Hilal-Markets-Website/src/pages/ContactPage.tsx` - contact form, process graph, animation, and
  branded success/error states.
- `Hilal-Markets-Website/vite.config.ts` and `Dockerfile` - deterministic production build copied
  into the FastAPI image.
- `src/ai_market_monitor/static/landing/` - matching local deployed bundle for non-Docker use.

### Server, persistence, and delivery

- `src/ai_market_monitor/templates/hilal/public/react_site.html`
- `src/ai_market_monitor/templates/hilal/public/index.html`
- `src/ai_market_monitor/templates/hilal/public/contact.html`
- `src/ai_market_monitor/static/hilalmarkets-consent.js`
- `src/ai_market_monitor/api/routers/public.py`
- `src/ai_market_monitor/api/routers/public_forms.py`
- `src/ai_market_monitor/api/routers/__init__.py`
- `src/ai_market_monitor/api/request_guards.py`
- `src/ai_market_monitor/main.py`
- `src/ai_market_monitor/core/config.py`
- `src/ai_market_monitor/core/startup.py`
- `src/ai_market_monitor/db/models/public_forms.py`
- `src/ai_market_monitor/db/models/__init__.py`
- `src/ai_market_monitor/schemas/public_forms.py`
- `src/ai_market_monitor/services/public_forms.py`
- `src/ai_market_monitor/services/email_delivery.py`
- `src/ai_market_monitor/worker.py`
- `alembic/versions/4def06102738_add_public_waitlist_and_contact_forms.py`
- `scripts/google_apps_script/waitlist_webhook.gs`

### Tests and documentation

- `tests/browser/test_landing_analytics.py`
- `tests/browser/test_dashboard_e2e.py`
- `tests/integration/test_public_forms_api.py`
- `tests/integration/test_landing_page.py`
- `tests/integration/test_hilal_public_site.py`
- `tests/unit/test_landing_analytics.py`
- `tests/unit/test_request_guards.py`
- `.env.example`, `.env.production.example`, `README.md`, `docs/ARCHITECTURE.md`, and
  `docs/OPERATIONS.md`.

Other pre-existing uncommitted support-chat changes in the working tree were preserved and are
not attributed to this implementation.

## 3. Landing and Contact Behavior

- `/` renders the supplied landing experience, with working in-page navigation, waitlist links,
  privacy, terms, and contact links.
- `/contact` reuses the supplied logo, navigation, footer, display/body typography, canvas,
  border, shadow, radius, lime accent, reveal animation, and responsive patterns.
- The contact form requires title, email, and description. It shows a branded checkmark receipt
  above Submit only after server-confirmed SMTP delivery, and a plain non-technical error when
  delivery fails.
- The backend creates one idempotent delivery event from `office@hilalmarkets.com` to
  `office@hilalmarkets.com`, with the visitor address in `Reply-To`. Repeated requests cannot
  create duplicate logical email events.
- The waitlist displays distinct created, duplicate, and safe failure messages. A created record
  remains authoritative even if the Google Sheet projection is temporarily unavailable; the
  worker retries that projection without creating a second signup.

## 4. Google Sheet Projection

The browser sends the waitlist email, optional first-touch attribution, and an idempotency key to
the same-origin API. The server adds the UTC timestamp and a trusted `CF-IPCountry` country code
when that production-only trust flag is enabled. It then posts to the configured Google Apps
Script Web App.

The Apps Script requires a server-shared secret, locks concurrent writes, and rejects duplicate
deliveries. Its visible worksheet is deliberately operator-friendly:

- Email Address;
- Joined At (UTC);
- Country;
- Signup Source;
- Campaign;
- Status, with controlled workflow choices;
- Notes, editable by the beta team.

The delivery identifier required for retry safety is stored in an automatically hidden final
column. Raw referrer URLs, landing URLs, UTM details that are not useful to daily waitlist work,
webhook fields, and JSON are not displayed. Existing rows from the earlier technical layout are
migrated on the first request handled by the upgraded Apps Script.

The Sheet URL and webhook secret are `SecretStr` server settings. They are absent from rendered
HTML and the compiled JavaScript bundle. Deploy/setup steps are in `docs/OPERATIONS.md`.

## 5. Analytics Architecture

Page components call only the provider-neutral API in `analytics.ts`; they never call `gtag`,
`dataLayer.push`, or `fbq` directly. The module is safe during SSR, bounds metadata, strips
forbidden parameter keys, deduplicates events, handles script errors without affecting forms or
navigation, and logs only sanitized diagnostics when debug mode is explicitly enabled.

Implemented events:

| User action | Google | Meta | Conversion |
| --- | --- | --- | --- |
| Page/SPA route view | `page_view` | `PageView` | No |
| Section 50% visible for 1 second | `section_view` | None | No |
| Tracked link/button click | `cta_click` | None | No |
| Waitlist form visible | `waitlist_form_view` | None | No |
| First email-field interaction | `waitlist_form_start` | None | No |
| Valid submit attempt | `waitlist_submit_attempt` | None | No |
| New server-confirmed signup | `generate_lead` | `Lead` | Candidate |
| Failed or duplicate signup | `waitlist_form_error` | None | No |

Only normalized error categories are emitted. The submitted email, title, description, raw server
errors, custom IP data, user identifiers, and credentials are never analytics parameters.

## 6. Consent and Attribution

- Essential behavior is always available.
- Google starts with Consent Mode values denied and its script loads only after Analytics consent.
- Meta loads only after Marketing consent; revocation gates later events and calls Meta consent
  revoke when available.
- Consent changes apply without a page reload and provider initialization is one-time.
- First-touch attribution is captured from the first landing URL, persisted in first-party local
  storage only after Analytics consent, and is not overwritten during internal navigation.
- Attribution remains optional. Storage denial or corruption cannot block signup.
- The server stores submitted attribution only when the current consent cookie authorizes it.

This follows Google's documented order of setting denied defaults before measurement commands and
updating state after the visitor's choice:
https://developers.google.com/tag-platform/security/guides/consent

## 7. Environment Contract

Both development and production environment files and examples contain the Vite-appropriate
public settings:

```env
VITE_ANALYTICS_ENABLED=false
VITE_GTM_ID=
VITE_GA4_MEASUREMENT_ID=
VITE_META_PIXEL_ID=
VITE_META_PIXEL_ENABLED=false
VITE_SITE_URL=
VITE_ANALYTICS_DEBUG=false
```

Server-only form settings include `PUBLIC_FORMS_ENABLED`, contact sender/recipient, Google Sheet
enablement, Apps Script `/exec` URL, webhook secret, retry limits, and trusted-country-header
control. Analytics, Meta, and Google Sheet delivery remain disabled by default. Startup validation
rejects malformed enabled IDs, an invalid/non-HTTPS Apps Script URL, a missing webhook secret,
Meta without Marketing consent, and public forms without SMTP.

## 8. Manual Provider Configuration

No GA4, GTM, Meta, Google Sheet, or SMTP control plane was accessed during this implementation.
No external event delivery is claimed.

### GA4

1. Configure one valid `VITE_GTM_ID` or `VITE_GA4_MEASUREMENT_ID` and enable analytics.
2. Grant Analytics consent on staging and submit a new, unique waitlist email.
3. Temporarily set `VITE_ANALYTICS_DEBUG=true` and inspect GA4 DebugView. Google documents
   DebugView and debug mode here: https://support.google.com/analytics/answer/7201382
4. After the event is observed, mark `generate_lead` as a key event in GA4. Do not mark section,
   CTA, form-view, form-start, attempt, or error events as key events.
5. Disable debug mode after verification.

### Meta

1. Create/select the web dataset in Meta Events Manager, place its numeric ID in
   `VITE_META_PIXEL_ID`, and enable Meta plus Marketing consent on staging.
2. Open Events Manager's Test Events view, grant Marketing consent, and submit a new unique
   waitlist record.
3. Verify exactly one `PageView` and one `Lead`, and verify no submitted email appears in event
   parameters. Meta's official Pixel setup entry point is:
   https://www.facebook.com/help/messenger-app/952192354843755

## 9. Verification Results

- Complete unit suite: **430 passed**.
- Complete integration suite: **180 passed**.
- Complete dashboard/engine/interpreter/services suite: **1,445 passed**, with two upstream
  `lxml` deprecation warnings.
- Complete Playwright browser suite: **23 passed**.
- Total pytest result across bounded groups: **2,078 passed**.
- Focused public-form/landing regressions: **26 passed** (also included above).
- Ruff over `src`, `tests`, and pending migrations: **passed**.
- MyPy over the seven new/changed production boundaries: **passed**.
- Full-project MyPy: **not run**; Docker execution was rejected after the environment reached its
  usage limit. This is an execution-environment limitation, not a passing result.
- Jinja validation: **63 templates loaded**.
- TypeScript/Vite production build: **passed**.
- Deployed/source `landing.js` SHA-256: identical.
- Deployed JavaScript and Google Apps Script syntax checks: **passed**.
- Alembic: one head, `4def06102738`; clean-database upgrade to head: **passed**.
- `git diff --check`: **passed** (line-ending notices only).

The initial monolithic pytest command exceeded its ten-minute process ceiling. The same complete
corpus passed after splitting it into the four bounded groups reported above.

## 10. Visual QA

- `reports/playwright/visual-qa/hilalmarkets-landing-1440.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-1024.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-768.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-390.png`
- `reports/playwright/visual-qa/hilalmarkets-contact-desktop.png`
- `reports/playwright/visual-qa/hilalmarkets-contact-mobile-390.png`

The browser run also verifies keyboard focus, reduced-motion preference, source-design breakpoints,
contact success, consent gating, provider-failure tolerance, section timing, CTA deduplication,
SPA page-view deduplication, duplicate-signup handling, and absence of the email from analytics
queues.

## 11. Remaining External Work

- Deploy the Apps Script and configure its Script Properties and target Sheet.
- Verify the `office@hilalmarkets.com` sender/domain with the SMTP provider.
- Add real GA4/GTM and Meta IDs, then complete DebugView/Test Events verification.
- Enable trusted Cloudflare country headers only after direct-origin traffic is blocked.
- Confirm retention, consent text, analytics purpose, and country collection with privacy/legal
  review before production enablement.
- Run full-project MyPy once the Python 3.12/Docker verification environment is available again.
