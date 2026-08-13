import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import StatementError

from ai_market_monitor.api.dependencies import get_market_data_provider
from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    AlertInboxItem,
    AuditEvent,
    ChartSnapshot,
    DashboardNotification,
    DashboardPreference,
    DisclaimerAcceptance,
    EdgeHealthSnapshot,
    ReferralRelationship,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyCondition,
    StrategySuggestion,
    StrategyTemplate,
    StrategyVersion,
    StrategyVersionVerification,
    SupportRequest,
    SupportTicketMessage,
    TelegramConnection,
    TelegramConversationState,
    UserExportJob,
    UserFeedback,
    UserIdentity,
    UserStrategyPreference,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConditionOutcome,
    ConditionType,
    ConnectionStatus,
    DeliveryChannel,
    DeliveryStatus,
    IdentityProvider,
    ScanJobStatus,
    ScanOutcome,
    SetupLifecycleState,
    StrategyStatus,
    StrategyVersionStatus,
)
from tests.factories import candles, load_strategy
from tests.support.entitlements import grant_monitor_plan


class DashboardFakeMarketProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT"]

    async def fetch_ohlcv(self, exchange: str, symbol: str, timeframe: str, limit: int):
        minutes = {"1m": 1, "15m": 15, "4h": 240, "1d": 1440}.get(timeframe, 15)
        return candles(
            limit,
            start=datetime(2026, 6, 20, tzinfo=UTC),
            minutes=minutes,
            close=100,
            volume=1000,
        )

    async def fetch_ohlcv_range(self, exchange, symbol, timeframe, start, end, limit):
        minutes = {"1m": 1, "15m": 15, "4h": 240, "1d": 1440}.get(timeframe, 15)
        return candles(limit, start=start, minutes=minutes, close=100, volume=1000)

    async def fetch_universe_metadata(
        self,
        exchange,
        symbols,
        *,
        include_listing_dates=False,
    ):
        return {
            symbol: {
                "quote_volume_24h": 25_000_000,
                "spread_bps": 3,
                "listed_at": datetime(2020, 1, 1, tzinfo=UTC),
                "market_cap": 1_000_000_000,
                "relative_strength_btc": 2.5,
                "category": "layer-1",
                "data_quality_ok": True,
            }
            for symbol in symbols
        }

    async def close(self) -> None:
        return None


class DashboardUnavailableRangeProvider(DashboardFakeMarketProvider):
    async def fetch_ohlcv_range(self, exchange, symbol, timeframe, start, end, limit):
        return []


async def _signup(test_context, email: str = "dashboard-api@example.com") -> None:
    client = test_context["client"]
    response = await client.post(
        "/signup",
        data={
            "email": email,
            "display_name": "Dashboard API",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/signup/verify")
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await client.post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303
    assert verified.headers["location"].startswith("/dashboard")


async def _connect_telegram(test_context, username: str = "traceuser") -> None:
    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(UserIdentity.provider == IdentityProvider.EMAIL)
        )
        assert user_id is not None
        suffix = uuid4().hex[:8]
        session.add(
            TelegramConnection(
                user_id=user_id,
                telegram_user_id=f"tg-{suffix}",
                chat_id=f"chat-{suffix}",
                username=username,
                status=ConnectionStatus.ACTIVE,
                connected_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _grant_monitor_plan(test_context) -> None:
    await grant_monitor_plan(test_context["session_factory"])


async def _approve_verified_interpretation(
    test_context,
    strategy_id: str,
    version_id: str,
) -> None:
    workspace = await test_context["client"].get(
        f"/api/v1/dashboard/strategies/{strategy_id}/verification",
        params={"version_id": version_id},
    )
    assert workspace.status_code == 200
    for statement in workspace.json()["interpretation"]:
        if statement["resolution_status"] != "unresolved":
            continue
        resolved = await test_context["client"].post(
            f"/api/v1/dashboard/strategies/{strategy_id}/interpretation/"
            f"{statement['id']}/resolve",
            json={"action": "accept"},
        )
        assert resolved.status_code == 200
async def _accept_current_disclaimer(test_context) -> None:
    async with test_context["session_factory"]() as session:
        identity = await session.scalar(
            select(UserIdentity).where(UserIdentity.provider == IdentityProvider.EMAIL)
        )
        assert identity is not None
        session.add(
            DisclaimerAcceptance(
                user_id=identity.user_id,
                identity_id=identity.id,
                disclaimer_version=test_context["settings"].disclaimer_version,
                acceptance_source="dashboard_test",
                accepted_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _approve_strategy_version(
    test_context,
    strategy_id: str,
    version_id: str,
    schema_hash: str,
) -> None:
    approved = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/approve",
        json={
            "strategy_version_id": version_id,
            "expected_schema_hash": schema_hash,
        },
    )
    assert approved.status_code == 200, approved.text


async def test_dashboard_api_uses_session_cookie_for_current_user(test_context):
    await _signup(test_context)

    response = await test_context["client"].get("/api/v1/dashboard/current-user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "dashboard-api@example.com"
    assert payload["role"] == "user"


async def test_dashboard_capabilities_endpoint_exposes_registry_and_templates(test_context):
    await _signup(test_context, "dashboard-capabilities@example.com")

    response = await test_context["client"].get("/api/v1/dashboard/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["total"] == 502
    assert payload["counts"]["executable"] == 502
    assert payload["counts"]["recognized_not_executable"] == 0
    assert len(payload["builtin_templates"]) >= 20
    assert any(item["key"] == "time_window" for item in payload["items"])
    assert payload["schema_version"] == "2.0"
    assert any(item["key"] == "ichimoku_cloud" for item in payload["items"])
    assert any(item["key"] == "within_last" for item in payload["logic_operators"])
    assert all("condition_template" in item for item in payload["items"])
    assert any(
        template["key"] == "six_month_high_breakout" for template in payload["builtin_templates"]
    )


async def test_dashboard_strategy_builder_interpretation_feedback_is_audited(test_context):
    await _signup(test_context, "dashboard-builder-feedback@example.com")

    response = await test_context["client"].post(
        "/api/v1/dashboard/strategies/interpret/feedback",
        json={
            "feedback_type": "missed_condition",
            "raw_prompt": "Find RSI below 30 and volume above average.",
            "prompt_coverage_report": {"coverage_score": 80, "confidence_score": 75},
            "strategy": {"conditions": {"children": [{"key": "rsi"}]}},
        },
    )

    assert response.status_code == 201
    async with test_context["session_factory"]() as session:
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "strategy_builder.interpretation_feedback"
            )
        )
        assert event is not None
        assert event.metadata_redacted["feedback_type"] == "missed_condition"
        assert event.metadata_redacted["coverage_score"] == 80

    response = await test_context["client"].post(
        "/api/v1/dashboard/strategies/interpret/feedback",
        json={
            "feedback_type": "unnecessary_question",
            "raw_prompt": "RSI below 30 on 15m.",
            "prompt_coverage_report": {"coverage_score": 100},
            "strategy": {"conditions": {"children": [{"key": "rsi"}]}},
        },
    )
    assert response.status_code == 201


async def test_dashboard_analytics_coverage_uses_scan_jobs(test_context):
    await _signup(test_context, "dashboard-coverage@example.com")
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        user = await session.scalar(select(User))
        strategy = Strategy(user_id=user.id, name="Coverage monitor")
        session.add(strategy)
        await session.flush()
        definition = load_strategy().model_dump(mode="json")
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="template",
            source_text="coverage test",
            schema_json=definition,
            schema_hash="coverage-test",
        )
        session.add(version)
        await session.flush()
        job = ScanJob(
            strategy_version_id=version.id,
            idempotency_key="coverage-test-job",
            status=ScanJobStatus.SUCCEEDED,
            scheduled_for=datetime(2026, 6, 23, tzinfo=UTC),
            completed_at=datetime(2026, 6, 23, 0, 1, tzinfo=UTC),
            symbols_planned=10,
            symbols_scanned=8,
        )
        session.add(job)
        await session.flush()
        for index in range(8):
            session.add(
                ScanResult(
                    scan_job_id=job.id,
                    strategy_version_id=version.id,
                    exchange="binance",
                    symbol=f"COIN{index}/USDT",
                    timeframe="15m",
                    direction="long",
                    outcome=ScanOutcome.FORMING,
                    completion_score=80,
                    candle_closed_at=datetime(2026, 6, 23, tzinfo=UTC),
                    evaluated_at=datetime(2026, 6, 23, 0, 1, tzinfo=UTC),
                    data_freshness_ms=500,
                    is_candle_complete=True,
                    proof_summary={},
                )
            )
        session.add(
            ScanResult(
                scan_job_id=job.id,
                strategy_version_id=version.id,
                exchange="bybit",
                symbol="COIN0/USDT:USDT",
                timeframe="15m",
                direction="long",
                outcome=ScanOutcome.FORMING,
                completion_score=80,
                candle_closed_at=datetime(2026, 6, 23, tzinfo=UTC),
                evaluated_at=datetime(2026, 6, 23, 0, 1, tzinfo=UTC),
                data_freshness_ms=500,
                is_candle_complete=True,
                proof_summary={},
            )
        )
        await session.commit()

    response = await test_context["client"].get("/api/v1/dashboard/analytics/coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbols_eligible"] == 10
    assert payload["symbols_scanned"] == 8
    assert payload["coverage_percentage"] == 80
    assert payload["deterministic"] is True

    dashboard = await test_context["client"].get("/dashboard")
    assert dashboard.status_code == 200
    assert "Coverage score" not in dashboard.text
    assert "Eligible screened assets" in dashboard.text


async def test_dashboard_strategy_template_is_persisted(test_context):
    await _signup(test_context, "dashboard-template@example.com")
    definition = load_strategy().model_dump(mode="json")

    created_strategy = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "dashboard test"},
    )
    assert created_strategy.status_code == 201
    strategy_id = created_strategy.json()["strategy"]["id"]

    template = await test_context["client"].post(
        "/api/v1/dashboard/templates",
        json={
            "name": "Dashboard Template",
            "category": "price_action",
            "tags": ["test"],
            "definition": definition,
            "source_strategy_id": strategy_id,
        },
    )
    assert template.status_code == 201

    async with test_context["session_factory"]() as session:
        template_id = UUID(template.json()["template"]["id"])
        assert (await session.get(StrategyTemplate, template_id)) is not None


async def test_dashboard_chart_candles_use_provider_dependency(test_context):
    await _signup(test_context, "dashboard-chart@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )

    response = await test_context["client"].get(
        "/api/v1/dashboard/charts/candles?exchange=binance&symbol=SOL/USDT&timeframe=15m&limit=12"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "SOL/USDT"
    assert len(payload["items"]) == 12
    assert payload["items"][0]["is_closed"] is True


async def test_dashboard_lifecycle_cards_chart_and_saved_annotations(test_context):
    await _signup(test_context, "dashboard-lifecycles@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    detected_at = datetime(2026, 6, 20, 14, 0, tzinfo=UTC)
    confirmed_at = detected_at + timedelta(minutes=15)

    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        user = await session.scalar(select(User))
        strategy = Strategy(user_id=user.id, name="Lifecycle continuation")
        session.add(strategy)
        await session.flush()
        definition = load_strategy().model_dump(mode="json")
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="template",
            source_text="lifecycle dashboard test",
            schema_json=definition,
            schema_hash="lifecycle-dashboard-test",
        )
        session.add(version)
        await session.flush()
        passed_definition = StrategyCondition(
            strategy_version_id=version.id,
            condition_key="price_above_ema",
            label="Price above four hour EMA",
            node_type="condition",
            condition_type=ConditionType.INDICATOR,
            timeframe="15m",
            comparator="gt",
            left_operand={"kind": "price", "name": "close"},
            right_operand={"kind": "indicator", "name": "ema", "parameters": {"period": 200}},
            required_value=Decimal("100"),
            weight=Decimal("1"),
            sequence=0,
            is_required=True,
        )
        pending_definition = StrategyCondition(
            strategy_version_id=version.id,
            condition_key="volume_confirmation",
            label="Volume confirmation above average",
            node_type="condition",
            condition_type=ConditionType.INDICATOR,
            timeframe="15m",
            comparator="gte",
            left_operand={"kind": "indicator", "name": "volume_ratio"},
            right_operand={"kind": "constant", "value": 1.5},
            required_value=Decimal("1.5"),
            weight=Decimal("1"),
            sequence=1,
            is_required=True,
        )
        session.add_all([passed_definition, pending_definition])
        await session.flush()
        scan_job = ScanJob(
            strategy_version_id=version.id,
            idempotency_key="lifecycle-dashboard-job",
            status=ScanJobStatus.SUCCEEDED,
            scheduled_for=confirmed_at,
            completed_at=confirmed_at,
            symbols_planned=1,
            symbols_scanned=1,
        )
        session.add(scan_job)
        await session.flush()
        scan_result = ScanResult(
            scan_job_id=scan_job.id,
            strategy_version_id=version.id,
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            direction="long",
            outcome=ScanOutcome.CONFIRMED,
            completion_score=Decimal("85"),
            candle_closed_at=confirmed_at,
            evaluated_at=confirmed_at,
            data_freshness_ms=400,
            is_candle_complete=True,
            proof_summary={},
        )
        session.add(scan_result)
        await session.flush()
        setup = SetupInstance(
            user_id=user.id,
            strategy_version_id=version.id,
            latest_scan_result_id=scan_result.id,
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            direction="long",
            setup_key="sol-lifecycle-dashboard",
            state=SetupLifecycleState.CONFIRMED,
            completion_score=Decimal("85"),
            first_detected_at=detected_at,
            last_evaluated_at=confirmed_at,
            confirmed_at=confirmed_at,
            entry_zone_low=Decimal("99"),
            entry_zone_high=Decimal("101"),
            stop_price=Decimal("97"),
            target_price=Decimal("107"),
            target_levels=[{"price": 104}, {"price": 107}],
        )
        session.add(setup)
        await session.flush()
        session.add(
            SetupInstance(
                user_id=user.id,
                strategy_version_id=version.id,
                exchange="binance",
                symbol="ETH/USDT",
                timeframe="15m",
                direction="long",
                setup_key="expired-lifecycle-dashboard",
                state=SetupLifecycleState.EXPIRED,
                completion_score=Decimal("60"),
                first_detected_at=detected_at,
                last_evaluated_at=confirmed_at,
                closed_at=confirmed_at,
                close_reason="expired",
            )
        )
        session.add_all(
            [
                SetupLifecycleEvent(
                    setup_instance_id=setup.id,
                    from_state=None,
                    to_state=SetupLifecycleState.DETECTED,
                    reason_code="candidate_detected",
                    evidence={},
                    occurred_at=detected_at,
                ),
                SetupLifecycleEvent(
                    setup_instance_id=setup.id,
                    from_state=SetupLifecycleState.FORMING,
                    to_state=SetupLifecycleState.CONFIRMED,
                    reason_code="all_required_conditions_passed",
                    evidence={},
                    occurred_at=confirmed_at,
                ),
                SetupConditionResult(
                    setup_instance_id=setup.id,
                    scan_result_id=scan_result.id,
                    strategy_condition_id=passed_definition.id,
                    condition_key=passed_definition.condition_key,
                    outcome=ConditionOutcome.PASSED,
                    required_value={"value": 100},
                    actual_value={"value": 102},
                    distance_to_pass=Decimal("0"),
                    contribution_score=Decimal("1"),
                    candle_timestamp=confirmed_at,
                    evaluated_at=confirmed_at,
                    data_freshness_ms=400,
                ),
                SetupConditionResult(
                    setup_instance_id=setup.id,
                    scan_result_id=scan_result.id,
                    strategy_condition_id=pending_definition.id,
                    condition_key=pending_definition.condition_key,
                    outcome=ConditionOutcome.PENDING,
                    required_value={"value": 1.5},
                    actual_value={"value": 1.42},
                    distance_to_pass=Decimal("0.08"),
                    contribution_score=Decimal("0.85"),
                    candle_timestamp=confirmed_at,
                    evaluated_at=confirmed_at,
                    data_freshness_ms=400,
                ),
            ]
        )
        await session.commit()
        setup_id = setup.id

    page = await test_context["client"].get("/dashboard/opportunities")
    assert page.status_code == 200
    assert "What is closest right now?" in page.text
    assert "SOL/USDT" in page.text
    assert "ETH/USDT" in page.text
    assert "Expired" in page.text
    assert "lifecycle-chart-dialog" in page.text
    assert "Confirmed" in page.text

    default_chart = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/chart"
    )
    assert default_chart.status_code == 200
    assert default_chart.json()["setup"]["selected_timeframe"] == "1m"

    chart = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/chart?timeframe=15m"
    )
    assert chart.status_code == 200
    chart_payload = chart.json()
    assert chart_payload["candles"]
    assert chart_payload["setup"]["state"] == "confirmed"
    assert chart_payload["completed_conditions"][0]["key"] == "price_above_ema"
    assert chart_payload["missing_conditions"][0]["key"] == "volume_confirmation"
    assert any(marker["kind"] == "condition" for marker in chart_payload["markers"])
    assert all(len(marker["text"].split()) <= 5 for marker in chart_payload["markers"])

    annotations = [
        {
            "id": "trend-1",
            "type": "line",
            "time1": 1781964000,
            "price1": 99.5,
            "time2": 1781964900,
            "price2": 103.25,
            "color": "#60a5fa",
        },
        {
            "id": "note-1",
            "type": "text",
            "time1": 1781964900,
            "price1": 102.0,
            "text": "Volume still pending",
            "color": "#60a5fa",
        },
    ]
    saved = await test_context["client"].put(
        f"/api/v1/dashboard/lifecycles/{setup_id}/annotations",
        json={"timeframe": "15m", "annotations": annotations},
    )
    assert saved.status_code == 200
    assert saved.json()["annotation_count"] == 2

    restored = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/chart?timeframe=15m"
    )
    assert restored.status_code == 200
    assert restored.json()["annotations"] == annotations

    layout = await test_context["client"].put(
        f"/api/v1/dashboard/lifecycles/{setup_id}/tradingview-layout",
        json={
            "timeframe": "1m",
            "symbol": "SOL/USDT",
            "chart_id": "tv-chart-1",
            "layout_id": "tv-layout-1",
            "name": "SOL lifecycle workspace",
            "chart_data": {"content": "serialized-layout", "symbol": "BINANCE:SOLUSDT"},
        },
    )
    assert layout.status_code == 200
    assert layout.json()["chart_id"] == "tv-chart-1"
    restored_layout = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/tradingview-layout?timeframe=1m&symbol=SOL/USDT"
    )
    assert restored_layout.status_code == 200
    assert restored_layout.json()["saved"] is True
    assert restored_layout.json()["chart_data"]["content"] == "serialized-layout"
    assert restored_layout.json()["charts"][0]["id"] == "tv-chart-1"

    drawings = await test_context["client"].put(
        f"/api/v1/dashboard/lifecycles/{setup_id}/tradingview-drawings",
        json={
            "timeframe": "1m",
            "symbol": "SOL/USDT",
            "chart_id": "tv-chart-1",
            "layout_id": "tv-layout-1",
            "line_tools_state": {
                "__traceedge_type": "Map",
                "entries": [["line-1", {"tool": "trend_line"}]],
            },
        },
    )
    assert drawings.status_code == 200
    restored_drawings = await test_context["client"].get(
        f"/api/v1/dashboard/lifecycles/{setup_id}/tradingview-drawings?timeframe=1m&symbol=SOL/USDT"
    )
    assert restored_drawings.status_code == 200
    assert restored_drawings.json()["line_tools_state"]["entries"][0][0] == "line-1"

    muted = await test_context["client"].post(
        f"/api/v1/dashboard/lifecycles/{setup_id}/mute"
    )
    assert muted.status_code == 200
    muted_page = await test_context["client"].get("/dashboard/opportunities")
    assert "SOL/USDT" not in muted_page.text
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(ChartSnapshot.id))) == 2
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "lifecycle_chart.annotations_saved"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "lifecycle_chart.tradingview_layout_saved"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "lifecycle_chart.tradingview_drawings_saved"
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "setup.lifecycle_muted"
                )
            )
            == 1
        )


async def test_dashboard_scan_prompt_interpret_understands_breakout(test_context):
    await _signup(test_context, "dashboard-prompt@example.com")

    response = await test_context["client"].post(
        "/api/v1/dashboard/scan-now/interpret",
        json={
            "prompt": "prices breaking all time high in the last 6 months",
            "exchange": "binance",
            "quote_currency": "USDT",
            "timeframe": "15m",
            "symbols": [],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["activation_blocked"] is False
    assert payload["strategy"]["conditions"]["children"][0]["left"]["name"] == "higher_high"
    assert "All eligible USDT spot pairs" in payload["understanding"]["pair_universe"]
    assert payload["understanding"]["risk"]["enabled"] is False
    assert payload["strategy"]["risk"]["enabled"] is False


async def test_dashboard_settings_persist_alert_schedule_without_theme_field(test_context):
    await _signup(test_context, "dashboard-settings@example.com")

    response = await test_context["client"].post(
        "/dashboard/settings",
        data={
            "timezone": "Europe/Moscow",
            "near_miss_enabled": "true",
            "near_miss_threshold": "82",
            "maximum_alerts_per_hour": "7",
            "maximum_alerts_per_day": "120",
            "alert_channels": ["telegram"],
            "providers": ["binance", "bybit"],
            "alert_days": ["Monday", "Friday"],
            "alert_hours": ["09:00", "21:00"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(select(DashboardPreference))
        assert preference is not None
        assert preference.theme == "dark"
        assert preference.default_timezone == "Europe/Moscow"
        assert preference.notification_preferences["near_miss_enabled"] is True
        assert preference.notification_preferences["near_miss_threshold"] == 82
        assert preference.notification_preferences["maximum_alerts_per_hour"] == 7
        assert preference.notification_preferences["maximum_alerts_per_day"] == 120
        assert preference.notification_preferences["alert_channels"] == ["web", "telegram"]
        assert preference.notification_preferences["channels"] == ["web", "telegram"]
        assert preference.notification_preferences["providers"] == ["binance", "bybit"]
        assert preference.notification_preferences["alert_days"] == ["Monday", "Friday"]
        assert preference.notification_preferences["alert_hours"] == ["09:00", "21:00"]


async def test_dashboard_settings_allow_external_channels_to_be_deselected(test_context):
    await _signup(test_context, "dashboard-in-app-only@example.com")

    response = await test_context["client"].post(
        "/dashboard/settings",
        data={"timezone": "UTC", "providers": ["bybit"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(select(DashboardPreference))
        assert preference is not None
        assert preference.notification_preferences["alert_channels"] == ["web"]
        assert preference.notification_preferences["providers"] == ["bybit"]


async def test_dashboard_disconnect_telegram_removes_backend_connection(test_context):
    await _signup(test_context, "dashboard-disconnect-telegram@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(UserIdentity.user_id))
        connection = TelegramConnection(
            user_id=user,
            telegram_user_id="tg-disconnect",
            chat_id="chat-disconnect",
            username="traceuser",
        )
        identity = UserIdentity(
            user_id=user,
            provider=IdentityProvider.TELEGRAM,
            provider_subject="tg-disconnect",
            display_identifier="@traceuser",
            is_verified=True,
            is_primary=False,
            profile_data={},
        )
        conversation = TelegramConversationState(
            user_id=user,
            telegram_user_id="tg-disconnect",
            chat_id="chat-disconnect",
            flow="main_menu",
            step="idle",
            state_data={},
            correlation_id="test-disconnect",
        )
        session.add_all([connection, identity, conversation])
        await session.commit()

    response = await test_context["client"].delete("/api/v1/dashboard/integrations/telegram")

    assert response.status_code == 200
    assert response.json()["telegram"] is None
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(TelegramConnection)) is None
        assert await session.scalar(
            select(UserIdentity).where(UserIdentity.provider == IdentityProvider.TELEGRAM)
        ) is None
        assert await session.scalar(select(TelegramConversationState)) is None
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "telegram.disconnected")
        )
        assert audit is not None


async def test_dashboard_theme_toggle_persists_without_full_settings_submit(test_context):
    await _signup(test_context, "dashboard-theme-toggle@example.com")

    response = await test_context["client"].put(
        "/api/v1/dashboard/preferences/theme",
        json={"theme": "light"},
    )

    assert response.status_code == 200
    assert response.json()["theme"] == "light"
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(select(DashboardPreference))
        assert preference.theme == "light"
        assert preference.notification_preferences["theme"] == "light"


async def test_dashboard_publish_marks_monitor_active(test_context):
    await _signup(test_context, "dashboard-publish@example.com")
    await _connect_telegram(test_context, "dashboardpublisher")
    await _grant_monitor_plan(test_context)
    await _accept_current_disclaimer(test_context)
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "publish test"},
    )
    assert created.status_code == 201
    payload = created.json()

    blocked = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{payload['strategy']['id']}/publish",
        json={
            "strategy_version_id": payload["version"]["id"],
            "expected_schema_hash": payload["version"]["schema_hash"],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "interpretation_approval_required"
    await _approve_verified_interpretation(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
    )
    await _approve_strategy_version(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
        payload["version"]["schema_hash"],
    )

    published = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{payload['strategy']['id']}/publish",
        json={
            "strategy_version_id": payload["version"]["id"],
            "expected_schema_hash": payload["version"]["schema_hash"],
        },
    )

    assert published.status_code == 200
    assert published.json()["strategy"]["status"] == "active"
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy.status == StrategyStatus.ACTIVE
        assert strategy.active_version_id == UUID(payload["version"]["id"])
    monitors = await test_context["client"].get("/dashboard/strategies/new#monitors")
    assert ">active<" in monitors.text
    detail = await test_context["client"].get(f"/dashboard/strategies/{payload['strategy']['id']}")
    assert "<strong>active</strong>" in detail.text

    paused = await test_context["client"].post(
        f"/dashboard/monitors/{payload['strategy']['id']}/pause",
        follow_redirects=False,
    )
    assert paused.status_code == 303
    assert "message=monitor_paused" in paused.headers["location"]
    resumed = await test_context["client"].post(
        f"/dashboard/monitors/{payload['strategy']['id']}/resume",
        follow_redirects=False,
    )
    assert resumed.status_code == 303
    assert "message=monitor_resumed" in resumed.headers["location"]
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy.status == StrategyStatus.ACTIVE


async def test_dashboard_publish_requires_notification_channel(test_context):
    await _signup(test_context, "dashboard-publish-no-channel@example.com")
    await _accept_current_disclaimer(test_context)
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "publish test"},
    )
    assert created.status_code == 201
    payload = created.json()
    await _approve_verified_interpretation(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
    )
    await _approve_strategy_version(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
        payload["version"]["schema_hash"],
    )

    published = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{payload['strategy']['id']}/publish",
        json={
            "strategy_version_id": payload["version"]["id"],
            "expected_schema_hash": payload["version"]["schema_hash"],
        },
    )

    assert published.status_code == 409
    assert published.json()["detail"] == "notification_channel_required"
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy.status != StrategyStatus.ACTIVE


async def test_standalone_interpretation_approval_is_read_only_and_exact_approval_binds_it(
    test_context,
):
    await _signup(test_context, "dashboard-single-approval@example.com")
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "single approval test"},
    )
    payload = created.json()
    strategy_id = payload["strategy"]["id"]
    version_id = payload["version"]["id"]

    compatibility = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/"
        f"{version_id}/interpretation/approve",
        json={},
    )

    assert compatibility.status_code == 200
    assert compatibility.json()["mutation_performed"] is False
    async with test_context["session_factory"]() as session:
        verification = await session.scalar(
            select(StrategyVersionVerification).where(
                StrategyVersionVerification.strategy_version_id == UUID(version_id)
            )
        )
        assert verification is not None
        assert verification.interpretation_status == "needs_review"

    await _approve_verified_interpretation(test_context, strategy_id, version_id)
    await _approve_strategy_version(
        test_context,
        strategy_id,
        version_id,
        payload["version"]["schema_hash"],
    )

    async with test_context["session_factory"]() as session:
        verification = await session.scalar(
            select(StrategyVersionVerification).where(
                StrategyVersionVerification.strategy_version_id == UUID(version_id)
            )
        )
        version = await session.get(StrategyVersion, UUID(version_id))
        assert verification is not None
        assert verification.interpretation_status == "approved"
        assert version is not None
        assert version.status == StrategyVersionStatus.APPROVED


async def test_dashboard_never_reports_draft_monitor_as_active_from_pointer_alone(
    test_context,
):
    await _signup(test_context, "dashboard-no-fake-active@example.com")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": load_strategy().model_dump(mode="json"),
            "source_text": "status truth test",
        },
    )
    payload = created.json()
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy is not None
        strategy.active_version_id = UUID(payload["version"]["id"])
        strategy.status = StrategyStatus.DRAFT
        await session.commit()

    listed = await test_context["client"].get("/api/v1/dashboard/strategies")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "draft"


async def test_activation_blocks_when_runtime_provider_is_disabled(test_context):
    await _signup(test_context, "dashboard-provider-disabled@example.com")
    await _connect_telegram(test_context, "providerdisabled")
    await _grant_monitor_plan(test_context)
    await _accept_current_disclaimer(test_context)
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": load_strategy().model_dump(mode="json"),
            "source_text": "provider activation gate",
        },
    )
    payload = created.json()
    await _approve_verified_interpretation(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
    )
    await _approve_strategy_version(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
        payload["version"]["schema_hash"],
    )
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(select(DashboardPreference))
        assert preference is not None
        preference.notification_preferences = {
            **preference.notification_preferences,
            "providers": ["bybit"],
        }
        await session.commit()

    published = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{payload['strategy']['id']}/publish",
        json={
            "strategy_version_id": payload["version"]["id"],
            "expected_schema_hash": payload["version"]["schema_hash"],
        },
    )

    assert published.status_code == 409
    assert published.json()["detail"] == "provider_disabled"
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy is not None
        assert strategy.status != StrategyStatus.ACTIVE


async def test_resume_rechecks_provider_gate_and_remains_paused(test_context):
    await _signup(test_context, "dashboard-resume-provider@example.com")
    await _connect_telegram(test_context, "resumeprovider")
    await _grant_monitor_plan(test_context)
    await _accept_current_disclaimer(test_context)
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": load_strategy().model_dump(mode="json"),
            "source_text": "provider resume gate",
        },
    )
    payload = created.json()
    await _approve_verified_interpretation(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
    )
    await _approve_strategy_version(
        test_context,
        payload["strategy"]["id"],
        payload["version"]["id"],
        payload["version"]["schema_hash"],
    )
    published = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{payload['strategy']['id']}/publish",
        json={
            "strategy_version_id": payload["version"]["id"],
            "expected_schema_hash": payload["version"]["schema_hash"],
        },
    )
    assert published.status_code == 200
    paused = await test_context["client"].post(
        f"/dashboard/monitors/{payload['strategy']['id']}/pause",
        follow_redirects=False,
    )
    assert paused.status_code == 303
    async with test_context["session_factory"]() as session:
        preference = await session.scalar(select(DashboardPreference))
        assert preference is not None
        preference.notification_preferences = {
            **preference.notification_preferences,
            "providers": ["bybit"],
        }
        await session.commit()

    resumed = await test_context["client"].post(
        f"/dashboard/monitors/{payload['strategy']['id']}/resume",
        follow_redirects=False,
    )

    assert resumed.status_code == 303
    assert "error=provider_disabled" in resumed.headers["location"]
    async with test_context["session_factory"]() as session:
        strategy = await session.get(Strategy, UUID(payload["strategy"]["id"]))
        assert strategy is not None
        assert strategy.status == StrategyStatus.PAUSED


async def test_dashboard_export_downloads_json_and_csv(test_context):
    await _signup(test_context, "dashboard-jobs@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    definition = load_strategy().model_dump(mode="json")
    created_strategy = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "dashboard job test"},
    )
    assert created_strategy.status_code == 201

    export = await test_context["client"].post(
        "/api/v1/dashboard/exports",
        json={"export_type": "dashboard", "format": "json", "filters": {}},
    )
    assert export.status_code == 201
    export_id = export.json()["job"]["id"]
    run_export = await test_context["client"].post(f"/api/v1/dashboard/exports/{export_id}/run")
    assert run_export.status_code == 200
    assert run_export.json()["job"]["status"] == "succeeded"
    download = await test_context["client"].get(f"/api/v1/dashboard/exports/{export_id}/download")
    assert download.status_code == 200
    assert download.json()["export_type"] == "dashboard"

    csv_export = await test_context["client"].post(
        "/api/v1/dashboard/exports",
        json={"export_type": "strategies", "format": "csv", "filters": {}},
    )
    assert csv_export.status_code == 201
    csv_id = csv_export.json()["job"]["id"]
    csv_run = await test_context["client"].post(f"/api/v1/dashboard/exports/{csv_id}/run")
    assert csv_run.status_code == 200
    csv_download = await test_context["client"].get(
        f"/api/v1/dashboard/exports/{csv_id}/download"
    )
    assert csv_download.status_code == 200
    assert csv_download.headers["content-type"].startswith("text/csv")
    assert "record_type,id,name,status,timestamp,data" in csv_download.text

    async with test_context["session_factory"]() as session:
        assert await session.get(UserExportJob, UUID(export_id)) is not None


async def test_setup_replay_and_near_miss_dashboard_sections_are_hidden(test_context):
    await _signup(test_context, "dashboard-hidden-sections@example.com")

    page = await test_context["client"].get("/dashboard/setup-replay")
    radar = await test_context["client"].get("/dashboard/near-miss")
    create = await test_context["client"].post("/api/v1/dashboard/setup-replay", json={})
    listing = await test_context["client"].get("/api/v1/dashboard/setup-replay")
    chart = await test_context["client"].get(
        "/api/v1/dashboard/charts/replay/00000000-0000-0000-0000-000000000000"
    )

    assert page.status_code == 404
    assert radar.status_code == 404
    assert create.status_code == 404
    assert listing.status_code == 404
    assert chart.status_code == 404
    openapi = await test_context["client"].get("/openapi.json")
    assert not any("setup-replay" in path for path in openapi.json().get("paths", {}))
    dashboard = await test_context["client"].get("/dashboard")
    assert "Setup Replay" not in dashboard.text
    assert "Near-Miss Radar" not in dashboard.text


async def test_dashboard_web_notifications_deliver_pending_web_alerts(test_context):
    await _signup(test_context, "dashboard-web-notification@example.com")
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        user = await session.scalar(select(User))
        alert = Alert(
            user_id=user.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key="dashboard-web-alert",
            title="SOL/USDT confirmed",
            body="Deterministic proof attached.",
            proof_receipt={"symbol": "SOL/USDT", "setup_completion_score": 100},
            candle_timestamp=datetime.now(UTC),
        )
        session.add(alert)
        await session.flush()
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.WEB,
            destination_key=f"dashboard:{user.id}",
            status=DeliveryStatus.PENDING,
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    response = await test_context["client"].get("/api/v1/dashboard/notifications/web")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["symbol"] == "SOL/USDT"
    assert item["completion_rate"] == 100
    async with test_context["session_factory"]() as session:
        delivery = await session.get(AlertDelivery, delivery_id)
        assert delivery.status == DeliveryStatus.DELIVERED


async def test_notification_center_is_user_scoped_and_persists_read_state(test_context):
    await _signup(test_context, "notification-center@example.com")
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        user = await session.scalar(select(User))
        session.add(
            DashboardNotification(
                user_id=user.id,
                level="info",
                title="Provider recovered",
                body="Market checks are current again.",
                action_url="/dashboard/activity",
                created_at=datetime.now(UTC),
            )
        )
        alert = Alert(
            user_id=user.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key="notification-center-confirmed",
            title="BTC/USDT confirmed",
            body="Deterministic proof attached.",
            proof_receipt={"symbol": "BTC/USDT", "setup_completion_score": 100},
            candle_timestamp=datetime.now(UTC),
        )
        session.add(alert)
        await session.flush()
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.WEB,
            destination_key=f"dashboard:{user.id}",
            status=DeliveryStatus.PENDING,
        )
        session.add(delivery)
        await session.commit()
        user_id = user.id
        delivery_id = delivery.id

    center = await test_context["client"].get("/api/v1/dashboard/notifications/center")
    assert center.status_code == 200
    payload = center.json()
    assert payload["unread_count"] == 2
    assert {item["kind"] for item in payload["items"]} >= {"system", "alert"}

    missing_csrf = await test_context["client"].post(
        "/api/v1/dashboard/notifications/center/read"
    )
    assert missing_csrf.status_code == 403
    marked = await test_context["client"].post(
        "/api/v1/dashboard/notifications/center/read",
        headers={"X-CSRF-Token": csrf_token(test_context["settings"], user_id)},
    )
    assert marked.status_code == 200
    assert marked.json() == {"dashboard_notifications": 1, "alert_deliveries": 1}

    async with test_context["session_factory"]() as session:
        notification = await session.scalar(select(DashboardNotification))
        delivery = await session.get(AlertDelivery, delivery_id)
        assert notification.read_at is not None
        assert delivery.status == DeliveryStatus.DELIVERED


async def test_historical_replay_is_hidden_and_inaccessible(test_context):
    await _signup(test_context, "dashboard-backtest-disabled@example.com")

    page = await test_context["client"].get("/dashboard/backtests")
    create = await test_context["client"].post("/api/v1/dashboard/backtests", json={})
    listing = await test_context["client"].get("/api/v1/dashboard/backtests")
    chart = await test_context["client"].get(
        "/api/v1/dashboard/charts/backtest/00000000-0000-0000-0000-000000000000"
    )

    assert page.status_code == 404
    assert create.status_code == 404
    assert listing.status_code == 404
    assert chart.status_code == 404
    openapi = await test_context["client"].get("/openapi.json")
    assert not any(
        "backtest" in path for path in openapi.json().get("paths", {})
    )
    dashboard = await test_context["client"].get("/dashboard")
    assert "Historical Replay" not in dashboard.text
    assert "/dashboard/backtests" not in dashboard.text


async def test_dashboard_support_ticket_api_creates_thread_message(test_context):
    await _signup(test_context, "dashboard-support@example.com")
    test_context["settings"].email_test_outbox.clear()

    response = await test_context["client"].post(
        "/api/v1/dashboard/support/tickets",
        json={
            "email": "dashboard-support@example.com",
            "subject": "Missing SOL alert",
            "description": "Please investigate the 15m candle.",
            "context": {"symbol": "SOL/USDT"},
            "screenshots": [
                {
                    "filename": "chart.png",
                    "content_type": "image/png",
                    "data_base64": base64.b64encode(
                        b"\x89PNG\r\n\x1a\nsupport-image"
                    ).decode(),
                }
            ],
        },
    )

    assert response.status_code == 201
    async with test_context["session_factory"]() as session:
        ticket_id = UUID(response.json()["ticket"]["id"])
        ticket = await session.get(SupportRequest, ticket_id)
        assert ticket is not None
        assert ticket.category == "general"
        assert ticket.context["contact_email"] == "dashboard-support@example.com"
        assert ticket.context["screenshot_count"] == 1
        message = await session.scalar(
            select(SupportTicketMessage).where(
                SupportTicketMessage.support_request_id == ticket_id
            )
        )
        assert message.attachments[0]["content_type"] == "image/png"
    outbox = test_context["settings"].email_test_outbox
    assert len(outbox) == 1
    assert outbox[0]["purpose"] == "support_ticket"
    assert outbox[0]["recipient"] == test_context["settings"].support_inbox_email
    assert outbox[0]["subject"] == "HilalMarkets support ticket: Missing SOL alert"
    assert "dashboard-support@example.com" in outbox[0]["body"]
    assert "Please investigate the 15m candle." in outbox[0]["body"]
    assert outbox[0]["attachments"][0]["filename"] == "screenshot-1.png"
    assert outbox[0]["attachments"][0]["content_type"] == "image/png"


async def test_referral_page_shows_paid_conversion_reward_balance(test_context):
    await _signup(test_context, "referrer@example.com")
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        referrer = await session.scalar(select(User))
        referred = User(display_name="Referred trader")
        session.add(referred)
        await session.flush()
        session.add(
            ReferralRelationship(
                referrer_user_id=referrer.id,
                referred_user_id=referred.id,
                status="paid_converted",
                reward_status="eligible_after_first_paid_month",
                metadata_json={"reward_amount_usd": "12.50"},
            )
        )
        await session.commit()

    response = await test_context["client"].get("/dashboard/referrals")
    assert response.status_code == 200
    assert "Rewards require paid conversion" in response.text
    assert "$12.50" in response.text


async def test_advanced_dashboard_pages_render(test_context):
    await _signup(test_context, "dashboard-pages@example.com")

    for path, expected in [
        ("/dashboard/strategies/new", "Market Assistant"),
        ("/dashboard/strategies/new", "AI Sheet"),
        ("/dashboard/strategies/new", "Preview mechanics"),
        ("/dashboard/strategies/new", "Visual Strategy Canvas"),
        ("/dashboard/strategies/new", "Search condition library"),
        ("/dashboard/strategies/new", "Monitor Overview"),
        ("/dashboard/strategies/new", "Proof &amp; Review"),
        ("/dashboard/strategies/new", "Six-Month High Breakout"),
        ("/dashboard/integrations", "Notifications"),
        ("/dashboard/opportunities", "What is closest right now?"),
        ("/dashboard/settings", "America/New_York"),
        ("/dashboard/settings", 'data-schedule-options="hours"'),
    ]:
        response = await test_context["client"].get(path)
        assert response.status_code == 200
        assert expected in response.text

    dashboard = await test_context["client"].get("/dashboard")
    assert "Alerts & Proof" not in dashboard.text
    assert "Latest Setups" not in dashboard.text
    assert "Strategy Cockpit" not in dashboard.text
    assert "Coverage score" not in dashboard.text
    # "Watchlist" is the Favorites list; the thing being created here is a Watchlist.
    # `scripts/check_release_invariants.py` enforces that vocabulary.
    assert "Create your first Watchlist" in dashboard.text
    assert "data-open-sidebar" in dashboard.text
    assert "data-close-sidebar" in dashboard.text
    assert "sidebar-create-quick" in dashboard.text
    assert 'action="/logout"' in dashboard.text
    assert "data-theme-toggle" not in dashboard.text
    assert "What is forming now" in dashboard.text
    assert "Latest alert proof" in dashboard.text
    assert "Notification channels" in dashboard.text
    assert "Screening policy" not in dashboard.text
    assert "analytics-coverage-panel" not in dashboard.text
    assert "Import or clone" not in dashboard.text
    assert "Open Setup Replay" not in dashboard.text
    assert "Near-Miss Radar" not in dashboard.text

    removed_latest = await test_context["client"].get("/dashboard/setups")
    assert removed_latest.status_code == 404
    removed_cockpit = await test_context["client"].get("/dashboard/cockpit")
    assert removed_cockpit.status_code == 404

    landing = await test_context["client"].get("/")
    assert "data-theme-toggle" not in landing.text
    assert "theme.js" not in landing.text


async def test_strategy_cockpit_validation_forecast_suggestion_and_preferences(test_context):
    await _signup(test_context, "cockpit-flow@example.com")
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "cockpit flow"},
    )
    assert created.status_code == 201
    strategy_id = created.json()["strategy"]["id"]

    validation = await test_context["client"].post(
        "/api/v1/dashboard/cockpit/strategies/validate",
        json={"definition": definition, "strategy_id": strategy_id},
    )
    assert validation.status_code == 200
    assert validation.json()["blocking"] is False

    health = await test_context["client"].get(
        f"/api/v1/dashboard/cockpit/strategies/{strategy_id}/health"
    )
    assert health.status_code == 200
    assert 0 <= health.json()["score"] <= 100
    assert "profitability" in health.json()["non_advisory_notice"]

    forecast = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/strategies/{strategy_id}/frequency-forecast"
    )
    assert forecast.status_code == 200
    assert forecast.json()["estimated_min_per_week"] <= forecast.json()[
        "estimated_max_per_week"
    ]

    suggestion = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/strategies/{strategy_id}/suggestions",
        json={"action": "make_less_noisy"},
    )
    assert suggestion.status_code == 201
    suggestion_id = suggestion.json()["id"]
    assert suggestion.json()["diff"]

    applied = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/suggestions/{suggestion_id}/apply"
    )
    assert applied.status_code == 200
    assert applied.json()["draft_version"]["status"] == "draft"
    assert "not activated" in applied.json()["message"]

    preferences = await test_context["client"].get(
        "/api/v1/dashboard/cockpit/preferences"
    )
    assert preferences.status_code == 200
    updated = await test_context["client"].put(
        "/api/v1/dashboard/cockpit/preferences",
        json={"preferences": {"preferred_entry_timeframe": "15m"}},
    )
    assert updated.json()["preferences"]["preferred_entry_timeframe"] == "15m"
    forgotten = await test_context["client"].delete(
        "/api/v1/dashboard/cockpit/preferences"
    )
    assert forgotten.json()["forgotten"] is True

    page = await test_context["client"].get("/dashboard/cockpit")
    assert page.status_code == 404
    monitors = await test_context["client"].get("/dashboard/strategies/new#monitors")
    assert monitors.status_code == 200
    assert "My Monitors" in monitors.text
    assert "Health" in monitors.text
    assert "Latency" in monitors.text
    assert "Alert Quality Inbox" not in monitors.text

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(EdgeHealthSnapshot.id))) == 1
        assert await session.scalar(select(func.count(StrategySuggestion.id))) == 1
        assert await session.scalar(select(func.count(UserStrategyPreference.id))) == 1


async def test_cockpit_feedback_inbox_proof_and_timeline(test_context):
    await _signup(test_context, "cockpit-inbox@example.com")
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        from ai_market_monitor.db.models import User

        user = await session.scalar(select(User))
        strategy = Strategy(user_id=user.id, name="Cockpit inbox monitor")
        session.add(strategy)
        await session.flush()
        definition = load_strategy().model_dump(mode="json")
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="template",
            schema_json=definition,
            schema_hash="cockpit-inbox-version",
        )
        session.add(version)
        await session.flush()
        setup = SetupInstance(
            user_id=user.id,
            strategy_version_id=version.id,
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            direction="long",
            setup_key="cockpit-inbox-setup",
            state=SetupLifecycleState.CONFIRMED,
            completion_score=Decimal("100"),
            first_detected_at=now - timedelta(minutes=30),
            last_evaluated_at=now,
            confirmed_at=now,
        )
        session.add(setup)
        await session.flush()
        session.add(
            SetupLifecycleEvent(
                setup_instance_id=setup.id,
                from_state=SetupLifecycleState.FORMING,
                to_state=SetupLifecycleState.CONFIRMED,
                reason_code="all_required_conditions_passed",
                evidence={},
                occurred_at=now,
            )
        )
        alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key="cockpit-inbox-alert",
            title="SOL/USDT confirmed",
            body="All mandatory conditions passed.",
            proof_receipt={
                "strategy_name": strategy.name,
                "strategy_version": "1",
                "symbol": "SOL/USDT",
                "timeframe": "15m",
                "setup_state": "confirmed",
                "conditions": [
                    {
                        "condition_id": "trend",
                        "name": "Trend filter",
                        "state": "passed",
                        "actual_value": 102,
                        "required_value": 100,
                    }
                ],
            },
            candle_timestamp=now,
        )
        session.add(alert)
        await session.commit()
        alert_id = alert.id
        setup_id = setup.id

    feedback = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/alerts/{alert_id}/feedback",
        json={"feedback_type": "too_early", "source": "dashboard"},
    )
    assert feedback.status_code == 201

    proof = await test_context["client"].get(
        f"/api/v1/dashboard/cockpit/alerts/{alert_id}/proof"
    )
    assert proof.status_code == 200
    assert proof.json()["proof_receipt"]["conditions"][0]["actual_value"] == 102

    timeline = await test_context["client"].get(
        f"/api/v1/dashboard/cockpit/setups/{setup_id}/timeline"
    )
    assert timeline.status_code == 200
    assert timeline.json()["timeline"][0]["state"] == "confirmed"

    inbox = await test_context["client"].get("/api/v1/dashboard/cockpit/inbox")
    assert inbox.status_code == 200
    item = next(
        row for row in inbox.json()["items"] if row["alert_id"] == str(alert_id)
    )
    reviewed = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/inbox/{item['id']}",
        json={"action": "review"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewed_at"] is not None

    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(UserFeedback.id))) == 1
        assert await session.scalar(select(func.count(AlertInboxItem.id))) >= 1


async def test_cockpit_universe_preview_and_version_experiment(test_context):
    await _signup(test_context, "cockpit-experiment@example.com")
    await _connect_telegram(test_context, "cockpitexperiment")
    await _grant_monitor_plan(test_context)
    await _accept_current_disclaimer(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "experiment base"},
    )
    strategy_id = created.json()["strategy"]["id"]
    first_version_id = created.json()["version"]["id"]
    revised_definition = load_strategy().model_copy(deep=True)
    revised_definition.alerts.cooldown_seconds = 1800
    revised = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions",
        json={
            "definition": revised_definition.model_dump(mode="json"),
            "source_text": "experiment variant",
        },
    )
    assert revised.status_code == 201
    second_version_id = revised.json()["version"]["id"]
    await _approve_verified_interpretation(
        test_context,
        strategy_id,
        second_version_id,
    )
    await _approve_strategy_version(
        test_context,
        strategy_id,
        second_version_id,
        revised_definition.canonical_hash(),
    )

    universe = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/strategies/{strategy_id}/universe-preview",
        json={
            "include_symbols": [],
            "exclude_symbols": [],
            "include_categories": ["layer-1"],
            "rank_by": "relative_strength",
        },
    )
    assert universe.status_code == 200
    assert universe.json()["summary"]["provider_symbols"] == 1
    assert universe.json()["included_symbols"] == ["SOL/USDT"]
    assert universe.json()["summary"]["deferred_filters"] == []
    assert universe.json()["included_metadata"]["SOL/USDT"]["spread_bps"] == 3

    experiment = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/strategies/{strategy_id}/experiments",
        json={
            "name": "Cooldown comparison",
            "version_ids": [first_version_id, second_version_id],
            "mode": "dry_run",
        },
    )
    assert experiment.status_code == 201
    assert experiment.json()["comparison"]["schema_diff"]

    promoted = await test_context["client"].post(
        f"/api/v1/dashboard/cockpit/experiments/{experiment.json()['id']}/promote",
        json={"version_id": second_version_id},
    )
    assert promoted.status_code == 200
    assert promoted.json()["promoted_version_id"] == second_version_id


async def test_cockpit_missed_move_analyzer_saves_deterministic_replay(test_context):
    await _signup(test_context, "cockpit-missed-move@example.com")
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "missed move monitor"},
    )
    strategy_id = created.json()["strategy"]["id"]

    response = await test_context["client"].post(
        "/api/v1/dashboard/cockpit/missed-moves",
        json={
            "strategy_id": strategy_id,
            "symbol": "SOL/USDT",
            "approximate_time": "2026-06-20T12:00:00Z",
            "direction": "long",
            "exchange": "binance",
            "timeframe": "15m",
            "target_move_threshold": 5,
            "question": "Why was there no alert before the move?",
        },
    )

    assert response.status_code == 404


async def test_prompt_interpretation_applies_saved_strategy_preferences(test_context):
    await _signup(test_context, "cockpit-preference-prompt@example.com")
    saved = await test_context["client"].put(
        "/api/v1/dashboard/cockpit/preferences",
        json={
            "preferences": {
                "preferred_trigger_mode": "intrabar",
                "preferred_alert_channels": ["web"],
                "typical_max_alerts_per_hour": 12,
            }
        },
    )
    assert saved.status_code == 200

    interpreted = await test_context["client"].post(
        "/api/v1/dashboard/scan-now/interpret",
        json={
            "prompt": "RSI crosses above 30",
            "exchange": "binance",
            "quote_currency": "USDT",
            "timeframe": "15m",
            "symbols": ["SOL/USDT"],
        },
    )

    assert interpreted.status_code == 200
    payload = interpreted.json()
    assert payload["strategy"]["trigger_mode"] == "intrabar"
    assert payload["strategy"]["alerts"]["channels"] == ["web"]
    assert payload["strategy"]["alerts"]["maximum_alerts_per_hour"] == 12
    assert payload["personal_preferences_applied"]


async def test_publish_blocks_critical_strategy_conflicts(test_context):
    await _signup(test_context, "cockpit-conflict-publish@example.com")
    await _connect_telegram(test_context, "conflictpublisher")
    definition = load_strategy().model_dump(mode="json")
    lower = definition["conditions"]["children"][1]
    lower["key"] = "volume_above_two"
    lower["left"] = {
        "kind": "market_metric",
        "name": "volume_multiplier",
        "parameters": {"period": 20},
    }
    lower["comparator"] = "gte"
    lower["right"] = {"kind": "constant", "value": 2}
    upper = dict(lower)
    upper["key"] = "volume_below_one"
    upper["label"] = "Volume below one"
    upper["comparator"] = "lte"
    upper["right"] = {"kind": "constant", "value": 1}
    definition["conditions"]["children"] = [lower, upper]
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "contradictory thresholds"},
    )
    assert created.status_code == 201

    published = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{created.json()['strategy']['id']}/publish",
        json={
            "strategy_version_id": created.json()["version"]["id"],
            "expected_schema_hash": created.json()["version"]["schema_hash"],
        },
    )

    assert published.status_code == 409
    assert published.json()["detail"] == "strategy_conflict_detected"


async def test_verified_strategy_workspace_tests_history_and_contract(test_context):
    await _signup(test_context, "verified-workflow@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": definition,
            "source_text": (
                "Watch a bullish liquidity sweep on 15m and require price above "
                "the 4h trend with strong volume."
            ),
        },
    )
    assert created.status_code == 201
    strategy_id = created.json()["strategy"]["id"]
    version = created.json()["version"]

    page = await test_context["client"].get(
        f"/dashboard/strategies/{strategy_id}/verify"
    )
    assert page.status_code == 200
    assert "What Hilal Markets understood" in page.text
    workspace = await test_context["client"].get(
        f"/api/v1/dashboard/strategies/{strategy_id}/verification",
        params={"version_id": version["id"]},
    )
    assert workspace.status_code == 200
    assert workspace.json()["interpretation"]
    assert all(item["rule_keys"] for item in workspace.json()["interpretation"])
    assert workspace.json()["activation_blockers"]

    saved = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/{version['id']}/save-draft",
        json={},
    )
    assert saved.status_code == 200
    assert saved.json()["saved"] is True
    async with test_context["session_factory"]() as session:
        draft_audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "strategy.draft_saved",
                AuditEvent.target_id == version["id"],
            )
        )
        assert draft_audit is not None

    await _approve_verified_interpretation(
        test_context,
        strategy_id,
        version["id"],
    )
    test_case = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/tests",
        params={"version_id": version["id"]},
        json={
            "title": "Flat SOL market should not confirm",
            "case_type": "negative",
            "expected_result": "should_not_trigger",
            "exchange": "binance",
            "symbol": "SOL/USDT",
            "timeframe": "15m",
            "evaluation_time": "2026-06-20T04:00:00Z",
            "notes": "A user-defined negative example.",
        },
    )
    assert test_case.status_code == 201
    assert test_case.json()["status"] in {"passed", "failed", "needs_review"}
    assert "condition_results" in test_case.json()

    historical = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/"
        f"{version['id']}/historical-validation",
        json={
            "exchange": "binance",
            "symbols": ["SOL/USDT"],
            "timeframe": "15m",
            "started_at": "2026-06-20T00:00:00Z",
            "ended_at": "2026-06-20T03:00:00Z",
        },
    )
    assert historical.status_code == 200
    summary = historical.json()["summary"]
    assert summary["evaluations"] > 0
    assert set(summary["examples_by_outcome"]) == {
        "matches",
        "near_matches",
        "invalidated",
        "non_matches",
    }
    assert summary["condition_statistics"]
    assert summary["chart"]["candles"]
    assert "not a future-performance estimate" in summary["notice"]

    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardUnavailableRangeProvider()
    )
    unavailable_history = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/"
        f"{version['id']}/historical-validation",
        json={
            "exchange": "binance",
            "symbols": ["SOL/USDT"],
            "timeframe": "15m",
            "started_at": "2025-01-01T00:00:00Z",
            "ended_at": "2025-01-01T03:00:00Z",
        },
    )
    assert unavailable_history.status_code == 200
    unavailable_summary = unavailable_history.json()["summary"]
    assert unavailable_summary["evaluations"] == 0, unavailable_history.json()
    assert unavailable_summary["unavailable_symbols"] == [
        {"symbol": "SOL/USDT", "reason": "no_candles_in_requested_window"}
    ]
    assert unavailable_summary["chart"]["candles"] == []

    approved = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/approve",
        json={
            "strategy_version_id": version["id"],
            "expected_schema_hash": version["schema_hash"],
        },
    )
    assert approved.status_code == 200
    approved_at = approved.json()["version"]["approved_at"]
    approved_again = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/approve",
        json={
            "strategy_version_id": version["id"],
            "expected_schema_hash": version["schema_hash"],
        },
    )
    assert approved_again.status_code == 200
    assert approved_again.json()["version"]["approved_at"].removesuffix("Z") == (
        approved_at.removesuffix("Z")
    )
    immutable_draft = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/{version['id']}/save-draft",
        json={},
    )
    assert immutable_draft.status_code == 409
    assert immutable_draft.json()["detail"] == "approved_version_immutable"

    contract = await test_context["client"].get(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/{version['id']}/contract"
    )
    assert contract.status_code == 200
    assert contract.json()["original_prompt"]
    assert contract.json()["structured_rules"]
    assert len(contract.json()["integrity_hash"]) == 64
    assert contract.json()["notice"].startswith("Monitoring contract only")


async def test_verified_version_diff_restore_and_saved_test_rerun(test_context):
    await _signup(test_context, "verified-versioning@example.com")
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "Sweep plus volume confirmation"},
    )
    strategy_id = created.json()["strategy"]["id"]
    first = created.json()["version"]
    await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/tests",
        params={"version_id": first["id"]},
        json={
            "title": "Saved non-match",
            "case_type": "negative",
            "expected_result": "should_not_trigger",
            "exchange": "binance",
            "symbol": "SOL/USDT",
            "timeframe": "15m",
            "evaluation_time": "2026-06-20T04:00:00Z",
        },
    )
    updated = load_strategy().model_dump(mode="json")
    updated["conditions"]["children"][2]["right"]["value"] = 1.8
    revision = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions",
        json={
            "definition": updated,
            "source_text": "Sweep plus volume confirmation at 1.8x",
        },
    )
    assert revision.status_code == 201
    second = revision.json()["version"]
    workspace = await test_context["client"].get(
        f"/api/v1/dashboard/strategies/{strategy_id}/verification",
        params={"version_id": second["id"]},
    )
    assert workspace.status_code == 200
    assert workspace.json()["version"]["parent_version_id"] == first["id"]
    assert any(
        item["path"].endswith("threshold")
        for item in workspace.json()["verification"]["semantic_diff"]
    )
    assert workspace.json()["test_cases"][0]["latest_run"] is not None

    comparison = await test_context["client"].post(
        "/api/v1/dashboard/strategies/compare",
        json={"left_version_id": first["id"], "right_version_id": second["id"]},
    )
    assert comparison.status_code == 200
    assert comparison.json()["diff"]
    assert "verification_effects" in comparison.json()
    assert comparison.json()["verification_effects"]["right"]["test_status"] in {
        "passed",
        "failed",
        "needs_review",
    }

    restored = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/{first['id']}/restore",
        json={},
    )
    assert restored.status_code == 200
    assert restored.json()["version"]["version_number"] == 3
    async with test_context["session_factory"]() as session:
        restored_row = await session.get(
            StrategyVersion,
            UUID(restored.json()["version"]["id"]),
        )
        assert restored_row.restored_from_version_id == UUID(first["id"])
        assert restored_row.approved_at is None


async def test_alert_proof_is_sealed_versioned_and_outcome_is_user_defined(test_context):
    await _signup(test_context, "verified-proof@example.com")
    # The forensic investigation at the end of this test is a Monitor-plan feature.
    await _grant_monitor_plan(test_context)
    test_context["app"].dependency_overrides[get_market_data_provider] = lambda: (
        DashboardFakeMarketProvider()
    )
    definition = load_strategy().model_dump(mode="json")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={"definition": definition, "source_text": "Proof test strategy"},
    )
    strategy_id = created.json()["strategy"]["id"]
    version_id = UUID(created.json()["version"]["id"])
    async with test_context["session_factory"]() as session:
        user_id = await session.scalar(
            select(UserIdentity.user_id).where(UserIdentity.provider == IdentityProvider.EMAIL)
        )
        alert = Alert(
            user_id=user_id,
            strategy_version_id=version_id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"verified-proof-{uuid4()}",
            title="Research match confirmed",
            body="All required conditions passed.",
            proof_receipt={
                "strategy_version_id": str(version_id),
                "symbol": "SOL/USDT",
                "conditions": [{"name": "Volume", "state": "passed"}],
            },
            candle_timestamp=datetime(2026, 6, 20, tzinfo=UTC),
        )
        session.add(alert)
        await session.commit()
        alert_id = alert.id
        assert alert.proof_hash and len(alert.proof_hash) == 64
        assert alert.proof_sealed_at is not None

    proof = await test_context["client"].get(
        f"/api/v1/dashboard/cockpit/alerts/{alert_id}/proof"
    )
    assert proof.status_code == 200
    assert proof.json()["proof_integrity"]["verified"] is True
    assert proof.json()["strategy_version"]["id"] == str(version_id)

    proof_page = await test_context["client"].get(
        f"/dashboard/alerts/{alert_id}/proof"
    )
    assert proof_page.status_code == 200
    assert "Immutable monitoring receipt" in proof_page.text
    assert "Rule-by-rule evidence" in proof_page.text
    assert "Integrity verified" in proof_page.text
    assert "View strategy version" in proof_page.text

    outcome = await test_context["client"].post(
        f"/api/v1/dashboard/alerts/{alert_id}/outcomes",
        json={
            "horizon_minutes": 240,
            "classification": "neutral",
            "classification_rules": {"definition": "User marked neutral"},
            "notes": "No automatic profit label.",
            "tags": ["reviewed"],
        },
    )
    assert outcome.status_code == 200
    assert outcome.json()["classification"] == "neutral"

    custom_outcome = await test_context["client"].post(
        f"/api/v1/dashboard/alerts/{alert_id}/outcomes",
        json={
            "horizon_minutes": 90,
            "classification": "positive",
            "classification_rules": {"definition": "User-defined 90-minute review"},
            "notes": "Custom horizon, not an automatic profit label.",
            "tags": ["custom-horizon", "reviewed"],
        },
    )
    assert custom_outcome.status_code == 200
    assert custom_outcome.json()["horizon_minutes"] == 90
    assert custom_outcome.json()["tags"] == ["custom-horizon", "reviewed"]
    assert custom_outcome.json()["price_path"]

    async with test_context["session_factory"]() as session:
        alert = await session.get(Alert, alert_id)
        alert.proof_receipt = {"tampered": True}
        with pytest.raises((StatementError, ValueError)):
            await session.commit()

    investigation = await test_context["client"].post(
        "/api/v1/dashboard/forensic-investigations",
        json={
            "strategy_id": strategy_id,
            "exchange": "binance",
            "symbol": "SOL/USDT",
            "timeframe": "15m",
            "requested_time": "2026-06-20T00:00:00Z",
        },
    )
    assert investigation.status_code == 201
    assert investigation.json()["evidence_availability"] == "system_evidence_only"
    assert investigation.json()["primary_category"] == "monitor_configuration"
    assert "No approved strategy version was active" in investigation.json()["conclusion"]
    assert investigation.json()["system_diagnostics"]["scan_found"] is False


async def test_verified_workspace_enforces_user_isolation(test_context):
    await _signup(test_context, "verified-owner@example.com")
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": load_strategy().model_dump(mode="json"),
            "source_text": "Owner-only strategy",
        },
    )
    strategy_id = created.json()["strategy"]["id"]
    version_id = created.json()["version"]["id"]
    await _signup(test_context, "verified-other@example.com")
    denied = await test_context["client"].get(
        f"/api/v1/dashboard/strategies/{strategy_id}/verification"
    )
    assert denied.status_code == 404
    page = await test_context["client"].get(f"/dashboard/strategies/{strategy_id}/verify")
    assert page.status_code == 404
    denied_save = await test_context["client"].post(
        f"/api/v1/dashboard/strategies/{strategy_id}/versions/{version_id}/save-draft",
        json={},
    )
    assert denied_save.status_code == 404


async def test_forensic_investigation_uses_historical_monitor_state(test_context):
    await _signup(test_context, "verified-forensic-state@example.com")
    await _grant_monitor_plan(test_context)
    created = await test_context["client"].post(
        "/api/v1/dashboard/strategies",
        json={
            "definition": load_strategy().model_dump(mode="json"),
            "source_text": "Monitor state forensic test",
        },
    )
    strategy_id = created.json()["strategy"]["id"]
    paused = await test_context["client"].post(
        f"/dashboard/monitors/{strategy_id}/pause",
        follow_redirects=False,
    )
    assert paused.status_code == 303

    investigation = await test_context["client"].post(
        "/api/v1/dashboard/forensic-investigations",
        json={
            "strategy_id": strategy_id,
            "exchange": "binance",
            "symbol": "SOL/USDT",
            "timeframe": "15m",
            "requested_time": datetime.now(UTC).isoformat(),
        },
    )

    assert investigation.status_code == 201
    assert investigation.json()["primary_category"] == "monitor_configuration"
    assert "paused" in investigation.json()["conclusion"].lower()
    assert investigation.json()["system_diagnostics"]["monitor_state"] == "paused"
