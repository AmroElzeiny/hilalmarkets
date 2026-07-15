# HilalMarkets Production UI

A production implementation reference for the HilalMarkets landing page, activation experience, customer dashboard, deferred modules, and internal System Brain.

## Reference preview

No build system is required.

```bash
cd HilalMarkets_UI_Prototype
python -m http.server 8080
```

Open `http://localhost:8080/preview.html`.

## Production status

- The static files in `HilalMarkets_UI_Prototype/` remain the visual reference and contain sample data only.
- Production Jinja templates live in `src/ai_market_monitor/templates/hilal/`.
- Production routes, authenticated data, screening evidence, billing records, and alerts are connected to existing backend services.
- No `#TODO_*` or `data-preview-href` value is served by a production HilalMarkets template.
- Existing security, authentication, admin roles, evidence versioning, and deterministic monitoring logic remain authoritative.

## Product architecture represented

1. **Discover** — Home, Sharia-Screened Market, Watchlist, Evidence Passport.
2. **Watch** — Watchlists and the Guided Builder. One-time Scanner is a mode inside the builder, not a second page.
3. **Review** — Opportunities & Evidence, Opportunity Detail, Compliance Changes.
4. **Trust** — How We Screen.
5. **Account** — Integrations, Billing, Settings, Support.
6. **Deferred** — Portfolio is excluded from primary navigation; Referrals is available through its direct account route.
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
