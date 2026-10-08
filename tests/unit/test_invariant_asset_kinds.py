"""A coin's kind — native coin, token or network — is read with the writer's own words.

Until 8 October 2026 the identity check wrote `native_coin`, and the Passport read a
native coin only when it said `native`, `coin` or `native_asset`. So every native coin on
every public Passport — Bitcoin, Ethereum, Solana, XRP, all 85 of them — was shown as a
"Token". Two lists for one word, each right about a different subset: the failure this
repository keeps repeating.

These tests hold the rule, not the case: every kind any writer in the code can record is
read back as that kind, every older spelling still reads as what it meant, and no other
module keeps a private list of its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.core.asset_kinds import (
    KIND_NOT_RECORDED,
    NATIVE_COIN,
    NETWORK,
    RECORDED_KINDS,
    TOKEN,
    asset_kind_label,
    is_native_coin,
    network_label,
)
from ai_market_monitor.schemas.sharia import PassportIdentity
from ai_market_monitor.services import sharia_identity_discovery
from ai_market_monitor.services.sharia_identity import REVIEWED_ASSET_CANDIDATES

SRC = Path(__file__).resolve().parents[2] / "src" / "ai_market_monitor"

#: Every kind a writer in this code base records, gathered from the writers themselves.
WRITTEN_KINDS = sorted(
    {candidate.asset_type for candidate in REVIEWED_ASSET_CANDIDATES.values()}
    | {
        candidate.asset_type
        for candidate in sharia_identity_discovery._STATIC_IDENTITIES.values()
    }
    | set(RECORDED_KINDS)
)

EXPECTED = {
    NATIVE_COIN: (True, "Native coin"),
    TOKEN: (False, "Token"),
    NETWORK: (None, "Network"),
}


def test_the_writers_record_only_kinds_the_vocabulary_knows():
    assert set(WRITTEN_KINDS) <= RECORDED_KINDS


@pytest.mark.parametrize("kind", WRITTEN_KINDS)
def test_every_written_kind_reads_back_as_itself(kind):
    native, label = EXPECTED[kind]
    assert is_native_coin(kind) is native
    assert asset_kind_label(kind) == label
    assert asset_kind_label(kind) != KIND_NOT_RECORDED


@pytest.mark.parametrize("spelling", ["native", "coin", "native_asset", "Native_Coin", " NATIVE "])
def test_every_older_spelling_of_a_native_coin_is_still_one(spelling):
    assert is_native_coin(spelling) is True
    assert asset_kind_label(spelling) == "Native coin"


@pytest.mark.parametrize("missing", [None, "", "  ", "unknown", "UNKNOWN"])
def test_a_kind_nobody_recorded_is_said_to_be_not_recorded(missing):
    assert is_native_coin(missing) is None
    assert asset_kind_label(missing) == KIND_NOT_RECORDED


def test_a_kind_the_vocabulary_has_never_seen_is_named_not_guessed():
    """Not called a token, not called a native coin: its own words, made readable."""

    assert is_native_coin("wrapped_asset") is None
    assert asset_kind_label("wrapped_asset") == "Wrapped asset"


@pytest.mark.parametrize(
    ("kind", "native", "label"),
    [(kind, *EXPECTED[kind]) for kind in sorted(RECORDED_KINDS)],
)
def test_the_passport_identity_carries_the_same_reading(kind, native, label):
    """Every page, popup and API reply reads the kind from this one object."""

    identity = PassportIdentity(name="Coin", symbol="CN", asset_type=kind, native_asset=native)
    assert identity.kind_label == label
    assert identity.model_dump()["kind_label"] == label


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        # Platform ids the identity check keeps for tokens, as found on the live site.
        ("ethereum", "Ethereum"),
        ("solana", "Solana"),
        ("binance-smart-chain", "BNB Smart Chain"),
        ("polygon-pos", "Polygon PoS"),
        ("tron", "TRON"),
        ("zksync", "zkSync"),
        ("optimistic-ethereum", "Optimism"),
        ("arbitrum-one", "Arbitrum One"),
        ("the-open-network", "The Open Network"),
        ("base", "Base"),
        # Names already written for people are kept exactly.
        ("Bitcoin", "Bitcoin"),
        ("The Open Network", "The Open Network"),
        ("NEAR Protocol", "NEAR Protocol"),
        ("eCash", "eCash"),
    ],
)
def test_a_network_is_shown_by_its_name_not_its_id(stored, shown):
    assert network_label(stored) == shown
    assert PassportIdentity(name="C", symbol="C", network=stored).network_label == shown


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_no_network_means_no_network_label(missing):
    assert network_label(missing) is None


#: A private word list for "native": a set literal holding one of its spellings, the
#: shape of the list that read every native coin as a token.
_PRIVATE_NATIVE_LIST = re.compile(r"\{[^{}]*[\"'](?:native|native_asset|native_coin)[\"'][^{}]*\}")


def test_no_module_keeps_its_own_list_of_native_spellings():
    owner = SRC / "core" / "asset_kinds.py"
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path != owner and _PRIVATE_NATIVE_LIST.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_script_decides_the_kind_for_itself():
    """The popups print the server's `kind_label`; they used to work it out again."""

    offenders = [
        path.name
        for path in (SRC / "static").glob("*.js")
        if "native_asset ===" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
