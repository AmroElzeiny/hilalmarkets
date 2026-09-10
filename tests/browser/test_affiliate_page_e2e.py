"""The affiliate page in a real browser: do the four time logs actually open?

Everything else about these popups can be proved without a browser — the markup renders,
the ids match, the rows are the server's own. **Whether they open cannot.** The popups
are driven by an ES module that imports two more modules and the vendored animation
bundle; a wrong import path, a class the shared helper looks for and cannot find, or a
bundle that quietly ignores an option all leave the page looking perfectly correct and
the button doing nothing. This product has been bitten by exactly that: every scripted
animation in it once ran on the wrong curve because the bundle ignored an option name
without warning.

So this file measures the three things only a browser can answer: the popup opens, it
holds the rows the card counted, and Escape closes it and gives the keyboard back to the
button that opened it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.browser.conftest import (
    RunningApp,
    _run_async_in_thread,
    assert_no_raw_traceback,
    close_any_open_guide,
    signup,
)


def seed_affiliate_with_earnings(database_url: str, email: str) -> None:
    """Make this account an approved affiliate who has already been paid.

    Written through the services rather than by inserting rows: the whole point of a
    browser test is that the page draws what the running product produces, and a
    hand-built row proves only that the page can draw a shape nothing writes.

    The code is different every time. One application is running for the whole file, so
    the database carries over between tests, and a fixed code is one an earlier test has
    already claimed — approval refuses it, correctly, and every later test fails at setup
    for a reason that has nothing to do with what it was measuring.
    """

    if not database_url:
        pytest.skip("Affiliate browser coverage requires the auto-started database.")
    code = f"BROWSERAFF{uuid4().hex[:8].upper()}"

    async def _seed() -> None:
        from ai_market_monitor.db.models import User, UserIdentity
        from ai_market_monitor.db.models.enums import IdentityProvider
        from ai_market_monitor.services.affiliate import AffiliateService
        from ai_market_monitor.services.affiliate_attribution import (
            CONTEXT_SIGNUP,
            ReferralAttributionService,
        )

        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            identity = await session.scalar(
                select(UserIdentity).where(
                    UserIdentity.provider == IdentityProvider.EMAIL,
                    UserIdentity.normalized_identifier == email.lower(),
                )
            )
            if identity is None:
                raise AssertionError(f"No browser test user identity found for {email}.")

            service = AffiliateService(session)
            await service.apply(
                user_id=identity.user_id,
                display_name="Browser Affiliate",
                social_links=["https://x.com/browseraffiliate"],
                requested_discount_code=code,
            )
            application = await service.application_for(identity.user_id)
            admin = User(display_name="Programme owner")
            session.add(admin)
            await session.flush()
            approved = await service.approve(
                application_id=application.id,
                admin_user_id=admin.id,
                discount_percent="10",
                commission_percent="25",
                subsequent_commission_percent="15",
            )

            attribution = ReferralAttributionService(session)
            customer = User(display_name="Karim Hassan")
            session.add(customer)
            await session.flush()
            await attribution.assign(
                user_id=customer.id, link_code=approved.discount_code
            )
            await attribution.record_code_use(
                code=approved.discount_code,
                user_id=customer.id,
                context=CONTEXT_SIGNUP,
                event_key=f"signup:{customer.id}",
            )
            # A first payment and a renewal, so all three money logs have something in
            # them and the two rates are visibly different.
            for number, amount in ((1, "40.00"), (2, "40.00")):
                await attribution.record_payment(
                    customer_user_id=customer.id,
                    event_key=f"browser-payment-{number}:{customer.id}",
                    paid_amount_usd=Decimal(amount),
                    occurred_at=datetime.now(UTC),
                )
            await session.commit()
        await engine.dispose()

    _run_async_in_thread(_seed)


@pytest.fixture
def affiliate_page(page: Page, base_url: str, browser_app: RunningApp) -> Page:
    email = signup(page, base_url)
    close_any_open_guide(page)
    seed_affiliate_with_earnings(browser_app.database_url, email)
    page.goto(f"{base_url}/dashboard/affiliate", wait_until="domcontentloaded")
    assert_no_raw_traceback(page)
    return page


#: Each card, the button on it, and a thing that must be inside the popup it opens.
#:
#: Parametrised rather than written four times: the rule is "every figure can be opened
#: and holds what it counted", and a test that only checks one of them would pass while
#: three buttons did nothing.
LOGS = (
    ("Times your code was used", "See every use", "Every time your code was used"),
    ("Earned in total", "See every payment", "Everything you have earned"),
    ("Earned the first time people paid", "See first payments", "Earned the first time"),
    ("Earned every time after that", "See later payments", "Earned every time after that"),
)


def test_the_page_shows_the_six_figures_and_both_rates(affiliate_page: Page) -> None:
    """The cards a person came to read, before any popup is opened."""

    page = affiliate_page
    for caption in (
        "Times your code was used",
        "People signed up through your link",
        "Earned in total",
        "Earned the first time people paid",
        "Earned every time after that",
        "Yours to take out now",
    ):
        expect(page.get_by_text(caption, exact=True).first).to_be_visible()

    # 25% of $40 once, then 15% of $40 — $10.00 and $6.00, $16.00 together.
    expect(page.get_by_text("$16.00").first).to_be_visible()
    expect(page.get_by_text("$10.00").first).to_be_visible()
    expect(page.get_by_text("$6.00").first).to_be_visible()
    # And the page states both rates in words a beginner can act on.
    expect(page.locator(".panel").first).to_contain_text(
        re.compile(r"25% the first time they pay", re.I)
    )
    expect(page.locator(".panel").first).to_contain_text(
        re.compile(r"15% every time after that", re.I)
    )


@pytest.mark.parametrize("caption,button,inside", LOGS, ids=[row[1] for row in LOGS])
def test_every_figure_opens_its_own_time_log(
    affiliate_page: Page, caption: str, button: str, inside: str
) -> None:
    """The button opens a popup, and the popup is the one it names."""

    page = affiliate_page
    del caption
    trigger = page.get_by_role("button", name=button)
    expect(trigger).to_be_visible()
    trigger.click()

    dialog = page.locator("dialog[open]")
    expect(dialog).to_have_count(1)
    expect(dialog).to_contain_text(inside)
    # The name of a person is in it, never an address — an affiliate is shown who, not
    # how to reach them.
    expect(dialog).to_contain_text("Karim Hassan")
    assert "@example.com" not in dialog.inner_text()
    assert_no_raw_traceback(page)


def test_escape_closes_the_log_and_gives_the_keyboard_back(affiliate_page: Page) -> None:
    """A popup that keeps the keyboard is a page somebody cannot leave without a mouse.

    The browser's own ``<dialog>`` handles Escape; giving focus back to the button that
    opened it does not happen by itself, and is the part `hm-dialog.js` adds.
    """

    page = affiliate_page
    trigger = page.get_by_role("button", name="See every payment")
    trigger.click()
    expect(page.locator("dialog[open]")).to_have_count(1)

    page.keyboard.press("Escape")
    expect(page.locator("dialog[open]")).to_have_count(0)
    focused = page.evaluate(
        "() => document.activeElement && document.activeElement.textContent.trim()"
    )
    assert "See every payment" in (focused or ""), (
        f"the keyboard was left on {focused!r} rather than on the button that opened it"
    )


def test_the_money_log_shows_which_rate_each_payment_was_paid_at(
    affiliate_page: Page,
) -> None:
    """Two rates are only useful if a person can see which one applied, and when."""

    page = affiliate_page
    page.get_by_role("button", name="See every payment").click()
    dialog = page.locator("dialog[open]")
    text = dialog.inner_text()
    assert "First time" in text
    assert "A later payment" in text
    assert "25%" in text and "15%" in text
    # And the moment, not only the day: two payments on one day must be tellable apart.
    assert re.search(r"\d{2} \w{3} \d{4}, \d{2}:\d{2} UTC", text), text[:400]
