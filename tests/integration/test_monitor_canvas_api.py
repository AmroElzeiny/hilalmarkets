"""The three questions the monitor canvas asks the server, through the real application.

The point of these is not that the endpoints exist. It is that they **fail closed**: a
board that cannot become a monitor is refused with a sentence a beginner can act on, and
nothing about it is quietly filled in on the way. A canvas that silently repaired a
half-finished board would produce a monitor watching something nobody drew.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    UserIdentity,
)
from tests.integration.test_dashboard_web import _signup_and_verify

COINS = "/api/v1/dashboard/monitor-canvas/coins"
FAVORITES = "/api/v1/dashboard/monitor-canvas/favorites"
ACTIVATE = "/api/v1/dashboard/monitor-canvas/activate"


def _board(**overrides):
    plan = {
        "name": "My first monitor",
        "root": {
            "kind": "group",
            "op": "and",
            "children": [
                {
                    "kind": "rule",
                    "mechanic": "price_moves_percent",
                    "values": {},
                    "required": True,
                }
            ],
        },
        "universe": {"mode": "eligible_market", "watchlist_id": None, "symbols": []},
        "alert": {"channels": ["web"], "cooldown_minutes": 15},
    }
    plan.update(overrides)
    return {"plan": plan, "in_plain_words": "Watch the screened coins."}


async def test_every_canvas_endpoint_needs_a_signed_in_person(test_context):
    client = test_context["client"]
    assert (await client.get(COINS)).status_code == 401
    assert (await client.get(FAVORITES)).status_code == 401
    assert (await client.post(ACTIVATE, json=_board())).status_code == 401


async def test_the_coin_search_answers_with_screened_coins_only(test_context):
    await _signup_and_verify(test_context, email="canvas-coins@example.com")
    answer = await test_context["client"].get(COINS, params={"q": "bit"})
    assert answer.status_code == 200
    body = answer.json()
    assert isinstance(body["items"], list)
    for item in body["items"]:
        # Every row carries the words the review already recorded. Nothing here decides
        # a status, and nothing reaches the page as a raw key.
        assert item["symbol"]
        assert item["status_label"]
        assert item["status_label"] != item["status"]


async def test_the_favorites_answer_is_scoped_to_the_signed_in_person(test_context):
    await _signup_and_verify(test_context, email="canvas-favorites@example.com")
    answer = await test_context["client"].get(FAVORITES)
    assert answer.status_code == 200
    assert answer.json() == {"items": []}


async def test_a_card_with_nothing_set_is_refused_rather_than_filled_in(test_context):
    """Fail closed. The card names a real condition, and no value on it was chosen."""

    await _signup_and_verify(test_context, email="canvas-empty-card@example.com")
    answer = await test_context["client"].post(ACTIVATE, json=_board())
    assert answer.status_code in {409, 422}
    detail = answer.json()["detail"]
    assert detail["message"]
    # A beginner has to be able to act on it, so it is a sentence, not a code.
    assert " " in detail["message"]


async def test_a_coins_choice_nobody_finished_is_refused(test_context):
    """"Coins I name myself" with no coin named is not a monitor, and never becomes one."""

    await _signup_and_verify(test_context, email="canvas-no-coins@example.com")
    answer = await test_context["client"].post(
        ACTIVATE,
        json=_board(universe={"mode": "explicit_assets", "watchlist_id": None, "symbols": []}),
    )
    assert answer.status_code in {409, 422}
    assert answer.json()["detail"]["message"]


async def test_a_favorites_list_that_is_not_yours_is_refused(test_context):
    await _signup_and_verify(test_context, email="canvas-foreign-list@example.com")
    answer = await test_context["client"].post(
        ACTIVATE,
        json=_board(
            universe={
                "mode": "approved_watchlist",
                "watchlist_id": "11111111-1111-1111-1111-111111111111",
                "symbols": [],
            }
        ),
    )
    assert answer.status_code in {409, 422}
    assert answer.json()["detail"]["message"]


async def test_a_way_of_being_told_the_platform_cannot_deliver_is_refused(test_context):
    await _signup_and_verify(test_context, email="canvas-bad-channel@example.com")
    answer = await test_context["client"].post(
        ACTIVATE,
        json=_board(alert={"channels": ["carrier_pigeon"], "cooldown_minutes": 15}),
    )
    assert answer.status_code in {409, 422}
    # Named in words, not as a code. A page cannot act on "channel_not_available".
    assert " " in answer.json()["detail"]["message"]


async def test_a_way_of_being_told_counts_only_when_it_can_reach_you(test_context):
    """The gate that decides whether a monitor may start, on a real account.

    It used to ask "is Telegram or WhatsApp connected?" and nothing else counted, so a
    monitor that said "tell me in the dashboard" was refused and the person was sent to
    connect a channel they had not chosen. Both halves are checked here: the dashboard
    always counts, and a monitor whose only channel cannot reach anybody is still
    refused — the gate was made correct, not removed.
    """

    import pytest

    from ai_market_monitor.db.models.enums import DeliveryChannel
    from ai_market_monitor.services.notification_preferences import deliverable_channels
    from ai_market_monitor.services.strategy import StrategyGateError, StrategyService

    email = "canvas-reachable@example.com"
    await _signup_and_verify(test_context, email=email)
    settings = test_context["settings"]

    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.normalized_identifier == email,
            )
        )
        reachable = await deliverable_channels(session, settings, user_id=user_id)
        # The dashboard needs no connection at all; the address was confirmed at signup;
        # neither chat app has been connected.
        assert DeliveryChannel.WEB in reachable
        assert DeliveryChannel.TELEGRAM not in reachable
        assert DeliveryChannel.WHATSAPP not in reachable

        service = StrategyService(session, settings.disclaimer_version, settings)
        definition = _minimal_definition(["telegram"])
        with pytest.raises(StrategyGateError) as refused:
            await service._assert_notification_channel(user_id, definition)
        assert refused.value.code == "notification_channel_required"

        # The same monitor, also asking for the dashboard, is allowed through.
        await service._assert_notification_channel(
            user_id, _minimal_definition(["telegram", "web"])
        )


def _minimal_definition(channels: list[str]):
    """A real compiled definition, differing only in how it asks to be told.

    Compiled from a draft rather than hand-built, so it is the same object the product
    runs on. A hand-written one would drift from the schema the first time a field moved.
    """

    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
    from ai_market_monitor.schemas.strategy import AlertPolicy
    from ai_market_monitor.schemas.strategy_draft_v2 import (
        Comparator,
        ConditionNodeType,
        ConditionNodeV2,
        FormulaKind,
        OperandV2,
        StrategyDraftV2,
    )

    leaf = ConditionNodeV2(
        node_id="leaf_1",
        node_type=ConditionNodeType.CONDITION,
        source_turn_id="turn_1",
        source_fragment="price rises at least 1%",
        formula=FormulaKind.CLOSE_TO_CLOSE_PERCENTAGE,
        operator=Comparator.GREATER_THAN_OR_EQUAL,
        threshold=1.0,
        unit="percent",
        trigger_timeframe="15m",
        operands=[
            OperandV2(
                role="measured_value",
                kind="market_metric",
                name="percentage_change",
                parameters={"formula": "close_to_close"},
            )
        ],
    )
    draft = StrategyDraftV2(
        name="Reachability",
        condition_ast=ConditionNodeV2(
            node_id="root_and",
            node_type=ConditionNodeType.AND,
            children=[leaf],
        ),
    )
    definition = compile_strategy_draft_v2(draft)
    return definition.model_copy(
        update={"alerts": AlertPolicy(channels=channels)}  # type: ignore[arg-type]
    )


async def test_the_favorites_answer_carries_the_coins_on_each_list(test_context):
    """Picking from a list needs the list's coins, or there is nothing to pick from."""

    email = "canvas-list-coins@example.com"
    await _signup_and_verify(test_context, email=email)
    async with test_context["session_factory"]() as session:
        # Found by the address that just signed up, never "the newest row": another test
        # sharing this database would make that the wrong person.
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(
                UserIdentity.normalized_identifier == email,
            )
        )
        assert user_id is not None
        watchlist = ApprovedWatchlist(user_id=user_id, name="Long term", is_default=True)
        session.add(watchlist)
        await session.flush()
        for coin in ("BTC", "ETH", "SOL"):
            session.add(
                ApprovedWatchlistAsset(
                    watchlist_id=watchlist.id,
                    canonical_asset=coin,
                    added_at=datetime.now(UTC),
                )
            )
        await session.commit()

    answer = await test_context["client"].get(FAVORITES)
    assert answer.status_code == 200
    items = answer.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Long term"
    assert items[0]["is_default"] is True
    assert items[0]["coins"] == ["BTC", "ETH", "SOL"]
    assert items[0]["empty_reason"] is None
