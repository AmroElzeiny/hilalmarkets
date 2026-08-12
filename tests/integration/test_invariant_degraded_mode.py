"""Whatever fails, a person can still build a setup.

This is the invariant the whole reliability effort exists for. The assistant, the
provider, the budget and the rollout config are four separate things that can break, and
each of them used to be able to take authoring down with it — because the Builder offered
only the capabilities the assistant did not need to explain, and because "the planner is
off" and "the product is off" were the same switch.

Losing the assistant is a degraded product. Losing authoring is a broken one. These tests
drive the real service and assert the difference.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from ai_market_monitor.services.ai_budget import (
    AIBudgetService,
    BudgetError,
    BudgetLimits,
)
from ai_market_monitor.services.feature_control import (
    Feature,
    FeatureControlService,
    load_rollout_config,
)
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import _act, _service, _user
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio

_RULE = {
    "mechanic_key": "open_to_close_percentage",
    "values": {"direction": "up", "comparator": "gte", "threshold": 5, "timeframe": "15m"},
}


def _rules(chat) -> list:
    draft = load_strategy_draft_v2(chat)
    from ai_market_monitor.engine.builder_operations import condition_nodes

    return condition_nodes(draft.condition_ast)


async def _build_a_setup(service, session, chat, prefix: str) -> None:
    await _act(service, session, chat, "select_mode", f"{prefix}-mode", value="monitor")
    await _act(service, session, chat, "select_universe", f"{prefix}-u", value="eligible_market")
    await _act(service, session, chat, "add_condition", f"{prefix}-r", **_RULE)


# ---------------------------------------------------------------------------
# The AI being off
# ---------------------------------------------------------------------------


async def test_a_setup_is_built_with_every_ai_surface_switched_off(test_context) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(
        test_context,
        planner,
        setup_chat_free_text_enabled=False,
        setup_chat_planner_enabled=False,
    )

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "off")

        assert len(_rules(chat)) == 1
        assert (planner.plan_calls, planner.reply_calls) == (0, 0)


async def test_the_planner_being_off_does_not_switch_authoring_off(test_context) -> None:
    """These were effectively one switch. They are two decisions and must fail apart."""

    config = load_rollout_config({"planner": {"default_enabled": False}})
    control = FeatureControlService(config)

    assert control.is_enabled(Feature.PLANNER) is False
    assert control.is_enabled(Feature.GUIDED_BUILDER) is True


async def test_a_rollout_config_that_would_disable_authoring_is_refused(
    test_context,
) -> None:
    """A malformed or hostile config degrades the assistant, never the product."""

    for payload in (
        {"guided_builder": {"default_enabled": False}},
        {"guided_builder": {"percentage": 0, "default_enabled": False}},
        "not a config at all",
    ):
        control = FeatureControlService(load_rollout_config(payload))
        assert control.is_enabled(Feature.GUIDED_BUILDER, user_id=str(uuid4())) is True


# ---------------------------------------------------------------------------
# The budget being exhausted
# ---------------------------------------------------------------------------


async def test_an_exhausted_budget_refuses_the_assistant_and_leaves_the_builder(
    test_context,
) -> None:
    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        budget = AIBudgetService(
            session,
            BudgetLimits(
                per_turn_max_usd=Decimal("1.00"),
                user_daily_usd=Decimal("0.01"),
                max_concurrent_reservations=0,
            ),
        )
        # The assistant cannot run: there is not enough allowance for one turn.
        with pytest.raises(BudgetError) as raised:
            await budget.reserve(
                user_id=user.id,
                idempotency_key=f"x{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.50"),
            )
        assert raised.value.code == "USER_DAILY_BUDGET_EXCEEDED"

        # Authoring is untouched by that, and costs nothing.
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "broke")

        assert len(_rules(chat)) == 1
        assert (planner.plan_calls, planner.reply_calls) == (0, 0)


async def test_a_refusal_tells_the_person_what_still_works(test_context) -> None:
    """A dead end is worse than a limit. The message has to name the way forward."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        budget = AIBudgetService(
            session,
            BudgetLimits(
                per_turn_max_usd=Decimal("1.00"),
                global_daily_usd=Decimal("0.01"),
                max_concurrent_reservations=0,
            ),
        )
        with pytest.raises(BudgetError) as raised:
            await budget.reserve(
                user_id=user.id,
                idempotency_key=f"y{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.50"),
            )
        assert "build setups" in raised.value.message.casefold()


# ---------------------------------------------------------------------------
# The provider being down
# ---------------------------------------------------------------------------


async def test_a_provider_outage_does_not_stop_authoring(test_context) -> None:
    """Nothing in the Builder path touches a provider, and this proves it stays that way."""

    user = await _user(test_context)
    planner = StandInPlanner(failure=RuntimeError("provider is down"))
    service = _service(test_context, planner)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "outage")

        assert len(_rules(chat)) == 1


async def test_the_circuit_being_open_does_not_stop_authoring(test_context) -> None:
    from ai_market_monitor.services.provider_reliability import CircuitBreaker, CircuitState

    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=600.0)
    await breaker.record_failure("openai")
    assert await breaker.state_for("openai") is CircuitState.OPEN

    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())
    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "circuit")

        assert len(_rules(chat)) == 1


# ---------------------------------------------------------------------------
# Existing work survives
# ---------------------------------------------------------------------------


async def test_monitoring_keeps_running_with_every_ai_surface_switched_off(
    test_context,
) -> None:
    """An alert that stops firing because the assistant is off is a broken product.

    The whole chain is driven here with every AI surface disabled: clicks in the Builder,
    the product's own compiler, then the rule engine. A future change that quietly makes
    monitoring depend on a model call fails at this test rather than in production at
    three in the morning.
    """

    from datetime import UTC, datetime, timedelta

    from ai_market_monitor.engine.evaluator import StrategyRuleEngine
    from ai_market_monitor.engine.models import MarketSnapshot
    from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
    from ai_market_monitor.services.feature_control import (
        FeatureControlService,
        rollout_config_from_settings,
    )
    from ai_market_monitor.services.interfaces import Candle

    off = {
        "ai_features_disabled": "planner,composer,free_text_ai,scanner",
        "setup_free_text_enabled": False,
        "setup_planner_enabled": False,
        "setup_composer_enabled": False,
    }
    control = FeatureControlService(
        rollout_config_from_settings(test_context["settings"].model_copy(update=off))
    )
    assert control.is_enabled(Feature.PLANNER) is False
    assert control.is_enabled(Feature.MONITOR) is True, "monitoring is a separate decision"
    assert control.is_enabled(Feature.GUIDED_BUILDER) is True

    user = await _user(test_context)
    planner = StandInPlanner()
    service = _service(test_context, planner, **off)

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "monitor-on")
        definition = compile_strategy_draft_v2(load_strategy_draft_v2(chat))

    now = datetime.now(UTC)
    candles = [
        Candle(
            timestamp=now - timedelta(minutes=15 * offset),
            open=100,
            high=112,
            low=99,
            close=110,
            volume=1000,
        )
        for offset in range(60, 0, -1)
    ]
    result = StrategyRuleEngine().evaluate(
        definition,
        MarketSnapshot(exchange="binance", symbol="BTC/USDT", quote_asset="USDT"),
        {timeframe: candles for timeframe in {definition.base_timeframe, "15m"}},
        evaluation_time=now,
        strategy_version="1",
    )

    assert result is not None, "a monitor still produces a verdict with the assistant off"
    assert (planner.plan_calls, planner.reply_calls) == (0, 0), "and pays for no model call"


async def test_a_mechanic_that_needs_a_connected_feed_is_authored_with_no_model_call(
    test_context,
) -> None:
    """A launch capability that needs a data feed is a Builder rule, not an AI-only rule.

    The Builder used to hide every capability with a ``provider_required``, which is how
    142 launch capabilities became authorable only by asking the assistant. Now the feed
    requirement is *described*: a connected feed means the rule is offered normally.
    """

    from ai_market_monitor.engine.builder_contract import capability_mechanics
    from ai_market_monitor.engine.capability_shortlist import (
        configured_runtime_provider_requirements,
    )

    connected = configured_runtime_provider_requirements(
        test_context["settings"].market_data_provider
    )
    offered = capability_mechanics(connected, frozenset())
    needs_a_feed = [item for item in offered if item.provider_requirements]

    assert needs_a_feed, "the registry does have capabilities that need a feed"
    met = [item for item in needs_a_feed if item.provider_requirements_met]
    unmet = [item for item in needs_a_feed if not item.provider_requirements_met]

    assert all(item.available for item in met), (
        "a capability whose feed is connected is offered like any other"
    )
    for item in unmet:
        assert item.unavailable_reason, "and one whose feed is missing says which feed"
        assert not item.available, "without being approvable"


async def test_a_draft_built_earlier_is_still_readable_and_editable_when_ai_is_off(
    test_context,
) -> None:
    """Disabling authoring for new work must not corrupt work already done."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = _service(test_context, StandInPlanner())
        chat = await service.create_session(session, user.id)
        await _build_a_setup(service, session, chat, "before")
        node_id = _rules(chat)[0].node_id

        # Now everything AI is switched off.
        degraded = _service(
            test_context,
            StandInPlanner(),
            setup_chat_free_text_enabled=False,
            setup_chat_planner_enabled=False,
        )
        chat = await degraded.owned_session(session, user.id, chat.id)

        await _act(
            degraded,
            session,
            chat,
            "update_condition",
            "after-edit",
            node_id=node_id,
            mechanic_key=_RULE["mechanic_key"],
            values={**_RULE["values"], "threshold": 7},
        )

        rules = _rules(chat)
        assert len(rules) == 1
        assert rules[0].threshold == 7
