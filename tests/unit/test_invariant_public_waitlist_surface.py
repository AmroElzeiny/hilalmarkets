"""The pre-launch public site has one owner for each of its decisions.

Every rule here is asserted across the whole family it governs, not on the one example
that was reported. The recurring failure in this area was a decision copied into a second
place: the menus knew Halal Assets was hidden while the page body still linked to it, and
the Help Center page knew there was no Plan & Billing while the assistant still said there
was. Each test below pins one owner and checks every caller agrees with it.
"""

from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.site_content import (
    ACCOUNT_ONLY_PATH_PREFIXES,
    FOOTER_NAVIGATION,
    HELP_CATEGORIES,
    PUBLIC_NAVIGATION,
    PUBLIC_PAGES,
    WAITLIST_BODY,
    WAITLIST_CTA_LABEL,
    WAITLIST_HEADLINE,
    WAITLIST_HIDDEN_PAGES,
    footer_navigation,
    is_account_only_path,
    public_help_categories,
    public_navigation,
)
from ai_market_monitor.services.public_chat import (
    PUBLIC_ROUTE_PATHS,
    offerable_route_ids,
)


def _settings(*, waitlist_mode: bool) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_default_methodology_code=None,
        public_waitlist_mode=waitlist_mode,
    )


@pytest.mark.parametrize("prefix", ACCOUNT_ONLY_PATH_PREFIXES)
@pytest.mark.parametrize(
    "shape",
    ["{prefix}", "{prefix}/", "{prefix}/child", "{prefix}?q=1", "{prefix}#top"],
)
def test_every_account_only_prefix_is_recognised_in_every_shape(prefix, shape):
    """One matcher decides what "inside the product" means, for every prefix and form.

    A link is written in more shapes than anyone remembers: with a child path, with a
    trailing slash, with a query and with a fragment. Each shape is a way for a leak to
    walk past a matcher that only handled the shapes somebody happened to think of.
    """

    assert is_account_only_path(shape.format(prefix=prefix)) is True


def test_the_dashboard_entry_address_is_named_rather_than_guessed_at():
    """/dashboard-entry is not a child of /dashboard, so it is listed in its own right."""

    assert "/dashboard-entry" in ACCOUNT_ONLY_PATH_PREFIXES
    assert is_account_only_path("/dashboard-entry") is True


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/features",
        "/how-it-works",
        "/how-we-screen",
        "/help",
        "/contact",
        "/about",
        "/trust-safety",
        "/risk-disclosure",
        "/privacy",
        "/terms",
        "/cookies",
        # Names that merely begin with the same letters are public pages, not leaks.
        "/accountability",
        "/billing-explained",
        "/signup-guide",
    ],
)
def test_public_addresses_are_never_mistaken_for_account_pages(path):
    assert is_account_only_path(path) is False


def test_the_menus_and_the_assistant_hide_the_same_pages():
    """Header, footer and assistant read one hidden-page set, so they cannot disagree."""

    waitlist_settings = _settings(waitlist_mode=True)

    header_pages = {item.page for item in public_navigation(waitlist_mode=True)}
    footer_pages = {
        item.page
        for group in footer_navigation(waitlist_mode=True)
        for item in group.items
    }
    offerable = offerable_route_ids(waitlist_settings)

    for hidden in WAITLIST_HIDDEN_PAGES:
        assert hidden not in header_pages
        assert hidden not in footer_pages
        assert hidden not in offerable

    # And nothing the assistant may offer needs an account to open.
    for route_id in offerable:
        _label, path = PUBLIC_ROUTE_PATHS[route_id]
        assert not is_account_only_path(path), route_id


def test_the_unlinked_page_is_unlinked_everywhere():
    """How We Screen is served but never linked, on every surface at once.

    A page is reached from four kinds of place, and each one remembers the decision
    separately: the header menu, the footer menu, a button written into the body of
    another page, and the assistant's list of links it may offer. Emptying the menus and
    leaving a button behind is exactly how the "Halal Assets" button survived on this same
    page after the menus had dropped it. So all four are asserted here together.

    The page itself stays reachable on purpose: an old bookmark or a search result must
    not turn into a "page not found".
    """

    unlinked = "how_we_screen"
    templates = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ai_market_monitor"
        / "templates"
    )

    # 1 and 2 - the two menus, in both product states.
    for mode in (True, False):
        assert unlinked not in {item.page for item in public_navigation(waitlist_mode=mode)}
        assert unlinked not in {
            item.page
            for group in footer_navigation(waitlist_mode=mode)
            for item in group.items
        }

    # 3 - the assistant cannot name it, so no answer can offer it as a next step.
    assert unlinked not in PUBLIC_ROUTE_PATHS
    for mode in (True, False):
        assert unlinked not in offerable_route_ids(_settings(waitlist_mode=mode))

    # 4 - no page body links to it. Both spellings are refused: the address written out,
    # and the route name a Jinja url_for() would use.
    offenders = []
    for template in templates.rglob("*.html"):
        if template.name == "how_we_screen.html":
            continue
        body = template.read_text(encoding="utf-8")
        if "/how-we-screen" in body or "public_how_we_screen" in body:
            offenders.append(template.name)
    assert offenders == [], offenders

    # And the page is still served, with its own address still in the site map.
    assert unlinked in {item.page for item in PUBLIC_PAGES}
    assert "/how-we-screen" in {item.path for item in PUBLIC_PAGES}


def test_turning_waitlist_mode_off_restores_every_menu_and_route():
    """The switch has to be a switch. A deletion would pass every pre-launch test."""

    assert public_navigation(waitlist_mode=False) == PUBLIC_NAVIGATION
    assert footer_navigation(waitlist_mode=False) == FOOTER_NAVIGATION
    assert public_help_categories(waitlist_mode=False) == HELP_CATEGORIES
    assert offerable_route_ids(_settings(waitlist_mode=False)) == frozenset(
        PUBLIC_ROUTE_PATHS
    )


def test_help_content_never_sends_a_pre_launch_reader_inside_the_product():
    """Every article, not only the one that was reported.

    The plan article was the one that named Plan & Billing. Checking only that article
    would leave the next one free to name the dashboard again.
    """

    forbidden = ("plan & billing", "pricing page", "sign up", "sign in", "checkout")
    for category in public_help_categories(waitlist_mode=True):
        for article in category["articles"]:
            text = f"{article['question']} {article['answer']}".casefold()
            for phrase in forbidden:
                assert phrase not in text, (article["question"], phrase)


def test_the_pre_launch_message_is_written_once_and_reads_plainly():
    """The wording is shared, so it is checked once — including that it stays readable.

    The audience is a beginner and often not a native English speaker. A closing section
    written in product jargon is a section that is not read.
    """

    assert WAITLIST_CTA_LABEL == "Join the waitlist"
    assert WAITLIST_HEADLINE.endswith(".")
    assert "waitlist" in WAITLIST_BODY.casefold()
    assert "invite-only" in WAITLIST_BODY.casefold()
    # No promise of a price, a plan, or an account that can be opened today. "sign up"
    # is refused even as a denial: a reader who skims sees the words, not the "cannot".
    for forbidden in ("free trial", "sign up", "sign in", "$", "per month", "plan"):
        assert forbidden not in WAITLIST_BODY.casefold(), forbidden
    # Short sentences: the longest stays inside what a beginner reads in one breath.
    sentences = [part.strip() for part in WAITLIST_BODY.split(".") if part.strip()]
    assert sentences
    assert max(len(part.split()) for part in sentences) <= 30
