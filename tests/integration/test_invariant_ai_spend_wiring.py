"""The budget and the rollout are not libraries sitting next to the code — they run.

A reservation service with no callers passes every test it has and protects nothing. So
these tests drive the *real* Setup turn through the *real* service and then look in the
database: was budget taken, was it given back, does the ledger row point at the
reservation, and does a refusal leave the Builder working.

Every refusal asserted here is a **degraded** product. The draft is untouched, the
deterministic Builder still authors, and the person is told in plain words what still
works. That is the difference this whole layer exists to protect.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import (
    AIBudgetCounter,
    AIBudgetReservation,
    AIUsageEvent,
)
from ai_market_monitor.services.ai_setup_chat import SetupChatError
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import _act, _service, _user
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio

_RULE = {
    "mechanic_key": "open_to_close_percentage",
    "values": {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "15m"},
}
_TYPED = "Monitor BTC/USDT when the 15m candle rises open-to-close by at least 5%"


def _rules(chat) -> list:
    from ai_market_monitor.engine.builder_operations import condition_nodes

    return condition_nodes(load_strategy_draft_v2(chat).condition_ast)


async def _reservations(session) -> list[AIBudgetReservation]:
    rows = await session.execute(select(AIBudgetReservation))
    return list(rows.scalars().all())


async def _usage_events(session) -> list[AIUsageEvent]:
    rows = await session.execute(select(AIUsageEvent))
    return list(rows.scalars().all())


async def _counters(session) -> list[AIBudgetCounter]:
    rows = await session.execute(select(AIBudgetCounter))
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# The money is really taken, and really given back
# ---------------------------------------------------------------------------


async def test_a_real_free_text_turn_reserves_budget_and_settles_it(test_context) -> None:
    """The whole point. Before this the counters were only ever written by tests."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=_TYPED, client_message_id="spend-first-turn"
        )

        held = await _reservations(session)
        assert len(held) == 1, "one turn takes exactly one reservation"
        assert held[0].state == "settled", "and does not leave it held"
        assert held[0].outcome == "completed"

        counters = await _counters(session)
        assert counters, "the locked counter rows are written by the live path"
        for counter in counters:
            assert Decimal(str(counter.reserved_usd)) == 0, "nothing stays promised"
            assert int(counter.reserved_count) == 0


async def test_the_ledger_row_points_back_at_the_reservation(test_context) -> None:
    """Two records of the same money that cannot be joined are two records of nothing."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=_TYPED, client_message_id="spend-ledger-link"
        )

        reservation = (await _reservations(session))[0]
        events = [item for item in await _usage_events(session) if item.reservation_id]
        assert events, "the ledger row records which reservation paid for it"
        assert events[0].reservation_id == reservation.id
        assert events[0].outcome == "completed"
        assert events[0].service_tier is not None
        assert events[0].rollout_version is not None


async def test_the_same_turn_replayed_is_not_charged_twice(test_context) -> None:
    """A retried request must find its own reservation, not take a second one."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        for _ in range(2):
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="spend-replayed-turn"
            )

        assert len(await _reservations(session)) == 1


# ---------------------------------------------------------------------------
# A refusal degrades the assistant, never the product
# ---------------------------------------------------------------------------


async def test_an_exhausted_daily_budget_refuses_the_turn_and_leaves_the_builder(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    # Less allowance than one turn is allowed to reserve, so the very first turn is
    # refused rather than the tenth.
    service = _service(test_context, planner, ai_budget_user_daily_usd=0.001)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        before = load_strategy_draft_v2(chat).semantic_hash

        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="budget-exhausted-1"
            )
        assert refused.value.code == "USER_DAILY_BUDGET_EXCEEDED"
        assert planner.plan_calls == 0, "no paid call happens after a refusal"
        assert load_strategy_draft_v2(chat).semantic_hash == before, "the draft is untouched"

        # And the deterministic Builder is completely unaffected.
        await _act(service, session, chat, "select_mode", "b-mode", value="monitor")
        await _act(service, session, chat, "select_universe", "b-u", value="eligible_market")
        await _act(service, session, chat, "add_condition", "b-r", **_RULE)
        assert len(_rules(chat)) == 1


async def test_a_refusal_message_names_what_still_works(test_context) -> None:
    """A dead end is worse than a limit. The words have to point somewhere."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner(), ai_budget_global_daily_usd=0.001)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="global-budget-broke"
            )
        assert "build setups" in str(refused.value).casefold()


async def test_a_refused_turn_holds_no_budget_afterwards(test_context) -> None:
    """A refusal that leaks a reservation shrinks the allowance a little every time."""

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner(), ai_budget_user_daily_usd=0.001)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError):
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="reservation-leak-check"
            )
        assert await _reservations(session) == []


async def test_the_environment_ceiling_refuses_the_assistant_but_not_authoring(
    test_context,
) -> None:
    """The emergency brake. A runtime control must never be able to release it."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner, ai_features_disabled="planner")

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError) as refused:
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="env-ceiling-turn"
            )
        assert refused.value.code == "AI_FEATURE_DISABLED"
        assert planner.plan_calls == 0

        await _act(service, session, chat, "select_mode", "c-mode", value="monitor")
        await _act(service, session, chat, "select_universe", "c-u", value="eligible_market")
        await _act(service, session, chat, "add_condition", "c-r", **_RULE)
        assert len(_rules(chat)) == 1


async def test_a_nonsense_name_in_the_emergency_switch_does_not_break_the_request(
    test_context,
) -> None:
    """An operator typo at three in the morning must not take the application down."""

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner, ai_features_disabled="planer, , scnaner")

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await service.handle_message(
            session, chat, message=_TYPED, client_message_id="operator-typo-turn"
        )
        assert planner.plan_calls == 1, "the misspelled names disabled nothing"


# ---------------------------------------------------------------------------
# The plan's message allowance
# ---------------------------------------------------------------------------


async def test_a_plan_message_quota_is_counted_in_turns_not_in_money(test_context) -> None:
    """"Forty questions a day" is a promise a person can check. Money is not."""

    from uuid import uuid4

    from ai_market_monitor.services.ai_budget import AIBudgetService, BudgetError, BudgetLimits

    user = await _user(test_context)
    limits = BudgetLimits(
        per_turn_max_usd=Decimal("1.00"),
        user_daily_usd=Decimal("100.00"),
        max_concurrent_reservations=0,
    ).for_plan(daily_usd=Decimal("100.00"), daily_messages=2)

    async with test_context["session_factory"]() as session:
        budget = AIBudgetService(session, limits)
        for index in range(2):
            granted = await budget.reserve(
                user_id=user.id,
                idempotency_key=f"quota-{index}-{uuid4().hex}",
                feature="planner",
                model="gpt-5.4-nano",
                # Deliberately almost free. Money is nowhere near exhausted; the promise
                # that runs out is the number of messages.
                estimated_cost_usd=Decimal("0.0001"),
            )
            await budget.reconcile(
                granted.reservation_id, actual_cost_usd=Decimal("0.0001")
            )

        with pytest.raises(BudgetError) as refused:
            await budget.reserve(
                user_id=user.id,
                idempotency_key=f"quota-over-{uuid4().hex}",
                feature="planner",
                model="gpt-5.4-nano",
                estimated_cost_usd=Decimal("0.0001"),
            )
        assert refused.value.code == "PLAN_MESSAGE_QUOTA_EXCEEDED"
        assert "build setups" in refused.value.message.casefold()


async def test_a_turn_that_never_reached_the_provider_uses_no_plan_message(
    test_context,
) -> None:
    from uuid import uuid4

    from ai_market_monitor.services.ai_budget import AIBudgetService, BudgetLimits

    user = await _user(test_context)
    limits = BudgetLimits(max_concurrent_reservations=0).for_plan(daily_messages=1)

    async with test_context["session_factory"]() as session:
        budget = AIBudgetService(session, limits)
        granted = await budget.reserve(
            user_id=user.id,
            idempotency_key=f"cancel-{uuid4().hex}",
            feature="planner",
            model="gpt-5.4-nano",
            estimated_cost_usd=Decimal("0.0001"),
        )
        await budget.release(granted.reservation_id)

        # The allowance is intact, so the next question still works.
        again = await budget.reserve(
            user_id=user.id,
            idempotency_key=f"after-cancel-{uuid4().hex}",
            feature="planner",
            model="gpt-5.4-nano",
            estimated_cost_usd=Decimal("0.0001"),
        )
        assert again.reservation_id is not None


# ---------------------------------------------------------------------------
# A paid turn that then failed
# ---------------------------------------------------------------------------


async def test_a_paid_turn_rejected_afterwards_still_settles_the_money_it_spent(
    test_context,
) -> None:
    """Tokens burned before a refusal are still on the invoice."""

    from ai_market_monitor.services.ai_setup_chat import AISetupChatService
    from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
    from tests.integration.test_guided_builder import _Provider, _scanner_settings
    from tests.integration.test_setup_chat_launch_v2 import PaidRejectedAgent

    user = await _user(test_context)
    service = AISetupChatService(
        _scanner_settings(test_context["settings"]),
        _Provider(),
        RuleBasedStrategyInterpreter(),
        launch_agent=PaidRejectedAgent(),
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        with pytest.raises(SetupChatError):
            await service.handle_message(
                session, chat, message=_TYPED, client_message_id="paid-then-rejected"
            )

        held = await _reservations(session)
        assert len(held) == 1
        assert held[0].state == "settled"
        assert held[0].outcome == "provider_failed"
        assert Decimal(str(held[0].actual_cost_usd or 0)) > 0, "a failed call still cost money"

        events = [item for item in await _usage_events(session) if item.reservation_id]
        assert events and events[0].outcome == "provider_failed"
