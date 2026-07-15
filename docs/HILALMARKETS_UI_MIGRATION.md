# HilalMarkets UI Migration

## Scope

The supplied `HilalMarkets_UI_Prototype` is now the product UI source for the live
HilalMarkets application. Its page structure, emerald/ivory/gold palette, Manrope and
DM Sans typography, logo, navigation hierarchy, controls, responsive layouts, and
Watchlist vocabulary are used directly by the production templates.

The migration deliberately keeps server-rendered data, validation, strategy approval,
screening evidence, billing, integration, and lifecycle behavior authoritative. The
prototype's example values and preview-only JavaScript are not served in production.

## Production Assets

| Prototype source | Production asset |
| --- | --- |
| `assets/img/logo-mark.svg` | `src/ai_market_monitor/static/hilalmarkets-logo-mark.svg` |
| `assets/css/styles.css` | `src/ai_market_monitor/static/hilalmarkets.css` |
| `assets/js/icons.js` | `src/ai_market_monitor/static/hilalmarkets-icons.js` |
| Production interaction adapter | `src/ai_market_monitor/static/hilalmarkets.js` |
| Guided builder components | `src/ai_market_monitor/static/hilalmarkets-builder.css` |
| Public components | `src/ai_market_monitor/static/hilalmarkets-public.css` |

The interaction adapter contains only accessibility and production shell behavior. It
does not replay prototype scan results, fake data, or placeholder links.

## Route Map

| HilalMarkets page | Live route | Source of truth |
| --- | --- | --- |
| Landing | `/` | Prototype landing structure with production links and plan catalog |
| Sign in / sign up | `/signin`, `/signup` | Auth and OTP services |
| Home | `/dashboard` | User-scoped dashboard overview |
| Sharia-Screened Market | `/dashboard/market` | Versioned screening methodology and evidence |
| Evidence Passport | `/dashboard/market/{asset_slug}` | Asset passport service |
| My Watchlist | `/dashboard/watchlist` | `ApprovedWatchlist` and `ApprovedWatchlistAsset` |
| Watchlists | `/dashboard/strategies` | User-owned monitor list, health and operations |
| New Watchlist | `/dashboard/strategies/new` | AI Setup Chat and Visual Canvas over the validated strategy compiler |
| Scanner | `/dashboard/strategies/new?mode=scanner` | A mode inside the builder; no duplicate page or navigation entry |
| Opportunities & Evidence | `/dashboard/activity` | Lifecycle and activity evidence |
| Compliance Changes | `/dashboard/compliance` | User-scoped compliance drift records |
| How We Screen | `/dashboard/methodology` | Approved methodology records |
| Integrations | `/dashboard/integrations` | Telegram and Discord connection records |
| Plan & Billing | `/dashboard/billing` | Entitlement and billing services |
| Settings | `/dashboard/settings` | User preferences |
| Support | `/dashboard/support` | Support request service |
| System Brain | `/system-brain` | Protected administrator console |

Backward-compatible routes such as `/dashboard/monitors`, `/dashboard/create-monitor`,
`/dashboard/scan-now`, and `/dashboard/trial` redirect to the consolidated page that
owns that workflow. They do not render duplicate sections.

`Check the Market Now` is an explicit Watch navigation item. It redirects into Scanner mode at
`/dashboard/strategies/new?mode=scanner`; it is not a duplicate scanner implementation or a
standalone Quick Scan page. Persistent monitoring remains under Watchlists. Both paths use the
same validated compiler and screened-universe services.

## Data and Preview Rules

- Production templates render values supplied by the existing backend only.
- Screening state, evidence, lifecycle status, and delivery state are never inferred
  from a visual sample.
- Empty states state when no persisted evidence is available.
- Prototype reference documentation is preserved under `docs/hilalmarkets-ui/`.
- Prototype previews remain design evidence only; production pages use persisted data,
  provider results, explicit empty states, and server plan definitions.
- New page routes are authenticated and scope queries to the current user.
- User-facing email, Telegram, Discord, billing, capability clarification, AI chat,
  screening, and lifecycle copy uses HilalMarkets. Legacy internal schema names,
  cookies, event keys, and persisted identifiers remain unchanged for compatibility.

## Validation Checklist

1. Check public, auth, dashboard, billing, and System Brain branding and favicon.
2. Verify every sidebar link resolves to an authenticated production route.
3. Verify watchlist and compliance pages show only persisted current-user records.
4. Confirm a saved asset opens its existing evidence passport.
5. Confirm AI Setup Chat, scanner, monitor approval, notification and billing flows
   retain their existing tests and API contracts.

## Verification

Current commands, exact results, public-route coverage, consent checks, and deployment caveats are
recorded in `docs/HILALMARKETS_EXPANSION_IMPLEMENTATION_REPORT.md`. Generated browser reports and
screenshots are local/CI artifacts under ignored `reports/`, `playwright-report/`, and
`test-results/` directories rather than repository source.

## Visual QA

Current production-backed captures:

- `reports/playwright/visual-qa/hilalmarkets-landing-1440.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-1024.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-768.png`
- `reports/playwright/visual-qa/hilalmarkets-landing-360.png`
- `reports/playwright/visual-qa/hilalmarkets-auth-desktop.png`
- `reports/playwright/visual-qa/hilalmarkets-auth-mobile-390.png`
- `reports/playwright/visual-qa/hilalmarkets-watch-plans-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-desktop.png`
- `reports/playwright/visual-qa/ai-setup-chat-mobile-390.png`
- `reports/playwright/visual-qa/setup-observability-desktop.png`
- `reports/playwright/visual-qa/setup-observability-mobile-390.png`
- `reports/visual-qa/sharia-first/screened-market-desktop.png`
- `reports/visual-qa/sharia-first/screened-market-mobile-390.png`
- `reports/visual-qa/sharia-first/sharia-evidence-passport-desktop.png`

The landing capture deliberately scrolls through every reveal component before taking
the full-page image, so hidden or uninitialized prototype sections fail browser QA.
The AI Setup Chat browser check also asserts that the prototype sidebar begins at the
left viewport edge and that topbar actions retain bounded prototype dimensions.
