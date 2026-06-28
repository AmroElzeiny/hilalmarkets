# Strategy Builder UX Audit

## Existing UI Found

The uploaded dashboard contains a working Strategy Builder in:

- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`

The existing builder already preserved the deterministic strategy schema, prompt interpretation,
templates, nested logic, validation, preview, draft save, and publication endpoints.

## Problems Found

- The main canvas exposed most schema fields as a large form.
- The condition library permanently occupied canvas space.
- Condition and group editing expanded inline and made the logic tree vertically dense.
- Monitor, universe, alerts, and risk were not visually connected as one monitoring flow.
- The explanation panel mixed summary, warnings, validation, and raw JSON without hierarchy.
- Raw schema JSON was visible by default.
- Condition cards did not provide strong required, optional, data, timeframe, or validity signals.
- Template cards applied immediately without a dedicated preview.
- Prompt interpretation moved too quickly toward the canvas instead of pausing for an
  understanding review.
- Tablet and mobile layouts stacked desktop sections instead of becoming guided navigation.

## Competitor Lessons Applied

### Included

- TrendSpider: reusable visual conditions and nested deterministic logic.
- TradingView: quick first-level rule creation and clear universe context.
- Option Alpha: readable grouped decision blocks without execution language.
- Coinrule: template-first onboarding with complexity labels and logic previews.
- Capitalise.ai: natural-language setup followed by explicit interpretation review.

### Avoided

- Raw scripting as the primary experience.
- Displaying every advanced field at once.
- Execution-bot, order-entry, or guaranteed-profit language.
- Direct activation from an AI prompt.
- Deep flowchart branching for ordinary monitoring rules.

## Before And After

Before:

`Path picker -> large form -> embedded condition catalog -> inline editors -> JSON`

After:

`Path picker -> understanding/template preview -> Strategy Canvas -> modal library and drawers ->
validation-gated monitoring`

The rebuilt canvas presents Monitor, Universe, Entry Logic, Filters, Alert Rules, Risk Context,
and Proof & Review as one connected monitoring map.
