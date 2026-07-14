# Playwright E2E Report

Generated: 2026-07-14T21:47:36.525574+00:00

Base URL tested: http://127.0.0.1:30855
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest tests\browser\test_dashboard_e2e.py --junitxml=reports\hilalmarkets-ui-final.xml -q`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 30855`

## Result

- Tests run: 16
- Passed: 16
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout` | passed | 2.151 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection` | passed | 4.353 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_visual_qa_states` | passed | 5.542 |
| `tests/browser/test_dashboard_e2e.py::test_approve_and_publish_executable_monitor` | passed | 5.834 |
| `tests/browser/test_dashboard_e2e.py::test_dashboard_loads_after_signup_and_navigation` | passed | 7.65 |
| `tests/browser/test_dashboard_e2e.py::test_hilalmarkets_landing_and_auth_visual_qa` | passed | 4.56 |
| `tests/browser/test_dashboard_e2e.py::test_legacy_scan_route_redirects_into_chat_scanner` | passed | 2.102 |
| `tests/browser/test_dashboard_e2e.py::test_monitor_and_lifecycle_smoke` | passed | 1.684 |
| `tests/browser/test_dashboard_e2e.py::test_provider_required_prompt_blocks_activation` | passed | 2.719 |
| `tests/browser/test_dashboard_e2e.py::test_screened_market_passport_and_mobile_visual_qa` | passed | 2.381 |
| `tests/browser/test_dashboard_e2e.py::test_seeded_proof_receipt_visible_without_ai_claims` | passed | 1.982 |
| `tests/browser/test_dashboard_e2e.py::test_setup_observability_desktop_mobile_and_visual_qa` | passed | 5.244 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_board_preserves_metadata_after_edit_save_reload` | passed | 5.099 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board` | passed | 3.512 |
| `tests/browser/test_dashboard_e2e.py::test_telegram_discord_handoff_links_smoke` | passed | 1.609 |
| `tests/browser/test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat` | passed | 3.166 |

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
