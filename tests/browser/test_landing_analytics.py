import re
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
            // A key pushed as `undefined` clears Tag Manager's copy; it is not sent.
            const {event, ...parameters} = item;
            return [Object.fromEntries(
              Object.entries(parameters).filter(([, value]) => value !== undefined)
            )];
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


# The landing page has had no waitlist since launch (`test_the_landing_page_leads_into_
# the_product_and_never_to_a_waitlist` forbids one). The browser tests that filled in its
# form were removed on 8 October 2026; they waited for a `#waitlist` that no longer
# exists and failed on every run. What they also covered about analytics — consent,
# calls to action, sections, the FAQ — is kept below without the form.


def test_consent_cta_and_page_views_are_grounded_and_deduplicated(
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

    # The hero's call to action: counted once however often it is clicked in a burst.
    page.wait_for_timeout(1100)
    assert any(
        event.get("section_name") == "hero"
        for event in _google_event_parameters(page, "section_view")
    )
    hero_cta = page.locator('main a[href*="dashboard-entry"]').first
    hero_cta.evaluate(
        "(element) => element.addEventListener('click', event => event.preventDefault())"
    )
    hero_cta.evaluate("(element) => { element.click(); element.click(); }")
    assert _event_count(page, "cta_click") == 1

    # A click carries its own details and nothing left over from the event before it.
    # Tag Manager keeps the last value of every key, so a `section_name` the click did not
    # send used to reach Google Analytics anyway — measured live on 8 October 2026.
    click = _google_event_parameters(page, "cta_click")[0]
    assert click["cta_location"] == "hero"
    assert "section_name" not in click
    cleared = page.evaluate(
        """() => {
          const item = (window.dataLayer || [])
            .find((entry) => entry && entry.event === 'cta_click');
          return Object.prototype.hasOwnProperty.call(item, 'section_name')
            && item.section_name === undefined;
        }"""
    )
    assert cleared is True


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
        "ecosystem",
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


def test_a_section_taller_than_the_window_is_counted_once(
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
