from uuid import uuid4

from playwright.sync_api import Page, expect


def _event_count(page: Page, event_name: str) -> int:
    return page.evaluate(
        """(name) => (window.dataLayer || []).filter((item) =>
            item && (item.event === name || item[0] === 'event' && item[1] === name)
        ).length""",
        event_name,
    )


def _meta_event_count(page: Page, event_name: str) -> int:
    return page.evaluate(
        """(name) => ((window.fbq && window.fbq.queue) || []).filter((item) =>
            item && item[0] === 'track' && item[1] === name
        ).length""",
        event_name,
    )


def _google_event_parameters(page: Page, event_name: str) -> list[dict]:
    return page.evaluate(
        """(name) => (window.dataLayer || []).flatMap((item) => {
          if (item && item.event === name) {
            const {event, ...parameters} = item;
            return [parameters];
          }
          if (item && item[0] === 'event' && item[1] === name) {
            return [item[2] || {}];
          }
          return [];
        })""",
        event_name,
    )


def _configure_fake_providers(page: Page) -> None:
    page.route(
        "https://www.googletagmanager.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* analytics transport intentionally empty in browser tests */",
        ),
    )
    page.route(
        "https://connect.facebook.net/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* pixel transport intentionally empty in browser tests */",
        ),
    )
    page.evaluate(
        """() => {
          window.HilalMarketsRuntimeConfig.analytics = {
            enabled: true,
            gtmId: 'GTM-KBBHH2FV',
            metaPixelEnabled: true,
            metaPixelId: '1234567890',
            debug: false,
          };
        }"""
    )


def _mock_waitlist_backend(page: Page) -> None:
    submission_count = 0

    def respond(route) -> None:
        nonlocal submission_count
        submission_count += 1
        created = submission_count == 1
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "status": "created" if created else "already_registered",
                "created": created,
                "code": "waitlist_created" if created else "duplicate_email",
                "sheet_delivery_status": "sent",
                "message": "Request accepted.",
            },
        )

    page.route("**/api/v1/public-forms/waitlist", respond)


def test_shared_public_shell_loads_gtm_once_only_after_consent(
    page: Page,
    base_url: str,
) -> None:
    google_requests: list[str] = []
    page.on(
        "request",
        lambda request: google_requests.append(request.url)
        if "googletagmanager.com" in request.url
        else None,
    )
    page.route(
        "https://www.googletagmanager.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* analytics transport intentionally empty in browser tests */",
        ),
    )
    page.goto(f"{base_url}/features", wait_until="domcontentloaded")
    assert page.locator('script[data-hm-provider="google-tag-manager"]').count() == 0
    assert google_requests == []

    page.locator("[data-cookie-accept-analytics]").click()
    page.wait_for_selector(
        'script[data-hm-provider="google-tag-manager"]',
        state="attached",
    )
    assert sum("gtm.js?id=GTM-KBBHH2FV" in url for url in google_requests) == 1
    assert not any("gtag/js?id=G-EJN34D4BEM" in url for url in google_requests)

    page.evaluate(
        """() => window.dispatchEvent(new CustomEvent('hm:consent-updated', {
          detail: {analytics: true, marketing: false}
        }))"""
    )
    assert page.locator('script[data-hm-provider="google-tag-manager"]').count() == 1
    assert sum("gtm.js?id=GTM-KBBHH2FV" in url for url in google_requests) == 1


def test_consent_cta_sections_and_waitlist_funnel_are_grounded_and_deduplicated(
    page: Page,
    base_url: str,
) -> None:
    _mock_waitlist_backend(page)
    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator("main h1")).to_be_visible()
    assert page.locator('script[data-hm-provider]').count() == 0
    assert _event_count(page, "page_view") == 0

    _configure_fake_providers(page)
    page.locator("[data-cookie-accept-analytics]").click()
    page.wait_for_selector(
        'script[data-hm-provider="google-tag-manager"]', state="attached"
    )
    assert _event_count(page, "page_view") == 1
    assert page.locator('script[data-hm-provider="meta-pixel"]').count() == 0

    page.evaluate(
        """() => window.dispatchEvent(new CustomEvent('hm:consent-updated', {
          detail: {analytics: true, marketing: true}
        }))"""
    )
    page.wait_for_selector('script[data-hm-provider="meta-pixel"]', state="attached")
    assert _event_count(page, "page_view") == 1
    assert _meta_event_count(page, "PageView") == 1
    page.evaluate("history.replaceState({}, '', location.pathname)")
    page.wait_for_timeout(50)
    assert _event_count(page, "page_view") == 1
    assert _meta_event_count(page, "PageView") == 1

    hero_cta = page.locator('main a[href="#waitlist"]').first
    hero_cta.evaluate("(element) => { element.click(); element.click(); }")
    assert _event_count(page, "cta_click") == 1

    waitlist = page.locator("#waitlist")
    waitlist.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    assert _event_count(page, "waitlist_form_view") == 1
    page.evaluate("window.scrollTo(0, 0)")
    waitlist.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    assert _event_count(page, "waitlist_form_view") == 1

    email = f"analytics-{uuid4().hex[:12]}@example.com"
    field = page.locator("#waitlist-email")
    field.focus()
    field.fill(email)
    field.press("End")
    assert _event_count(page, "waitlist_form_start") == 1
    page.locator('#waitlist button[type="submit"]').click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert _event_count(page, "waitlist_submit_attempt") == 1
    assert _event_count(page, "waitlist_signup_success") == 1
    assert _event_count(page, "generate_lead") == 0
    assert _meta_event_count(page, "Lead") == 1
    assert email not in page.evaluate(
        """() => JSON.stringify({
          google: window.dataLayer || [],
          meta: (window.fbq && window.fbq.queue) || []
        })"""
    )

    page.get_by_role("button", name="Use another email").click()
    page.wait_for_timeout(800)
    page.locator("#waitlist-email").fill(email)
    page.locator('#waitlist button[type="submit"]').click()
    duplicate_error = page.get_by_text(
        "This email is already on the waitlist. Please use a different email."
    )
    expect(duplicate_error).to_be_visible()
    expect(page.locator("#waitlist-email")).to_have_attribute("aria-invalid", "true")
    assert _event_count(page, "waitlist_submit_attempt") == 2
    assert _event_count(page, "waitlist_signup_success") == 1
    assert _event_count(page, "generate_lead") == 0
    assert _meta_event_count(page, "Lead") == 1


def test_sections_retry_after_consent_and_faq_tracks_only_deliberate_stable_id(
    page: Page,
    base_url: str,
) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    _configure_fake_providers(page)

    page.wait_for_timeout(1200)
    assert _event_count(page, "section_view") == 0
    assert _event_count(page, "faq_open") == 0

    page.locator("[data-cookie-accept-analytics]").click()
    page.wait_for_timeout(1100)
    assert any(
        event.get("section_name") == "hero"
        for event in _google_event_parameters(page, "section_view")
    )

    expected = [
        "hero",
        "problem_solution",
        "how_it_works",
        "feature_screen",
        "feature_build",
        "feature_monitor",
        "feature_connect",
        "trust_control",
        "faq",
    ]
    for section_name in expected[1:]:
        section = page.locator(f'[data-analytics-section="{section_name}"]')
        section.scroll_into_view_if_needed()
        page.wait_for_timeout(1100)

    section_events = _google_event_parameters(page, "section_view")
    section_names = [event.get("section_name") for event in section_events]
    for section_name in expected:
        assert section_names.count(section_name) == 1
    assert "features" not in section_names

    assert _event_count(page, "faq_open") == 0
    target = page.get_by_role("button", name="Who is Hilal Markets designed for?")
    target.click()
    target.click()
    target.click()
    assert _event_count(page, "faq_open") == 1
    faq_events = _google_event_parameters(page, "faq_open")
    assert faq_events == [{"faq_id": "target_audience", "page_path": "/"}]
    serialized = str(faq_events)
    assert "Who is Hilal Markets designed for?" not in serialized
    assert "@example.com" not in serialized


def test_long_entry_section_and_percentage_waitlist_visibility(
    page: Page,
    base_url: str,
) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    _configure_fake_providers(page)
    page.locator("[data-cookie-accept-analytics]").click()

    long_section = page.locator('[data-analytics-section="feature_screen"]')
    long_section.evaluate("element => { element.style.height = '400vh'; }")
    long_section.scroll_into_view_if_needed()
    page.wait_for_timeout(1100)
    section_names = [
        event.get("section_name")
        for event in _google_event_parameters(page, "section_view")
    ]
    assert section_names.count("feature_screen") == 1

    waitlist = page.locator("#waitlist")
    geometry = waitlist.evaluate(
        """element => ({
          top: element.getBoundingClientRect().top + window.scrollY,
          height: element.getBoundingClientRect().height,
          viewport: window.innerHeight,
        })"""
    )
    page.evaluate(
        "({top, height, viewport}) => window.scrollTo(0, top - viewport + height * 0.4)",
        geometry,
    )
    page.wait_for_timeout(1100)
    assert _event_count(page, "waitlist_form_view") == 0

    page.evaluate(
        "({top, height, viewport}) => window.scrollTo(0, top - viewport + height * 0.6)",
        geometry,
    )
    page.wait_for_timeout(500)
    assert _event_count(page, "waitlist_form_view") == 0
    page.wait_for_timeout(600)
    assert _event_count(page, "waitlist_form_view") == 1


def test_missing_or_failed_tracking_provider_does_not_block_waitlist_submission(
    page: Page,
    base_url: str,
) -> None:
    _mock_waitlist_backend(page)
    page.route(
        "https://www.googletagmanager.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* the test dispatches the provider error explicitly */",
        ),
    )
    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator("main h1")).to_be_visible()
    page.evaluate(
        """() => {
          window.HilalMarketsRuntimeConfig.analytics = {
            enabled: true,
            gtmId: 'GTM-KBBHH2FV',
            metaPixelEnabled: false,
          };
        }"""
    )
    page.locator("[data-cookie-accept-analytics]").click()
    provider_script = page.locator('script[data-hm-provider="google-tag-manager"]')
    expect(provider_script).to_be_attached()
    provider_script.dispatch_event("error")
    email = f"provider-failure-{uuid4().hex[:12]}@example.com"
    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill(email)
    page.locator('#waitlist button[type="submit"]').click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()


def test_failed_waitlist_submission_never_emits_success_event(
    page: Page,
    base_url: str,
) -> None:
    page.route(
        "**/api/v1/public-forms/waitlist",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="{invalid-response",
        ),
    )
    page.goto(base_url, wait_until="domcontentloaded")
    _configure_fake_providers(page)
    page.locator("[data-cookie-accept-analytics]").click()
    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill(
        f"failed-{uuid4().hex[:12]}@example.com"
    )
    page.locator('#waitlist button[type="submit"]').click()
    expect(page.get_by_text("We could not submit your email. Please try again.")).to_be_visible()
    assert _event_count(page, "waitlist_signup_success") == 0
    assert _event_count(page, "generate_lead") == 0


def test_contact_form_shows_branded_success_without_duplicate_client_submission(
    page: Page,
    base_url: str,
) -> None:
    page.goto(f"{base_url}/contact", wait_until="domcontentloaded")
    form = page.locator("[data-contact-form]")
    expect(form).to_be_visible()
    form.locator('input[name="title"]').fill("Private beta contact")
    form.locator('input[name="email"]').fill(
        f"contact-{uuid4().hex[:12]}@example.com"
    )
    form.locator('textarea[name="description"]').fill(
        "I would like to understand the private beta contact process."
    )
    form.locator('button[type="submit"]').click()
    expect(page.locator("[data-contact-success]")).to_be_visible()
    expect(page.locator("[data-contact-success]")).to_contain_text(
        "The Hilal Markets team has received one copy."
    )
