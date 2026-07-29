# Playwright E2E Report

Generated: 2026-07-29T11:05:16.772746+00:00

Base URL tested: http://127.0.0.1:21080
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest -q tests\browser\test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board tests\browser\test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout tests\browser\test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat tests\browser\test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 21080`

## Result

- Tests run: 4
- Passed: 4
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout` | passed | 2.92 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection` | passed | 9.248 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board` | passed | 10.793 |
| `tests/browser/test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat` | passed | 3.6 |

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
