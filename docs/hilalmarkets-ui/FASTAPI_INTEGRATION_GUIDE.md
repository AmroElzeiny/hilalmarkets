# FastAPI and Jinja Integration Guide

## Goal

Translate the static reference into the existing application without replacing working domain logic.

## 1. Create shared Jinja partials

Recommended structure:

```text
templates/
  hilal/
    base_public.html
    base_dashboard.html
    partials/
      logo.html
      public_nav.html
      dashboard_sidebar.html
      dashboard_topbar.html
      status_badge.html
      opportunity_card.html
      evidence_row.html
      empty_state.html
      footer.html
```

Do not copy the full sidebar into every template.

## 2. Preserve current route behavior

Map each placeholder to the existing FastAPI route where the capability already exists. Create new routes only where the Sharia-first product layer requires them.

Use server-side `url_for` rather than hard-coded production paths.

## 3. Replace prototype data

Suggested service/read-model mapping:

- Home: user Watchlist health, resolved screened coverage, latest opportunities, compliance changes.
- Screened Market: `ShariaUniverseResolver`, current effective assessments, market opportunity read model.
- Passport: assessment, methodology version, evidence sources, status history, methodology comparison.
- Watchlists: existing monitor/strategy services plus Sharia universe and drift policy.
- Activity: unified lifecycle, alert, compliance, and investigation read model.
- Opportunity Detail: immutable evidence receipt plus current status context.
- Compliance Changes: compliance change/read-review services and user impact.
- Methodology: published methodology and approved authority records.
- Integrations/Billing/Settings/Support: existing services, not static values.

## 4. Security requirements

- Keep all dashboard routes authenticated.
- Keep System Brain hidden from navigation and protected by Cloudflare Access plus application ADMIN authorization.
- Preserve CSRF protection on forms and mutation endpoints.
- Keep rate limits and idempotency on scan, alert-test, billing, support, and compliance actions.
- Never allow the browser to set Sharia status.
- Preserve methodology and assessment versions on historical evidence.
- Fail closed when required screening evidence is unavailable.

## 5. Progressive enhancement

The pages should remain useful after server rendering. JavaScript should enhance tabs, drawers, filters, animation, and live updates—not hold required business logic.

## 6. Migration order

1. Add brand assets and design tokens.
2. Create shared public/dashboard shells.
3. Migrate Landing and authentication.
4. Migrate Home and Screened Market.
5. Migrate Passport and Watchlists.
6. Migrate Activity, Opportunity Detail, and Compliance.
7. Migrate account/support pages.
8. Integrate System Brain last and test authorization separately.

## 7. Definition of done

- No `#TODO_*` remains.
- No prototype sample value remains in production templates.
- All empty, loading, failure, permission, and stale-data states exist.
- Mobile layouts pass at 360px, 768px, 1024px, and 1440px.
- Keyboard, focus, semantic heading, contrast, and reduced-motion checks pass.
- Existing unit and Playwright suites pass.
