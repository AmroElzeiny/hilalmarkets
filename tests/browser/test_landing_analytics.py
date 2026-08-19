import re
from uuid import uuid4

import pytest
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


def _stub_waitlist_api(
    page: Page,
    *,
    created: bool = True,
    status: int = 200,
) -> list[dict]:
    """Answer the public-forms endpoints in the browser and keep what was sent.

    `created=False` is the address that is already on the list; `status` above 399 is a
    submission the server refused. Neither is a new signup, so neither may be counted.
    """

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
        if status >= 400:
            route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps({"detail": "The request could not be handled."}),
            )
            return
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

    # An email address is the only thing the form asks for.
    expect(page.locator("#waitlist form input")).to_have_count(1)

    email = page.locator("#waitlist-email")
    email.fill(f"browser-{uuid4().hex[:8]}@example.com")
    assert _event_count(page, "waitlist_form_start") == 1

    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert _event_count(page, "waitlist_submit_attempt") == 1
    assert _event_count(page, "waitlist_signup_success") == 1
    # The GA4 waitlist conversion: one per confirmed signup, and nothing attached to it.
    assert _event_count(page, "waitlist_join") == 1
    assert _google_event_parameters(page, "waitlist_join") == [{}]
    assert _event_count(page, "waitlist_form_error") == 0
    assert _meta_event_count(page, "Lead") == 1

    # The browser sends the email and nothing about beta-testing consent, and no
    # analytics event carries the address that was typed.
    assert len(submitted) == 1
    assert "beta_contact_consent" not in submitted[0]
    success_events = _google_event_parameters(page, "waitlist_signup_success")
    assert success_events == [{}]
    assert "@example.com" not in str(_google_event_parameters(page, "waitlist_form_start"))

    # A repeated callback for the same submission reports nothing further. This is the
    # real duplicate: the same signup told to the page again, by a re-render, a retried
    # promise, or a handler that ran twice.
    page.evaluate(
        """(key) => {
          window.HilalAnalytics.trackWaitlistSuccess('landing_final', key);
          window.HilalAnalytics.trackWaitlistSuccess('landing_final', key);
        }""",
        submitted[0]["idempotency_key"],
    )
    assert _event_count(page, "waitlist_join") == 1
    assert _event_count(page, "waitlist_signup_success") == 1
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


def test_the_form_shows_no_consent_box_and_sends_no_consent_answer(
    page: Page,
    base_url: str,
) -> None:
    """The withdrawn box is gone from the running page, not only from the source.

    It was offered already ticked, which records an answer the person never gave. Checked
    on the real page because the source and the served bundle are two different things.
    """

    submitted = _stub_waitlist_api(page)
    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("#waitlist").scroll_into_view_if_needed()
    expect(page.locator("#waitlist-beta-consent")).to_have_count(0)
    expect(page.locator("#waitlist input[type='checkbox']")).to_have_count(0)
    page.locator("#waitlist-email").fill(f"no-beta-{uuid4().hex[:8]}@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()
    assert len(submitted) == 1
    assert "beta_contact_consent" not in submitted[0]


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
    assert _event_count(page, "waitlist_join") == 0
    assert _meta_event_count(page, "Lead") == 0
    errors = _google_event_parameters(page, "waitlist_form_error")
    assert [event.get("error_type") for event in errors] == ["duplicate_email"]


@pytest.mark.deliberate_console_errors("429 (Too Many Requests)")
def test_a_refused_submission_reports_no_waitlist_conversion(
    page: Page,
    base_url: str,
) -> None:
    """A signup that did not happen is never counted - before or after consent.

    Two ways the conversion could be invented are checked together: the server refusing
    the submission, and analytics running before the visitor allowed it. Each is checked
    on the running page, because the count that matters is the one GTM would receive.

    The refusal is a 429. A 5xx would be caught by the fixture's own check for failed
    API calls, and every refusal reaches the same branch of the submit handler.
    """

    _stub_waitlist_api(page, status=429)
    page.goto(base_url, wait_until="domcontentloaded")

    # First: no consent yet. Nothing may be pushed at all.
    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill(f"refused-{uuid4().hex[:8]}@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    expect(page.get_by_text("You are on the waitlist.")).to_have_count(0)
    assert _event_count(page, "waitlist_join") == 0

    # Then with analytics switched on, so "no event" means the transport was there and
    # stayed silent rather than being absent.
    _configure_fake_providers(page)
    page.locator("[data-cookie-accept-analytics]").click()
    page.wait_for_selector(
        'script[data-hm-provider="google-tag-manager"]', state="attached"
    )
    page.locator("#waitlist-email").fill(f"refused-{uuid4().hex[:8]}@example.com")
    page.get_by_role("button", name="Join the waitlist").last.click()
    errors = _google_event_parameters(page, "waitlist_form_error")
    assert [event.get("error_type") for event in errors] == ["rate_limited"]
    assert _event_count(page, "waitlist_join") == 0
    assert _event_count(page, "waitlist_signup_success") == 0
    assert _meta_event_count(page, "Lead") == 0

    # A retry of the refused submission is still not a signup.
    page.get_by_role("button", name="Join the waitlist").last.click()
    assert _event_count(page, "waitlist_join") == 0


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

    # The whole form can be completed from the keyboard: type the address, one Tab to the
    # button. There is nothing else in it to reach.
    email = page.locator("#waitlist-email")
    email.focus()
    email.type("keyboard@example.com")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Join the waitlist")).to_be_focused()

    page.emulate_media(reduced_motion="reduce")
    assert page.locator("html").evaluate(
        "element => getComputedStyle(element).scrollBehavior"
    ) == "auto"


def test_the_hero_illustration_is_visible_and_fits_at_every_width(
    page: Page,
    base_url: str,
) -> None:
    """The picture in the hero is shown on a phone, a tablet and a desktop.

    It used to be dropped below 640 pixels wide, so a phone saw the headline and nothing
    else. Every width is checked, not only the phone that was reported: the illustration
    must be on the screen, none of its three panels may be empty, and nothing inside it
    may stick out past the side of the screen.
    """

    for width in (320, 390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(base_url, wait_until="domcontentloaded")
        flow = page.locator('[data-name="Hero flow"]')
        flow.scroll_into_view_if_needed()
        expect(flow).to_be_visible()

        # The three panels are all drawn, and each one has real height.
        panels = flow.locator("> div")
        expect(panels).to_have_count(3)
        for index in range(3):
            box = panels.nth(index).bounding_box()
            assert box is not None, (width, index)
            assert box["height"] > 40, (width, index, box)

        # Nothing inside the illustration reaches past the edge of the screen.
        overflow = page.evaluate(
            """() => {
              const flow = document.querySelector('[data-name="Hero flow"]');
              if (!flow) return ['missing'];
              const viewport = window.innerWidth;
              return [...flow.querySelectorAll('*'), flow]
                .filter((element) => {
                  const rect = element.getBoundingClientRect();
                  return rect.width > 0 && (rect.left < -1 || rect.right > viewport + 1);
                })
                .slice(0, 6)
                .map((element) => element.className || element.tagName);
            }"""
        )
        assert overflow == [], (width, overflow)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ), width


def test_contact_page_is_only_the_form_and_the_address(
    page: Page,
    base_url: str,
) -> None:
    """No private-beta band and no inbox diagram are drawn on the contact page."""

    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{base_url}/contact", wait_until="domcontentloaded")
        expect(page.locator("[data-contact-form]")).to_be_visible()
        body = page.locator("main").inner_text()
        for removed in ("A clear route to the team", "Secure delivery", "Human review"):
            assert removed not in body, (width, removed)
        # The waitlist band is the only thing that used to sit between the form and the
        # footer, so a page that still had it would still offer this link inside <main>.
        assert page.locator('main a[href*="#waitlist"]').count() == 0, width


def test_contact_form_shows_branded_success_without_duplicate_client_submission(
    page: Page,
    base_url: str,
) -> None:
    """One press, one message — and the page says so in the brand's own words.

    This test had been failing since `/contact` was rebuilt. It filled the fields by
    `name=`, which the rebuilt form does not use, and it pressed a submit button that no
    longer sends anything on its own: there is a review window in between now.

    Its title promised something it never actually checked, so that is what it checks
    now. Counting the requests is the whole point — a form that sends twice creates two
    tickets, spends two of somebody's allowance, and sends them two emails.
    """

    sent: list[str] = []
    page.on(
        "request",
        lambda request: sent.append(request.url)
        if request.method == "POST" and request.url.endswith("/public-forms/contact")
        else None,
    )

    page.goto(f"{base_url}/contact", wait_until="domcontentloaded")
    expect(page.locator("[data-contact-form]")).to_be_visible()
    page.locator("main [id$='-title']").fill("How the contact route works")
    page.locator("main [id$='-email']").fill(f"contact-{uuid4().hex[:12]}@example.com")
    page.locator("main [id$='-description']").fill(
        "I would like to understand how a message reaches the team."
    )

    page.get_by_role("button", name=re.compile("Check and send", re.I)).click()
    expect(page.get_by_role("dialog")).to_be_visible(timeout=5_000)
    page.get_by_role("button", name=re.compile("Send message", re.I)).click()

    expect(page.locator("[data-contact-success]")).to_be_visible(timeout=15_000)
    expect(page.locator("[data-contact-success]")).to_contain_text("Your message was sent.")
    page.wait_for_timeout(1_000)
    assert len(sent) == 1, f"one press sent {len(sent)} messages"
