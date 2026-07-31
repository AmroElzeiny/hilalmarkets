from types import SimpleNamespace

import pytest

from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ShariaAssetStatus,
)
from ai_market_monitor.engine.action_grounding import action_is_grounded
from ai_market_monitor.engine.capability_contract import _parameter_value_grounded
from ai_market_monitor.engine.capability_shortlist import (
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.setup_turn_execution import _ground_sharia_policy
from ai_market_monitor.schemas.setup_agent import SegmentKind, TurnSegment
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2


@pytest.mark.parametrize(
    ("text", "value", "schema", "semantic_unit"),
    [
        ("use 5% as the threshold", 5.0, {"type": "number"}, "percent"),
        ("use a 14 candle period", 14, {"type": "integer"}, "count"),
        ("use the closing price", "close", {"type": "string"}, "plain"),
        ("make the direction نازل", "bearish", {"type": "string"}, "plain"),
        ("enable confirmation", True, {"type": "boolean"}, "plain"),
        ("disable confirmation", False, {"type": "boolean"}, "plain"),
        ("watch BTC/USDT", "BTC/USDT", {"type": "string"}, "symbol"),
        ("confirm on the hourly candle", "1h", {"type": "string"}, "timeframe"),
        (
            "watch BTC/USDT and ETH/USDT",
            ["BTC/USDT", "ETH/USDT"],
            {"type": "array", "items": {"type": "string", "x-semantic-unit": "symbol"}},
            "symbol",
        ),
        (
            "use the closing price and enable confirmation on 1h",
            {"field": "close", "enabled": True, "timeframe": "1h"},
            {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "timeframe": {
                        "type": "string",
                        "x-semantic-unit": "timeframe",
                    },
                },
                "additionalProperties": False,
            },
            "plain",
        ),
    ],
)
def test_every_trader_controlled_parameter_type_requires_source_grounding(
    text, value, schema, semantic_unit
):
    assert _parameter_value_grounded(
        text,
        value,
        schema,
        semantic_unit=semantic_unit,
    )


def test_symbol_lists_are_grounded_symbol_by_symbol():
    assert not _parameter_value_grounded(
        "watch BTC/USDT",
        ["BTC/USDT", "ETH/USDT"],
        {"type": "array", "items": {"type": "string", "x-semantic-unit": "symbol"}},
        semantic_unit="symbol",
    )


def test_capability_provider_shortlist_tracks_the_configured_adapter():
    assert "ccxt" in configured_runtime_provider_requirements("ccxt")
    assert "ccxt" not in configured_runtime_provider_requirements("memory")
    assert {
        "ohlcv",
        "market_data",
        "candles",
    }.issubset(configured_runtime_provider_requirements("memory"))


def test_every_changed_sharia_policy_field_requires_source_grounding():
    draft = StrategyDraftV2()
    policy = draft.sharia_policy.model_copy(
        update={
            "allowed_statuses": [ShariaAssetStatus.ELIGIBLE],
            "compliance_change_behavior": ComplianceChangeBehavior.NOTIFY_ONLY,
        }
    )
    operation = AuthorizedPatchOperation(
        operation_id="policy-1",
        kind="set_sharia_policy",
        authorizing_segment_id="segment-1",
        sharia_policy=policy,
    )

    def errors(text: str) -> list[str]:
        segment = TurnSegment(
            segment_id="segment-1",
            exact_source_text=text,
            start_offset=0,
            end_offset=len(text),
            kind=SegmentKind.STRATEGY_INSTRUCTION,
            action_required=True,
            confidence=1.0,
        )
        return _ground_sharia_policy(
            operation,
            segment,
            SimpleNamespace(draft=draft),
            {},
            {},
            {},
        )

    assert errors("change the policy")
    assert errors("only eligible and notify only") == []


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("استبعد ETH/USDT", "exclude"),
        ("شيل شرط RSI", "remove_condition"),
        ("خليه مطلوب", "required"),
        ("خليه اختياري", "optional"),
        ("امسح فريم التأكيد", "clear"),
        ("estab3ed ETH/USDT", "exclude"),
        ("sheel condition el RSI", "remove_condition"),
        ("5aly el condition lazem", "required"),
        ("khally el condition ekhtiyary", "optional"),
        ("ems7 confirmation timeframe", "clear"),
    ],
)
def test_semantic_action_vocabulary_accepts_arabic_egyptian_and_arabizi(text, action):
    assert action_is_grounded(text, action)
