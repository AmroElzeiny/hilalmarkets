from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ai_market_monitor.engine.evaluator import StrategyRuleEngine
from ai_market_monitor.engine.models import ensure_aware
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.services.fixture_market_data import FixtureMarketDataProvider
from ai_market_monitor.services.market_preview import market_snapshot_from_candles

REPORT_PATH = Path("WORKER_SMOKE_REPORT.md")


async def _run() -> tuple[bool, dict[str, object]]:
    provider = FixtureMarketDataProvider()
    strategy = StrategyDefinition.model_validate(
        {
            "name": "Worker Smoke Research Monitor",
            "direction": "long",
            "base_timeframe": "15m",
            "supporting_timeframes": [],
            "trigger_mode": "candle_close",
            "universe": {
                "exchange": "binance",
                "market_type": "spot",
                "quote_currencies": ["USDT"],
                "include_symbols": ["SOL/USDT"],
                "exclude_symbols": [],
                "min_historical_candles": 50,
                "exclude_stablecoins": True,
                "exclude_leveraged_tokens": True,
            },
            "conditions": {
                "key": "smoke_required_conditions",
                "operator": "and",
                "children": [
                    {
                        "key": "price_above_50",
                        "label": "Price above 50",
                        "condition_type": "price_action",
                        "timeframe": "15m",
                        "left": {"kind": "price", "field": "close"},
                        "comparator": "gt",
                        "right": {"kind": "constant", "value": 50},
                        "required": True,
                        "weight": 1,
                        "required_data": ["ohlcv"],
                    },
                    {
                        "key": "volume_above_average",
                        "label": "Volume at least 1.5x average",
                        "condition_type": "indicator",
                        "timeframe": "15m",
                        "left": {
                            "kind": "indicator",
                            "name": "volume_ratio",
                            "parameters": {"period": 20},
                        },
                        "comparator": "gte",
                        "right": {"kind": "constant", "value": 1.5},
                        "required": True,
                        "weight": 1,
                        "required_data": ["ohlcv"],
                    },
                ],
            },
            "entry": {"calculation": "signal_close", "expires_after_candles": 3},
            "targets": [],
            "risk": {
                "enabled": False,
                "stop_method": "structure",
                "target_method": "risk_multiple",
            },
            "near_miss": {"enabled": True},
            "alerts": {
                "forming_alerts": True,
                "near_miss_threshold": 70,
                "channels": ["web"],
                "maximum_alerts_per_hour": 50,
            },
            "expiry": {"expire_after_candles": 3},
            "forward_test": {"enabled": False},
            "position_sizing": {"enabled": False},
        }
    )
    timeframes = {strategy.base_timeframe, *strategy.supporting_timeframes}
    candle_sets = {
        timeframe: await provider.fetch_ohlcv(
            strategy.universe.exchange,
            "SOL/USDT",
            timeframe,
            300,
        )
        for timeframe in timeframes
    }
    evaluated_at = ensure_aware(candle_sets[strategy.base_timeframe][-1].timestamp)
    metadata = await provider.fetch_universe_metadata(strategy.universe.exchange, ["SOL/USDT"])
    market = market_snapshot_from_candles(
        strategy,
        "SOL/USDT",
        candle_sets,
        evaluated_at,
        metadata["SOL/USDT"],
    )
    evaluation = StrategyRuleEngine().evaluate(
        strategy,
        market,
        candle_sets,
        evaluation_time=evaluated_at,
        strategy_version="smoke:fixture",
        strategy_id="smoke-user-strategy",
        strategy_version_id="smoke-version",
        strategy_version_number=1,
        market_data_provider=type(provider).__name__,
    )
    proof = evaluation.proof_receipt()
    sink_record = {
        "destination_type": "test_sink",
        "user": "worker-smoke-user",
        "strategy": strategy.name,
        "symbol": evaluation.symbol,
        "proof_reference": proof["strategy_schema_hash"],
        "delivery_status": "recorded" if evaluation.outcome.value == "confirmed" else "not_sent",
        "message_payload_shape": {
            "title": "Research match confirmed",
            "symbol": evaluation.symbol,
            "match_status": proof["match_status"],
            "required_completion_percent": proof["required_completion_percent"],
        },
    }
    passed = (
        evaluation.outcome.value == "confirmed"
        and proof["research_monitor"] is True
        and proof["required_completion_percent"] == 100
        and sink_record["delivery_status"] == "recorded"
    )
    return passed, {
        "environment": "local/test",
        "database_mode": "not used by local smoke script",
        "market_data_mode": "fixture",
        "worker_command": "celery -A ai_market_monitor.worker.app worker --loglevel=INFO",
        "strategy_name": strategy.name,
        "scan_job_reference": "inline-worker-smoke",
        "symbols_scanned": 1,
        "proof_created": bool(proof),
        "alert_sink_recorded": sink_record["delivery_status"] == "recorded",
        "outcome": evaluation.outcome.value,
        "proof": proof,
        "sink_record": sink_record,
    }


def _write_report(passed: bool, payload: dict[str, object]) -> None:
    now = datetime.now(UTC).isoformat()
    failures = [] if passed else ["Evaluator did not confirm and record the test sink payload."]
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Worker Smoke Report",
                "",
                f"- Date/time: {now}",
                "- Command run: `.venv\\Scripts\\python.exe scripts\\smoke_worker.py`",
                f"- Environment: {payload['environment']}",
                f"- Database mode: {payload['database_mode']}",
                f"- Market data mode: {payload['market_data_mode']}",
                f"- Worker command: `{payload['worker_command']}`",
                f"- Strategy created: {payload['strategy_name']}",
                f"- Scan job id/reference: {payload['scan_job_reference']}",
                f"- Symbols scanned: {payload['symbols_scanned']}",
                f"- Proof created: {'yes' if payload['proof_created'] else 'no'}",
                f"- Alert sink recorded: {'yes' if payload['alert_sink_recorded'] else 'no'}",
                f"- Result: {'PASS' if passed else 'FAIL'}",
                "",
                "## Failures",
                "",
                *(f"- {failure}" for failure in failures),
                *([] if failures else ["- None."]),
                "",
                "## Remaining Risks",
                "",
                "- This local smoke path does not require Redis, Postgres, Telegram, or Discord.",
                "- Run the documented Docker/Celery flow before staging rollout.",
                "- Fixture data is blocked in staging/production by runtime validation.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    import asyncio

    passed, payload = asyncio.run(_run())
    _write_report(passed, payload)
    print(f"{'PASS' if passed else 'FAIL'} - {REPORT_PATH}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
