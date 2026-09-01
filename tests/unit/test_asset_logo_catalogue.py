"""One answer to "where does a coin's picture come from", everywhere.

Two duplications used to live here, and each hid a different missing logo.

**The address** was written out by hand in seven places — four in Python, three more in
templates and browser code. Raising the version meant finding all seven, and missing one
is silent: the card falls back to three letters and nobody files a report about a logo
that never appeared.

**The sources** were worse. *Which* pictures exist for a coin was decided independently
in eight readers, and each knew a different subset. Three of them never looked at the
picture stored on the coin's own record, which is the only picture a small or newly
listed token has — so those pages showed letters for coins the platform had a logo for,
while the page next to them showed the logo.

Both are owned by `core/asset_logos.py` now. These tests fail if a copy comes back.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from ai_market_monitor.core import asset_logos
from ai_market_monitor.core.asset_logos import (
    _MONOGRAM_LETTERS,
    LOGO_CATALOG,
    asset_logo,
    asset_logo_module_url,
    stored_logo_url,
)

ROOT = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: The only place left that has to write the catalogue address out by hand.
#:
#: The browser cannot import a Python constant, and this file is where the catalogue's
#: module URLs are actually fetched. Every other front-end copy is gone: the templates
#: ask the `asset_logo` global, and `hm-market-vocabulary.js` uses whatever the record
#: already carries.
FRONT_END_COPIES = ("static/asset-logos.js",)

#: Front-end files that used to write it out and must never do so again.
FORMER_FRONT_END_COPIES = (
    "static/hm-market-vocabulary.js",
    "templates/hilal/macros/opportunity_card.html",
    "templates/hilal/dashboard_test/macros/coin.html",
)

_VERSION = re.compile(r"@web3icons/core@([\d.]+)")


def _read(relative: str) -> str:
    path = ROOT / relative
    assert path.exists(), relative
    return path.read_text(encoding="utf-8")


def _pinned_version(text: str) -> str:
    found = _VERSION.search(text)
    assert found, "the catalogue address changed shape; this check needs updating"
    return found.group(1)


# ── The address ──────────────────────────────────────────────────────────────


def test_the_owner_pins_one_version():
    assert _pinned_version(LOGO_CATALOG)


@pytest.mark.parametrize("relative", FRONT_END_COPIES)
def test_every_front_end_copy_asks_for_the_same_version(relative: str):
    assert _pinned_version(_read(relative)) == _pinned_version(
        LOGO_CATALOG
    ), f"{relative} asks for a different catalogue version from core/asset_logos.py"


@pytest.mark.parametrize("relative", FORMER_FRONT_END_COPIES)
def test_a_removed_copy_does_not_come_back(relative: str):
    assert "web3icons" not in _read(relative), (
        f"{relative} writes the catalogue address out again; "
        "ask core/asset_logos.py through the `asset_logo` global instead"
    )


def test_no_python_file_writes_the_address_out_again():
    """The whole point of the owner. A new copy has to fail here rather than at a
    customer's browser months later, when one page shows logos and another does not."""

    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if path.name != "asset_logos.py" and "web3icons" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"these write the catalogue address out again: {offenders}"


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("SOL", "SOL"),
        ("sol", "SOL"),
        (" sol ", "SOL"),
        ("1000SHIB", "1000SHIB"),
        ("MUBARAK", "MUBARAK"),
    ],
)
def test_a_coin_becomes_one_catalogue_address(symbol: str, expected: str):
    assert asset_logo_module_url(symbol) == f"{LOGO_CATALOG}/{expected}.svg.js"


@pytest.mark.parametrize("symbol", ["", "   ", "SOL/USDT", "sol usdt", "A" * 25, None])
def test_a_ticker_that_cannot_be_a_ticker_gets_no_address(symbol):
    """No URL is built from unchecked text. The monogram is the answer instead."""

    assert asset_logo_module_url(symbol) is None
    assert asset_logo(symbol).module_url is None


# ── The sources ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"logo_url": "https://assets.example/coin.png"}, "https://assets.example/coin.png"),
        ({"logo_url": "  https://assets.example/coin.png  "}, "https://assets.example/coin.png"),
        # Anything the browser would refuse to draw is treated as absent, because a
        # blocked image shows as broken and the letters it replaced were readable.
        ({"logo_url": "http://assets.example/coin.png"}, None),
        ({"logo_url": "//assets.example/coin.png"}, None),
        ({"logo_url": "/static/coin.png"}, None),
        ({"logo_url": ""}, None),
        ({"logo_url": None}, None),
        ({}, None),
        (None, None),
        ("not a mapping", None),
    ],
)
def test_only_a_picture_the_browser_can_draw_counts_as_stored(stored, expected):
    assert stored_logo_url(stored) == expected


def test_a_coin_in_the_catalogue_and_on_its_own_record_offers_both():
    """Both, in order. The stored picture is the coin's own, so it is tried first."""

    logo = asset_logo("SOL", {"logo_url": "https://assets.example/sol.png"})
    assert logo.image_url == "https://assets.example/sol.png"
    assert logo.module_url == f"{LOGO_CATALOG}/SOL.svg.js"
    assert logo.sources == (logo.image_url, logo.module_url)


def test_a_coin_only_on_its_own_record_still_offers_that_picture():
    """The reported failure. Mubarak is not in the shared catalogue, so the picture
    stored when its identity was verified is the only one it has — and three readers
    used to drop it, which is why the coin showed three letters."""

    logo = asset_logo("MUBARAK", {"logo_url": "https://assets.example/mubarak.png"})
    assert logo.image_url == "https://assets.example/mubarak.png"
    assert logo.sources[0] == logo.image_url


def test_a_coin_with_no_picture_anywhere_still_has_something_to_draw():
    logo = asset_logo("ZZZZ")
    assert logo.image_url is None
    assert logo.monogram == "ZZZ"


@pytest.mark.parametrize(
    ("symbol", "monogram"),
    [("BTC", "BTC"), ("SOL", "SOL"), ("ETH", "ETH"), ("1000SHIB", "100"), ("OP", "OP")],
)
def test_the_letters_are_always_there(symbol: str, monogram: str):
    """Never empty. A card with nothing to draw is the one state that must not exist."""

    assert asset_logo(symbol).monogram == monogram
    assert asset_logo(symbol).monogram != ""


def test_the_template_and_python_agree_on_how_many_letters():
    """The macro slices the ticker in Jinja, which cannot import the constant. If the
    two ever disagree, the fallback is a different size on a server-rendered card than
    on one the browser drew."""

    macro = _read("templates/hilal/macros/coin_logo.html")
    assert "resolved.monogram" in macro, (
        "the macro cuts the ticker itself again; it must use the owner's monogram"
    )
    assert _MONOGRAM_LETTERS == 3


# ── Every reader asks the owner ──────────────────────────────────────────────


#: Every reader that turns a coin into something a page can draw.
#:
#: The rule is not "imports the module" — it is "does not decide for itself". Reading
#: `provider_ids["logo_url"]` by hand is exactly the decision that was made eight
#: different ways.
READERS = (
    "services/lifecycle_dashboard.py",
    "services/sharia_passports.py",
    "services/live_market_quotes.py",
    "services/sharia_screening.py",
    "api/routers/dashboard.py",
    "api/routers/dashboard_test.py",
)


@pytest.mark.parametrize("relative", READERS)
def test_no_reader_digs_the_stored_picture_out_of_a_record(relative: str):
    """Passing an already-resolved address along is fine. Reaching into a record's
    `provider_ids` for it is the decision that was made eight different ways, and each
    hand-written version accepted a different set of values as usable."""

    # Every public function the owner exposes counts as "handed to the owner". Derived
    # rather than typed, because a hand-written exemption list drifts from the module it
    # exempts: this one allowed `stored_logo_url(` alone, so a caller that correctly used
    # `asset_logo(` — the owner's *main* entry point, and the one this test's own failure
    # message recommends — was reported as an offender. The rule and its exemption
    # disagreed, and the exemption was the narrower of the two.
    owners = tuple(
        f"{name}("
        for name, value in inspect.getmembers(asset_logos, inspect.isfunction)
        if not name.startswith("_")
    )
    offenders = [
        line.strip()
        for line in _read(relative).splitlines()
        if "provider_ids" in line
        and "logo_url" in line
        # Handing the record to the owner is the whole point; it is only doing the
        # reading itself that is banned.
        and not any(owner in line for owner in owners)
    ]
    assert offenders == [], (
        f"{relative} reads the stored picture out of a record itself: {offenders}; "
        f"call one of core.asset_logos: {', '.join(sorted(owners))}"
    )


@pytest.mark.parametrize(
    "relative",
    (
        "templates/hilal/dashboard/passport.html",
        "templates/hilal/dashboard/activity.html",
        # `dashboard/home.html` used to be here. The Home page was removed and Main took
        # its place, so the front page that names coins is the one below it.
        "templates/hilal/main/home.html",
        "templates/hilal/dashboard_test/opportunities.html",
        "templates/hilal/macros/opportunity_card.html",
    ),
)
def test_no_template_writes_the_logo_markup_itself(relative: str):
    """One markup owner. Three of these used to wrap *both* addresses in a single
    `{% if logo_module_url %}`, so a coin with a stored picture and no catalogue entry
    lost the only picture it had."""

    body = _read(relative)
    assert "data-asset-logo-module=" not in body, f"{relative} writes the attributes itself"
    assert "coin_logo" in body, f"{relative} does not use the shared macro"
