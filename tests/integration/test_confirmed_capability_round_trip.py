from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_market_monitor.engine.builder_operations import condition_nodes
from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.strategy_compiler_v2 import compile_strategy_draft_v2
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2
from ai_market_monitor.services.ai_setup_chat import SetupChatError
from ai_market_monitor.services.interfaces import Candle
from ai_market_monitor.services.setup_chat_launch import load_strategy_draft_v2
from tests.integration.test_guided_builder import _act, _service, _user
from tests.integration.test_setup_chat_launch_v2 import StandInPlanner

pytestmark = pytest.mark.anyio


def _impulse_history() -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Candle(
            timestamp=start + timedelta(minutes=15 * index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1000,
            is_closed=True,
        )
        for index in range(20)
    ]
    rows.append(
        Candle(
            timestamp=start + timedelta(minutes=300),
            open=100,
            high=104,
            low=96,
            close=97,
            volume=1000,
            is_closed=True,
        )
    )
    return rows


async def test_builder_session_persists_impulse_pivot_and_price_action_parameters(
    test_context,
) -> None:
    user = await _user(test_context)
    service = _service(test_context, StandInPlanner())

    async with test_context["session_factory"]() as session:
        chat = await service.create_session(session, user.id)
        await _act(service, session, chat, "select_mode", "confirmed-mode", value="monitor")
        before_invalid = load_strategy_draft_v2(chat).executable_hash
        with pytest.raises(SetupChatError) as invalid:
            await _act(
                service,
                session,
                chat,
                "add_condition",
                "invalid-pivot",
                mechanic_key="capability:pivot_points",
                values={
                    "component": "not-a-pivot",
                    "comparator": "gt",
                    "threshold": 0,
                    "timeframe": "15m",
                },
            )
        assert invalid.value.code == "VALUE_NOT_OFFERED"
        assert load_strategy_draft_v2(chat).executable_hash == before_invalid

        await _act(
            service,
            session,
            chat,
            "add_condition",
            "confirmed-impulse",
            mechanic_key="capability:impulse_candle",
            values={"direction": "up", "comparator": "is_true", "timeframe": "15m"},
        )
        await _act(
            service,
            session,
            chat,
            "add_condition",
            "confirmed-pivot",
            mechanic_key="capability:pivot_points",
            values={
                "component": "r1",
                "comparator": "gt",
                "threshold": 0,
                "timeframe": "15m",
            },
        )
        await _act(
            service,
            session,
            chat,
            "add_condition",
            "confirmed-level",
            mechanic_key="capability:level_strength_score",
            values={
                "lookback": 5,
                "tolerance_percent": 0.1,
                "comparator": "gte",
                "threshold": 60,
                "timeframe": "15m",
            },
        )
        draft = load_strategy_draft_v2(chat)
        by_key = {node.capability_key: node for node in condition_nodes(draft.condition_ast)}

        await _act(
            service,
            session,
            chat,
            "update_condition",
            "confirmed-impulse-edit",
            node_id=by_key["impulse_candle"].node_id,
            mechanic_key="capability:impulse_candle",
            values={"direction": "down", "comparator": "is_true", "timeframe": "15m"},
        )
        await _act(
            service,
            session,
            chat,
            "update_condition",
            "confirmed-pivot-edit",
            node_id=by_key["pivot_points"].node_id,
            mechanic_key="capability:pivot_points",
            values={
                "component": "s2",
                "comparator": "gt",
                "threshold": 0,
                "timeframe": "15m",
            },
        )
        await _act(
            service,
            session,
            chat,
            "update_condition",
            "confirmed-level-edit",
            node_id=by_key["level_strength_score"].node_id,
            mechanic_key="capability:level_strength_score",
            values={
                "lookback": 20,
                "tolerance_percent": 1.0,
                "comparator": "gte",
                "threshold": 60,
                "timeframe": "15m",
            },
        )
        reloaded_chat = await service.owned_session(session, user.id, chat.id)
        reloaded = load_strategy_draft_v2(reloaded_chat)

    saved = {node.capability_key: node for node in condition_nodes(reloaded.condition_ast)}
    assert saved["impulse_candle"].movement_direction.value == "down"
    assert saved["pivot_points"].capability_parameters["component"] == "s2"
    assert saved["level_strength_score"].capability_parameters["lookback"] == 20
    assert saved["level_strength_score"].capability_parameters["tolerance_percent"] == 1.0

    definition = compile_strategy_draft_v2(StrategyDraftV2(condition_ast=reloaded.condition_ast))
    operands = {
        rule.capability_key: rule.left for rule in definition.conditions.children
    }
    assert operands["impulse_candle"].parameters["direction"] == "down"
    assert StrategyRuleEngine._price_action(
        operands["impulse_candle"], _impulse_history()
    )
    assert operands["pivot_points"].parameters["component"] == "s2"
    assert operands["level_strength_score"].parameters["lookback"] == 20
    assert operands["level_strength_score"].parameters["tolerance_percent"] == 1.0
