# Codex Handoff Checklist

## Before editing

- Inspect the local working tree, including uncommitted chatbot-first and Sharia-first changes.
- Run existing tests.
- Identify current routes, templates, services, models, and API contracts.
- Preserve working domain logic.

## UI migration

- [ ] Add HilalMarkets brand tokens and logo.
- [ ] Convert the public shell to shared Jinja.
- [ ] Convert the dashboard shell/sidebar/topbar to shared Jinja.
- [ ] Replace every `#TODO_*` route.
- [ ] Remove every `data-preview-href`.
- [ ] Replace sample data.
- [ ] Add loading, empty, stale, unavailable, unauthorized, and error states.
- [ ] Keep desktop navigation labels visible.
- [ ] Keep Sharia-screened context on every opportunity and alert.
- [ ] Hide Portfolio and Referrals from primary navigation.
- [ ] Hide System Brain from all public/user navigation.

## Backend connection

- [ ] Use one Sharia universe resolver everywhere.
- [ ] Preserve methodology/version/assessment on historical evidence.
- [ ] Apply compliance-change behavior before every scheduled scan.
- [ ] Keep all final status changes admin/reviewer controlled.
- [ ] Connect Telegram test and delivery audit.
- [ ] Connect billing, usage, support, and settings services.
- [ ] Keep first-party product events free of PII and private strategy prompts.

## QA

- [ ] Unit tests.
- [ ] API integration tests.
- [ ] Playwright desktop and mobile.
- [ ] Accessibility and keyboard testing.
- [ ] Reduced-motion testing.
- [ ] Authorization testing.
- [ ] Visual regression screenshots.
- [ ] Existing feature regression.
- [ ] No placeholder or test methodology shown as real.
