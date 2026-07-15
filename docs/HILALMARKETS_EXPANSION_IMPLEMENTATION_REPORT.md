# HilalMarkets Expansion Implementation Report

## Audit Matrix

The implementation started with a repository and design-kit audit on 15 July 2026.
The matrix below records the state before this expansion work; it is intentionally
kept in the final report so that preserved domain behavior is distinguishable from
new presentation work.

| Area | Baseline state | Decision |
| --- | --- | --- |
| Sharia methodology, assessments, evidence, status history, and universe resolver | Complete | Preserve as the authority for all screening presentation. |
| Strategy compiler, AI Setup Chat, Visual Canvas, approval hashes, and one-time scanner | Complete | Reuse; no duplicate scanner or strategy engine. |
| Authentication, tenant isolation, billing, Telegram, support, lifecycle evidence, and System Brain application authorization | Complete | Preserve existing services and permissions. |
| Server plan catalog and entitlement enforcement | Complete | Use as the only public and dashboard pricing source. |
| HilalMarkets logo, icon set, emerald/ivory/gold token foundation, and responsive dashboard shell | Partial | Consolidate and remove legacy visual dependencies after route verification. |
| Landing page | Partial | Replace static opportunity claims and monolithic markup with shared components and safe real-data/empty states. |
| Dashboard pages | Partial | Keep current read models, add the missing market-check entry, centralize navigation, and use progressive disclosure in the builder. |
| Public information architecture | Missing | Add the required public route set, shared base/header/footer, page metadata, sitemap, and robots policy. |
| Cookie consent and optional analytics gating | Missing | Add versioned preferences and Consent Mode defaults before any optional tag loader. |
| Shared opportunity, Watch Plan, status, evidence, and empty-state components | Missing | Add Jinja macros backed by presentation read models. |
| Public help/legal content sources | Missing | Add centralized article and legal metadata; mark legal text for counsel review. |
| Legacy public routes and templates | Conflicting/duplicated | Preserve redirects, then remove duplicate render paths after replacements pass. |
| Purple/TraceEdge CSS and bridge layers | Conflicting/duplicated | Stop loading them from migrated Hilal pages; remove only after rendered replacements are verified. |
| Generated repository artifacts | Conflicting/duplicated | Ignore and untrack local environments, caches, reports, test output, logs, and exports without deleting local files. |

## Safest Implementation Order

1. Centralize site navigation, status wording, help content, legal metadata, and
   public read models.
2. Add shared public/dashboard Jinja foundations and production public routes.
3. Replace landing and dedicated public pages with real bindings and safe empty
   states.
4. Align dashboard navigation and the guided Watch Plan / market-check entry flow.
5. Consolidate styles and assets, then remove verified duplicate render paths.
6. Run focused, full, browser, accessibility-oriented, and hygiene verification.

## Final Architecture

### Current versus final

| Concern | Previous runtime | Current runtime |
| --- | --- | --- |
| Public web | One large landing template with overlapping legacy style layers | `base_public.html`, shared header/footer/consent partials, page templates, centralized metadata, and dedicated public routes |
| Customer web | A 4,000+ line dashboard template | `base_dashboard.html`, sidebar/topbar partials, page templates, focused strategy/proof partials, and scoped scripts |
| Brand | Purple TraceEdge bridge and polish overrides | HilalMarkets emerald, ivory, charcoal, restrained gold, Manrope, DM Sans, local logo, local icons, and original local illustrations |
| Public market | Prototype opportunity percentages and sample cards could be mistaken for current facts | `PublicSiteReadService` exposes only active-methodology/current-assessment records; unavailable and empty states are explicit |
| Navigation | Repeated hard-coded header/sidebar labels | `core/site_content.py` owns public, footer, and customer dashboard navigation |
| Plans | Public and account plan copy could diverge | Public Pricing and dashboard Billing render the same `PLAN_DEFINITIONS` entries selected by `PURCHASABLE_PLAN_CODES` |
| Market check | Legacy Quick Scan naming and ambiguous entry points | `Check the Market Now` is explicit in Watch navigation and redirects to the shared Scanner mode |
| Builder | Chat, canvas, templates, and detailed mechanics competed for attention | Guided behavior cards and AI conversation lead; the deterministic workspace is hidden under Advanced Controls |
| Consent | No production consent gate | Consent Mode v2 denied defaults precede the versioned preference center and optional GTM loader |
| Admin | Application login and OTP only | Optional Cloudflare Access outer gate plus the existing password, email OTP, database session, and CSRF controls |
| Repository | Environments, bytecode, exports, logs, and test output were tracked | Generated/runtime paths are ignored and removed from Git's index while remaining local |

### Shared Jinja structure

```text
templates/hilal/
  base_public.html
  base_dashboard.html
  partials/
    public_header.html
    public_footer.html
    cookie_banner.html
    dashboard_sidebar.html
    dashboard_topbar.html
  macros/
    status_badge.html
    opportunity_card.html
    watch_plan_card.html
    evidence_row.html
    empty_state.html
  public/*.html
  dashboard/*.html
  dashboard/partials/*.html
```

The public and authenticated Screened Market both call the same `opportunity_card` macro. Status
language is selected from `SHARIA_STATUS_PRESENTATION`; plan presentation comes from the plan
catalog; Help Center answers come from `HELP_CATEGORIES`; customer sidebar links come from
`DASHBOARD_NAVIGATION`.

### Production files changed

- Foundations and read models: `core/site_content.py`, `core/plans.py`, `core/config.py`,
  `services/public_site.py`, and the public/dashboard/System Brain routers.
- Shared presentation: `templates/hilal/base_public.html`, `base_dashboard.html`, the five shared
  macros, public/dashboard partials, and focused page templates under `public/` and `dashboard/`.
- Runtime presentation: `hilalmarkets.css`, `hilalmarkets-public.css`,
  `hilalmarkets-builder.css`, the local SVG illustration set, the local icon catalog, consent,
  Help Center, cockpit, builder, chat, and retained deterministic dashboard scripts.
- Product/operations documentation: `README.md`, `docs/ARCHITECTURE.md`,
  `docs/HILALMARKETS_UI_MIGRATION.md`, this report, and both example environment files.
- Verification: Hilal public/consent integration tests plus updated route, dashboard, builder,
  System Brain, static-asset, and Playwright coverage.
- Repository hygiene: `.gitignore` plus index-only removal of generated environments, reports,
  caches, logs, exports, and test output. Local copies were not deleted.

## Routes, Partials, and Components

Public routes implemented:

- `/features`
- `/how-it-works`
- `/how-we-screen`
- `/pricing`
- `/help`
- `/contact`
- `/about`
- `/trust-safety`
- `/risk-disclosure`
- `/privacy`
- `/terms`
- `/cookies`

`/faq` redirects permanently to `/help`; `/risk` redirects permanently to `/risk-disclosure`.
`/dashboard/check-market` and the older `/dashboard/scan-now` redirect to the shared
`/dashboard/strategies/new?mode=scanner` path. Sitemap and robots output excludes dashboard, API,
and System Brain paths.

## CSS and JavaScript Consolidation

Active HilalMarkets pages load the shared token/component layer from `hilalmarkets.css`, public
extensions from `hilalmarkets-public.css`, and guided-builder additions from
`hilalmarkets-builder.css`. Existing deterministic builder/chat/proof runtime CSS remains scoped
where those mature controls still depend on it, but its visible purple palette was mapped to the
HilalMarkets tokens.

Removed from the runtime:

- `static/hilalmarkets-bridge.css`
- `static/traceedge-polish.css`
- the old `templates/index.html`
- the old monolithic `templates/dashboard.html`
- duplicate top-level Hilal dashboard and landing templates

`hilalmarkets-icons.js` is a local, single-style icon source. Active Hilal templates and dynamic
chat/lifecycle renderers do not request remote Iconify icons. `hilalmarkets-consent.js`, `hilalmarkets-help.js`,
`hilalmarkets-builder.js`, and `hilalmarkets-cockpit.js` each own one bounded interaction area.

## Backend Services Connected Per Page

- Landing Screened Market: `PublicSiteReadService`, active `ShariaMethodology`, current
  `AssetShariaAssessment`, and evidence-source counts.
- Dashboard Home: current-user lifecycle, alert proof, eligibility, compliance, delivery, and
  entitlement read models.
- Screened Market and Evidence Passport: `ShariaScreeningService` and stored evidence/history.
- Watch Plans and guided builder: existing strategy, compiler, AI chat, approval, scanner,
  `ShariaUniverseResolver`, health, and monitor-operation services.
- Opportunities & Evidence: activity, lifecycle, observability, proof, and investigation services.
- Compliance Changes and How We Screen: screening governance and compliance-watch services.
- Integrations: real Telegram/Discord connection records. Discord is presented as unavailable
  unless the HTTP adapter is actually enabled.
- Pricing/Billing: `PLAN_DEFINITIONS`, `PlanCatalogService`, `EntitlementService`, and
  `BillingService`. Internal founder/trial/legacy codes cannot be purchased by posting hidden form
  values.
- Settings/Support: current-user preferences, screening policy, consent/data controls, support
  tickets, and approved diagnostics.
- System Brain: existing capability/governance data behind optional Cloudflare Access and existing
  application authorization.

## Copy and Terminology

The first viewport now identifies the Muslim audience, methodology-specific Sharia screening,
evidence-led monitoring, spot-only scope, and no-execution boundary. Watch Plan, Check the Market
Now, Opportunities & Evidence, Compliance Watch, and Evidence Passport replace primary legacy
strategy/lifecycle/Quick Scan labels. Public content does not use absolute halal claims, promise
alerts or returns, or imply that AI issues religious decisions.

The public header is exactly Features, How It Works, How We Screen, Pricing, Help Center, Sign in,
and Start free. Company and legal routes are kept in the shared footer. Portfolio, Referrals, and
System Brain are absent from primary customer navigation.

## Consent and Google Tag Behavior

Before any optional loader runs, `base_public.html` initializes Consent Mode v2 with:

- `ad_storage`, `analytics_storage`, `ad_user_data`, and `ad_personalization`: denied;
- `functionality_storage` and `personalization_storage`: denied;
- `security_storage`: granted.

The first-visit banner gives equal Essential only, Customize, and Accept analytics controls. The
preference center stores a version, category choices, and ISO timestamp in first-party local
storage plus a same-site cookie fallback. Cookie Settings reopens it from the footer. GTM is loaded
only after analytics consent, only when optional analytics is enabled, and only when a syntactically
valid container ID is configured. Marketing remains disabled unless separately approved. No event
code sends emails, raw prompts, Watch Plan text, credentials, reviewer notes, attachments, or
holding data.

Tag Assistant validation and legal/CMP review are deployment tasks because no production GTM
container or legal vendor list is configured in this repository.

## SEO and Accessibility

Every public page has a unique title, description, canonical URL, Open Graph metadata, and JSON-LD.
Organization/WebSite, SoftwareApplication, BreadcrumbList, and displayed FAQ schemas are emitted
only where relevant. Public pages use semantic navigation/main/footer landmarks, one skip link,
labelled controls, visible brand-consistent focus states, keyboard-operable menus/dialogs,
descriptive image alternatives, responsive layouts, and reduced-motion overrides.

## Verification

- Full non-browser suite:
  `.venv\Scripts\python.exe -m pytest --ignore=tests\browser --junitxml=reports\pytest-backend-results.xml -q`
  completed on the final tree with **1,865 tests, 0 failures, 0 errors, 0 skipped** in
  932.692 seconds.
- Full browser suite:
  `.venv\Scripts\python.exe -m pytest tests\browser --junitxml=reports\playwright\playwright-results.xml -q`
  completed on the final tree with **16 tests, 0 failures, 0 errors, 0 skipped** in 163.786 seconds.
- The local-icon and chart-palette regression set was additionally verified with all seven
  static-asset tests, four affected chat/lifecycle/observability browser journeys, and a
  three-test chart/icon rerun; all **14 checks passed**. Both changed scripts also passed
  `node --check`, and a regression test now rejects remote Iconify URLs.
- The pre-broad focused integration group completed with **53 passing tests** covering public,
  consent, landing, System Brain, builder preservation, provider blocking, and condition expansion.
- Ruff passed for all changed Python modules/tests. Targeted Mypy passed for the central content,
  public read-model, and public-router modules. Eight changed JavaScript files passed syntax checks.
- `alembic heads` reported the single current head `c5d6e7f8a9b0`; this presentation expansion did
  not require a migration.
- `git diff --check` and `git diff --cached --check` passed. The only remaining console messages are
  Git's Windows LF-to-CRLF checkout notices, not whitespace defects.

Generated XML reports and visual QA files are intentionally ignored build artifacts.

## Visual QA Artifacts

The browser suite writes rendered test-environment captures to:

- `reports/playwright/visual-qa/hilalmarkets-landing-1440.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-1024.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-768.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-360.png`
- `reports/playwright/visual-qa/hilalmarkets-auth-desktop.png`
- `reports/playwright/visual-qa/hilalmarkets-auth-mobile-360.png`
- `reports/playwright/visual-qa/hilalmarkets-watch-plans-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-mobile-390.png`
- `reports/playwright/visual-qa/setup-observability-desktop.png`
- `reports/playwright/visual-qa/setup-observability-mobile-390.png`
- `reports/playwright/visual-qa/immutable-alert-proof-desktop.png`

Additional chat, proof, Watch Plan, and observability captures remain generated by the broader
browser suite.

## Deployment and Redirect Procedure

1. Configure verified HilalMarkets public/app domains, support/privacy/security contacts, and an OG
   image URL in the deployment secret file.
2. Obtain qualified approval for an executable Sharia methodology and configure its code. Keep
   deployed scanning fail-closed until that record and its assessments exist.
3. Run `alembic upgrade head`, then build and start API, worker, scheduler, Redis, and PostgreSQL.
4. Protect `/system-brain` with Cloudflare Access, firewall direct origin access, and set
   `SYSTEM_BRAIN_CLOUDFLARE_ACCESS_REQUIRED=true`. The app password and OTP remain required.
5. Keep optional analytics disabled until legal review, GTM configuration, data-layer inspection,
   and Tag Assistant verification pass. Increment `COOKIE_CONSENT_VERSION` when categories/vendors
   materially change.
6. Deploy staging; compare the four landing widths and authenticated critical flows against the
   supplied kit. Exercise keyboard, reduced motion, consent withdrawal, screening empty states,
   billing, Telegram, and support.
7. Deploy production, purge edge/template caches, and verify `/`, every public route, redirects,
   sitemap, robots, public metadata, and that no old TraceEdge/purple page is served.
8. Monitor application health, workers, provider state, consent/GTM requests, and 4xx/5xx rates.

No staging or production deployment was performed from this local workspace because deployment
credentials and verified HilalMarkets domain configuration were not supplied.

## Real Data, Governance, and Legal Inputs Still Required

- Verified operating entity, address, governing law, refund/cancellation rights, applicable
  consumer terms, privacy-controller/DPO details, regulatory statements, and support commitments.
- Qualified counsel review of Privacy, Terms, Cookie Policy, Risk Disclosure, and consent design.
- Qualified human governance approval of a production executable methodology, version history,
  reviewer authority, evidence sources, and current asset assessments.
- Verified public/app domains, email addresses, OG image, billing product configuration, and live
  Telegram/Discord operational decisions.
- Production GTM container, approved analytics purpose/vendor list, Tag Assistant evidence, and a
  certified CMP decision where legally required.
- Cloudflare Access policy, Access audience/identity policy, and network rules that prevent direct
  origin access.

## Unresolved Risks

- The Cloudflare header check is an outer defense, not JWT cryptographic verification in the app;
  it is safe only when the origin accepts traffic exclusively from the trusted Access proxy.
- Legal pages are structured drafts and deliberately incomplete until counsel supplies verified
  business facts.
- Public Screened Market can be empty until qualified governance publishes real active records;
  this is intentional and preferable to prototype claims.
- Remote Google Fonts are non-essential presentation requests. A production privacy review may
  choose to self-host the licensed font files.
- The supplied expansion kit remains a local design reference; prototype data and placeholder links
  are not production sources.
