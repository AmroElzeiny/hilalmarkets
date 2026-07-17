import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_market_monitor.engine.models import MarketSnapshot
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.interfaces import Candle


def load_strategy(name: str = "liquidity_sweep_continuation.json") -> StrategyDefinition:
    path = Path("samples/strategies") / name
    return StrategyDefinition.model_validate(json.loads(path.read_text()))


def methodology_rules(
    *,
    source_family: str = "qualified_test_source",
    source_adapter: str = "manual_test",
) -> dict:
    outcomes = ["pass", "qualification", "fail", "not_applicable", "needs_evidence"]
    use_decisions = [
        "covered",
        "qualified",
        "not_covered",
        "not_applicable",
        "under_review",
        "excluded",
    ]
    return {
        "schema_version": "1",
        "criteria_version": "test.criteria.1",
        "source_family": source_family,
        "source_adapter": source_adapter,
        "executable": True,
        "required_criteria": [
            {
                "key": "reviewed_evidence",
                "label": "Reviewed evidence",
                "description": "Verify the retained test evidence and exact asset identity.",
                "required": True,
                "allowed_outcomes": outcomes,
                "evidence_categories": ["reviewed_test_evidence"],
                "qualification_rules": {"written_reason_required": True},
                "blocking_outcomes": ["fail", "not_applicable", "needs_evidence"],
            }
        ],
        "use_cases": [
            {
                "key": "spot_monitoring",
                "label": "Spot market monitoring",
                "description": "Test-only spot market monitoring coverage for this asset.",
                "required": True,
                "allowed_decisions": use_decisions,
                "criterion_keys": ["reviewed_evidence"],
                "evidence_categories": ["reviewed_test_evidence"],
                "default_scope": "Spot market monitoring in isolated tests.",
                "execution_blocking_decisions": [
                    "not_covered",
                    "not_applicable",
                    "under_review",
                    "excluded",
                ],
            }
        ],
    }


def methodology_evidence_requirements() -> dict:
    return {
        "schema_version": "1",
        "mandatory_source_categories": ["reviewed_test_evidence"],
        "minimum_evidence_completeness": 1.0,
        "maximum_source_age_days": 3650,
        "critical_missing_fields": ["test_evidence.identity"],
        "contradiction_policy": "block_any_unresolved",
        "review_cadence_days": 365,
    }


def candles(
    count: int,
    *,
    start: datetime,
    minutes: int,
    close: float = 100,
    volume: float = 1000,
) -> list[Candle]:
    return [
        Candle(
            timestamp=start + timedelta(minutes=minutes * index),
            open=close - 0.2,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=volume,
            is_closed=True,
        )
        for index in range(count)
    ]


def market() -> MarketSnapshot:
    return MarketSnapshot(
        exchange="binance",
        symbol="SOL/USDT",
        base_asset="SOL",
        quote_asset="USDT",
        quote_volume_24h=5_000_000,
        average_candle_volume=2_000,
        spread_bps=5,
        listed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def candle_sets(volume_multiplier: float = 1.42, include_active_lookahead: bool = False):
    start_15m = datetime(2026, 6, 14, 0, 0, tzinfo=UTC)
    base = candles(30, start=start_15m, minutes=15, close=100, volume=1000)
    base[-1] = Candle(
        timestamp=base[-1].timestamp,
        open=100,
        high=102,
        low=98,
        close=100.5,
        volume=1000 * volume_multiplier,
        is_closed=True,
    )
    if include_active_lookahead:
        base.append(
            Candle(
                timestamp=base[-1].timestamp + timedelta(minutes=15),
                open=100,
                high=106,
                low=90,
                close=103,
                volume=3000,
                is_closed=False,
            )
        )
    start_4h = datetime(2026, 1, 1, tzinfo=UTC)
    higher = candles(220, start=start_4h, minutes=240, close=100, volume=5000)
    higher[-1] = Candle(
        timestamp=higher[-1].timestamp,
        open=100,
        high=104,
        low=99,
        close=103,
        volume=5000,
        is_closed=True,
    )
    return {"15m": base, "4h": higher}
