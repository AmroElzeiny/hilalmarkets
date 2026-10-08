"""Every public Passport opens with the same template, filled from its own record.

The page answers the question people search for — "Is Bitcoin halal?" — without ever
answering it as a ruling: the sentence under the heading names one Shariah standard, its
version and its result, and says that it is a screening result under that standard, not
a universal religious ruling. These tests hold that for every status a standard can
reach, every way a coin can be named, current and older records, and the machine-made
standard — not for one sample coin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_market_monitor.core.copy_rules import FORBIDDEN_CLAIM_PHRASES
from ai_market_monitor.db.models.enums import ShariaAssetStatus
from ai_market_monitor.schemas.sharia import (
    AssetAssessmentSummary,
    AssetPassportResponse,
    PassportHistoricalContext,
    PassportIdentity,
)
from ai_market_monitor.services.passport_page import (
    RELATED_PASSPORT_COUNT,
    PublicPassport,
    passport_headline,
    related_passports,
)
from ai_market_monitor.services.sharia_automated_screen import METHODOLOGY_SYSTEM_CODE
from ai_market_monitor.services.sharia_screening import STATUS_LABELS

NOT_A_RULING = (
    "This is a methodology-specific screening result, not a universal religious ruling."
)

#: (name, symbol, network, kind, how the coin is written in the heading)
COINS = [
    ("Bitcoin", "BTC", "Bitcoin", "native_coin", "Bitcoin (BTC)"),
    ("USD Coin", "USDC", "ethereum", "token", "USD Coin (USDC)"),
    # The name and the symbol are the same word: written once, not "XRP (XRP)".
    ("XRP", "XRP", "XRP", "native_coin", "XRP"),
]


def _passport(
    *,
    name: str = "Bitcoin",
    symbol: str = "BTC",
    network: str | None = "Bitcoin",
    kind: str = "native_coin",
    status: ShariaAssetStatus = ShariaAssetStatus.ELIGIBLE,
    standard: str = "Test Shariah Standard",
    version: str = "2026.07",
    code: str = "TEST_STANDARD",
    qualification: str | None = None,
    historical: bool = False,
    identity: bool = True,
) -> AssetPassportResponse:
    now = datetime.now(UTC)
    return AssetPassportResponse(
        assessment=AssetAssessmentSummary(
            id=uuid4(),
            canonical_asset=symbol,
            asset_name=name,
            methodology_id=uuid4(),
            methodology_name=standard,
            methodology_code=code,
            methodology_version=version,
            status=status,
            status_label=STATUS_LABELS[status],
            summary="A reviewer recorded this.",
            qualifications=[qualification] if qualification else [],
            reviewed_by="Reviewer",
            reviewed_at=now,
            valid_from=now,
            valid_until=None,
        ),
        why_this_status="A reviewer recorded this.",
        reviewed_dimensions=[],
        methodology_result={},
        evidence_sources=[],
        status_history=[],
        evidence_available=False,
        notice="",
        identity=(
            PassportIdentity(name=name, symbol=symbol, network=network, asset_type=kind)
            if identity
            else None
        ),
        main_qualification=qualification,
        historical=PassportHistoricalContext(is_historical=historical),
    )


@pytest.mark.parametrize("status", list(ShariaAssetStatus))
@pytest.mark.parametrize(("name", "symbol", "network", "kind", "coin"), COINS)
def test_every_status_and_coin_gets_the_same_template(status, name, symbol, network, kind, coin):
    headline = passport_headline(
        _passport(name=name, symbol=symbol, network=network, kind=kind, status=status)
    )
    label = STATUS_LABELS[status]

    assert headline.eyebrow == f"{symbol} Evidence Passport"
    assert headline.question == f"Is {coin} Halal?"
    assert headline.answer == (
        f"Under Test Shariah Standard v2026.07, {name} is currently classified as {label}. "
        f"{NOT_A_RULING}"
    )
    assert headline.page_title == f"Is {name} Halal? {symbol} Shariah Screening & Evidence"
    for fact in (coin, label, "Test Shariah Standard v2026.07", "Hilal Markets"):
        assert fact in headline.description, fact


@pytest.mark.parametrize(
    ("network", "kind", "line"),
    [
        ("Bitcoin", "native_coin", "BTC · Bitcoin · Native coin"),
        ("ethereum", "token", "BTC · Ethereum · Token"),
        ("binance-smart-chain", "token", "BTC · BNB Smart Chain · Token"),
        (None, "token", "BTC · Token"),
        ("Base", "network", "BTC · Base · Network"),
        ("Bitcoin", "unknown", "BTC · Bitcoin · Type not recorded"),
    ],
)
def test_the_identity_line_names_symbol_network_and_kind(network, kind, line):
    assert passport_headline(_passport(network=network, kind=kind)).identity_line == line


def test_a_coin_without_an_identity_record_is_named_from_its_assessment():
    headline = passport_headline(_passport(identity=False))
    assert headline.question == "Is Bitcoin (BTC) Halal?"
    assert headline.identity_line == "BTC · Type not recorded"


@pytest.mark.parametrize("status", list(ShariaAssetStatus))
def test_an_older_record_never_says_currently(status):
    answer = passport_headline(_passport(status=status, historical=True)).answer
    assert "currently" not in answer
    assert f"this older record classified Bitcoin as {STATUS_LABELS[status]}" in answer
    assert answer.endswith(NOT_A_RULING)


def test_a_condition_is_pointed_to_not_dropped():
    answer = passport_headline(
        _passport(
            status=ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
            qualification="Spot holding only.",
        )
    ).answer
    assert "classified as Eligible with qualifications, with the condition shown below." in answer


def test_the_machine_standard_says_what_it_is_in_the_answer():
    headline = passport_headline(
        _passport(standard="Hilal Markets Methodology", code=METHODOLOGY_SYSTEM_CODE)
    )
    assert "Hilal Markets Methodology v2026.07 (automated, no Shariah advisor)" in headline.answer
    assert "(automated, no Shariah advisor)" in headline.description


@pytest.mark.parametrize("status", list(ShariaAssetStatus))
@pytest.mark.parametrize("historical", [False, True])
def test_no_headline_carries_a_forbidden_claim(status, historical):
    headline = passport_headline(_passport(status=status, historical=historical))
    for text in (
        headline.eyebrow,
        headline.question,
        headline.answer,
        headline.page_title,
        headline.description,
        headline.report_title,
    ):
        for claim in FORBIDDEN_CLAIM_PHRASES:
            assert claim not in text.casefold(), (claim, text)


def test_two_standards_give_two_answers_for_one_coin():
    """Each standard reaches its own result; the page never merges them into one."""

    first = passport_headline(_passport(standard="First standard"))
    second = passport_headline(
        _passport(standard="Second standard", status=ShariaAssetStatus.UNDER_REVIEW)
    )
    assert first.answer != second.answer
    assert "First standard" in first.answer and "Second standard" not in first.answer
    assert "Under review" in second.answer and "Eligible" not in second.answer


def test_no_two_coins_share_a_title_heading_answer_or_description():
    headlines = [
        passport_headline(_passport(name=name, symbol=symbol, network=network, kind=kind))
        for name, symbol, network, kind, _coin in COINS
    ]
    for field in ("eyebrow", "question", "answer", "page_title", "description"):
        values = [getattr(item, field) for item in headlines]
        assert len(set(values)) == len(values), field


# -- links to other Passports ------------------------------------------------------------


def _entry(symbol: str, kind: str = "Token", network: str | None = "Ethereum") -> PublicPassport:
    return PublicPassport(symbol=symbol, name=f"{symbol} coin", kind=kind, network=network)


def _catalogue() -> list[PublicPassport]:
    natives = [_entry(s, "Native coin", s.title()) for s in ("BTC", "ETH", "SOL", "XRP", "ADA")]
    ethereum = [_entry(s) for s in ("USDC", "USDT", "LINK", "UNI", "AAVE", "DAI", "MKR")]
    solana = [_entry(s, network="Solana") for s in ("JUP", "BONK", "PYTH")]
    return natives + ethereum + solana


def test_a_passport_never_links_to_itself_and_links_a_fixed_number():
    catalogue = _catalogue()
    for item in catalogue:
        related = related_passports(item.symbol, catalogue)
        assert item.symbol not in {other.symbol for other in related}
        assert len(related) == min(RELATED_PASSPORT_COUNT, len(catalogue) - 1)


def test_closest_coins_come_first():
    catalogue = _catalogue()
    usdc = related_passports("USDC", catalogue)
    # Five other Ethereum tokens fill the list before any token elsewhere.
    assert all(item.network == "Ethereum" and item.kind == "Token" for item in usdc[:6])
    btc = related_passports("BTC", catalogue)
    assert [item.kind for item in btc[:4]] == ["Native coin"] * 4


def test_the_same_coin_always_gets_the_same_links():
    catalogue = _catalogue()
    assert related_passports("SOL", catalogue) == related_passports("SOL", catalogue[::-1])


def test_every_passport_is_linked_from_another():
    """The list starts after each coin and wraps, so nobody is left without a link."""

    catalogue = _catalogue()
    linked = {
        other.symbol
        for item in catalogue
        for other in related_passports(item.symbol, catalogue)
    }
    assert linked == {item.symbol for item in catalogue}


def test_a_coin_missing_from_the_list_still_gets_links():
    related = related_passports("NEW", _catalogue())
    assert len(related) == RELATED_PASSPORT_COUNT
