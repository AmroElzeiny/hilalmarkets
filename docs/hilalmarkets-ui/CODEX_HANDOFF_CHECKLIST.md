# Codex Handoff Checklist

## Before editing

- Inspect the local working tree, including uncommitted chatbot-first and Sharia-first changes.
- Run existing tests.
- Identify current routes, templates, services, models, and API contracts.
- Preserve working domain logic.

## UI migration

- [x] Add HilalMarkets brand tokens and logo.
- [x] Convert the public shell to shared Jinja.
- [x] Convert the dashboard shell/sidebar/topbar to shared Jinja.
- [x] Replace every `#TODO_*` route.
- [x] Remove every `data-preview-href`.
- [x] Replace sample data.
- [x] Add loading, empty, stale, unavailable, unauthorized, and error states.
- [x] Keep desktop navigation labels visible.
- [x] Keep Sharia-screened context on every opportunity and alert.
- [x] Hide Portfolio and Referrals from primary navigation.
- [x] Hide System Brain from all public/user navigation.

## Backend connection

- [ ] Use one Sharia universe resolver everywhere.
- [ ] Preserve methodology/version/assessment on historical evidence.
- [ ] Apply compliance-change behavior before every scheduled scan.
- [ ] Keep all final status changes admin/reviewer controlled.
- [x] Connect Telegram test and delivery audit.
- [x] Connect billing, usage, support, and settings services.
- [ ] Keep first-party product events free of PII and private strategy prompts.

## QA

- [x] Unit tests.
- [x] API integration tests.
- [x] Playwright desktop and mobile.
- [ ] Accessibility and keyboard testing.
- [ ] Reduced-motion testing.
- [ ] Authorization testing.
- [x] Visual regression screenshots.
- [x] Existing feature regression.
- [x] No placeholder or test methodology shown as real.
