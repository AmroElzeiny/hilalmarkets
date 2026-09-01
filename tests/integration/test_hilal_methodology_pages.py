"""The published standard, reached the way a person reaches it.

Everything else about this work is proved a layer down: the module tests check the file,
the service tests check what publishing writes, the template tests check which surfaces
include the warning. What none of them can answer is whether a person who opens the
product actually meets it — so these go through the real app.

Two journeys:

* a visitor opens `/hilal-methodology` and reads the whole rule without an account;
* somebody signed in picks the standard on the Halal Assets page and is told, there and
  then, that a machine decided it and no Shariah advisor stands behind it.
"""

from __future__ import annotations

from ai_market_monitor.core.dashboard_paths import MARKET_PATH
from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_PUBLIC_PATH,
    UNDER_DEVELOPMENT_NOTICE,
    admitted_symbols,
    publish,
)
from ai_market_monitor.services.sharia_automated_screen import METHODOLOGY_DISPLAY_NAME
from ai_market_monitor.services.sharia_conditions import (
    applied_conditions,
    out_of_reach_conditions,
)
from tests.integration.test_dashboard_web import _signup_and_verify

#: The sentence a person must meet before acting on a machine's verdict.
WARNING = "No Shariah advisor stands behind it"


def _visible_part(html: str) -> str:
    """The page a person sees, without the dialogs that sit closed at the end of it.

    Every dialog on this page is closed until something opens it, and one of them — the
    Passport popup — carries its own hidden copy of the warning. Searching the whole
    document would find that copy and call the page warned when nothing is on screen.
    """

    return html.split("<dialog", 1)[0]


# --------------------------------------------------------------------------------
# The public page
# --------------------------------------------------------------------------------


async def test_the_public_page_is_served_without_an_account(test_context):
    """A page explaining the limits of a screen is worth little behind a sign-in."""

    response = await test_context["client"].get(METHODOLOGY_PUBLIC_PATH)
    assert response.status_code == 200
    assert "Hilal Markets Methodology" in response.text


async def test_the_page_carries_the_live_register_rather_than_a_copy(test_context):
    """Every figure on it comes from the server at render time.

    A page that shipped "68 approved" inside a JavaScript bundle would be correct on the
    day it was built and quietly wrong the day the owner approved another — and the
    website is the version a reader believes.
    """

    page = (await test_context["client"].get(METHODOLOGY_PUBLIC_PATH)).text

    assert '"methodology":' in page
    assert UNDER_DEVELOPMENT_NOTICE in page
    # The rules that run and the rules that are skipped both reach the page by code.
    assert applied_conditions()[0].code in page
    assert out_of_reach_conditions()[0].code in page
    # And every coin the standard has an answer for, refusals included.
    for symbol in sorted(admitted_symbols())[:5]:
        assert f'"{symbol}"' in page


async def test_every_other_page_is_handed_nothing(test_context):
    """The register does not travel inside every HTML document on the site."""

    page = (await test_context["client"].get("/features")).text
    assert '"methodology": null' in page or '"methodology":null' in page


async def test_the_footer_leads_to_it(test_context):
    """Reachable before somebody meets a result from it, not only afterwards."""

    page = (await test_context["client"].get("/")).text
    assert METHODOLOGY_PUBLIC_PATH in page


# --------------------------------------------------------------------------------
# Inside the product
# --------------------------------------------------------------------------------


async def test_the_market_page_offers_the_standard_and_warns_about_it(test_context):
    """The headline journey: choose it on Halal Assets, and be told what it is.

    The warning has to arrive with the list, not after the person has acted on it, so it
    is asserted on the same response that carries the standard's name.
    """

    await _signup_and_verify(test_context, email="hm-market@example.com")
    async with test_context["session_factory"]() as session:
        await publish(session)
        await session.commit()

    listing = (await test_context["client"].get(MARKET_PATH)).text
    # Selectable: the standard is one of the options in the picker.
    assert METHODOLOGY_DISPLAY_NAME in listing
    # And reachable from here whichever standard is chosen.
    assert METHODOLOGY_PUBLIC_PATH in listing

    async with test_context["session_factory"]() as session:
        from sqlalchemy import select

        from ai_market_monitor.db.models import ShariaMethodology
        from ai_market_monitor.services.sharia_automated_screen import (
            METHODOLOGY_SYSTEM_CODE,
        )

        methodology = await session.scalar(
            select(ShariaMethodology).where(
                ShariaMethodology.code == METHODOLOGY_SYSTEM_CODE
            )
        )

    chosen = (
        await test_context["client"].get(
            MARKET_PATH, params={"methodology_id": str(methodology.id)}
        )
    ).text
    assert WARNING in _visible_part(chosen)
    assert "Skipping is not passing" in chosen
    assert METHODOLOGY_PUBLIC_PATH in chosen


async def test_choosing_a_reviewed_standard_shows_no_such_warning(test_context):
    """The other half. A warning on every coin trains people to ignore it."""

    await _signup_and_verify(test_context, email="hm-market-quiet@example.com")
    listing = (await test_context["client"].get(MARKET_PATH)).text
    assert WARNING not in _visible_part(listing)


def test_the_popup_carries_its_copy_hidden_until_the_script_reveals_it():
    """Why the two tests above measure the page body rather than the whole document.

    The Passport popup is on the market page whatever standard is chosen, and it carries
    its own copy of the warning so the popup and the Passport page can never word it
    differently. That copy sits inside `data-pq-automated hidden` and is revealed by the
    script only when the coin in the dialog was decided by the machine standard.

    So the string is in the document even when no warning should be visible. Asserting
    on the raw HTML would therefore have been wrong in both directions: red when the page
    was right, and green if the notice ever escaped the hidden wrapper.
    """

    from pathlib import Path

    partial = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ai_market_monitor"
        / "templates"
        / "hilal"
        / "dashboard_test"
        / "partials"
        / "passport_quick_view.html"
    ).read_text(encoding="utf-8")
    body = partial.split("data-pq-automated hidden", 1)
    assert len(body) == 2, "the popup's copy is no longer inside a hidden wrapper"
    assert "automated_methodology_notice.html" in body[1]
