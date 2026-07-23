# Playwright E2E Report

Generated: 2026-07-23T15:53:37.594212+00:00

Base URL tested: http://127.0.0.1:39392
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest -q tests/browser/test_landing_analytics.py`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 39392`

## Result

- Tests run: 8
- Passed: 8
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_landing_analytics.py::test_consent_cta_sections_and_waitlist_funnel_are_grounded_and_deduplicated` | passed | 9.281 |
| `tests/browser/test_landing_analytics.py::test_contact_form_shows_branded_success_without_duplicate_client_submission` | passed | 1.776 |
| `tests/browser/test_landing_analytics.py::test_failed_waitlist_submission_never_emits_success_event` | passed | 2.053 |
| `tests/browser/test_landing_analytics.py::test_long_entry_section_and_percentage_waitlist_visibility` | passed | 4.5 |
| `tests/browser/test_landing_analytics.py::test_missing_or_failed_tracking_provider_does_not_block_waitlist_submission` | passed | 2.245 |
| `tests/browser/test_landing_analytics.py::test_sections_retry_after_consent_and_faq_tracks_only_deliberate_stable_id` | passed | 13.804 |
| `tests/browser/test_landing_analytics.py::test_shared_public_shell_loads_gtm_once_only_after_consent` | passed | 2.098 |
| `tests/browser/test_landing_analytics.py::test_x_pixel_loads_once_after_marketing_consent_and_not_in_system_brain` | passed | 3.039 |

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
