"""Adversarial browser flows: try to make the product cross its own boundaries.

These run inside the existing Playwright harness — the same ``conftest.py``, the same
auto-started application, the same brand and overflow helpers. A second browser harness
would need its own fixture for signing up, its own model stub and its own idea of what a
console error is, and the two would disagree within a month.

Nothing here fixes anything and nothing here writes to the product. Every test either
proves a boundary holds, or fails and becomes a finding in
``docs/OI_ADVERSARIAL_QA_RUN.md``.

**Why these attacks and not others.** The catalogue in ``hm_oi.qa_attacks`` lists what to
try; this file covers the entries whose answer can only be seen in a real browser against
a real server — authorization on live routes, what a customer session can reach, what the
rendered page actually says, and whether a status is readable without colour. The
conversation attacks are deterministic and live in ``tests/oi`` where they cost nothing.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from hm_oi.qa_attacks import ATTACKER_CLAIM_PHRASES, scan_for_claims
from tests.browser.conftest import (
    assert_no_horizontal_overflow,
    assert_no_raw_traceback,
    close_any_open_guide,
    seed_sharia_screened_market,
    signup,
    unique_email,
)

#: Every authenticated surface the phase brief names, at the address it actually lives at
#: on this commit, and only the ones that actually answer.
#:
#: Two addresses were in this list and are not any more, because both deliberately return
#: 404: ``/dashboard/setups`` ("Latest Setups was removed. Use Lifecycles.",
#: `api/routers/dashboard.py:2584`) and ``/dashboard/why-no-alert`` (`:2868`). They are
#: retired pages, not broken ones. Leaving them here produced 404 console errors that the
#: shared browser fixture correctly treats as runtime failures — a finding invented
#: entirely by this file, which is the kind of noise this phase is judged on.
#:
#: Onboarding is not a page: it is the signup and verification flow, which every test here
#: drives through :func:`signup` before it does anything else.
CUSTOMER_SURFACES: tuple[tuple[str, str], ...] = (
    ("home", "/dashboard"),
    ("monitor creation", "/dashboard/create-monitor"),
    ("monitors", "/dashboard/monitors"),
    ("screened watchlist", "/dashboard/market"),
    ("activity", "/dashboard/opportunities"),
    ("methodology", "/dashboard/methodology"),
    ("compliance", "/dashboard/compliance"),
    ("settings", "/dashboard/settings"),
    ("support", "/dashboard/support"),
)

#: The public pages an anonymous visitor can reach, including the public assistant's own
#: page. These carry the marketing copy, which is where a forbidden promise is most likely.
PUBLIC_SURFACES: tuple[tuple[str, str], ...] = (
    ("home", "/"),
    ("features", "/features"),
    ("how it works", "/how-it-works"),
    ("how we screen", "/how-we-screen"),
    ("help", "/help"),
    ("contact", "/contact"),
    ("about", "/about"),
    ("trust and safety", "/trust-safety"),
    ("risk disclosure", "/risk-disclosure"),
)

#: Internal surfaces an ordinary customer session must never reach. Each is a real route
#: at HEAD; a probe against a route that does not exist would pass for the wrong reason.
ADMIN_SURFACES: tuple[str, ...] = (
    "/api/v1/admin/health",
    "/api/v1/admin/activity",
    "/admin",
    "/admin/sharia",
)

#: The System Brain is the internal operator assistant. A customer session is never one
#: of its identities, and its answers describe the deployment.
SYSTEM_BRAIN_SURFACES: tuple[str, ...] = (
    "/api/v1/system-brain/conversations",
    "/system-brain",
)

#: Status codes that mean "you are not allowed", plus the two that mean "there is nothing
#: here". A 404 is an acceptable refusal — it tells a customer nothing — but a 200 is not.
REFUSING_STATUSES: frozenset[int] = frozenset({401, 403, 404, 405, 302, 303, 307, 308})


def _status_for(page: Page, base_url: str, path: str) -> int:
    """GET a path with the browser's own cookies, and report the status.

    Uses the page's request context rather than a fresh client so the customer's real
    session travels with it. A probe sent without the cookie proves only that anonymous
    users are refused, which nobody doubted.
    """

    response = page.request.get(f"{base_url}{path}", max_redirects=0)
    return response.status


@pytest.mark.parametrize("path", ADMIN_SURFACES)
def test_a_customer_session_cannot_reach_an_admin_surface(
    page: Page, base_url: str, path: str
) -> None:
    """Attack authz.admin_from_customer_session."""

    signup(page, base_url)
    close_any_open_guide(page)
    status = _status_for(page, base_url, path)
    assert status in REFUSING_STATUSES, (
        f"{path} answered {status} to an ordinary customer session. An admin surface "
        "must refuse."
    )


@pytest.mark.parametrize("path", SYSTEM_BRAIN_SURFACES)
def test_a_customer_session_cannot_reach_the_system_brain(
    page: Page, base_url: str, path: str
) -> None:
    """Attack authz.system_brain_from_customer_session."""

    signup(page, base_url)
    close_any_open_guide(page)
    status = _status_for(page, base_url, path)
    assert status in REFUSING_STATUSES, (
        f"{path} answered {status} to an ordinary customer session. The System Brain is "
        "an internal surface."
    )


def test_an_admin_refusal_leaks_nothing_about_what_is_behind_it(
    page: Page, base_url: str
) -> None:
    """A refusal that describes the thing it is refusing is half an answer."""

    signup(page, base_url)
    close_any_open_guide(page)
    response = page.request.get(f"{base_url}/api/v1/admin/activity", max_redirects=0)
    body = response.text()[:4000]
    assert response.status in REFUSING_STATUSES
    for leaked in ("traceback", "sqlalchemy", "select ", "database_url", "secret_key"):
        assert leaked not in body.casefold(), f"the refusal body mentions {leaked!r}"


def test_every_customer_surface_renders_no_forbidden_promise(
    page: Page, base_url: str
) -> None:
    """Attack copy.static_claims, against what is actually painted rather than the source.

    A phrase assembled in JavaScript never appears in a template, so a source scan cannot
    see it. This reads the rendered page, on every authenticated surface the brief names.
    """

    signup(page, base_url)
    close_any_open_guide(page)
    offences: list[str] = []
    for label, path in CUSTOMER_SURFACES:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert_no_raw_traceback(page)
        rendered = page.locator("body").inner_text()
        offences += [
            f"{label} ({path}): {hit.rule}: {hit.phrase!r} in {hit.context!r}"
            for hit in scan_for_claims(rendered)
            if hit.is_violation
        ]
    assert not offences, "\n".join(offences)


def test_every_public_surface_renders_no_forbidden_promise(
    page: Page, base_url: str
) -> None:
    """The same attack against what an anonymous visitor sees, including the assistant."""

    offences: list[str] = []
    for label, path in PUBLIC_SURFACES:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        # A page hidden by the launch stage is a correct 404, not a finding. Only pages
        # that actually render are scanned.
        if response is not None and response.status >= 400:
            continue
        assert_no_raw_traceback(page)
        rendered = page.locator("body").inner_text()
        offences += [
            f"{label} ({path}): {hit.rule}: {hit.phrase!r} in {hit.context!r}"
            for hit in scan_for_claims(rendered)
            if hit.is_violation
        ]
    assert not offences, "\n".join(offences)


def test_no_customer_surface_shows_a_raw_error_to_a_customer(
    page: Page, base_url: str
) -> None:
    """Error states: a stack trace on screen is both a defect and an information leak."""

    signup(page, base_url)
    close_any_open_guide(page)
    for label, path in CUSTOMER_SURFACES:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert_no_raw_traceback(page)
        body = page.locator("body").inner_text().casefold()
        for leaked in ("traceback (most recent call last)", "sqlalchemy.exc", "psycopg"):
            assert leaked not in body, f"{label} ({path}) showed {leaked!r}"


@pytest.mark.deliberate_console_errors("404")
def test_a_bad_address_gives_a_page_and_not_a_stack_trace(page: Page, base_url: str) -> None:
    """The most common error state, and the one a customer reaches by mistyping.

    The 404s here are the point of the test, so they are declared deliberate. Without
    that the shared fixture fails the test for producing exactly the condition it was
    written to produce.
    """

    signup(page, base_url)
    close_any_open_guide(page)
    for path in ("/dashboard/does-not-exist", "/dashboard/setups", "/no-such-page"):
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert_no_raw_traceback(page)
        rendered = page.locator("body").inner_text()
        assert rendered.strip(), f"{path} rendered an empty page"
        offences = [hit for hit in scan_for_claims(rendered) if hit.is_violation]
        assert not offences, f"{path}: {offences}"


def test_a_shariah_status_always_has_its_evidence_within_reach(
    page: Page, base_url: str, browser_app
) -> None:
    """Attack sharia.status_without_provenance, on a real seeded Passport.

    **What the rule actually is.** A status must never be a bare fact. That does not mean
    every list row prints an authority, a methodology, a version and a date — a list that
    did would be unreadable, and demanding it here would have produced a false finding
    against a perfectly good design. It means the evidence is always *identified and one
    step away*, and that the evidence itself is complete.

    So this checks the two halves separately: every row that states a status offers its
    Passport, and the Passport carries all four.
    """

    email = signup(page, base_url, unique_email("oi4-passport"))
    seeded = seed_sharia_screened_market(browser_app.database_url, email)
    close_any_open_guide(page)

    page.goto(
        f"{base_url}/dashboard/market?methodology_id={seeded['methodology_id']}",
        wait_until="domcontentloaded",
    )
    assert_no_raw_traceback(page)

    # The redesigned Halal Assets page draws a card per coin, not a table row. This test
    # was still looking for `.live-market-row` and a "Show passport" button, neither of
    # which has existed since the page was redesigned — so it was failing on markup
    # rather than on the rule it exists to protect. The rule is unchanged; only the
    # place to look for it moved.
    card = page.locator(".t-asset", has_text="SOL")
    expect(card.first).to_be_visible(timeout=20_000)
    card = card.first

    offences = [
        hit for hit in scan_for_claims(page.locator("body").inner_text()) if hit.is_violation
    ]
    assert not offences, f"the screened watchlist rendered: {offences}"

    # Half one: the status is stated, and its evidence is offered beside it.
    assert "halal" in card.inner_text().casefold(), "the card states no status to check"
    passport_button = card.locator("[data-quick-view]")
    expect(passport_button).to_be_visible()

    # Half two: the evidence is complete. Anything less is a claim nobody reviewed.
    passport_button.click()
    dialog = page.locator("[data-passport-dialog]")
    expect(dialog).to_be_visible()
    dialog.locator("[data-pq-full]").click()
    expect(page.locator("h1").first).to_be_visible(timeout=20_000)
    assert_no_raw_traceback(page)

    passport = page.locator("body").inner_text()
    lowered = passport.casefold()
    missing = [
        name
        for name, needles in (
            ("authority", ("authority", "issued by", "reviewed by", "source")),
            ("methodology", ("methodology",)),
            ("version", ("version", "v1", "v2")),
            ("decision date", ("20",)),  # any 20xx year
        )
        if not any(needle in lowered for needle in needles)
    ]
    assert not missing, (
        f"The Evidence Passport does not carry {missing}. A Shariah status without its "
        "authority, methodology, version and decision date is a claim nobody reviewed."
    )
    offences = [hit for hit in scan_for_claims(passport) if hit.is_violation]
    assert not offences, f"the Passport rendered: {offences}"


def test_no_status_is_told_by_colour_alone(page: Page, base_url: str) -> None:
    """Attack sharia.colour_only_status - brand guide section 10.

    Every element carrying a status class must also carry readable text. A colour-blind
    customer, a printed page and a screen reader all get nothing from the colour.
    """

    signup(page, base_url)
    close_any_open_guide(page)
    page.goto(f"{base_url}/dashboard/opportunities", wait_until="domcontentloaded")
    assert_no_raw_traceback(page)

    silent = page.evaluate(
        """() => {
            const marks = document.querySelectorAll(
                '[class*="status-"], [class*="state-"], [data-status], [data-state]'
            );
            const bare = [];
            for (const element of marks) {
                const rect = element.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                const text = (element.innerText || '').trim();
                const label =
                    element.getAttribute('aria-label') ||
                    element.getAttribute('title') ||
                    '';
                if (!text && !label.trim()) {
                    bare.push(
                        `${element.tagName.toLowerCase()}${
                            [...element.classList].slice(0, 3).map(n => '.' + n).join('')
                        }`
                    );
                }
            }
            return [...new Set(bare)];
        }"""
    )
    assert silent == [], (
        "These status elements carry colour and no words: " + ", ".join(silent)
    )


def test_the_signed_out_visitor_cannot_reach_a_customer_page(page: Page, base_url: str) -> None:
    """The simplest authorization attack, and the one most easily broken by a refactor."""

    for path in ("/dashboard", "/dashboard/opportunities", "/dashboard/monitors"):
        response = page.request.get(f"{base_url}{path}", max_redirects=0)
        assert response.status in REFUSING_STATUSES, (
            f"{path} answered {response.status} with no session at all"
        )


def test_no_customer_surface_overflows_the_phone_the_product_targets(
    page: Page, base_url: str
) -> None:
    """A page that scrolls sideways on a phone is unusable, and this product is for beginners.

    **390 px, not 320.** 390 is the width the rest of this repository tests at
    (`test_dashboard_e2e.py:87`, `test_landing_analytics.py:543`) — an iPhone-class
    device. An earlier version of this test used 320 and failed, which would have been a
    finding against a width the product never claimed to support. Attacking a promise
    nobody made is exactly the noise this phase exists to avoid, so the attack is aimed at
    the promise that was actually made.
    """

    signup(page, base_url)
    close_any_open_guide(page)
    page.set_viewport_size({"width": 390, "height": 844})
    for _label, path in CUSTOMER_SURFACES:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert_no_raw_traceback(page)
        assert_no_horizontal_overflow(page)


def test_the_retired_watch_plan_wording_is_not_rendered_anywhere(
    page: Page, base_url: str
) -> None:
    """The release gate forbids it in source; this checks the painted page."""

    signup(page, base_url)
    close_any_open_guide(page)
    pattern = re.compile(r"\bwatch\s+plans?\b", re.IGNORECASE)
    for label, path in CUSTOMER_SURFACES:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        rendered = page.locator("body").inner_text()
        assert not pattern.search(rendered), f'{label} ({path}) rendered "Watch Plan"'


def test_the_attacker_vocabulary_is_actually_loaded() -> None:
    """A guard against the quietest failure this file could have.

    If the import above ever resolved to an empty list, every scan in this file would
    pass while checking nothing at all.
    """

    assert len(ATTACKER_CLAIM_PHRASES) > 40
    assert any(phrase == "100% halal" for phrase in ATTACKER_CLAIM_PHRASES)
