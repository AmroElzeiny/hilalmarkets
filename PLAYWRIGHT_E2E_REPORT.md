# Playwright E2E Report

Generated: 2026-07-24T10:27:24.673078+00:00

Base URL tested: http://127.0.0.1:22055
Browser: chromium
App auto-started: True
Command: `.venv\Scripts\python.exe -m pytest tests/browser -q`
App command: `C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\Trading_assistant\.venv\Scripts\python.exe -m uvicorn ai_market_monitor.main:app --host 127.0.0.1 --port 22055`

## Result

- Tests run: 33
- Passed: 33
- Failed: 0
- Skipped: 0
- Screenshots/traces/videos: `test-results/browser`
- HTML report: `playwright-report/index.html`
- JUnit XML: `reports/playwright/playwright-results.xml`
- JSON summary: `reports/playwright/playwright-summary.json`

## Tests

| Test | Outcome | Seconds |
| --- | --- | ---: |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_mobile_layout` | passed | 5.249 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_optimistic_retry_and_option_selection` | passed | 8.891 |
| `tests/browser/test_dashboard_e2e.py::test_ai_setup_chat_visual_qa_states` | passed | 11.613 |
| `tests/browser/test_dashboard_e2e.py::test_all_customer_dashboard_pages_use_the_brand_system` | passed | 36.365 |
| `tests/browser/test_dashboard_e2e.py::test_approve_and_publish_executable_monitor` | passed | 17.565 |
| `tests/browser/test_dashboard_e2e.py::test_dashboard_loads_after_signup_and_navigation` | passed | 5.082 |
| `tests/browser/test_dashboard_e2e.py::test_hilalmarkets_landing_and_auth_visual_qa` | passed | 12.594 |
| `tests/browser/test_dashboard_e2e.py::test_legacy_scan_route_redirects_into_chat_scanner` | passed | 4.684 |
| `tests/browser/test_dashboard_e2e.py::test_monitor_and_lifecycle_smoke` | passed | 3.407 |
| `tests/browser/test_dashboard_e2e.py::test_notification_channel_handoff_links_smoke` | passed | 3.077 |
| `tests/browser/test_dashboard_e2e.py::test_private_beta_billing_desktop_and_mobile_visual_qa` | passed | 5.142 |
| `tests/browser/test_dashboard_e2e.py::test_provider_required_prompt_blocks_activation` | passed | 5.393 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_consent_grounding_inquiry_and_returning_profile` | passed | 10.164 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_renders_safe_bold_without_executable_html` | passed | 2.112 |
| `tests/browser/test_dashboard_e2e.py::test_public_product_chat_session_profile_offline_and_focus_containment` | passed | 4.664 |
| `tests/browser/test_dashboard_e2e.py::test_screened_market_passport_and_mobile_visual_qa` | passed | 14.669 |
| `tests/browser/test_dashboard_e2e.py::test_screening_change_opens_evidence_difference_dialog` | passed | 4.244 |
| `tests/browser/test_dashboard_e2e.py::test_seeded_proof_receipt_visible_without_ai_claims` | passed | 4.16 |
| `tests/browser/test_dashboard_e2e.py::test_setup_observability_desktop_mobile_and_visual_qa` | passed | 12.795 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_board_preserves_metadata_after_edit_save_reload` | passed | 8.842 |
| `tests/browser/test_dashboard_e2e.py::test_strategy_prompt_to_coverage_preview_opens_board` | passed | 6.996 |
| `tests/browser/test_dashboard_e2e.py::test_system_brain_reviewer_first_desktop_and_mobile` | passed | 7.29 |
| `tests/browser/test_dashboard_e2e.py::test_visual_canvas_is_secondary_to_ai_chat` | passed | 4.479 |
| `tests/browser/test_landing_analytics.py::test_consent_cta_sections_and_waitlist_funnel_are_grounded_and_deduplicated` | passed | 9.459 |
| `tests/browser/test_landing_analytics.py::test_contact_form_shows_branded_success_without_duplicate_client_submission` | passed | 2.032 |
| `tests/browser/test_landing_analytics.py::test_failed_waitlist_submission_never_emits_success_event` | passed | 2.235 |
| `tests/browser/test_landing_analytics.py::test_long_entry_section_and_percentage_waitlist_visibility` | passed | 4.549 |
| `tests/browser/test_landing_analytics.py::test_missing_or_failed_tracking_provider_does_not_block_waitlist_submission` | passed | 2.676 |
| `tests/browser/test_landing_analytics.py::test_sections_retry_after_consent_and_faq_tracks_only_deliberate_stable_id` | passed | 14.644 |
| `tests/browser/test_landing_analytics.py::test_shared_public_shell_loads_gtm_once_only_after_consent` | passed | 1.341 |
| `tests/browser/test_landing_analytics.py::test_x_pixel_loads_once_after_marketing_consent_and_not_in_system_brain` | passed | 2.058 |
| `tests/browser/test_sharia_governance_admin.py::test_sharia_governance_workspace_visual_qa` | passed | 23.393 |
| `tests/browser/test_sharia_governance_admin.py::test_system_brain_user_controls_use_branded_confirmation_dialog` | passed | 7.779 |

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
