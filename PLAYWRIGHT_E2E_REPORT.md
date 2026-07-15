# Playwright E2E Report

Generated: 2026-07-15T20:41:02.328046+00:00

Base URL tested: http://127.0.0.1:27125
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 27125`

## Result

- Tests run: 17
- Passed: 17
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout` | passed | 2.307 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection` | passed | 5.522 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_visual_qa_states` | passed | 8.639 |
| `tests/browser/test_dashboard_e2e.py::test_approve_and_publish_executable_monitor` | passed | 6.108 |
| `tests/browser/test_dashboard_e2e.py::test_dashboard_loads_after_signup_and_navigation` | passed | 2.903 |
| `tests/browser/test_dashboard_e2e.py::test_hilalmarkets_landing_and_auth_visual_qa` | passed | 5.875 |
| `tests/browser/test_dashboard_e2e.py::test_legacy_scan_route_redirects_into_chat_scanner` | passed | 2.34 |
| `tests/browser/test_dashboard_e2e.py::test_monitor_and_lifecycle_smoke` | passed | 1.739 |
| `tests/browser/test_dashboard_e2e.py::test_provider_required_prompt_blocks_activation` | passed | 3.279 |
| `tests/browser/test_dashboard_e2e.py::test_screened_market_passport_and_mobile_visual_qa` | passed | 3.156 |
| `tests/browser/test_dashboard_e2e.py::test_seeded_proof_receipt_visible_without_ai_claims` | passed | 1.959 |
| `tests/browser/test_dashboard_e2e.py::test_setup_observability_desktop_mobile_and_visual_qa` | passed | 5.185 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_board_preserves_metadata_after_edit_save_reload` | passed | 34.492 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board` | passed | 5.285 |
| `tests/browser/test_dashboard_e2e.py::test_telegram_discord_handoff_links_smoke` | passed | 1.987 |
| `tests/browser/test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat` | passed | 2.527 |
| `tests/browser/test_sharia_governance_admin.py::test_sharia_governance_workspace_visual_qa` | passed | 4.646 |

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
