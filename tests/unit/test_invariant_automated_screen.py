"""The Hilal Markets screen must stay honest, and must stay measured.

Three things are asserted here, and the middle one is the reason the file exists.

1. **The rule fails closed.** An unanswered question is never a pass.
2. **Accuracy is measured on facts written blind.** The first version of this screen
   scored 100% against Fasset's 240 labels, which was not a result: the same hand wrote
   the facts and knew the answers. The fixture used below was produced by a model that
   was given only a symbol and a name — never told Fasset exists, never shown a verdict,
   never shown which answers block. That is the production path, and it is the only
   number worth quoting.
3. **SC Malaysia is the ruler.** The screen may never refuse an asset the Malaysian
   regulator's own Shariah Advisory Council has approved. If it does, the rule is wrong
   — not the regulator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_market_monitor.services.sharia_automated_screen import (
    AUTOMATED_DISCLOSURE,
    METHODOLOGY_DISPLAY_NAME,
    Activity,
    AssetFacts,
    HolderReturn,
    Verdict,
    screen,
)

ROOT = Path(__file__).resolve().parents[2]
PACK = (
    ROOT
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "HilalMarkets_Sharia_Methodology_Import_Pack"
    / "data"
)
BLIND_FACTS = ROOT / "tests" / "fixtures" / "sharia_screen_blind_facts.json"

#: The bar the owner set for shipping this methodology.
REQUIRED_ACCURACY = 90.0


def _labels() -> dict[str, bool]:
    """Fasset's own verdicts, the only place both sides of a line are visible."""

    labels: dict[str, bool] = {}
    for row in json.loads(
        (PACK / "fasset_compliant_assets.json").read_text(encoding="utf-8")
    ):
        labels[row["canonical_symbol_candidate"]] = True
    for row in json.loads(
        (PACK / "fasset_noncompliant_guard.json").read_text(encoding="utf-8")
    ):
        symbol = row["canonical_symbol_candidate"]
        if symbol in labels:
            # FRAX is one ticker over two different assets: the network token, which
            # Fasset accepts, and Legacy Frax Dollar, which it refuses. No screen can
            # separate them from a ticker, so it is excluded and covered by its own
            # test below.
            labels.pop(symbol)
            continue
        labels[symbol] = False
    return labels


def _blind_facts() -> dict[str, dict]:
    return json.loads(BLIND_FACTS.read_text(encoding="utf-8"))


def test_blind_accuracy_meets_the_bar():
    labels = _labels()
    facts = _blind_facts()

    decided = correct = 0
    wrongly_eligible: list[str] = []
    for symbol, expected in labels.items():
        payload = facts.get(symbol)
        if not payload:
            continue
        result = screen(AssetFacts.from_mapping({**payload, "canonical_symbol": symbol}))
        if result.verdict is Verdict.INSUFFICIENT_FACTS:
            continue
        decided += 1
        got = result.verdict is Verdict.ELIGIBLE
        if got == expected:
            correct += 1
        elif got:
            wrongly_eligible.append(symbol)

    accuracy = 100.0 * correct / decided
    assert decided > 200, "the fixture must cover the whole labelled set"
    assert accuracy >= REQUIRED_ACCURACY, (
        f"blind accuracy fell to {accuracy:.1f}% "
        f"(wrongly eligible: {', '.join(sorted(wrongly_eligible))})"
    )


def test_the_dangerous_errors_do_not_grow():
    """Refusing a good asset costs coverage. Passing a bad one costs trust.

    The two are not equal and must not be traded off silently, so the count of assets
    the screen wrongly calls eligible is pinned separately from overall accuracy.
    """

    labels = _labels()
    facts = _blind_facts()
    wrongly_eligible = [
        symbol
        for symbol, expected in labels.items()
        if not expected
        and (payload := facts.get(symbol))
        and screen(
            AssetFacts.from_mapping({**payload, "canonical_symbol": symbol})
        ).verdict
        is Verdict.ELIGIBLE
    ]
    assert len(wrongly_eligible) <= 7, sorted(wrongly_eligible)


def test_sc_malaysia_is_the_ruler_the_screen_is_measured_against():
    """The regulator's approvals are a floor. Refusing one means our rule is wrong."""

    facts = _blind_facts()
    approved = {
        row["canonical_symbol_candidate"]
        for row in json.loads(
            (PACK / "sc_malaysia_compliant_assets.json").read_text(encoding="utf-8")
        )
    }
    refused: list[str] = []
    for symbol in sorted(approved):
        payload = facts.get(symbol)
        if not payload:
            continue
        result = screen(AssetFacts.from_mapping({**payload, "canonical_symbol": symbol}))
        if result.verdict is Verdict.NOT_ELIGIBLE:
            refused.append(f"{symbol} ({','.join(a.value for a in result.blocking_activities)})")
    assert refused == [], (
        "The automated screen refused an asset the SC Malaysia Shariah Advisory "
        f"Council approved: {'; '.join(refused)}"
    )


# --------------------------------------------------------------------------
# Fail-closed behaviour: an unanswered question is never a pass.
# --------------------------------------------------------------------------


def test_no_facts_is_not_eligible():
    result = screen(AssetFacts(canonical_symbol="AAA", asset_name="Nothing known"))
    assert result.verdict is Verdict.INSUFFICIENT_FACTS
    assert "activities" in result.missing_facts


def test_a_peg_claim_alone_cannot_reach_a_verdict():
    """The measured failure: five yield-bearing dollar tokens passed as "fully backed"."""

    result = screen(
        AssetFacts(
            canonical_symbol="YIELDY",
            asset_name="Yieldy Dollar",
            activities=frozenset({Activity.FULLY_BACKED_REDEEMABLE}),
        )
    )
    assert result.verdict is Verdict.INSUFFICIENT_FACTS
    assert "holder_return" in result.missing_facts


def test_a_governance_token_cannot_be_cleaner_than_what_it_governs():
    result = screen(
        AssetFacts(
            canonical_symbol="GOVX",
            asset_name="Governance of a lender",
            activities=frozenset({Activity.PLATFORM_ACCESS_OR_GOVERNANCE}),
            governed_activities=frozenset({Activity.LENDING_BORROWING}),
        )
    )
    assert result.verdict is Verdict.NOT_ELIGIBLE
    assert Activity.LENDING_BORROWING in result.blocking_activities


def test_an_undescribed_governance_token_is_sent_back_not_passed():
    result = screen(
        AssetFacts(
            canonical_symbol="GOVY",
            asset_name="Governance of something",
            activities=frozenset({Activity.PLATFORM_ACCESS_OR_GOVERNANCE}),
        )
    )
    assert result.verdict is Verdict.INSUFFICIENT_FACTS
    assert "governed_activities" in result.missing_facts


@pytest.mark.parametrize(
    ("holder_return", "expected"),
    [
        (HolderReturn.NONE, Verdict.ELIGIBLE),
        (HolderReturn.FROM_WORK, Verdict.ELIGIBLE),
        (HolderReturn.FROM_LENDING_OR_PROMISE, Verdict.NOT_ELIGIBLE),
    ],
)
def test_work_and_lending_are_not_the_same_return(
    holder_return: HolderReturn,
    expected: Verdict,
):
    """The distinction the whole methodology rests on.

    Staking pays for work and is eligible; a lending or promised rate is not. Losing
    this distinction is what refused Chainlink, Hedera, NEAR, stETH and rETH in an
    earlier version.
    """

    result = screen(
        AssetFacts(
            canonical_symbol="TESTX",
            asset_name="Test asset",
            activities=frozenset(
                {Activity.STAKING_OR_VALIDATION, Activity.INTEREST_BEARING_HOLDING}
            ),
            holder_return=holder_return,
        )
    )
    assert result.verdict is expected


@pytest.mark.parametrize(
    "activity",
    [
        Activity.LENDING_BORROWING,
        Activity.DERIVATIVES_OR_LEVERAGE,
        Activity.GAMBLING,
        Activity.TOKENIZED_SECURITY,
        Activity.NO_UNDERLYING_UTILITY,
    ],
)
def test_every_blocking_activity_blocks_on_its_own(activity: Activity):
    result = screen(
        AssetFacts(
            canonical_symbol="BLOCKX",
            asset_name="Blocked asset",
            activities=frozenset({Activity.OWN_SETTLEMENT_NETWORK, activity}),
        )
    )
    assert result.verdict is Verdict.NOT_ELIGIBLE
    assert activity in result.blocking_activities
    assert result.reasons, "a refusal must say why in plain words"


def test_every_result_carries_the_automated_disclosure():
    """A result from this screen must never travel without saying what it is."""

    payload = screen(
        AssetFacts(
            canonical_symbol="BTC",
            asset_name="Bitcoin",
            activities=frozenset({Activity.OWN_SETTLEMENT_NETWORK}),
        )
    ).as_dict()
    assert payload["human_reviewed"] is False
    assert payload["disclosure"] == AUTOMATED_DISCLOSURE
    assert "No scholar has reviewed" in AUTOMATED_DISCLOSURE
    assert METHODOLOGY_DISPLAY_NAME == "Hilal Markets Methodology"
