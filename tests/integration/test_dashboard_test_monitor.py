"""The visual canvas at `/dashboard/create-monitor`.

The redesign brief made several things non-negotiable, and none of them can be checked
by looking at a screenshot:

* the canvas is its own page, reached from the side menu directly after "Watchlists";
* the assistant does not come with it — no chat panel, no chat popup, and no
  "Hilal Markets Assistant" box anywhere on it;
* every rule, option, limit and way of being told comes from the server's own
  contract, so the browser has nothing left to invent;
* nothing on the page claims that monitoring has started;
* no forbidden claim from `brand guide.md` section 17 reaches it.
"""

from __future__ import annotations

import re

import pytest

from ai_market_monitor.core.dashboard_paths import LEGACY_MONITOR_PATH, MONITOR_PATH
from ai_market_monitor.db.models.enums import DeliveryChannel
from tests.integration.test_dashboard_web import _signup_and_verify


async def _signed_in(test_context, email: str) -> str:
    await _signup_and_verify(test_context, email=email)
    response = await test_context["client"].get(MONITOR_PATH)
    assert response.status_code == 200, response.text[:800]
    return response.text


async def test_the_canvas_has_its_own_page(test_context):
    page = await _signed_in(test_context, email="monitor-render@example.com")

    assert "hm-monitor-test.js" in page
    assert "hm-monitor-test.css" in page
    assert 'data-monitor-root' in page
    assert "<h1>Monitor</h1>" in page


def test_the_side_menu_offers_the_canvas_right_after_the_monitors():
    """The brief puts it in one exact place, so the position is the assertion.

    The two entries are called Monitors and Create a monitor now — the menu says what a
    person makes, and the page it opens is called the same thing.
    """
    from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION

    labels = [item.label for group in DASHBOARD_NAVIGATION for item in group.items]
    assert "Create a monitor" in labels
    assert labels[labels.index("Monitors") + 1] == "Create a monitor"


async def test_the_menu_link_on_the_page_points_at_the_canvas(test_context):
    """The entry has to be a working link, not only an entry in a list."""
    page = await _signed_in(test_context, email="monitor-menu@example.com")

    menu = page[page.index('data-testid="dashboard-nav"') : page.index("</nav>")]
    # `url_for` renders an absolute address, so the path is what is asserted.
    assert f'{MONITOR_PATH}"' in menu
    assert menu.index(">Monitors<") < menu.index(">Create a monitor<")


@pytest.mark.parametrize(
    "marker",
    [
        "Hilal Markets Assistant",
        "ai-setup-chat.js",
        "ai-setup-chat.css",
        "data-ai-setup-chat",
        "data-ai-chat-messages",
        "hilalmarkets-public-chat.js",
    ],
)
async def test_no_assistant_comes_with_the_canvas(test_context, marker):
    email = f"monitor-noai-{abs(hash(marker)) % 9999}@example.com"
    page = await _signed_in(test_context, email=email)
    assert marker not in page


async def test_the_page_offers_no_new_watchlist(test_context):
    """Every page on this path drops that action; this one is no exception."""
    page = await _signed_in(test_context, email="monitor-nowatch@example.com")

    assert not re.search(r"new\s+watchlist", page, re.IGNORECASE)
    assert "/dashboard/strategies/new" not in page


async def test_the_page_carries_no_passport_popup(test_context):
    """There is no coin on this page, so there is nothing for that dialog to open."""
    page = await _signed_in(test_context, email="monitor-popup@example.com")

    assert "data-passport-dialog" not in page
    assert "data-passport-quick-dialog" not in page


@pytest.mark.parametrize(
    "claim",
    [
        "100% halal",
        "guaranteed halal",
        "guaranteed profit",
        "winning signal",
        "risk-free",
        "buy now",
        "sell now",
        "AI trades for you",
    ],
)
async def test_no_forbidden_claim_reaches_the_canvas(test_context, claim):
    email = f"monitor-claim-{abs(hash(claim)) % 9999}@example.com"
    page = await _signed_in(test_context, email=email)
    assert claim.casefold() not in page.casefold()


async def test_the_page_never_says_monitoring_has_started(test_context):
    """`AI never approves`, and neither does a drawing. The page says so plainly.

    A monitor is switched on from this page now, so the page has to be clearer than
    before, not vaguer: drawing is still only drawing, and the thing that starts it is
    a button a person presses after reading the plan back.
    """

    page = await _signed_in(test_context, email="monitor-honest@example.com")

    assert "Nothing is being watched while you are here" in page
    assert "You switch it on from here" in page
    assert "Nothing is watching yet" in page
    # And nothing on it claims a monitor is already running.
    assert "is watching now" not in page


async def test_the_shape_limits_come_from_the_compiler(test_context):
    """A limit typed into the template would drift the moment the compiler changed."""
    from ai_market_monitor.engine.builder_boolean import boolean_limits

    page = await _signed_in(test_context, email="monitor-limits@example.com")
    limits = boolean_limits().to_dict()

    assert f'"max_depth": {limits["max_depth"]}' in page
    assert f'"max_nodes": {limits["max_nodes"]}' in page


async def test_the_ways_of_being_told_are_ways_that_actually_work(test_context):
    """Two questions, and the canvas has to pass both.

    *Would the compiler accept it?* and *can we actually deliver it?* This test used to
    ask only the first, and asserted the answer of a channel that has been retired —
    encoding the defect as a requirement. The canvas really did offer it, and nothing
    would ever have delivered a message there.
    """
    from ai_market_monitor.services.notification_preferences import offered_channels

    page = await _signed_in(test_context, email="monitor-channels@example.com")

    start = page.index("data-channels='")
    channels = page[start : page.index("'", start + len("data-channels='"))]

    deliverable = {
        channel.value for channel in offered_channels(test_context["settings"])
    }
    assert "telegram" in channels
    assert "web" in channels

    every_value = {channel.value for channel in DeliveryChannel}
    for value in every_value - deliverable:
        assert f'"{value}"' not in channels, (
            f"the canvas offers {value}, which this platform does not deliver"
        )


async def test_the_contract_the_canvas_reads_is_a_real_route(test_context):
    """The canvas points at one endpoint. A broken address would leave it empty."""
    await _signup_and_verify(test_context, email="monitor-contract@example.com")
    client = test_context["client"]

    page = (await client.get(MONITOR_PATH)).text
    start = page.index('data-contract-url="') + len('data-contract-url="')
    url = page[start : page.index('"', start)]

    response = await client.get(url)
    assert response.status_code == 200
    payload = response.json()
    assert payload["mechanics"], "the canvas would have nothing to offer"
    assert payload["boolean_limits"]["max_depth"] >= 1
    assert payload["universes"] and payload["logic"]


async def test_every_mechanic_the_contract_offers_carries_the_words_to_draw_it(test_context):
    """A rule with no label or no explanation would reach the board as a blank card."""
    await _signup_and_verify(test_context, email="monitor-words@example.com")
    payload = (
        await test_context["client"].get("/api/v1/dashboard/setup-chat/builder-contract")
    ).json()

    missing = [
        item["key"]
        for item in payload["mechanics"]
        if not item.get("label") or not item.get("explanation") or not item.get("category")
    ]
    assert not missing, f"{len(missing)} mechanics could not be drawn: {missing[:5]}"


async def test_an_unavailable_mechanic_says_why_instead_of_disappearing(test_context):
    """Fail closed: "not yet, because" beats an option that quietly went missing."""
    await _signup_and_verify(test_context, email="monitor-unavailable@example.com")
    payload = (
        await test_context["client"].get("/api/v1/dashboard/setup-chat/builder-contract")
    ).json()

    blocked = [item for item in payload["mechanics"] if not item["available"]]
    assert blocked, "the fixture provider runs everything, so this check proves nothing"
    assert all(item["unavailable_reason"] for item in blocked)


async def test_the_big_contract_is_compressed_on_the_way_out(test_context):
    """It is 2 MB of JSON. Sending it uncompressed made every Builder page slow."""
    await _signup_and_verify(test_context, email="monitor-gzip@example.com")

    response = await test_context["client"].get(
        "/api/v1/dashboard/setup-chat/builder-contract",
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


async def test_a_client_without_compression_still_gets_the_page(test_context):
    """Compression is an improvement, never a requirement placed on the reader."""
    await _signup_and_verify(test_context, email="monitor-nogzip@example.com")

    response = await test_context["client"].get(
        MONITOR_PATH, headers={"Accept-Encoding": "identity"}
    )
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert "<h1>Monitor</h1>" in response.text


async def test_the_canvas_answers_at_the_address_a_person_came_to_use(test_context):
    """The page is where a monitor is made, and its address says so."""
    await _signup_and_verify(test_context, email="monitor-address@example.com")

    response = await test_context["client"].get(MONITOR_PATH)
    assert MONITOR_PATH == "/dashboard/create-monitor"
    assert response.status_code == 200
    assert "data-monitor-root" in response.text


async def test_the_old_canvas_address_still_opens_the_canvas(test_context):
    """A saved bookmark and an already-sent redirect both still name the old address."""
    await _signup_and_verify(test_context, email="monitor-legacy@example.com")

    response = await test_context["client"].get(LEGACY_MONITOR_PATH, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == MONITOR_PATH


async def test_the_new_address_is_not_shared_with_the_older_builder(test_context):
    """It used to serve the older strategy builder as well. One address, one page."""
    await _signup_and_verify(test_context, email="monitor-one-owner@example.com")

    canvas = (await test_context["client"].get(MONITOR_PATH)).text
    builder = (await test_context["client"].get("/dashboard/strategies/new")).text
    assert "data-monitor-root" in canvas
    assert "data-monitor-root" not in builder
