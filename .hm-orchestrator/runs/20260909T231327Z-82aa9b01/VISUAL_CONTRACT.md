# Visual contract — payment states must be visible

Scope: only the states a customer sees when trying to pay, upgrade, or downgrade. This is a
**functional** mission. Nothing here invites a new visual design.

## The rule this contract exists to prove

A customer must never see a control that cannot work and says nothing. Either the control
works, or the reason is on the screen in words a beginner understands.

The reported bug is exactly this failure: the button was there, the click did nothing, and the
screen stayed silent.

## Reference implementation — copy this, do not invent

The checkout page already does this correctly. It is the pattern:

- `src/ai_market_monitor/templates/hilal/dashboard/checkout.html` lines 79-89 — the
  "There is no way to pay for this plan yet" notice, using `notice notice-error` with
  `data-icon="alert"` and `role="status"`.

Use the classes that already exist. Do not add new ones:

| Purpose | Existing class |
|---|---|
| Blocking explanation | `.notice.notice-error` |
| Caution, still usable | `.notice.notice-warning` |
| Confirmation | `.notice.notice-success` |
| A method that cannot be chosen | `.is-unavailable` on the option label |
| Live status text in the billing dialog | `[data-billing-dialog-status]`, `role="status"`, `aria-live="polite"` |

Colours, spacing, radius, type and icons all come from the shipped dashboard stylesheets
(`hilalmarkets-dashboard-v2.css`, `hilalmarkets.css`). Introduce no new token, no new hex
value, no new font size.

## Viewports for every screenshot

Use the product's own breakpoints. Capture each state at all three:

| Name | Width × height |
|---|---|
| Desktop | 1440 × 900 |
| Tablet | 900 × 1200 |
| Phone | 390 × 844 |

900 and 390 sit inside the real `max-width: 980px` and `max-width: 420px` rules, so both
responsive paths are exercised.

## States to capture

For the Pro plan, on both the billing page dialog and the checkout page:

| # | State | What the picture must show |
|---|---|---|
| S1 | Before the fix, Pro card + crypto | The reported failure: the pay control not responding. This is the "before" evidence. |
| S2 | Card available, selected | The pay control clearly enabled and usable. |
| S3 | Crypto available, selected | Same, for crypto. |
| S4 | A method genuinely unavailable | The method marked `.is-unavailable` **and** a readable reason next to it. Never a bare greyed control. |
| S5 | No method available at all | The blocking notice, in the checkout page's existing pattern. No dead button anywhere on screen. |
| S6 | Nothing selected, user presses pay | A visible message telling the user to choose a payment method. Silence fails this contract. |
| S7 | Upgrade confirmation dialog | What changes, what is charged, and when it takes effect — all readable. |
| S8 | Downgrade confirmation dialog | Same, including what happens to the period already paid for. |

## Acceptance criteria

1. Every state above is captured at all three viewports.
2. In S4, S5 and S6 the reason is **readable text on the screen**, not only a tooltip, not only
   a `title` attribute, and not only a line in the developer console.
3. Text contrast meets at least 4.5:1 against its background. Measure it — do not assume a
   token is safe. The apple accent measures about 1.21:1 on white and cannot carry meaning on
   its own.
4. A disabled control always has a visible explanation within the same panel.
5. No horizontal scrolling of the page body at 390 px.
6. Focus is visible on every control that can still be used.
7. Nothing outside these states changes appearance. If a screenshot shows an unrelated visual
   difference from `HEAD`, that is a finding.

## Verification

A vision-capable OpenCode Go reviewer must actually look at the images and check them against
this file, state by state.

If screenshots cannot be produced, or no vision-capable reviewer can genuinely inspect them,
mark visual verification **unverified** and escalate. Never infer a visual pass from reading
CSS.
