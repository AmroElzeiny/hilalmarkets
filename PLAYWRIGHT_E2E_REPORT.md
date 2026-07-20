# Playwright E2E Report

Generated: 2026-07-20T04:29:36.176249+00:00

Base URL tested: http://127.0.0.1:40801
Browser: chromium
App auto-started: True
Command: `/usr/local/bin/python -m pytest tests/browser/test_landing_analytics.py -k consent_cta_sections_and_waitlist_funnel -q`
App command: `/usr/local/bin/python -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 40801`

## Result

- Tests run: 1
- Passed: 1
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_landing_analytics.py::test_consent_cta_sections_and_waitlist_funnel_are_grounded_and_deduplicated` | passed | 8.55 |

## Runtime Checks

- Critical console errors: 0
- Critical API/network errors: 0
- Page errors: 0

## Tested User Flow Summary

- Browser signup and session creation.
- Dashboard navigation and Strategy Builder entry.
- Prompt interpretation coverage preview for RSI, volume, and EMA.
- Strategy Board opening, condition drawer editing, draft save, reload, and metadata preservation.
- Provider-required prompt blocking for open-interest requirements.
- Publish flow for executable monitors and active monitor list visibility.
- Quick Scan/Finder prompt interpretation and backend fixture light-scan result rendering path.
- Seeded deterministic proof receipt API visibility.
- Screened Market search, Passport Quick View focus behavior, full Passport, and mobile layout.
- Server-catalog checkout review at desktop and mobile widths.
- System Brain customer isolation, responsive review workspace, and separate approve/publish flow.
- Setup observability, lifecycle, and integrations smoke screens.

## Remaining Browser-Side Risks

- Live Telegram delivery is not exercised without dedicated staging credentials.
- Quick Scan interpretation is mocked for browser determinism; light-scan submission uses the backend fixture market-data provider.
- Long-running worker scans and production billing webhooks remain covered by non-browser tests/manual checks.

## Next Recommended Fixes

- Keep immutable proof and historical Passport routes in the browser regression set as alert UI
  evolves.
- Keep Quick Scan browser coverage on fixture market data; add a staging-only live-provider smoke
  test before using real exchange candles in browser E2E.
- Run controlled provider-sandbox checkout, SMTP, Telegram, and WhatsApp delivery tests in
  staging; CI remains fake/no-send.
- Run the same browser suite in CI after installing Chromium with
  `.venv\Scripts\python.exe -m playwright install chromium`.
