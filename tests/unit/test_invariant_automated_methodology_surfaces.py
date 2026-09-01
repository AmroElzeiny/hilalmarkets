"""Every screen that can show a machine verdict says so, and links to the whole rule.

The warning is the product's honesty, and honesty that depends on somebody remembering
to include a partial is not a property of the product — it is a property of whoever last
edited a template. So the check is mechanical: find the surfaces that can show a result
from the automated standard, and require the notice on each.

The second half is about reach. A coin admitted by this standard is admitted as a coin,
not as a coin-on-one-exchange, so it has to appear for every exchange that lists it. That
is how the code already works; this proves it, because "it should work" is what was
believed about the notice on the Passport for weeks while it was not there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_market_monitor.services.hilal_methodology import (
    METHODOLOGY_PUBLIC_PATH,
    admitted_symbols,
)
from ai_market_monitor.services.sharia_screening import canonical_asset

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "src" / "ai_market_monitor" / "templates"
NOTICE = "hilal/partials/automated_methodology_notice.html"

#: Every screen that can put a result from the automated standard in front of a person.
#:
#: Each one is here because of what it is, not because it happens to include the partial
#: today. The market list and the monitor builder are where a standard is *chosen*; the
#: Passport and its popup are where one coin's answer is *read*; the two research pages
#: exist only for this standard's output.
SURFACES = [
    ("hilal/dashboard_test/market.html", "the list of screened coins"),
    ("hilal/dashboard_test/monitor.html", "where a monitor is built"),
    ("hilal/dashboard_test/passport.html", "one coin's full record"),
    ("hilal/dashboard_test/partials/passport_quick_view.html", "the Passport popup"),
    ("hilal/dashboard_test/research.html", "the list of machine-researched coins"),
    ("hilal/dashboard_test/research_detail.html", "one machine-researched coin"),
    ("hilal/dashboard/passport.html", "an older version of one coin's record"),
]


@pytest.mark.parametrize(("template", "what"), SURFACES, ids=[item[0] for item in SURFACES])
def test_a_surface_that_can_show_a_machine_verdict_draws_the_warning(template, what):
    """`{what}` can show an automated result, so it must include the shared notice."""

    text = (TEMPLATES / template).read_text(encoding="utf-8")
    assert NOTICE in text, (
        f"{template} ({what}) can show a result from the automated standard and does "
        "not include the warning partial."
    )


def test_the_warning_is_written_in_exactly_one_place():
    """Six surfaces, one wording.

    A reader who met a softer version on one screen and a firmer one on another would
    learn to trust whichever they saw first. Any template writing the sentence itself
    instead of including the partial is the beginning of that.
    """

    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if path.name == "automated_methodology_notice.html":
            continue
        text = path.read_text(encoding="utf-8")
        if "No Shariah advisor stands behind it" in text:
            offenders.append(path.relative_to(TEMPLATES).as_posix())
    assert offenders == []


def test_the_notice_links_to_the_published_standard():
    """A warning a reader cannot follow up is a warning they have to take on trust."""

    text = (TEMPLATES / NOTICE).read_text(encoding="utf-8")
    assert "automated_methodology_path" in text
    assert "Read how it works" in text


def test_the_notice_address_is_a_global_and_never_typed_into_a_page():
    """One owner for the address, so a moved page does not leave six dead links.

    A *link* to it is what this forbids. Naming the address in a comment is how a
    template explains itself, and a rule that also banned that would push the
    explanation out of the file that needs it.
    """

    typed = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for quote in ('"', "'"):
            if f"href={quote}{METHODOLOGY_PUBLIC_PATH}" in text:
                typed.append(path.relative_to(TEMPLATES).as_posix())
                break
    assert typed == [], f"the address is written by hand in {typed}"


def test_the_notice_draws_nothing_when_the_standard_is_not_selected():
    """The other half. Without it the rule above passes by showing it everywhere.

    A warning on every coin, including ones a Shariah board really did assess, would
    train people to ignore it — which costs more than not showing it at all.
    """

    text = (TEMPLATES / NOTICE).read_text(encoding="utf-8")
    assert "{%- if _is_automated -%}" in text
    assert "automated_methodology_selected or" in text


def test_the_compact_form_keeps_the_warning_and_drops_only_the_detail():
    """A popup has less room; it does not get a softer warning."""

    text = (TEMPLATES / NOTICE).read_text(encoding="utf-8")
    headline, _, rest = text.partition("{%- if not compact %}")
    assert "No Shariah advisor stands behind it" in headline
    assert "automated_methodology_path" in headline
    assert "Skipping is not passing" in rest


# --------------------------------------------------------------------------------
# Reach: an admitted coin appears wherever it trades
# --------------------------------------------------------------------------------

#: What each exchange listed against USDT on 31 August 2026, for the ten coins that were
#: newly researched. Recorded rather than fetched: a test that called an exchange would
#: fail on a train, and the point here is the *rule*, not today's listings.
LISTED_ON = {
    "binance": {"ZEC", "USD1", "ENSO", "PUMP", "RLUSD", "XAUT", "PEPE", "ZKC", "PROM", "U"},
    "bybit": {"USD1", "ENSO", "PUMP", "RLUSD", "XAUT", "PEPE", "ZKC"},
}


@pytest.mark.parametrize("exchange", sorted(LISTED_ON))
def test_an_admitted_coin_shows_on_every_exchange_that_lists_it(exchange):
    """The market list is filtered by the exchange's own symbols, not by a stored pair.

    `screened_market_context` builds `asset_scope` from `provider.list_symbols(exchange)`
    and hands it to `list_screened_assets`, so a coin admitted by this standard appears
    for whichever exchange lists it. Nothing about the admission is exchange-specific,
    and this is what proves it stays that way: an admission record that grew an
    `exchange` field would make this fail.
    """

    scope = {canonical_asset(f"{symbol}/USDT") for symbol in LISTED_ON[exchange]}
    admitted = admitted_symbols()
    visible = scope & admitted
    expected = {symbol for symbol in LISTED_ON[exchange] if symbol in admitted}
    assert visible == expected
    assert visible, f"no admitted coin trades on {exchange}, so this proves nothing"


def test_the_bybit_half_is_a_real_subset():
    """Guards the test above from passing because the two exchanges list the same set."""

    assert LISTED_ON["bybit"] < LISTED_ON["binance"]


def test_an_admission_record_never_names_an_exchange():
    """The reason the rule above holds, checked at its source.

    A coin is admitted as a coin. The moment a record carried "binance", the standard
    would be answering a different question on each exchange, and a person switching the
    exchange picker would see a Shariah result change for no religious reason.
    """

    from ai_market_monitor.services.hilal_methodology import admissions_payload

    for row in admissions_payload():
        blob = repr(row).lower()
        assert "binance" not in blob
        assert "bybit" not in blob
