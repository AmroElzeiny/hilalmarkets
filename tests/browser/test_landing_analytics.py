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
            ga4MeasurementId: 'G-HILALTEST1',
            metaPixelEnabled: true,
            metaPixelId: '1234567890',
            debug: false,
          };
        }"""
    )


def test_consent_cta_sections_and_waitlist_funnel_are_grounded_and_deduplicated(
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
        'script[data-hm-provider="google-analytics"]', state="attached"
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
    assert _event_count(page, "generate_lead") == 1
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
    expect(page.get_by_text("You are already on the waitlist.")).to_be_visible()
    assert _event_count(page, "waitlist_submit_attempt") == 2
    assert _event_count(page, "generate_lead") == 1
    assert _meta_event_count(page, "Lead") == 1


def test_missing_or_failed_tracking_provider_does_not_block_waitlist_submission(
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
            ga4MeasurementId: 'G-HILALTEST2',
            metaPixelEnabled: false,
          };
        }"""
    )
    page.locator("[data-cookie-accept-analytics]").click()
    provider_script = page.locator('script[data-hm-provider="google-analytics"]')
    expect(provider_script).to_be_attached()
    provider_script.dispatch_event("error")
    email = f"provider-failure-{uuid4().hex[:12]}@example.com"
    page.locator("#waitlist").scroll_into_view_if_needed()
    page.locator("#waitlist-email").fill(email)
    page.locator('#waitlist button[type="submit"]').click()
    expect(page.get_by_text("You are on the waitlist.")).to_be_visible()


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
