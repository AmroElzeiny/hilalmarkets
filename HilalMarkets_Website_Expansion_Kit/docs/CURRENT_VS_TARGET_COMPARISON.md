# Current System vs HilalMarkets Target

## Executive judgment

The current repository has implemented most of the Sharia-first domain capabilities, and the authenticated navigation is substantially closer to the intended product structure. The main gap is no longer missing backend features. It is **presentation architecture**:

- the live domain still presents the old TraceEdge purple technical-monitor identity;
- the repository landing template has HilalMarkets copy layered on top of legacy TraceEdge sections and CSS;
- the dashboard uses the correct new navigation categories but remains a very large template with several style layers and legacy technical language;
- Sharia identity appears as content in places, but does not consistently control the visual hierarchy and first user action;
- public information is concentrated on one long landing page instead of dedicated pages;
- pricing, methodology wording, header/footer navigation, and support answers risk duplication when hard-coded in multiple templates.

## Page comparison

| Area | Current system | Target in this kit | Required action |
|---|---|---|---|
| Live landing | Old TraceEdge headline, purple identity, technical setup/lifecycle emphasis | Halal-conscious, evidence-led HilalMarkets story | Deploy the new template and verify the production domain |
| Landing header | How it works, Features, Pricing, FAQ | Features, How It Works, How We Screen, Pricing, Help Center | Use one shared public-header partial |
| Hero | Generic monitoring first | Sharia-screened market and Muslim investor problem first | Place methodology/evidence context in the first viewport |
| Trust story | Mixed into technical sections | Dedicated trust block immediately after hero | Keep the full explanation on How We Screen |
| Technical builder | Visible early and described in depth | Guided Watch Plans first; AI/advanced mechanics secondary | Move technical capability depth to Features and dashboard |
| Pricing | Legacy Free/Pro/Creator values and messaging | Free/Core/Pro from one plan catalog | Read public and dashboard pricing from one backend source |
| Dashboard nav | Mostly aligned | Add explicit Check the Market Now under Watch | Preserve Discover / Watch / Review / Trust / Account |
| Dashboard home | Screened wording plus legacy coverage/lifecycle metrics | Eligible market, forming opportunities, drift, Watch Plan activation | Remove generic score-first hierarchy |
| Screened Market | Implemented | Opportunity and screening context in one card | Keep methodology/date/status compact and Passport-linked |
| Watch Plan builder | Chat + canvas + large condition library on one page | Guided flow by default; exact mechanics collapsed | Use progressive disclosure and one primary next action |
| Activity | Implemented but legacy lifecycle terms remain in places | Opportunities & Evidence with Forming, Alerts, Ended, Compliance, Investigations | Add a presentation terminology mapping |
| Compliance | Implemented | Show reason, review state, affected Watch Plans, and user policy impact | Make impact the visual priority |
| Methodology | Implemented | Strong public How We Screen page plus detailed authenticated page | Avoid duplicating the full methodology on other pages |
| Integrations | Telegram and Discord appear | Telegram first; hide or mark unavailable channels honestly | Do not advertise unfinished delivery |
| Billing | Dashboard plans exist | Same catalog, names, limits, and copy as public Pricing | Eliminate hard-coded duplication |
| Help and contact | Links or dashboard support only | Separate Help Center and Contact pages | Reuse support categories and article records |
| Legal and privacy | Insufficient public architecture | Privacy, Terms, Cookie Policy, Risk Disclosure, Trust & Safety | Require legal review before launch |
| Cookies | Consent work planned | First-visit banner + preference center + Consent Mode v2 | Optional tags remain denied until choice |
| CSS architecture | Legacy and HilalMarkets style layers coexist | One token system and scoped components | Remove old purple overrides after visual migration |
| Template architecture | Large monolithic templates | Jinja bases, partials, and macros | Do not duplicate header, footer, card, badge, or pricing markup |
| Repository hygiene | Generated and environment files tracked | Clean source repository | Remove `.venv`, reports, test outputs, and logs from Git history |

## Public information architecture

### Landing header

Use only:

1. Features
2. How It Works
3. How We Screen
4. Pricing
5. Help Center
6. Sign in
7. Start free

Do not put About, Contact, Privacy, Terms, Cookies, Risk Disclosure, or Trust & Safety in the primary header.

### Dedicated public pages

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

A separate FAQ page is unnecessary initially. Keep four purchase-critical questions on the landing page and put operational questions in the Help Center.

### Footer

- Product: Features, How It Works, Pricing, Screened Market
- Trust: How We Screen, Trust & Safety, Risk Disclosure
- Company & Support: About, Help Center, Contact
- Legal: Privacy, Terms, Cookie Policy, Cookie Settings

## Repetition rule

Use Sharia and Islamic identity at the points where they change a decision:

- headline or eyebrow;
- status badge;
- methodology/date line;
- Evidence Passport;
- policy and drift behavior;
- How We Screen page.

Do not repeat the same “Sharia screening is foundational” paragraph in every section. Use compact context and link to the authoritative page.

## Vocabulary

Prefer:
- halal-conscious monitoring;
- Sharia-screened under HM-01;
- Islamic-finance-aware safeguards;
- guided Muslim investor;
- qualified human governance.

Avoid:
- definitely halal;
- universally halal coin;
- AI-approved Sharia status;
- halal score;
- guaranteed compliant forever.
