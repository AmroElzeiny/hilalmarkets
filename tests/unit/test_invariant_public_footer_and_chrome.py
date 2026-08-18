"""The footer, the back-to-top button and the cookie control, on both halves of the site.

This site draws its chrome twice — once in Jinja for the server-rendered pages, once in
React for the rest — and every defect this file pins is the same defect: **a decision
that exists in one of the two places and not the other.**

* The menu was one list in Python and a second list typed into the React fallback.
* "Cookie settings" was a button that went nowhere, wired by a script that collects its
  handlers before one of the two renderers has drawn anything — correct today, and only
  because deferred module scripts happen to run just before `DOMContentLoaded`.
* The back-to-top button was styled in the React bundle's own sheet, which the other half
  never loads, and positioned with no knowledge of the assistant's button underneath it.
* The footer logo had a width and no height in one renderer, and was a different brand
  mark entirely in the other.

Every test asserts the rule across the whole family, never on the one example: all six
removed addresses, all three social channels, both renderers, both sheets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.core.site_content import (
    COOKIE_SETTINGS_PATH,
    DASHBOARD_NAVIGATION,
    FOOTER_NAVIGATION,
    SOCIAL_LINKS,
    is_account_only_path,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "ai_market_monitor" / "static"
TEMPLATES = ROOT / "src" / "ai_market_monitor" / "templates"
REACT = ROOT / "Hilal-Markets-Website" / "src"

SITE_CHROME = (REACT / "components" / "SiteChrome.tsx").read_text(encoding="utf-8")
JINJA_FOOTER = (TEMPLATES / "hilal" / "partials" / "public_footer.html").read_text(
    encoding="utf-8"
)
PUBLIC_CSS = (STATIC / "hilalmarkets-public.css").read_text(encoding="utf-8")
CHAT_CSS = (STATIC / "hilalmarkets-public-chat.css").read_text(encoding="utf-8")
REACT_CSS = (REACT / "index.css").read_text(encoding="utf-8")
CONSENT_JS = (STATIC / "hilalmarkets-consent.js").read_text(encoding="utf-8")


def _pixels(css: str, rule: str, property_: str) -> int:
    """One pixel number, read out of a named rule in a real stylesheet.

    Read rather than written down here, so the assertions below compare what the two
    sheets actually say instead of comparing this file to itself.
    """

    block = re.search(rf"{re.escape(rule)}\s*\{{(.*?)\}}", css, re.DOTALL)
    assert block, f"{rule} is not in the stylesheet at all"
    found = re.search(rf"\b{property_}\s*:\s*(\d+)px", block.group(1))
    assert found, f"{rule} has no {property_} in pixels"
    return int(found.group(1))


# --------------------------------------------------------------------------------
# The footer menu: one list, two renderers.
# --------------------------------------------------------------------------------

#: The six addresses taken out of the footer. Every one of them, not the one reported.
#:
#: Each page is still served and still in the sitemap — this is about what the footer
#: leads to, never about deleting a page. `test_a_removed_footer_link_is_gone_from_both`
#: is the whole family in one place, so putting any one of them back in one renderer
#: fails here rather than in review.
REMOVED_FROM_FOOTER = (
    ("Pricing", "/pricing"),
    ("Halal Assets", "/dashboard/market"),
    ("Trust & Safety", "/trust-safety"),
    ("Risk Disclosure", "/risk-disclosure"),
    ("About", "/about"),
    ("Help Center", "/help"),
)


def _react_fallback_groups() -> list[tuple[str, list[str]]]:
    """The menu the React footer draws when it was opened without the server shell.

    Read by position rather than by splitting the text, because a group's label and an
    item's label are written the same way — splitting on `label:` put every item's name
    where a group's name belongs and made the comparison meaningless.
    """

    block = re.search(
        r"const FALLBACK_FOOTER_GROUPS = \[(.*?)\n\]", SITE_CHROME, re.DOTALL
    )
    assert block, "the React footer no longer has a fallback menu"
    body = block.group(1)

    # A group is a label immediately followed by its own `items:`; an item is a label
    # with an `href` beside it. Each item belongs to the last group declared above it.
    groups = [
        (found.start(), found.group(1), [])
        for found in re.finditer(r"label: '([^']+)',\s*\n?\s*items:", body)
    ]
    assert groups, "the fallback menu has no groups in it"
    for found in re.finditer(r"\{ label: '([^']+)', href: '[^']+' \}", body):
        owner = max(group for group in groups if group[0] < found.start())
        owner[2].append(found.group(1))
    return [(label, names) for _, label, names in groups]


def test_both_footers_offer_the_same_menu() -> None:
    """The server list and the React fallback are one menu written twice.

    They already disagreed once — the React footer offered three links while the Jinja
    one offered twelve — and the fallback is the copy nobody looks at, so it is the one
    that goes stale.
    """

    assert _react_fallback_groups() == [
        (group.label, [item.label for item in group.items])
        for group in FOOTER_NAVIGATION
    ]


@pytest.mark.parametrize("label,path", REMOVED_FROM_FOOTER, ids=lambda item: str(item))
def test_a_removed_footer_link_is_gone_from_both(label: str, path: str) -> None:
    """Removed from the footer means removed from *the* footer, both renderings of it."""

    del path
    assert label not in {
        item.label for group in FOOTER_NAVIGATION for item in group.items
    }
    assert label not in [
        name for _, names in _react_fallback_groups() for name in names
    ]


def test_the_footer_never_leads_into_the_product() -> None:
    """Halal Assets was a dashboard page sitting in a public footer.

    Checked as a rule rather than as one name: any future entry that needs an account is
    caught here on the day it is added.
    """

    for group in FOOTER_NAVIGATION:
        for item in group.items:
            assert not is_account_only_path(f"/{item.page}"), item.label
    # And the dashboard's own menu still has it, because removing it from the public
    # footer must not remove it from the product.
    assert "screened_market" in {
        item.page for group in DASHBOARD_NAVIGATION for item in group.items
    }


# --------------------------------------------------------------------------------
# Cookie settings: a link, in both, reaching the same place.
# --------------------------------------------------------------------------------


def test_cookie_settings_is_a_link_in_both_footers() -> None:
    """A link with an address, in both, and the address comes from one place.

    Neither footer writes the address itself: the Jinja one prints `cookie_settings_path`
    and the React one reads `cookieSettingsHref` off the runtime config, and the server
    fills both from :data:`COOKIE_SETTINGS_PATH`. Two footers each holding their own copy
    of an address is how the two come to point at different places.
    """

    assert 'href="{{ cookie_settings_path }}"' in JINJA_FOOTER
    assert "data-cookie-settings" in JINJA_FOOTER
    assert '<button type="button" data-cookie-settings' not in JINJA_FOOTER

    assert "<a href={cookieSettingsHref} data-cookie-settings>" in SITE_CHROME
    assert '<button type="button" data-cookie-settings>' not in SITE_CHROME

    # And the server hands the same value to both renderers.
    public_router = (
        ROOT / "src" / "ai_market_monitor" / "api" / "routers" / "public.py"
    ).read_text(encoding="utf-8")
    assert '"cookie_settings_path": COOKIE_SETTINGS_PATH' in public_router
    assert '"cookieSettingsHref": COOKIE_SETTINGS_PATH' in public_router


def test_the_cookie_address_opens_the_panel_without_any_script() -> None:
    """The link has to work when nothing catches the click.

    It lands on the Cookie Policy carrying `settings=1`, and the consent script opens the
    panel from that on load. Both halves of that promise are asserted, because either one
    alone is a link that goes somewhere and does nothing.
    """

    assert COOKIE_SETTINGS_PATH.startswith("/cookies")
    assert "settings=1" in COOKIE_SETTINGS_PATH
    assert 'get("settings") === "1"' in CONSENT_JS


def test_every_consent_control_is_answered_by_delegation() -> None:
    """No consent control may depend on when it was drawn.

    `querySelectorAll(...).forEach(el => el.addEventListener(...))` at DOMContentLoaded
    binds only the controls that exist at that moment. Half of this site is drawn by
    React, from a deferred module script, and the footer exists in time only because
    deferred modules happen to run just before `DOMContentLoaded` — an ordering nothing
    here asks for and nothing would report if it changed. A control that is on the page
    and attached to nothing throws no error and logs nothing; it just stops working.

    One listener on the document removes the dependency. `test_the_footer_is_the_same_on_every_page`
    in the browser suite is the other half of this: it presses the control on every page
    rather than checking that it exists.
    """

    assert 'document.addEventListener("click", onDocumentClick)' in CONSENT_JS
    for hook in (
        "data-cookie-settings",
        "data-cookie-customize",
        "data-cookie-essential",
        "data-cookie-accept-analytics",
        "data-cookie-close",
        "data-cookie-save",
    ):
        assert f"{hook}" in CONSENT_JS, hook
    # And no control is bound the old way any more.
    assert not re.search(
        r'querySelectorAll\("\[data-cookie-[^"]+\]"\)\s*\.forEach', CONSENT_JS
    )


# --------------------------------------------------------------------------------
# The channel marks.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("channel", SOCIAL_LINKS, ids=lambda item: item.label)
def test_every_channel_is_drawn_in_both_footers(channel) -> None:
    assert f"channel.label == '{channel.label}'" in JINJA_FOOTER
    assert f"  {channel.label}:" in SITE_CHROME or f"'{channel.label}'" in SITE_CHROME


def test_the_threads_mark_is_the_real_one_and_the_same_in_both() -> None:
    """Threads' own glyph, byte for byte, in both renderers.

    It was a hand-drawn approximation: close enough to recognise, wrong in every detail.
    A company's logo is the one drawing that may not be redrawn by us, and having two
    copies of it is how one of them gets fixed and the other does not.
    """

    #: The opening of the published Threads path. Enough to identify it, short enough
    #: that this test is not a second copy of the artwork.
    opening = "M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509"
    assert opening in JINJA_FOOTER
    assert opening in SITE_CHROME
    # The old approximation is gone from both.
    assert "M16.2 11.2a6.9 6.9" not in JINJA_FOOTER
    assert "M16.2 11.2a6.9 6.9" not in SITE_CHROME


# --------------------------------------------------------------------------------
# The footer logo.
# --------------------------------------------------------------------------------


def test_the_footer_logo_is_31px_tall_in_both_footers() -> None:
    """One logo, one height.

    The React footer's link had a width and no height at all, and its only child is
    absolutely positioned — so the link collapsed to nothing and the logo did not appear.
    The Jinja footer drew a different mark entirely: the symbol beside the words typed
    out in HTML.
    """

    assert _pixels(REACT_CSS, ".hm-footer-logo", "height") == 31
    assert (
        _pixels(
            PUBLIC_CSS,
            ".hm-jinja-footer .hm-footer-brand .hm-footer-logo img",
            "height",
        )
        == 31
    )
    # The same artwork file, not a second drawing of the same words.
    assert "hilal-markets-logo.svg" in JINJA_FOOTER
    assert "hilal-markets-symbol.svg" not in JINJA_FOOTER


def test_the_react_footer_logo_keeps_its_own_proportions() -> None:
    """31px tall and 197px wide is the artwork's ratio.

    The SVG is drawn `preserveAspectRatio="none"` and fills its box, so any other pair of
    numbers squeezes the letters — which `brand guide.md` section 5 forbids outright.
    """

    assert _pixels(REACT_CSS, ".hm-footer-logo", "width") == 197
    assert _pixels(REACT_CSS, ".hm-footer-logo", "height") == 31


# --------------------------------------------------------------------------------
# Back to the top, above the assistant.
# --------------------------------------------------------------------------------


def test_back_to_top_sits_clear_above_the_assistant_button() -> None:
    """Measured against the assistant's own numbers, not against a number typed here.

    Both controls are fixed to the same corner and neither knew about the other, so they
    overlapped on every long page. The arithmetic is read out of both sheets so that
    moving the assistant fails this test instead of silently re-creating the overlap.
    """

    launcher_bottom = _pixels(CHAT_CSS, ".public-chat-launcher", "bottom")
    launcher_height = _pixels(CHAT_CSS, ".public-chat-launcher", "height")
    to_top_bottom = _pixels(PUBLIC_CSS, ".hm-to-top", "bottom")

    assert to_top_bottom >= launcher_bottom + launcher_height, (
        "back-to-top overlaps the assistant's button"
    )


def test_back_to_top_has_exactly_one_owner() -> None:
    """It is styled in the sheet both halves of the site load, and nowhere else.

    It used to live in the React bundle's own sheet. The server-rendered pages never load
    that sheet, so the button could not be put on them at all — and a second copy there
    would have been a second answer to "where is this corner", with the sheet loaded
    second silently winning.
    """

    assert ".hm-to-top {" in PUBLIC_CSS
    assert not re.search(r"^\.hm-to-top\s*\{", REACT_CSS, re.MULTILINE)


# --------------------------------------------------------------------------------
# What actually shipped.
# --------------------------------------------------------------------------------

BUILT_JS = (STATIC / "landing" / "assets" / "landing.js").read_text(encoding="utf-8")
BUILT_CSS = (STATIC / "landing" / "assets" / "landing.css").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "name,present,text",
    [
        ("the real Threads glyph", True, "M12.186 24h-.007c-3.581-.024"),
        ("the hand-drawn Threads glyph", False, "M16.2 11.2a6.9 6.9"),
        ("the cookie control", True, "data-cookie-settings"),
        ("its address with no script", True, "/cookies?settings=1"),
        ("the back-to-top button", True, "hm-to-top hm-no-print"),
        ("the feature card sentence", False, "hm-feature-line"),
        ("the card 'Test before you commit'", False, "Test before you commit"),
        ("the card 'Ready-made starts'", False, "Ready-made starts"),
        ("the card 'Or just say it'", False, "Or just say it"),
    ],
    ids=lambda item: str(item),
)
def test_the_built_bundle_carries_the_change(name: str, present: bool, text: str) -> None:
    """Read out of the built bundle, never out of the source.

    Editing `Hilal-Markets-Website/src/` changes nothing a visitor sees until the bundle
    is rebuilt **and copied by hand**; there is no script that does the copy. A test that
    reads the source proves what somebody meant to ship. This reads
    `static/landing/assets/`, which is what the server actually sends.
    """

    assert (text in BUILT_JS) is present, name


def test_the_built_sheet_draws_the_footer_logo_at_31px() -> None:
    tight = BUILT_CSS.replace(" ", "").replace("\n", "")
    assert "width:197px" in tight and "height:31px" in tight
    # And it does not carry its own idea of where the back-to-top button sits, which
    # would override the sheet both halves of the site load.
    assert ".hm-to-top{" not in tight


def test_every_public_page_carries_a_back_to_top() -> None:
    """Both renderers, and every React page — including the two that never had one.

    The home page and Contact are the longest pages on the site and both had no way back
    but scrolling, because the button was copied into three page files by hand and those
    two were missed. It is one component now.
    """

    base_public = (TEMPLATES / "hilal" / "base_public.html").read_text(encoding="utf-8")
    assert "partials/back_to_top.html" in base_public

    assert "export function BackToTop(" in SITE_CHROME
    pages = [
        REACT / "App.tsx",
        REACT / "pages" / "ContactPage.tsx",
        REACT / "pages" / "FeaturesPage.tsx",
        REACT / "pages" / "HowItWorksPage.tsx",
        REACT / "pages" / "LegalPage.tsx",
    ]
    for page in pages:
        body = page.read_text(encoding="utf-8")
        assert "<BackToTop />" in body, page.name
        # And no page draws its own copy of it any more.
        assert "hm-to-top hm-no-print" not in body, page.name
