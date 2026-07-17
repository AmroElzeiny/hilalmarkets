# Lifecycle Investigation and Monitor Naming Report

> Historical record notice (2026-07-17): Discord references below describe retired behavior and
> are not part of the private-beta product. See `docs/RETIRED_DISCORD_COMPATIBILITY.md`.

## 1. Files changed

- `src/ai_market_monitor/services/setup_observability.py`
- `src/ai_market_monitor/services/lifecycle_dashboard.py`
- `src/ai_market_monitor/services/ai_setup_chat.py`
- `src/ai_market_monitor/api/routers/dashboard.py`
- `src/ai_market_monitor/api/routers/dashboard_api.py`
- `src/ai_market_monitor/templates/dashboard.html`
- `src/ai_market_monitor/static/dashboard.js`
- `src/ai_market_monitor/static/dashboard.css`
- `tests/unit/test_setup_observability.py`
- `tests/integration/test_setup_observability_api.py`
- `tests/unit/test_ai_setup_chat.py`
- `tests/browser/conftest.py`
- `tests/browser/test_dashboard_e2e.py`

## 2. Investigation mechanism

`Why Wasn't I Alerted?` appears only when a lifecycle has no successful delivery and
its outcome supports investigation. The endpoint resolves the exact user-owned
lifecycle, monitor, immutable strategy version, symbol, timeframe, retained scan,
condition snapshots, lifecycle events, evaluation-cycle health, alerts, and delivery
records. It then selects one deterministic primary category: strategy condition,
technical monitor, provider/data, cooldown/exclusion, delivery failure, or delivery
not attempted.

Conditions are returned as Passed, Failed, Not evaluated, Unsupported, or Data
unavailable with actual, required, distance, timeframe, and evaluation timestamp
when retained. Retry Notification is exposed only for a confirmed alert with a
failed delivery and reuses the existing delivery service.

## 3. Evidence sources and fallback behavior

Exact condition snapshots are preferred. Lifecycle events and the nearest retained
scan provide context when exact snapshots are absent. The response includes
`exact`, `closest_available`, or `unavailable` evidence state. Missing historical
values are never inferred from current candles. The AI explanation endpoint receives
this same bounded response and cannot override its result.

## 4. Monitor-filter implementation

The branded `Filter by Monitor` control is populated only with the signed-in user's
monitors. It supports search, All Monitors clearing, keyboard/focus behavior, and a
`monitor` URL query parameter. Server rendering and live radar API calls both enforce
the selected monitor and tenant. Counts, pages, empty states, and polling use the
same filter. The selected-monitor empty message is: `No lifecycle records found for
this monitor.`

## 5. Strategy-tag behavior

Every lifecycle card shows the current monitor name as the primary tag and the
immutable strategy version as compact metadata. The name is joined from the monitor,
so a rename is reflected without modifying historical version identity. Long names
truncate safely with the full value available through the native title tooltip.

## 6. Monitor-naming chat flow

Persistent Monitor mode now stops before final approval and asks: `What would you
like to name this monitor?` Three concise setup-derived suggestions are rendered as
chips, and custom typed names use the same optimistic/idempotent chat flow. Scanner
mode does not ask.

Names must contain 3-80 allowed characters and cannot be blank. A duplicate owned
monitor name is rejected with distinct alternatives. Confirming or renaming updates
the draft strategy, translation sheet, canvas payload, chat title, final summary,
and canonical schema hash. Any previous approval reference/date is cleared, so the
changed draft must be approved again. No generated suggestion is accepted silently.

## 7. API and database changes

Investigation and grounded explanation endpoints are listed in the setup
observability report. The candidate, cycle, summary, aggregate, and explanation
models were added in migrations `d0e1f2a3b4c5` and `e1f2a3b4c5d6`. Monitor naming
uses existing chat/session, StrategyDefinition, Strategy, and approval-hash storage;
it does not add a second naming table.

## 8. Tests and exact results

Tests cover deterministic failed-condition explanation, failed delivery, successful
delivery action suppression, tenant isolation, monitor filtering and URL persistence,
strategy tags, mandatory naming, three suggestion chips, custom validation,
duplicates, idempotency, and approval-hash invalidation.

- Focused backend/integration matrix: 79 passed.
- Full suite: 1,758 passed.
- Browser suite: 14 passed.
- Static Python, MyPy, JavaScript, compile, and migration checks: passed.

## 9. Visual QA screenshot paths

- `reports/playwright/visual-qa/setup-observability-desktop.png`
- `reports/playwright/visual-qa/setup-observability-mobile-390.png`
- `reports/playwright/visual-qa/candidate-detail-timeline.png`
- `reports/playwright/visual-qa/near-miss-candidate.png`
- `reports/playwright/visual-qa/provider-error-state.png`
- `reports/playwright/visual-qa/empty-radar-state.png`

## 10. Known limitations

Lifecycles created before retained condition snapshots cannot provide exact historic
actual values. The UI explicitly reports closest/unavailable evidence. Browser live
refresh is visibility-aware polling rather than push transport in the current runtime.

## 11. Manual QA checklist

- Create Monitor mode draft and verify naming occurs before final approval.
- Select a suggested name and verify it appears immediately as a user message.
- Enter invalid, duplicate, and custom valid names.
- Rename after approval-ready state and verify prior approval becomes invalid.
- Confirm the chosen name in translation, canvas, monitor page, filter, and cards.
- Confirm the action is hidden after successful Telegram/Discord delivery.
- Investigate condition failure, provider error, cooldown, and failed delivery.
- Verify Retry Notification is absent unless delivery failed after confirmation.
- Search/filter monitors, refresh, and navigate back/forward.
- Verify drawer focus, Escape close, mobile sheet, loading, empty, and error states.
