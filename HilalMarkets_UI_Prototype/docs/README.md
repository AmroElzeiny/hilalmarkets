# HilalMarkets UI Prototype

A complete static design and implementation reference for the HilalMarkets landing page, activation experience, customer dashboard, deferred modules, and internal System Brain.

## Start

No build system is required.

```bash
cd HilalMarkets_UI_Prototype
python -m http.server 8080
```

Open `http://localhost:8080/preview.html`.

## Important boundaries

- All visible data is sample prototype data.
- Nothing in this folder is a live Sharia determination, market feed, user record, billing record, or alert.
- Production routes deliberately use `href="#TODO_*"`.
- Local browsing works through `data-preview-href`.
- Codex must remove `data-preview-href` after connecting FastAPI/Jinja routes.
- Existing security, authentication, admin roles, evidence versioning, and deterministic monitoring logic must be preserved.

## Product architecture represented

1. **Discover** — Home, Sharia-Screened Market, Watchlist, Evidence Passport.
2. **Watch** — Watch Plans, Guided Builder, Check the Market Now.
3. **Review** — Opportunities & Evidence, Opportunity Detail, Compliance Changes.
4. **Trust** — How We Screen.
5. **Account** — Integrations, Billing, Settings, Support.
6. **Deferred** — Portfolio and Referrals are designed but intentionally excluded from primary navigation.
7. **Internal** — System Brain is not linked from user-facing navigation.

## Files

- `index.html` — landing page
- `preview.html` — visual page library
- `auth-*.html` — authentication
- `onboarding.html` — activation path
- `dashboard-*.html` — customer product pages
- `admin-system-brain.html` — protected internal workspace
- `assets/css/styles.css` — design system and responsive layouts
- `assets/js/icons.js` — original inline SVG icon set
- `assets/js/app.js` — prototype interactions, charts, animations, sidebar, tabs, and builder behavior
- `docs/` — implementation guides

## Brand direction

- Deep emerald communicates trust and identity.
- Warm ivory avoids a clinical trading-terminal feel.
- Restrained gold is reserved for evidence, methodology, and emphasis.
- Status colors retain functional meaning: emerald = eligible, amber = qualification/review, red = exclusion/critical issue.
- Islamic identity is expressed through product logic, language, a subtle crescent/market mark, governance, and evidence—not generic decorative motifs.
