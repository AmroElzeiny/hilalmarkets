"""A coin's Passport has one address, and one owner builds it.

The Passport moved from `/dashboard/market/<coin>` to `/passports/<coin>` on 5 October
2026. Twenty places — services, routers, templates and two scripts — had written the old
address by hand, each a little differently (one upper-cased the coin, one cut a pair,
one forgot the standard). Moving the page meant finding all of them. These tests keep
it from happening again: the address is built by `core.dashboard_paths.passport_path`
(or `core.app_links.passport_link` for a link that leaves the website), and nothing
else writes one.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from ai_market_monitor.core.app_links import absolute_passport_link, passport_link
from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.dashboard_paths import PASSPORTS_PATH, passport_path

SRC = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: Every spelling a coin arrives in: as stored, as typed, as a trading pair.
SPELLINGS = [
    ("BTC", "btc"),
    ("btc", "btc"),
    (" Sol ", "sol"),
    ("SOL/USDT", "sol"),
    ("ltc/usdc", "ltc"),
    ("1INCH", "1inch"),
]


def _settings(**overrides: object) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(("asset", "slug"), SPELLINGS)
@pytest.mark.parametrize("report", [False, True])
def test_every_spelling_of_a_coin_has_one_address(asset, slug, report):
    expected = f"{PASSPORTS_PATH}/{slug}{'/report' if report else ''}"
    assert passport_path(asset, report=report) == expected


@pytest.mark.parametrize(("asset", "slug"), SPELLINGS)
def test_the_standard_rides_in_the_address(asset, slug):
    methodology_id = uuid4()
    assert passport_path(asset, methodology_id=methodology_id) == (
        f"/passports/{slug}?methodology_id={methodology_id}"
    )
    assert passport_path(asset, methodology_id=methodology_id, report=True) == (
        f"/passports/{slug}/report?methodology_id={methodology_id}"
    )


@pytest.mark.parametrize("empty", [None, ""])
def test_no_standard_means_no_query(empty):
    assert passport_path("BTC", methodology_id=empty) == "/passports/btc"


def test_a_coin_cannot_break_out_of_the_address():
    # A slash is a trading pair, so everything after it goes; what is left must still
    # be a name, never a direction a browser would follow.
    assert passport_path("../admin") == "/passports/%2E%2E"
    assert passport_path(".") == "/passports/%2E"
    assert passport_path("a?b#c") == "/passports/a%3Fb%23c"


def test_the_link_names_the_website_when_the_product_has_two_hostnames():
    two = _settings(
        public_base_url="https://hilalmarkets.com", app_base_url="https://app.hilalmarkets.com"
    )
    assert passport_link(two, "BTC") == "https://hilalmarkets.com/passports/btc"
    assert passport_link(two, "BTC", report=True) == (
        "https://hilalmarkets.com/passports/btc/report"
    )
    one = _settings(public_base_url="http://localhost:8000", app_base_url="http://localhost:8000")
    assert passport_link(one, "BTC") == "/passports/btc"
    # A message read outside the website always needs the hostname.
    assert absolute_passport_link(one, "BTC") == "http://localhost:8000/passports/btc"


#: A Passport address written by hand: the old dashboard address followed by a coin in
#: any templating syntax (Python f-string, JavaScript template, Jinja), or the new
#: address assembled the same way.
HAND_WRITTEN = re.compile(
    r"/dashboard/market/(?:\{|\$\{|\{\{|\"\s*\+|'\s*\+)"
    r"|/passports/(?:\{|\$\{|\{\{|\"\s*\+|'\s*\+)(?![^\n]*versions)"
)

#: Following a coin and reporting a problem are actions, not the page, and keep their
#: own addresses. So is `/quick-view`: the data the Passport popup on the public Market
#: page reads (`/api/v1/public-market/passports/{asset}/quick-view`), not a page anyone
#: opens.
ACTION_SUFFIXES = ("/watchlist", "/problem-reports", "/quick-view")


def _hand_written_addresses() -> list[str]:
    found: list[str] = []
    for path in SRC.rglob("*"):
        if path.suffix not in {".py", ".html", ".js"} or "vendor" in path.parts:
            continue
        if path.parts[-3:-1] == ("landing", "assets"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HAND_WRITTEN.search(line) and not any(end in line for end in ACTION_SUFFIXES):
                found.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    return found


def test_no_file_builds_a_passport_address_by_hand():
    assert _hand_written_addresses() == [], (
        "Build the address with `passport_path` / `passport_link` "
        "(`passport_link(settings, ...)` in a template). Found:\n"
        + "\n".join(_hand_written_addresses())
    )
