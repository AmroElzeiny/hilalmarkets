import pytest
from sqlalchemy import func, select

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.db.models import Alert, ScanResult, SetupInstance, UsageRecord, User
from ai_market_monitor.db.models.enums import ConditionType
from ai_market_monitor.schemas.on_demand import OnDemandScanRequest
from ai_market_monitor.schemas.strategy import Comparator, ConditionRule, Operand, OperandKind
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService
from tests.factories import candle_sets, load_strategy


class OnDemandProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT", "LINK/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        return candle_sets(volume_multiplier=1.6)[timeframe][-limit:]


class ConfirmedOnDemandProvider(OnDemandProvider):
    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        rows = candle_sets(volume_multiplier=1.6).get(timeframe) or candle_sets(
            volume_multiplier=1.6
        )["15m"]
        duration = rows[-1].timestamp - rows[-2].timestamp
        extended = [
            *rows,
            *[
                rows[-1].__class__(
                    timestamp=rows[-1].timestamp + (index + 1) * duration,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1000,
                    is_closed=True,
                )
                for index in range(max(0, 60 - len(rows)))
            ],
        ]
        return extended[-limit:]


class WideConfirmedOnDemandProvider(ConfirmedOnDemandProvider):
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return [f"COIN{index}/USDT" for index in range(12)]


def scan_strategy():
    strategy = load_strategy()
    risk = strategy.risk.model_copy(update={"enabled": False})
    universe = strategy.universe.model_copy(
        update={
            "include_symbols": ["SOL/USDT"],
            "min_quote_volume_24h": None,
            "min_average_candle_volume": None,
            "min_listing_age_days": None,
            "max_spread_bps": None,
        }
    )
    return strategy.model_copy(update={"universe": universe, "risk": risk})


async def test_on_demand_scan_returns_proof_without_live_alert_persistence(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="On Demand")
        session.add(user)
        await session.flush()
        strategy = scan_strategy()
        request = OnDemandScanRequest(
            strategy=strategy,
            approved_schema_hash=strategy.canonical_hash(),
            symbols=["SOL/USDT"],
            max_symbols=1,
        )

        response = await OnDemandScanService(session, OnDemandProvider()).run(user.id, request)

        assert response.status == "succeeded"
        assert response.plan_code == "demo"
        assert response.quota_limit == 1
        assert response.quota_remaining == 0
        assert response.results
        assert response.results[0].proof_receipt["on_demand_scan"] is True
        assert response.results[0].proof_receipt["live_alert_created"] is False
        assert response.results[0].proof_receipt["research_monitor"] is True
        assert response.results[0].proof_receipt["monitor_mode"] == "research"
        assert response.results[0].proof_receipt["match_status"] == "confirmed_match"
        assert response.results[0].proof_receipt["required_completion_percent"] == 100
        assert response.results[0].proof_receipt["match_rule"] == (
            "100% of required monitored conditions must pass"
        )
        assert await session.scalar(select(func.count(UsageRecord.id))) == 1
        assert await session.scalar(select(func.count(Alert.id))) == 0
        assert await session.scalar(select(func.count(ScanResult.id))) == 0
        assert await session.scalar(select(func.count(SetupInstance.id))) == 0

        with pytest.raises(OnDemandScanError) as exc:
            await OnDemandScanService(session, OnDemandProvider()).run(user.id, request)
        assert exc.value.code == "on_demand_quota_exceeded"


async def test_on_demand_scan_api_enforces_user_and_quota(test_context):
    provider = OnDemandProvider()
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: provider
    async with test_context["session_factory"]() as session:
        user = User(display_name="On Demand API")
        session.add(user)
        await session.commit()
        strategy = scan_strategy()

    payload = {
        "strategy": strategy.model_dump(mode="json"),
        "approved_schema_hash": strategy.canonical_hash(),
        "symbols": ["SOL/USDT"],
        "max_symbols": 1,
    }
    first = await test_context["client"].post(
        "/api/v1/on-demand-scans",
        json=payload,
        headers={"X-User-ID": str(user.id)},
    )
    assert first.status_code == 201
    assert first.json()["results"][0]["proof_receipt"]["on_demand_scan"] is True

    second = await test_context["client"].post(
        "/api/v1/on-demand-scans",
        json=payload,
        headers={"X-User-ID": str(user.id)},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "on_demand_quota_exceeded"


async def test_light_scan_succeeds_without_saved_strategy_or_approval(test_context):
    provider = ConfirmedOnDemandProvider()
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: provider
    async with test_context["session_factory"]() as session:
        user = User(display_name="Quick Scan")
        session.add(user)
        await session.commit()

    response = await test_context["client"].post(
        "/api/v1/dashboard/light-scan",
        headers={"X-User-ID": str(user.id)},
        json={
            "prompt": "Bring me symbols with price above 50 dollars",
            "exchange": "binance",
            "quote_currency": "USDT",
            "timeframe": "15m",
            "symbols": ["SOL/USDT"],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["light_scan"] is True
    assert payload["scan"]["plan_code"] == "demo"
    assert payload["scan"]["quota_limit"] == 3
    assert payload["scan"]["results"][0]["proof_receipt"]["light_scan"] is True
    assert payload["scan"]["results"][0]["proof_receipt"]["scan_mode"] == "light_prompt"
    async with test_context["session_factory"]() as session:
        usage = await session.scalar(select(UsageRecord))
        assert usage.metric == "light_prompt_scans"
        assert usage.metadata_json["scan_mode"] == "light_prompt"


async def test_light_scan_blank_symbols_returns_broad_universe_matches_by_default(
    test_context,
):
    provider = WideConfirmedOnDemandProvider()
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: provider
    async with test_context["session_factory"]() as session:
        user = User(display_name="Quick Scan Broad Universe")
        session.add(user)
        await session.commit()

    response = await test_context["client"].post(
        "/api/v1/dashboard/light-scan",
        headers={"X-User-ID": str(user.id)},
        json={
            "prompt": "Bring me symbols with price above 50 dollars",
            "exchange": "binance",
            "quote_currency": "USDT",
            "timeframe": "15m",
            "symbols": [],
        },
    )

    assert response.status_code == 201, response.text
    scan = response.json()["scan"]
    assert scan["symbols_requested"] == 12
    assert scan["symbols_scanned"] == 12
    assert len(scan["results"]) == 12


async def test_light_scan_accepts_broad_universe_and_fast_timeframes(test_context):
    provider = ConfirmedOnDemandProvider()
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: provider
    async with test_context["session_factory"]() as session:
        user = User(display_name="Quick Scan Broad Fast")
        session.add(user)
        await session.commit()

    broad = await test_context["client"].post(
        "/api/v1/dashboard/light-scan",
        headers={"X-User-ID": str(user.id)},
        json={
            "prompt": "Bring me symbols with price above 50 dollars",
            "timeframe": "15m",
            "symbols": [f"COIN{i}/USDT" for i in range(51)],
        },
    )
    assert broad.status_code == 201, broad.text
    assert broad.json()["scan"]["symbols_requested"] == 51

    fast = await test_context["client"].post(
        "/api/v1/dashboard/light-scan",
        headers={"X-User-ID": str(user.id)},
        json={
            "prompt": "Bring me symbols with price above 50 dollars",
            "timeframe": "1m",
            "symbols": ["SOL/USDT"],
        },
    )
    assert fast.status_code == 201, fast.text
    assert fast.json()["scan"]["results"]

    unsupported = await test_context["client"].post(
        "/api/v1/dashboard/light-scan",
        headers={"X-User-ID": str(user.id)},
        json={
            "prompt": "Find vibes-based influencer coins before anyone talks about them",
            "timeframe": "15m",
            "symbols": ["SOL/USDT"],
        },
    )
    assert unsupported.status_code == 409
    assert unsupported.json()["detail"]["code"] == "clarification_required"


async def test_optional_failed_or_unavailable_conditions_do_not_block_confirmation(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Optional Conditions")
        session.add(user)
        await session.flush()
        strategy = scan_strategy().model_copy(deep=True)
        strategy.conditions.children.append(
            ConditionRule(
                key="optional_unsupported_indicator",
                label="Optional unsupported indicator",
                condition_type=ConditionType.INDICATOR,
                timeframe="15m",
                left=Operand(kind=OperandKind.INDICATOR, name="unknown_indicator"),
                comparator=Comparator.GREATER_THAN_OR_EQUAL,
                right=Operand(kind=OperandKind.CONSTANT, value=1),
                required=False,
                weight=0.25,
            )
        )
        strategy.conditions.children.append(
            ConditionRule(
                key="optional_unavailable_ema",
                label="Optional long warmup EMA",
                condition_type=ConditionType.INDICATOR,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.INDICATOR,
                    name="ema",
                    parameters={"period": 1000, "field": "close"},
                ),
                comparator=Comparator.GREATER_THAN_OR_EQUAL,
                right=Operand(kind=OperandKind.CONSTANT, value=1),
                required=False,
                weight=0.25,
            )
        )
        request = OnDemandScanRequest(
            strategy=strategy,
            approved_schema_hash=strategy.canonical_hash(),
            symbols=["SOL/USDT"],
            max_symbols=1,
        )

        response = await OnDemandScanService(session, OnDemandProvider()).run(user.id, request)

        result = response.results[0]
        assert result.outcome == "confirmed"
        proof_by_id = {
            condition["condition_id"]: condition for condition in result.proof_receipt["conditions"]
        }
        assert proof_by_id["optional_unsupported_indicator"]["blocking"] is False
        assert proof_by_id["optional_unsupported_indicator"]["state"] == "error"
        assert proof_by_id["optional_unavailable_ema"]["blocking"] is False
        assert proof_by_id["optional_unavailable_ema"]["state"] == "pending"


async def test_mandatory_unavailable_condition_is_not_returned_as_finder_match(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Mandatory Conditions")
        session.add(user)
        await session.flush()
        strategy = scan_strategy().model_copy(deep=True)
        strategy.conditions.children.append(
            ConditionRule(
                key="mandatory_long_warmup_ema",
                label="Mandatory long warmup EMA",
                condition_type=ConditionType.INDICATOR,
                timeframe="15m",
                left=Operand(
                    kind=OperandKind.INDICATOR,
                    name="ema",
                    parameters={"period": 1000, "field": "close"},
                ),
                comparator=Comparator.GREATER_THAN_OR_EQUAL,
                right=Operand(kind=OperandKind.CONSTANT, value=1),
                required=True,
                weight=1,
            )
        )
        request = OnDemandScanRequest(
            strategy=strategy,
            approved_schema_hash=strategy.canonical_hash(),
            symbols=["SOL/USDT"],
            max_symbols=1,
        )

        response = await OnDemandScanService(session, OnDemandProvider()).run(user.id, request)

        assert response.status == "succeeded"
        assert response.symbols_scanned == 1
        assert response.results == []
