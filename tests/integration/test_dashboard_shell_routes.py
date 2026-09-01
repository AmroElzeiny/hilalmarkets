"""The shell's routes and its two removed pages, through the real application.

Three things a person can only be sure of by asking the running app:

* a page that was removed really is gone, and nothing still links to it;
* the side menu really renders the entries it is supposed to, on a real page;
* the product really answers on its own hostname.

`docs/dashboard-shell-redesign-rules.md` is where these came from.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from pydantic import AnyHttpUrl

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.core.dashboard_paths import (
    MONITOR_PATH,
)
from ai_market_monitor.core.site_content import DASHBOARD_NAVIGATION
from tests.integration.test_dashboard_web import _signup_and_verify


async def _signed_in(test_context, email: str) -> None:
    await _signup_and_verify(test_context, email=email)


# ---------------------------------------------------------------------------
# The two removed pages.
# ---------------------------------------------------------------------------


async def test_the_old_home_page_takes_a_person_to_today(test_context):
    """`/dashboard` is written into old email, into Telegram buttons and into two
    database columns, so it moves rather than refusing. What it no longer does is serve
    a second front page that answered the same question less well."""

    await _signed_in(test_context, email="shell-home@example.com")
    response = await test_context["client"].get("/dashboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/home"


async def test_the_page_that_was_main_still_answers_at_its_old_address(test_context):
    """`/main` was the front page's address and is now `/home`.

    It moves rather than refusing, for exactly the reasons `/dashboard` does: the old
    address is in email we have already sent, in Telegram buttons, in the `target_path`
    column of two tables and in people's bookmarks, and none of those can be corrected
    now. It is also not gated on being signed in — Home's own guard does that, and a
    second copy of the rule in front of a redirect is how the two come to disagree.
    """

    response = await test_context["client"].get("/main", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/home"


def test_the_home_page_template_is_deleted():
    from pathlib import Path

    import ai_market_monitor

    gone = (
        Path(ai_market_monitor.__file__).parent
        / "templates"
        / "hilal"
        / "dashboard"
        / "home.html"
    )
    assert not gone.exists()


@pytest.mark.parametrize("path", ["/dashboard/check-market", "/dashboard/scan-now"])
async def test_the_trading_assistant_page_is_gone_by_both_of_its_names(
    test_context, path: str
):
    """One page with two addresses is how a removal misses one of them."""

    await _signed_in(test_context, email=f"shell-scan-{path.count('-')}@example.com")
    response = await test_context["client"].get(path, follow_redirects=False)

    assert response.status_code == 404


async def test_nothing_in_the_product_still_links_to_a_removed_page(test_context):
    """A dead link is worse than a missing button: it looks like the product is broken.

    The Telegram "open a quick scan" buttons pointed at one of the two removed addresses,
    and a button inside a chat message lives for as long as the message does.
    """

    from pathlib import Path

    import ai_market_monitor

    package = Path(ai_market_monitor.__file__).parent
    sources = [
        path
        for folder in ("templates", "static")
        for path in (package / folder).rglob("*")
        if path.suffix in {".html", ".js"} and "landing" not in path.parts
    ]
    sources.append(package / "telegram" / "service.py")
    sources.append(package / "whatsapp" / "service.py")

    # A quoted address is a link. The same words inside backticks are two files
    # explaining where their buttons used to point, and deleting the explanation is not
    # the fix this is asking for.
    link = re.compile(r"""["']/dashboard/(check-market|scan-now)\b""")
    offenders = [
        str(path.relative_to(package))
        for path in sources
        if link.search(path.read_text("utf-8"))
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# The menu, on a real page.
# ---------------------------------------------------------------------------


async def test_the_side_menu_shows_the_entries_it_is_supposed_to(test_context):
    await _signed_in(test_context, email="shell-menu@example.com")
    page = (await test_context["client"].get("/home")).text

    menu = page.split('data-testid="dashboard-nav"', 1)[1].split("</nav>", 1)[0]
    for name in ("Home", "Halal Assets", "Monitors", "Create a monitor", "Opportunities"):
        assert f">{name}</span>" in menu, name
    # "Main" was this page's name and it is an internal word for the place a person is
    # meant to start on. "Trading Assistant" was a page that no longer exists.
    for gone in ("Trading Assistant", "Main"):
        assert f">{gone}</span>" not in menu, gone

    # Every name is on the link itself, so it is still there when the menu is minimized
    # and the label is moved off screen.
    #
    # Counted against `DASHBOARD_NAVIGATION` rather than a number written here. It said
    # ten, the product had eleven — "Coins we researched" had been added to the menu and
    # to nothing else — and this test failed for a menu that was correct. A count in a
    # test is a second copy of a product decision, and it is always the copy that rots.
    expected = sum(len(group.items) for group in DASHBOARD_NAVIGATION)
    assert menu.count('class="hm-nav-text"') == expected


async def test_the_menu_never_points_at_an_older_copy_of_a_page(test_context):
    """Seven entries opened the redesigned page and two opened the first version of it.

    So the menu and the front page sent a person to two different Opportunities screens,
    and neither of them said the other existed.
    """

    await _signed_in(test_context, email="shell-consistent@example.com")
    page = (await test_context["client"].get("/home")).text
    menu = page.split('data-testid="dashboard-nav"', 1)[1].split("</nav>", 1)[0]
    # `url_for` renders an absolute address, so the path is what is compared.
    opened = [
        urlsplit(href).path
        for href in re.findall(r'class="nav-item hm-nav-link[^"]*"\s+href="([^"]+)"', menu)
    ]

    # Resolved from `DASHBOARD_NAVIGATION`, never written out again here. This list was
    # spelled out twice before — once as literal addresses, which went stale when
    # `/dashboard/monitor` became `/dashboard/create-monitor`, and then as constants in
    # a hand-kept order, which went stale again when a destination was added above them.
    # A check that exists to catch a stale link must not itself be a copy that can go
    # stale. It reads the definition and asks the router for each address, so the only
    # thing it can now disagree with is the page that renders them.
    expected = [
        urlsplit(str(test_context["app"].url_path_for(item.endpoint))).path
        for group in DASHBOARD_NAVIGATION
        for item in group.items
    ]
    assert opened == expected, opened
    assert len(set(opened)) == len(opened), "a destination is in the menu twice"


# ---------------------------------------------------------------------------
# The actions the topbar draws for each page.
# ---------------------------------------------------------------------------


async def test_the_monitors_page_offers_its_create_action_from_the_topbar(test_context):
    await _signed_in(test_context, email="shell-create@example.com")
    page = (await test_context["client"].get("/dashboard/monitors")).text

    topbar = page.split('<header class="topbar hm-top"', 1)[1].split("</header>", 1)[0]
    assert "Create a monitor" in topbar
    # The address comes from its one owner. Written out here it read
    # `/dashboard/monitor`, which is a *prefix* of the real one and of `/dashboard/
    # monitors` besides — so this assertion would have passed on the wrong page.
    assert f'href="{MONITOR_PATH}"' in topbar.replace("http://testserver", "")

    # And the page under it draws no action of its own, in either of its two layouts.
    body = page.split("</header>", 1)[1]
    assert "w-new" not in body
    assert not re.search(r"new\s+watchlist", body, re.IGNORECASE)


async def test_the_opportunities_page_offers_the_way_back_from_the_topbar(test_context):
    await _signed_in(test_context, email="shell-back@example.com")
    page = (await test_context["client"].get("/dashboard/opportunities")).text

    topbar = page.split('<header class="topbar hm-top"', 1)[1].split("</header>", 1)[0]
    assert "Monitors" in topbar
    assert "/dashboard/monitors" in topbar
    assert "Your Watchlists" not in page


async def test_the_topbar_says_which_page_you_are_on(test_context):
    await _signed_in(test_context, email="shell-here@example.com")
    page = (await test_context["client"].get("/dashboard/monitors")).text

    topbar = page.split('<header class="topbar hm-top"', 1)[1].split("</header>", 1)[0]
    assert 'class="hm-top-here-group">Your monitors<' in topbar
    assert 'class="hm-top-here-name">Monitors<' in topbar


# ---------------------------------------------------------------------------
# The product's own hostname.
# ---------------------------------------------------------------------------


def _with_hosts(test_context, *, site: str, app: str):
    settings = test_context["settings"]
    # `model_copy` skips validation, so the URLs are built rather than passed as strings.
    changed = settings.model_copy(
        update={
            "public_base_url": AnyHttpUrl(site),
            "app_base_url": AnyHttpUrl(app),
        }
    )
    test_context["app"].dependency_overrides[get_settings] = lambda: changed
    return changed


async def test_the_root_of_the_products_own_hostname_opens_the_product(test_context):
    """One deployment answers on two names: the site, and the product.

    On `app.hilalmarkets.com` the root is the dashboard. Somebody who is not signed in is
    taken to sign-in from there by the dashboard's own guard, not by a second copy of
    that rule sitting in the public router.
    """

    _with_hosts(test_context, site="http://testserver", app="http://app.testserver")
    response = await test_context["client"].get(
        "http://app.testserver/", follow_redirects=False
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/home"


async def test_the_marketing_root_is_untouched_by_that(test_context):
    _with_hosts(test_context, site="http://testserver", app="http://app.testserver")
    response = await test_context["client"].get("http://testserver/")

    assert response.status_code == 200


async def test_every_way_into_the_product_lands_on_the_products_own_hostname(test_context):
    """The whole point of a second hostname is that the product is served on it.

    That was half true. The *root* of `app.hilalmarkets.com` opened the dashboard, but
    every way in from the marketing site was a plain path — "Start free", "Sign in",
    "Open dashboard" — so a visitor who pressed one stayed on `hilalmarkets.com` and used
    the entire product from there. Two hostnames served the same signed-in pages and the
    one named after the product was the one almost nobody reached.

    All three doors are checked together, because fixing one and leaving the others is
    exactly how this happened.
    """

    _with_hosts(test_context, site="http://testserver", app="http://app.testserver")
    response = await test_context["client"].get("http://testserver/about")
    assert response.status_code == 200

    for door in ("/signin", "/signup", "/dashboard-entry"):
        assert f"http://app.testserver{door}" in response.text or door not in response.text, (
            f"{door} is linked on the marketing site without the product's hostname"
        )
    # And at least one of them really is on the page, so this cannot pass by finding none.
    assert "http://app.testserver/sign" in response.text


async def test_one_hostname_keeps_plain_paths(test_context):
    """A single-domain install must not grow absolute URLs it has no use for.

    `app_link` returns the plain path when the two names are the same, which is what a
    local run and any one-domain deployment get.
    """

    _with_hosts(test_context, site="http://testserver", app="http://testserver")
    response = await test_context["client"].get("http://testserver/about")

    assert response.status_code == 200
    assert 'href="/signin"' in response.text
    assert "http://testserver/signin" not in response.text
    assert "<html" in response.text.lower()


async def test_one_name_for_everything_keeps_the_landing_page_at_the_root(test_context):
    """The local default has both names pointing at the same host.

    Reading `APP_BASE_URL` on its own would then replace the landing page with a redirect
    to sign-in for every visitor, including the ones who have never heard of the product.
    """

    _with_hosts(test_context, site="http://testserver", app="http://testserver")
    response = await test_context["client"].get(
        "http://testserver/", follow_redirects=False
    )

    assert response.status_code == 200
