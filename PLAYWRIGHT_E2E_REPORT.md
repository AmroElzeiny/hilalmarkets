# Playwright E2E Report

Generated: 2026-07-30T22:29:40.863762+00:00

Base URL tested: http://127.0.0.1:13238
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest -q tests/browser/test_dashboard_e2e.py`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 13238`

## Result

- Tests run: 25
- Passed: 25
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout` | passed | 3.545 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection` | passed | 5.384 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_v2_deterministic_preview_and_exact_approval` | passed | 5.73 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_visual_qa_states` | passed | 7.907 |
| `tests/browser/test_dashboard_e2e.py::test_all_customer_dashboard_pages_use_the_brand_system` | passed | 29.83 |
| `tests/browser/test_dashboard_e2e.py::test_approve_and_publish_executable_monitor` | passed | 17.771 |
| `tests/browser/test_dashboard_e2e.py::test_billing_portal_is_branded_responsive_and_accessible` | passed | 3.822 |
| `tests/browser/test_dashboard_e2e.py::test_dashboard_loads_after_signup_and_navigation` | passed | 3.918 |
| `tests/browser/test_dashboard_e2e.py::test_hilalmarkets_landing_and_auth_visual_qa` | passed | 12.256 |
| `tests/browser/test_dashboard_e2e.py::test_legacy_scan_route_redirects_into_chat_scanner` | passed | 4.391 |
| `tests/browser/test_dashboard_e2e.py::test_monitor_and_lifecycle_smoke` | passed | 3.225 |
| `tests/browser/test_dashboard_e2e.py::test_notification_channel_handoff_links_smoke` | passed | 2.696 |
| `tests/browser/test_dashboard_e2e.py::test_private_beta_billing_desktop_and_mobile_visual_qa` | passed | 5.738 |
| `tests/browser/test_dashboard_e2e.py::test_provider_required_prompt_blocks_activation` | passed | 4.689 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_consent_grounding_inquiry_and_returning_profile` | passed | 8.96 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_renders_safe_bold_without_executable_html` | passed | 1.559 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_session_profile_offline_and_focus_containment` | passed | 3.231 |
| `tests/browser/test_dashboard_e2e.py::test_screened_market_passport_and_mobile_visual_qa` | passed | 11.475 |
| `tests/browser/test_dashboard_e2e.py::test_screening_change_opens_evidence_difference_dialog` | passed | 4.389 |
| `tests/browser/test_dashboard_e2e.py::test_seeded_proof_receipt_visible_without_ai_claims` | passed | 3.938 |
| `tests/browser/test_dashboard_e2e.py::test_setup_observability_desktop_mobile_and_visual_qa` | passed | 11.319 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_board_preserves_metadata_after_edit_save_reload` | passed | 7.946 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board` | passed | 4.809 |
| `tests/browser/test_dashboard_e2e.py::test_system_brain_reviewer_first_desktop_and_mobile` | passed | 6.149 |
| `tests/browser/test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat` | passed | 4.121 |

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
