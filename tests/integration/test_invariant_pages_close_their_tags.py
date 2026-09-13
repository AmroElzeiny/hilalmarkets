"""Every page the server sends closes each tag where it opened it.

A browser repairs a stray end tag without a word. On the settings page one extra
``</div>`` closed the list that lays the groups out, so every group from "How much we
send" down was drawn outside it. Every element was still there, every test that looked
for an element still passed, and the page was wrong.

This reads the HTML as the server sent it, before any browser repair, for every page a
person can open: the public ones, and every signed-in page named in
``core/dashboard_paths.py``.
"""

from __future__ import annotations

import pytest

from ai_market_monitor.core import dashboard_paths as paths
from tests.integration.test_dashboard_web import _signup_and_verify
from tests.support.html_balance import unbalanced_tags

PUBLIC_PAGES = (
    "/",
    "/signup",
    # Steps two and three of signing up are sent back to step one without an address to
    # carry, so they are opened the way the journey opens them.
    "/signup/password?email=tags%40example.com&name=Tags",
    "/signup/verify?email=tags%40example.com&message=code_sent",
    "/signin",
    "/signin/code",
    "/reset-password",
)

SIGNED_IN_PAGES = (
    paths.HOME_PATH,
    paths.MARKET_PATH,
    paths.MONITOR_PATH,
    paths.MONITORS_PATH,
    paths.OPPORTUNITIES_PATH,
    paths.CONNECTIONS_PATH,
    paths.SUBSCRIPTION_PATH,
    paths.SETTINGS_PATH,
    paths.SUPPORT_PATH,
    paths.AFFILIATE_PATH,
    paths.LIFECYCLES_PATH,
    paths.RESEARCH_PATH,
    # The older billing page, which still carries the checkout dialog.
    "/dashboard/billing",
)


def test_the_checker_finds_the_stray_end_tag_it_exists_for() -> None:
    """The exact shape the settings page shipped with, its repaired form, and the
    opposite mistake — an end tag left out."""

    stray = "<div class='list'><section><div>head</div>\n</div>\n<p>row</p></section></div>"
    # Named once, at the stray tag. The checker sets it aside, so the rest of the page is
    # judged as written and one mistake is not reported again at every later end tag.
    assert unbalanced_tags(stray) == [
        "line 2: </div> does not close the <section> opened on line 1",
    ]
    repaired = "<div class='list'><section><div>head</div>\n<p>row</p></section></div>"
    assert unbalanced_tags(repaired) == []
    missing = "<section><div>head\n</section>"
    assert unbalanced_tags(missing) == [
        "line 2: </section> does not close the <div> opened on line 1",
        "line 1: <section> is never closed",
        "line 1: <div> is never closed",
    ]


@pytest.mark.parametrize("path", PUBLIC_PAGES)
async def test_a_public_page_closes_every_tag_where_it_opened_it(test_context, path: str):
    response = await test_context["client"].get(path)
    assert response.status_code == 200, (path, response.status_code)
    assert unbalanced_tags(response.text) == [], path


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
async def test_a_signed_in_page_closes_every_tag_where_it_opened_it(test_context, path: str):
    name = path.strip("/").replace("/", "-")
    await _signup_and_verify(test_context, email=f"tags-{name}@example.com")
    response = await test_context["client"].get(path)
    assert response.status_code == 200, (path, response.status_code)
    assert unbalanced_tags(response.text) == [], path
