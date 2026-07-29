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


def test_consent_cta_sections_and_pricing_events_are_grounded_and_deduplicated(
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

    hero_cta = page.locator('main a[href^="/subscribe?plan_code=demo"]').first
    hero_cta.evaluate(
        "(element) => element.addEventListener('click', event => event.preventDefault())"
    )
    hero_cta.evaluate("(element) => { element.click(); element.click(); }")
    assert _event_count(page, "cta_click") == 1

    pricing = page.locator("#pricing")
    pricing.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    assert _event_count(page, "pricing_section_view") == 1
    page.evaluate("window.scrollTo(0, 0)")
    pricing.scroll_into_view_if_needed()
    page.wait_for_timeout(1200)
    assert _event_count(page, "pricing_section_view") == 1

    page.get_by_label("Annual").check()
    assert _event_count(page, "billing_interval_changed") == 1
    expect(page.get_by_text("$120", exact=True)).to_be_visible()
    expect(page.get_by_text("Save $24 per year", exact=True)).to_be_visible()

    monitor_cta = page.get_by_role("link", name="Choose Monitor")
    expect(monitor_cta).to_have_attribute(
        "href",
        "/subscribe?plan_code=trader&billing_interval=annual",
    )
    monitor_cta.evaluate(
        "(element) => element.addEventListener('click', event => event.preventDefault())"
    )
    monitor_cta.click()
    assert _event_count(page, "plan_selected") == 1
    assert _event_count(page, "checkout_started") == 0
    assert _event_count(page, "waitlist_signup_success") == 0

    page.get_by_role("button", name="View all features").first.click()
    expect(page.get_by_role("button", name="Show fewer features").first).to_be_visible()


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
        "pricing",
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


def test_long_entry_section_and_pricing_visibility(
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

    pricing = page.locator("#pricing")
    pricing.evaluate("element => { element.style.minHeight = '300vh'; }")
    pricing.scroll_into_view_if_needed()
    page.wait_for_timeout(1100)
    assert _event_count(page, "pricing_section_view") == 1


def test_missing_or_failed_tracking_provider_does_not_block_plan_navigation(
    page: Page,
    base_url: str,
) -> None:
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
    page.locator("#pricing").scroll_into_view_if_needed()
    page.get_by_role("link", name="Start free").click()
    expect(page).to_have_url(
        f"{base_url}/signup?plan_code=demo&billing_interval=monthly"
    )


def test_paid_plan_selection_preserves_plan_and_interval_when_billing_is_disabled(
    page: Page,
    base_url: str,
) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("#pricing").scroll_into_view_if_needed()
    page.get_by_label("Annual").check()
    page.get_by_role("link", name="Choose Pro").click()
    expect(page).to_have_url(
        f"{base_url}/signup?plan_code=pro&billing_interval=annual"
    )
    expect(
        page.get_by_text("Your Pro plan choice will be kept after authentication.")
    ).to_be_visible()


def test_pricing_is_responsive_keyboard_accessible_and_reduced_motion_safe(
    page: Page,
    base_url: str,
) -> None:
    for width in (1440, 768, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(base_url, wait_until="domcontentloaded")
        pricing = page.locator("#pricing")
        pricing.scroll_into_view_if_needed()
        expect(pricing).to_be_visible()
        expect(pricing.locator(".pricing-card")).to_have_count(3)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )
        columns = pricing.locator(".pricing-grid").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert columns == (3 if width == 1440 else 2 if width == 768 else 1)
        if width == 1440:
            for selector in (
                ".pricing-heading p",
                ".pricing-card-head p",
                ".plan-price span",
                ".plan-features li",
                ".pricing-trust",
            ):
                assert pricing.locator(selector).first.evaluate(
                    "element => getComputedStyle(element).color"
                ) == "rgb(43, 46, 53)"

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url, wait_until="domcontentloaded")
    menu = page.get_by_role("button", name="Menu")
    menu.click()
    pricing_link = page.get_by_role("navigation", name="Mobile navigation").get_by_role(
        "link", name="Pricing"
    )
    expect(pricing_link).to_be_visible()
    pricing_link.click()
    expect(page.locator("#pricing")).to_be_in_viewport()

    expander = page.locator(".feature-expander").first
    expander.focus()
    expander.press("Enter")
    expect(expander).to_have_attribute("aria-expanded", "true")

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
