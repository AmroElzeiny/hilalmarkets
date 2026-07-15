# Codex Prompt

```text
You are modifying the current local Trace-Edge/HilalMarkets working tree. Inspect the complete working tree and current tests before editing. Build on the implemented Sharia-first services, chatbot flow, market scanner, Watch Plans, evidence, compliance, billing, support, authentication, and admin logic. Do not replace working domain logic or perform unrelated refactoring.

OBJECTIVE
Align the current product and deployed website with the HilalMarkets design kit and business position: a modern, evidence-led, halal-conscious market-monitoring platform for beginner-to-mid self-directed Muslim crypto spot investors. The result must feel human-designed, coherent, easy to use, and visibly Islamic-finance-aware without repetitive religious copy, decorative clichés, absolute halal claims, or a generic AI/SaaS template.

REFERENCE
Use the supplied HilalMarkets_Website_Expansion_Kit as the visual, content, page-structure, component, and responsive reference. Treat its sample data and #TODO_* links as prototypes only. Production must use current services, named routes, real approved data, and safe empty states.

CURRENT GAPS TO FIX
1. The live domain still shows the old TraceEdge purple technical-monitor landing page.
2. The repository landing page has HilalMarkets copy layered over legacy TraceEdge sections, purple variables, inline CSS, and duplicate style layers.
3. The dashboard navigation is mostly correct, but “Check the Market Now” is not an explicit Watch item and legacy lifecycle/strategy terminology remains.
4. Landing and dashboard templates are oversized and duplicate header, footer, status, opportunity, pricing, and copy logic.
5. The builder exposes chat, visual canvas, condition library, and advanced mechanics too early.
6. Pricing, methodology, support answers, and status text can diverge because they are hard-coded in multiple places.
7. Islamic identity is mentioned but not consistently prioritized in the first viewport, page hierarchy, badges, and primary user flow.
8. Repository hygiene still includes generated/environment artifacts such as .venv, browser reports, test output, and logs.

INFORMATION ARCHITECTURE

PUBLIC HEADER — exactly:
- Features
- How It Works
- How We Screen
- Pricing
- Help Center
- Sign in
- Start free

Do not put About, Contact, legal pages, Cookie Policy, or Risk Disclosure in the primary header.

CREATE OR COMPLETE PUBLIC ROUTES:
- /features
- /how-it-works
- /how-we-screen
- /pricing
- /help
- /contact
- /about
- /trust-safety
- /risk-disclosure
- /privacy
- /terms
- /cookies

LANDING PAGE ORDER:
1. Hero: “Halal-conscious crypto monitoring, built around evidence” or equivalent; state Muslim audience, Sharia-screened assets, no execution, and evidence-led monitoring.
2. Trust strip: disclosed methodology, Evidence Passport, qualified governance, Compliance Watch.
3. Screened Market opportunity preview combining status, methodology, review date, readiness, and missing requirement.
4. Product journey: screened market → Evidence Passport → guided Watch Plan → one-time market check → continuous monitoring → evidence and drift.
5. Main capabilities, summarized once.
6. Opportunity journey and missed-alert explanation.
7. Compliance Watch and Drift Alerts.
8. Small methodology/trust summary linking to How We Screen.
9. Free/Core/Pro summary sourced from the real plan catalog.
10. Four purchase-critical FAQs.
11. Final CTA.
Do not lead with AI, indicators, canvas, technical toolkit, Creator/Community, or Discord.

PUBLIC FOOTER:
- Product: Features, How It Works, Pricing, Screened Market
- Trust: How We Screen, Trust & Safety, Risk Disclosure
- Company & Support: About, Help Center, Contact
- Legal: Privacy, Terms, Cookie Policy, Cookie Settings
Use one shared footer partial.

DASHBOARD NAVIGATION:
Discover:
- Home
- Sharia-Screened Market
- My Watchlist
Watch:
- Watch Plans
- Check the Market Now
Review:
- Opportunities & Evidence
- Compliance Changes
Trust:
- How We Screen
Account:
- Integrations
- Plan & Billing
- Settings
- Support
Keep Portfolio and Referrals out of primary navigation until production-ready. Keep System Brain completely absent from customer/public navigation and protect it with Cloudflare Access + application ADMIN authorization.

PAGE-SPECIFIC DASHBOARD CHANGES
- Home: prioritize eligible assets, forming opportunities, compliance changes, Watch Plan activation, and evidence freshness. Replace generic “Coverage score” and lifecycle-volume emphasis.
- Screened Market: one consistent Opportunity Card containing asset, “Sharia-screened: [status]”, methodology/version, review date, opportunity type, readiness/direction, present conditions, main missing requirement, View Evidence, and Watch action.
- Watch Plans: every card shows screened universe, methodology, allowed statuses, eligible-asset count, compliance-change behavior, last scan, and health.
- Builder: Guided Watch Plan mode is the default. Start from breakout, pullback, unusual activity, price level, or natural language. Ask universe and compliance behavior before activation. Keep Visual Canvas, nested logic, indicators, exact thresholds, and custom mechanics under collapsed Advanced Controls. Never show chat, canvas, library, and all advanced controls simultaneously.
- Check the Market Now: replace visible “Quick Scan” terminology; use the same ShariaUniverseResolver as persistent monitoring; separate policy exclusions from technical non-matches.
- Opportunities & Evidence: tabs = Forming, Alerts, Ended, Compliance Changes, Investigations. Use customer language mapping rather than internal enums.
- Opportunity Detail: show journey, actual vs required evidence, data freshness, Sharia status at event time, current status, methodology/version, policy decision, delivery state, and “Why didn’t this alert happen?”
- Compliance Changes: prioritize reason, severity, review state, affected watchlist/Watch Plans, automatic policy action, user notification, and status history.
- How We Screen: visual methodology, status definitions, review authority, version history, Evidence Passport example, and Methodology Comparison.
- Integrations: Telegram first. Hide Discord or clearly mark unavailable until operational.
- Billing: public Pricing and dashboard Billing must read the same backend PlanCatalog/entitlement service.
- Settings: default methodology, allowed statuses, drift behavior, alert preferences, privacy, cookie settings, and data controls.
- Help/Support: public Help Center and authenticated Support should reuse article categories and diagnostic flows; do not duplicate answer text.

IDENTITY AND COPY
Use deep emerald, warm ivory, charcoal, restrained gold, semantic amber/red, Manrope/DM Sans, original icons, evidence shapes, charts, subtle crescent/market symbolism, 150–250ms interface motion, and reduced-motion support. Remove the old purple identity after migration; do not keep bridge CSS indefinitely.

Highlight these terms naturally:
- halal-conscious;
- Muslim investors;
- Sharia-screened;
- Islamic-finance-aware;
- qualified human governance.
Use no more than one primary Islamic-positioning phrase in a page hero and one deeper trust explanation per page. On repeated product cards use compact status/methodology/date context, not repeated paragraphs.

Never say:
- definitely halal;
- universally halal coin;
- AI-approved Sharia status;
- halal score;
- guaranteed compliant;
- guaranteed alert or return.
Use: “Screened as eligible under [methodology], version [x], reviewed [date].”

TEMPLATE AND CSS ARCHITECTURE
Refactor to shared Jinja architecture:
templates/hilal/
- base_public.html
- base_dashboard.html
- partials/public_header.html
- partials/public_footer.html
- partials/cookie_banner.html
- partials/dashboard_sidebar.html
- partials/dashboard_topbar.html
- macros/status_badge.html
- macros/opportunity_card.html
- macros/watch_plan_card.html
- macros/evidence_row.html
- macros/empty_state.html
- public/*.html
- dashboard/*.html

Create one HilalMarkets token/component CSS system. Audit and then remove obsolete purple variables, inline overrides, traceedge-polish/bridge duplication, and copied page-level CSS. Do not delete a legacy rule until its rendered replacement is verified. Keep page CSS scoped and component-driven. Do not maintain a 4,000+ line monolithic dashboard template.

SINGLE SOURCES OF TRUTH
- Header/footer navigation: one configuration or shared partials.
- Pricing/limits/entitlements: existing PlanCatalog/Billing service.
- Methodology/status/reviewer/date: Sharia services and effective-assessment resolver.
- Opportunity card: one macro/read model across Home, Market, Scan, Activity, and Watch Plan detail.
- Status wording: one presentation mapping layer.
- Help articles: one article repository/content source.
- legal metadata, canonical URLs, and company contact details: central settings/config.
Never copy the same plan, methodology description, disclaimer, or feature list into multiple templates.

COOKIE CONSENT AND GOOGLE
Implement a real first-visit consent banner and preference center:
- Equal, clear choices: Essential only, Customize, Accept analytics.
- Essential always on.
- Analytics and functional optional.
- Marketing disabled by default unless explicitly approved and legally configured.
- Store a versioned choice and timestamp.
- Add footer “Cookie Settings”.
- Create /cookies.
- Execute Google Consent Mode v2 default before GTM: ad_storage, analytics_storage, ad_user_data, ad_personalization, functionality_storage, personalization_storage denied; security_storage granted.
- Update consent only after the choice.
- Do not fire optional analytics before consent.
- Do not send PII, emails, raw prompts, private Watch Plan text, credentials, reviewer notes, support attachments, or asset-holding data to GA4/GTM.
- Keep first-party product analytics as the source of truth.
- Validate with Tag Assistant.
Treat the supplied custom banner as implementation reference, not a substitute for legal review or a certified CMP where legally required.

PUBLIC PAGE CONTENT
Use the text and hierarchy from the design kit, but bind production values to real data. Charts must use real backend aggregates or be clearly labelled illustrative; never imply traction, performance, success rate, or halal certainty from sample values. Use original/local illustrations and licensed assets only; do not copy competitor layouts or imagery.

SEO AND ACCESSIBILITY
- Unique title, description, canonical, Open Graph, and structured data for every public page.
- sitemap.xml and robots.txt; exclude authenticated/admin routes.
- Semantic headings, landmarks, labels, keyboard behavior, visible focus, alt text, contrast, 360px–1440px responsiveness, and prefers-reduced-motion.
- Add JSON-LD for Organization, SoftwareApplication, FAQ only where displayed, BreadcrumbList, and Help Center articles as appropriate.

LEGAL
Create structured Privacy, Terms, Cookie Policy, Risk Disclosure, and Trust & Safety pages. Mark legal copy for qualified counsel review. Do not invent the legal entity, address, governing law, refund rights, DPO, regulatory status, or support SLA. Use configuration placeholders until verified.

REPOSITORY HYGIENE
Update .gitignore and remove tracked generated artifacts from Git history/index where safe:
- .venv/
- playwright-report/
- test-results/
- cloudflared.log
- caches, bytecode, local exports, screenshots not intentionally versioned.
Do not remove intentional visual baselines or reports without migrating them to an appropriate ignored/artifact workflow.

TESTS
Add/adjust:
- route and template rendering tests for every public page;
- header/footer consistency;
- no duplicate IDs or #TODO placeholders;
- no test methodology or sample assessment presented as real;
- PlanCatalog consistency between Pricing and Billing;
- Opportunity Card consistency across pages;
- auth/RBAC/System Brain protection;
- consent defaults before GTM, preference persistence, withdrawal, and no optional tags before consent;
- no prohibited analytics properties;
- mobile/desktop Playwright visual tests;
- keyboard, focus, contrast, reduced motion, and screen-reader semantics;
- existing scanner, Watch Plan, Sharia resolver, alert, billing, Telegram, support, and admin regression suites.

DEPLOYMENT
Update brand/domain/email/metadata from TraceEdge to HilalMarkets where verified. Preserve redirects from old public URLs. Deploy to staging, compare against the supplied kit at 1440, 1024, 768, and 360 widths, then deploy production. Confirm the live domain no longer serves old TraceEdge copy or purple identity.

COMPLETION REPORT
Return:
1. current-vs-final architecture;
2. routes/templates/partials/macros created;
3. CSS/JS consolidated and legacy files removed;
4. backend services connected per page;
5. copy/terminology changes;
6. consent/GTM behavior;
7. SEO/accessibility work;
8. tests and results;
9. screenshots at four widths;
10. deployment/redirect steps;
11. real data/governance/legal items still required;
12. unresolved risks.
Do not mark complete if the work is only static frontend, contains sample production claims, duplicates source-of-truth data, exposes System Brain, or leaves the live site on the old TraceEdge identity.
```
