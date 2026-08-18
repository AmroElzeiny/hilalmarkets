"""What Hilal is able to find, tested against real rows.

Every check here comes from one real conversation that went wrong three times in a row:

    "Is litecon halal?"      -> "I don't have a record for 'litecon'. Did you mean Litecoin?"
    "yes"                    -> "I don't have a review record for Litecoin"      <- wrong
    "I see it as LTCUSDT"    -> "I don't have a coin record for that symbol"     <- wrong

Litecoin is on the platform. Two separate faults produced those answers, and each is
tested here as a rule rather than as that one coin:

* **A follow-up carries no name.** "yes" has no coin in it, so nothing was looked up,
  and an empty result was read as "we do not have it". Saying a coin is not here when
  it is, is the same invention as making up a status — pointing the other way.
* **A trading pair is not a coin name.** People read "LTCUSDT" off a chart and type it
  whole. Nothing matched it, so a listed coin came back unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_market_monitor.core.config import get_settings
from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    CanonicalAsset,
    ExchangeMarket,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaAssetStatus, ShariaMethodologyStatus
from ai_market_monitor.services.hilal_chat_knowledge import HilalChatKnowledge
from tests.factories import methodology_evidence_requirements, methodology_rules


async def _seed(session, *, symbol: str, name: str, pairs: tuple[str, ...], reviewed: bool):
    """One coin, the way the platform really holds one."""

    now = datetime.now(UTC)
    asset = CanonicalAsset(
        symbol=symbol,
        name=name,
        asset_type="coin",
        contract_addresses={},
        provider_ids={},
        identity_hash=uuid4().hex + uuid4().hex,
        mapping_state="resolved",
        mapping_evidence={},
    )
    session.add(asset)
    await session.flush()
    for market in pairs:
        base, _, quote = market.partition("/")
        session.add(
            ExchangeMarket(
                canonical_asset_id=asset.id,
                exchange="binance",
                market_symbol=market,
                base_asset=base,
                quote_asset=quote,
                market_type="spot",
                is_active=True,
                metadata_hash=uuid4().hex + uuid4().hex,
            )
        )
    if reviewed:
        methodology = ShariaMethodology(
            code=f"KNOW_{uuid4().hex[:10].upper()}",
            name="Knowledge test standard",
            version="1.0",
            description="Used only to give a review something to hang from.",
            status=ShariaMethodologyStatus.ACTIVE,
            governing_body="Qualified test governance",
            reviewer_group="Qualified test reviewers",
            published_at=now - timedelta(days=2),
            effective_from=now - timedelta(days=2),
            rules_json=methodology_rules(source_family="knowledge_test"),
            evidence_requirements_json=methodology_evidence_requirements(),
        )
        session.add(methodology)
        await session.flush()
        session.add(
            AssetShariaAssessment(
                canonical_asset=symbol,
                asset_name=name,
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary="A qualified reviewer recorded this test conclusion.",
                qualifications=[],
                exclusion_reasons=[],
                evidence_snapshot={},
                reviewed_by="Qualified test reviewer",
                reviewed_at=now - timedelta(days=1),
                valid_from=now - timedelta(days=1),
            )
        )
    await session.commit()


# --------------------------------------------------------------------------------
# A coin is found however a person writes it.
# --------------------------------------------------------------------------------

#: Every way somebody refers to the same coin. Each one used to be a separate risk of
#: being told the platform does not have it.
SPELLINGS = [
    "LTC",
    "ltc",
    "$LTC",
    "Litecoin",
    "litecoin",
    "the litecoin coin",
    "LTC/USDT",
    "LTCUSDT",
    "ltcusdt",
    "LTC/USDC",
    "is LTCUSDT halal?",
    "what about litecoin",
]


@pytest.mark.parametrize("written", SPELLINGS, ids=lambda item: item[:24])
async def test_a_listed_coin_is_found_however_it_is_written(test_context, written: str):
    async with test_context["session_factory"]() as session:
        await _seed(
            session,
            symbol="LTC",
            name="Litecoin",
            pairs=("LTC/USDT", "LTC/USDC"),
            reviewed=True,
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message=written, view=None
        )
    found = [item.symbol for item in evidence.asked_about]
    assert "LTC" in found, f"{written!r} did not find the coin"
    assert evidence.looked_for_but_not_listed == [], (
        f"{written!r} was reported as not listed while it was found"
    )


# --------------------------------------------------------------------------------
# A follow-up is still about the coin.
# --------------------------------------------------------------------------------

#: What people actually type when they answer a question Hilal asked them.
FOLLOW_UPS = ["yes", "yes please", "correct", "that one", "yeah", "اي", "evet"]


@pytest.mark.parametrize("reply", FOLLOW_UPS, ids=lambda item: item[:16])
async def test_a_short_answer_still_knows_which_coin_is_meant(test_context, reply: str):
    """The exact failure from the transcript. "yes" has no coin in it."""

    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message=reply,
            view=None,
            earlier=[
                "is litecon halal?",
                "I do not have a record for that spelling. Did you mean Litecoin?",
            ],
        )
    assert [item.symbol for item in evidence.asked_about] == ["LTC"], (
        f"answering {reply!r} lost the coin the conversation was about"
    )


async def test_the_new_question_beats_the_old_one(test_context):
    """Carrying the subject forward must never bury the coin just asked about."""

    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        await _seed(
            session, symbol="SOL", name="Solana", pairs=("SOL/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="what about SOL",
            view=None,
            earlier=["is litecoin eligible?", "Here is what the review recorded."],
        )
    found = [item.symbol for item in evidence.asked_about]
    assert found[0] == "SOL", f"the coin just asked about came second: {found}"


async def test_an_old_miss_is_not_reported_again(test_context):
    """A coin nobody has mentioned for three turns should stop being announced."""

    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="and litecoin?",
            view=None,
            earlier=["what about $NOTACOIN"],
        )
    assert evidence.looked_for_but_not_listed == []
    assert [item.symbol for item in evidence.asked_about] == ["LTC"]


#: Ways a person marks a word as a coin symbol rather than as English.
MEANT_AS_A_TICKER = ["$NOTACOIN", "NOTACOIN", "$zzz", "NOTACOIN/USDT"]


@pytest.mark.parametrize("written", MEANT_AS_A_TICKER, ids=lambda item: item[:20])
async def test_a_coin_that_really_is_not_here_is_still_reported_missing(
    test_context, written: str
):
    """The other half of the rule. Being careful about "no" must not remove it.

    This could not fire at all before: the words were lowered before anything looked at
    them, so a leading ``$`` and the capitals were both gone by the time the check ran.
    Hilal therefore never received "this coin is not here" — only an empty result, from
    which it guessed.
    """

    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message=f"is {written} eligible?", view=None
        )
    assert evidence.looked_for_but_not_listed, "a coin we do not have was not reported"
    assert not evidence.asked_about


#: Ordinary English that must never be announced as an unlisted coin.
NOT_TICKERS = [
    "is it eligible?",
    "IS IT ELIGIBLE?",
    "WHAT COINS ARE HALAL",
    "hello, can you help me",
    "what is a Passport",
]


@pytest.mark.parametrize("written", NOT_TICKERS, ids=lambda item: item[:24])
async def test_ordinary_words_are_never_announced_as_unlisted_coins(
    test_context, written: str
):
    """Keeping the capitals is what makes a ticker recognisable — and a shouted
    sentence is not a sentence full of tickers."""

    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message=written, view=None
        )
    assert evidence.looked_for_but_not_listed == [], (
        f"{written!r} produced nonsense: {evidence.looked_for_but_not_listed}"
    )


# --------------------------------------------------------------------------------
# "Here with no review" is not "not here".
# --------------------------------------------------------------------------------


async def test_a_listed_coin_without_a_review_says_so_in_words(test_context):
    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=False
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="is LTCUSDT halal?", view=None
        )
    assert [item.symbol for item in evidence.asked_about] == ["LTC"]
    said = evidence.asked_about[0].to_evidence()["what_we_have"]
    assert "on this platform" in said, said
    assert "No review is published" in said, said


async def test_a_reviewed_coin_says_a_review_exists(test_context):
    async with test_context["session_factory"]() as session:
        await _seed(
            session, symbol="LTC", name="Litecoin", pairs=("LTC/USDT",), reviewed=True
        )
        evidence = await HilalChatKnowledge(session, get_settings()).gather(
            message="is litecoin halal?", view=None
        )
    said = evidence.asked_about[0].to_evidence()["what_we_have"]
    assert "review is recorded" in said, said


# --------------------------------------------------------------------------------
# The same question twice gathers the same thing.
# --------------------------------------------------------------------------------


async def test_the_same_question_always_gathers_the_same_coins(test_context):
    """The lookup ran over an unordered set, so which coins survived the cap on how
    many may be gathered was a coin toss. The same question could be answered from
    different records on a second run."""

    async with test_context["session_factory"]() as session:
        for symbol, name in (("LTC", "Litecoin"), ("SOL", "Solana"), ("ADA", "Cardano")):
            await _seed(
                session,
                symbol=symbol,
                name=name,
                pairs=(f"{symbol}/USDT",),
                reviewed=True,
            )
        knowledge = HilalChatKnowledge(session, get_settings())
        runs = [
            [
                item.symbol
                for item in (
                    await knowledge.gather(
                        message="compare litecoin, solana and cardano", view=None
                    )
                ).asked_about
            ]
            for _ in range(4)
        ]
    assert len({tuple(run) for run in runs}) == 1, f"the lookup was not stable: {runs}"
