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
    page.route(
        "https://static.ads-twitter.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* X Pixel transport intentionally empty in browser tests */",
        ),
    )
    page.evaluate(
        """() => {
          window.HilalMarketsRuntimeConfig.analytics = {
            enabled: true,
            gtmId: 'GTM-KBBHH2FV',
            metaPixelEnabled: true,
            metaPixelId: '1234567890',
            xPixelEnabled: true,
            xPixelId: 're20l',
            debug: false,
          };
        }"""
    )


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


def test_x_pixel_loads_once_after_marketing_consent_and_not_in_system_brain(
    page: Page,
    base_url: str,
) -> None:
    x_requests: list[str] = []
    page.on(
        "request",
        lambda request: x_requests.append(request.url)
        if "ads-twitter.com" in request.url or "analytics.twitter.com" in request.url
        else None,
    )
    page.route(
        "https://static.ads-twitter.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* X Pixel transport intentionally empty in browser tests */",
        ),
    )

    page.goto(f"{base_url}/features", wait_until="domcontentloaded")
    assert page.locator('script[data-hm-provider="x-pixel"]').count() == 0
    assert x_requests == []

    page.locator("[data-cookie-customize]").click()
    page.locator("input[data-consent-marketing]").check()
    page.locator("[data-cookie-save]").click()
    page.wait_for_selector('script[data-hm-provider="x-pixel"]', state="attached")

    assert sum("static.ads-twitter.com/uwt.js" in url for url in x_requests) == 1
    assert page.evaluate(
        """() => (window.twq?.queue || []).filter((item) =>
          item && item[0] === 'config' && item[1] === 're20l'
        ).length"""
    ) == 1

    page.locator("[data-cookie-settings]").first.click()
    page.locator("input[data-consent-marketing]").uncheck()
    page.locator("[data-cookie-save]").click()
    page.goto(f"{base_url}/privacy", wait_until="domcontentloaded")
    assert page.locator('script[data-hm-provider="x-pixel"]').count() == 0
    assert sum("static.ads-twitter.com/uwt.js" in url for url in x_requests) == 1

    response = page.request.get(f"{base_url}/dashboard/system-brain")
    assert "static.ads-twitter.com" not in response.text()
    assert "hilalmarkets-consent.js" not in response.text()
    assert "xPixelId" not in response.text()


def _stub_waitlist_api(page: Page, *, created: bool = True) -> list[dict]:
    """Answer the public-forms endpoints in the browser and keep what was sent."""

    import json

    submitted: list[dict] = []

    page.route(
        "**/api/v1/public-forms/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "csrf_token": "browser-test-token",
                    "waitlist_endpoint": "/api/v1/public-forms/waitlist",
                    "contact_endpoint": "/api/v1/public-forms/contact",
                }
            ),
        ),
    )

    def _waitlist(route) -> None:
        submitted.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "created" if created else "already_registered",
                    "created": created,
                    "code": "waitlist_created" if created else "duplicate_email",
                    "sheet_delivery_status": "not_configured",
                    "message": "You are on the waitlist.",
                }
            ),
        )

    page.route("**/api/v1/public-forms/waitlist", _waitlist)
    return submitted


def test_consent_cta_sections_and_waitlist_events_are_grounded_and_deduplicated(
    page: Page,
    base_url: str,
) -> None:
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

    submitted = _stub_waitlist_api(page)

    hero_cta = page.locator('main a[href="#waitlist"]').first
    hero_cta.evaluate(
        "(element) => element.addEventListener('click', event => event.preventDefault())"
    )
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

    # The beta-contact question is offered already answered "yes".
    consent = page.locator("#waitlist-beta-consent")
    expect(consent).to_be_checked()

    email = page.locator("#waitlist-email")
    email.fill(f"browser-{uuid4().hex[:8]}@example.com")
    assert _event_count(page, "waitlist_form_start") == 1

    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert _event_count(page, "waitlist_submit_attempt") == 1
    assert _event_count(page, "waitlist_signup_success") == 1
    assert _event_count(page, "waitlist_form_error") == 0
    assert _meta_event_count(page, "Lead") == 1

    # What the browser sent is what the box said, and no analytics event carries the
    # address that was typed.
    assert len(submitted) == 1
    assert submitted[0]["beta_contact_consent"] is True
    success_events = _google_event_parameters(page, "waitlist_signup_success")
    assert success_events == [{}]
    assert "@example.com" not in str(_google_event_parameters(page, "waitlist_form_start"))


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
        "waitlist",
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


def test_long_entry_section_and_waitlist_visibility(
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

    # A section taller than the window never reaches full visibility, so the form is
    # counted as seen from the share of it that is on screen.
    waitlist = page.locator("#waitlist")
    waitlist.evaluate("element => { element.style.minHeight = '300vh'; }")
    waitlist.scroll_into_view_if_needed()
    page.wait_for_timeout(1100)
    assert _event_count(page, "waitlist_form_view") == 1


def test_missing_or_failed_tracking_provider_does_not_block_waitlist_submission(
    page: Page,
    base_url: str,
) -> None:
    """Analytics is never allowed to stand between a visitor and the waitlist."""

    page.route(
        "https://www.googletagmanager.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="/* the test dispatches the provider error explicitly */",
        ),
    )
    submitted = _stub_waitlist_api(page)
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

    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill(f"broken-provider-{uuid4().hex[:8]}@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert len(submitted) == 1


def test_a_cleared_consent_box_still_joins_the_waitlist(
    page: Page,
    base_url: str,
) -> None:
    """Clearing the box is a choice about contact, not a refusal to join."""

    submitted = _stub_waitlist_api(page)
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("#waitlist").scroll_into_view_if_needed()
    consent = page.locator("#waitlist-beta-consent")
    expect(consent).to_be_checked()
    consent.uncheck()
    page.locator("#waitlist-email").fill(f"no-beta-{uuid4().hex[:8]}@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert len(submitted) == 1
    assert submitted[0]["beta_contact_consent"] is False


def test_a_duplicate_email_is_explained_without_claiming_success(
    page: Page,
    base_url: str,
) -> None:
    _stub_waitlist_api(page, created=False)
    page.goto(base_url, wait_until="domcontentloaded")
    # Analytics is switched on deliberately: "no success event" only means something
    # once the transport that would have carried one is actually loaded.
    _configure_fake_providers(page)
    page.locator("[data-cookie-accept-analytics]").click()
    page.wait_for_selector(
        'script[data-hm-provider="google-tag-manager"]', state="attached"
    )
    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill("already-there@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(
        page.get_by_text("This email is already on the waitlist.")
    ).to_be_visible()
    assert _event_count(page, "waitlist_signup_success") == 0
    assert _meta_event_count(page, "Lead") == 0
    errors = _google_event_parameters(page, "waitlist_form_error")
    assert [event.get("error_type") for event in errors] == ["duplicate_email"]


def test_the_waitlist_is_responsive_keyboard_accessible_and_offers_no_account(
    page: Page,
    base_url: str,
) -> None:
    for width in (1440, 768, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(base_url, wait_until="domcontentloaded")
        waitlist = page.locator("#waitlist")
        waitlist.scroll_into_view_if_needed()
        expect(waitlist).to_be_visible()
        expect(page.locator("#waitlist-email")).to_be_visible()
        expect(page.locator("#waitlist-beta-consent")).to_be_visible()
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url, wait_until="domcontentloaded")
    menu = page.get_by_role("button", name="Menu")
    menu.click()
    mobile_menu = page.get_by_role("navigation", name="Mobile navigation")
    # No route into the product exists on a phone either.
    expect(mobile_menu.get_by_role("link", name="Sign in")).to_have_count(0)
    expect(mobile_menu.get_by_role("link", name="Pricing")).to_have_count(0)
    waitlist_link = mobile_menu.get_by_role("link", name="Join the waitlist")
    expect(waitlist_link).to_be_visible()
    waitlist_link.click()
    expect(page.locator("#waitlist")).to_be_in_viewport()

    # The whole form can be completed from the keyboard.
    email = page.locator("#waitlist-email")
    email.focus()
    email.type("keyboard@example.com")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Join the waitlist")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#waitlist-beta-consent")).to_be_focused()
    page.keyboard.press("Space")
    expect(page.locator("#waitlist-beta-consent")).not_to_be_checked()

    page.emulate_media(reduced_motion="reduce")
    assert page.locator("html").evaluate(
        "element => getComputedStyle(element).scrollBehavior"
    ) == "auto"


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
