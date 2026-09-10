# Visual Contract — the "Ask AI" button, and the billing clean-up

Claude Code owns this contract. Workers must implement it; they may not invent a
different visual direction. Every value below is exact and testable.

## Surface

Route/component:

| # | Route | Template | Where the button goes |
|---|---|---|---|
| S1 | `/dashboard/market` (Halal Assets) | `hilal/dashboard_test/market.html` | last child of `div.t-controls`, after the Favorites action |
| S2 | `/dashboard/create-monitor` | `hilal/dashboard_test/monitor.html` | inside `.m-bar > .m-bar-group` (the first group), immediately after the `data-add-group` button |
| S3 | `/dashboard/opportunities` | `hilal/dashboard_test/opportunities.html` | inside `.w-bar`, immediately after `label.t-search.w-search` |
| S4 | `/dashboard/settings` | `hilal/dashboard_test/settings.html` | last child of `.g-group-head` in **each** of the 8 `section.g-group` blocks |
| S5 | `/dashboard/subscription` | `hilal/dashboard_test/subscription.html` | **no Ask AI button** — this surface is the billing clean-up only |

The 8 settings groups, by id: `g-where`, `g-when`, `g-howmuch`, `g-about`,
`g-screen`, `g-evidence`, `g-market`, `g-data`.

User state: signed in, on a page whose chrome sets the dashboard assistant on
(`_PATH_CHROME["hilal_chat"]` in `api/routers/dashboard_test.py`).

Reference source(s):
- `brand guide.md` (project root) — master brand rules.
- `src/ai_market_monitor/static/hilalmarkets-brand.css` — the `--hm-*` token file.
- `src/ai_market_monitor/static/hm-dashboard-test.css` lines 642–705 — the shipped
  `.t-action` button family. The new button is a **modifier on that family**, not a
  new button.
- `src/ai_market_monitor/templates/hilal/dashboard_test/partials/hilal_chat.html`
  and `static/hm-hilal-chat.js` — the assistant this button opens.

## Required viewports

| ID | Width | Height | Purpose |
|---|---:|---:|---|
| V1 | 1440 | 900 | Desktop. All four surfaces. |
| V2 | 1024 | 768 | Small laptop / tablet landscape. Bar wrapping. |
| V3 | 390 | 844 | Phone. The bars stack; the button must not overflow. |

## The colour: "modern baby blue", resolved

The literal request cannot be built as stated, and this contract resolves it rather
than leaving it to a worker.

A true pastel baby blue (`#89cff0`) carries our white (`#ffffff`) at **1.71:1**.
WCAG AA asks 4.5:1 for this text size. White on that colour is unreadable, so the
pastel cannot be the fill behind white text.

Resolution: the **fill** is the deepest-legible member of the same sky-blue family —
still a modern, friendly blue and clearly not the emerald/apple palette — and the
pale baby blue is kept for the soft tint. Measured with `tests/support/contrast.py`,
the repository's single owner for this sum:

| New token | Value | Role | Measured |
|---|---|---|---|
| `--hm-sky` | `#0e78af` | button fill at rest | `#ffffff` on it = **4.86:1** (AA text ✓); on canvas `#f5f8fb` = **4.56:1** (≥3:1 non-text ✓) |
| `--hm-sky-strong` | `#0a6086` | hover / active fill | `#ffffff` on it = **6.93:1** ✓ |
| `--hm-sky-soft` | `#e6f4fb` | soft tint (icon plate, seeded-input flash) — never behind white text | `--hm-ink` on it = **12.10:1** ✓ |

Rules:
- Declare all three in `hilalmarkets-brand.css`, beside the other `--hm-*` colours.
  Nowhere else. No raw hex in the page stylesheets.
- The button text colour is `--hm-surface` (`#ffffff`) — "our white degree".
- `--hm-sky-soft` must never sit behind white text.
- These tokens carry **no product meaning**. `brand guide.md` section 10 keeps brand
  colour and product meaning apart: sky blue means "ask the assistant", nothing about
  price, direction, Shariah status, or safety. It must not be reused for a status.

## Typography

| Role | Family | Weight | Size | Line height | Case |
|---|---|---:|---:|---:|---|
| Button label | inherited (`font: inherit` from `.t-action`) | 600 | `.85rem` | inherited | Sentence case — the words are exactly `Ask AI` |

Weight is 600, not the family's 500: white on a coloured fill needs the extra weight
to hold at `.85rem`. No other deviation from `.t-action`.

## Geometry

Inherited from `.t-action` and not re-declared:
`display:inline-flex`, `align-items:center`, `justify-content:center`,
`gap:var(--t-2)`, `padding:0 var(--t-3)`, `border-radius:999px`,
`border:1px solid`, `text-decoration:none`.

Set by the modifier:

| Property | Value |
|---|---|
| `min-height` | **44px** on every instance, including Settings |
| `border-color` | `--hm-sky` (rest), `--hm-sky-strong` (hover/active) |
| `background` | `--hm-sky` (rest), `--hm-sky-strong` (hover/active) |
| `color` | `--hm-surface` |
| `flex` | `0 0 auto` on S1 and S3, so it never stretches across the bar |
| icon | `data-icon="spark"`, `data-icon-class="icon-sm"` (16×16, as the family sets) |
| label | one line, never wraps, never truncated |

**44px is a floor, not a preference.** `hm-dashboard-test.css` line 648 records why:
this path's own rule asks 44×44 and a header action was once four pixels short. The
freedom granted on "different sizes" is spent on padding and label length, never on
height.

Settings variant (`is-compact` modifier, S4 only): same 44px height, padding
`0 var(--t-2)`, and `margin-inline-start:auto` so it sits at the right edge of the
group head. Nothing else changes.

## The animation — "2D scaling in and out"

Pure CSS keyframes. **Do not load or use a motion library.** The vendored bundle in
this repository is Motion 11 and silently ignores other call shapes; a CSS keyframe
is provable from the stylesheet and from a screenshot.

```
@keyframes hm-ask-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.045); }
}
```

| Property | Value |
|---|---|
| duration | `2600ms` |
| timing | `ease-in-out` |
| iteration | `infinite` |
| `transform-origin` | `center` |
| paused on | `:hover`, `:focus-visible`, `:active` — set `animation-play-state: paused` |
| reduced motion | `@media (prefers-reduced-motion: reduce)` → `animation: none` and `transform: none` |

Constraints:
- `transform` only. Never animate `width`, `height`, `margin`, `padding`, `top` or
  `left` — those move the bar around the button.
- Peak scale is `1.045`. At the widest instance this grows the painted box by about
  6px. The button must therefore keep at least **10px** of gap from its neighbour at
  every viewport, or be the last item in its bar.
- The pause-on-interaction rule is the same one the shipped `hilal-tag` marquee
  already follows. Match it; do not invent a second convention.
- The reduced-motion rule must **not** be written inside a bare `:where()`.
  `:where()` counts zero for specificity in this codebase and has already painted a
  state wrongly once. Give the rule the same `body.hilal-dashboard :is(...)` prefix
  the rest of the file uses.

## Interaction states

| State | Look |
|---|---|
| Rest | fill `--hm-sky`, border `--hm-sky`, text `--hm-surface`, breathing |
| Hover | fill and border `--hm-sky-strong`, `transform: translateY(-1px)` (family rule), breathing paused |
| Focus (`:focus-visible`) | the shipped convention only: `--hm-focus-ring` + `--hm-focus-halo`. Do **not** invent a blue focus ring. Breathing paused. |
| Active | fill `--hm-sky-strong`, no lift, breathing paused |
| Disabled | **not reachable.** The button is never disabled. Where the assistant is not loaded the button is not rendered at all. |
| Loading / Empty / Error | none. The button owns no state of its own; the assistant window owns all of it. |

## Behaviour

- One click opens the Hilal window. It never closes it. It is therefore **not** a
  toggle: `aria-haspopup="dialog"` and `aria-controls="hilal-window"`, and **no**
  `aria-expanded` (the orb owns that attribute; two owners drift).
- Opening must go through the assistant's own open path in `static/hm-hilal-chat.js`,
  including its focus move. No page script may show the window itself.
- Optional `data-hilal-topic` on the button seeds the composer with a plain-language
  starter question and focuses it. **It is never sent automatically** — the person
  presses send. Seeded text must be beginner-plain: no field names, no jargon, and no
  statement about Shariah status, price direction, or what to buy.

## Accessibility

- Contrast: as measured in the colour table. Prove each pair with
  `tests/support/contrast.py`; never assert a value by eye.
- Target: 44×44 minimum, every instance.
- Keyboard: a real `<button type="button">`, in natural tab order, `Enter` and
  `Space` both open the window, focus lands inside the window exactly as the orb's
  path already does.
- Unique accessible name. Eight buttons reading "Ask AI" on Settings fail WCAG 2.4.4.
  Visible label stays `Ask AI`; the accessible name is extended per instance:

| Surface | Accessible name |
|---|---|
| S1 Halal Assets | `Ask AI about this list of screened coins` |
| S2 Create monitor | `Ask AI about building this monitor` |
| S3 Opportunities | `Ask AI about what your monitors found` |
| S4 Settings, per group | `Ask AI about <that group's own heading>` — 8 distinct names, taken from the heading already in the markup, not typed again |

  Use `aria-label`, or a visible `Ask AI` plus an `.sr-only` continuation. Either is
  acceptable; the name must contain the visible text as its start (WCAG 2.5.3).
- Reduced motion: as above.

## Must not change

- The `.t-action` base rule and every other modifier on it.
- The apple-green accent, the emerald tokens, and any existing colour token's value.
- The assistant orb, its tag, the window, and every route it calls.
- Any existing filter, search box, tile or control on the four surfaces — the button
  is added beside them, nothing is moved or restyled.
- The `#s-now`, `#s-plans`, `#s-payments` section ids on the subscription page. Only
  the `nav.a-jump` that links to them is removed.

## Billing clean-up — visual part

1. Remove `nav.a-jump` (subscription page, the bar reading **What you have · Other
   plans · Your payments**) together with every rule and script line that existed
   only for it. A rule left behind with nothing to match is how the next reader
   believes the bar is still there.
2. Each payment in **Your payments** that is not complete shows one action, as a
   `.t-action.is-primary` in the row's existing action slot. Same geometry as the
   button already at line 274. Never two actions on one row.

## Screenshot acceptance

For every surface × viewport, produce a PNG under
`.hm-orchestrator/runs/<run>/screens/<surface>-<viewport>.png`.

Compare, per shot:
- the button is present, in the exact position this contract names;
- its fill, text colour, radius and height;
- it does not overlap or push any neighbour, and the bar does not scroll sideways at
  V3;
- on Settings, all 8 are present and aligned to the right of each group head;
- on the subscription page, the jump bar is gone and no gap or stray rule is left
  where it was, and an unfinished payment row shows exactly one action.

Also capture one shot with reduced motion forced on, proving the button renders
un-scaled and static.

Tolerance is for font rasterising only. Never for a wrong token, height, position or
word.

## Escalation triggers

Escalate to Claude if:
- a measured contrast value in the colour table does not reproduce;
- the 44px floor cannot be met at a viewport without moving an existing control;
- the assistant cannot be opened from a second control without changing its own
  focus or state handling;
- a seeded question cannot be written without naming an internal field;
- a screenshot cannot be produced, or a vision reviewer cannot actually inspect it;
- a reviewer still sees a material mismatch after two repair attempts.
