# Playwright E2E Report

Generated: 2026-07-09T17:33:19.287088+00:00

Base URL tested: http://127.0.0.1:53160
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest tests\unit\test_dashboard_static_assets.py tests\integration\test_dashboard_api.py::test_dashboard_settings_persist_alert_schedule_without_theme_field tests\integration\test_dashboard_api.py::test_dashboard_publish_marks_monitor_active tests\integration\test_dashboard_api.py::test_dashboard_publish_requires_notification_channel tests\integration\test_dashboard_api.py::test_publish_blocks_critical_strategy_conflicts tests\integration\test_dashboard_api.py::test_advanced_dashboard_pages_render tests\browser\test_dashboard_e2e.py::test_approve_and_publish_executable_monitor -q`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 53160`

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
| `tests/browser/test_dashboard_e2e.py::test_approve_and_publish_executable_monitor` | passed | 5.62 |

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
- Strategy Cockpit and integrations smoke screens.

## Remaining Browser-Side Risks

- Live Telegram/Discord message delivery is not exercised without real test tokens.
- Quick Scan interpretation is mocked for browser determinism; light-scan submission uses the backend fixture market-data provider.
- Long-running worker scans and production billing webhooks remain covered by non-browser tests/manual checks.

## Next Recommended Fixes

- Add a dedicated visual proof receipt page if product wants proof viewing outside the cockpit API.
- Keep Quick Scan browser coverage on fixture market data; add a separate staging-only live-provider smoke test before using real exchange candles in browser E2E.
- Run the same browser suite in CI after installing Chromium with `.venv\Scripts\python.exe -m playwright install chromium`.
