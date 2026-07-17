from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    CandidateReadinessSnapshot,
    MonitorHealthSummary,
    NearMissSnapshot,
    ScanJob,
    ScanResult,
    SetupConditionResult,
    SetupInstance,
    SetupLifecycleEvent,
    Strategy,
    StrategyCondition,
    StrategyVersion,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ConditionOutcome,
    ConditionType,
    DeliveryChannel,
    DeliveryStatus,
    ScanJobStatus,
    ScanOutcome,
    SetupLifecycleState,
    ShariaAssetStatus,
    StrategyVersionStatus,
)
from ai_market_monitor.schemas.sharia import AssetAssessmentSummary
from ai_market_monitor.services.opportunity_cards import OpportunityCardReadService
from ai_market_monitor.services.setup_observability import (
    GroundedObservabilityExplainer,
    SetupObservabilityService,
)
from tests.factories import load_strategy


async def _seed_monitor(session, *, user=None, name="RSI Readiness"):
    user = user or User(display_name="Observer")
    session.add(user)
    await session.flush()
    strategy = Strategy(user_id=user.id, name=name)
    session.add(strategy)
    await session.flush()
    definition = load_strategy().model_copy(update={"name": name})
    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=1,
        status=StrategyVersionStatus.ACTIVE,
        source_type="test",
        schema_json=definition.model_dump(mode="json"),
        schema_hash=definition.canonical_hash(),
        approved_schema_hash=definition.canonical_hash(),
        approved_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    strategy.active_version_id = version.id
    return user, strategy, version


async def _seed_lifecycle(session, user, strategy, version):
    now = datetime.now(UTC)
    condition = StrategyCondition(
        strategy_version_id=version.id,
        condition_key="volume_ratio",
        label="Volume confirmation",
        node_type="condition",
        condition_type=ConditionType.INDICATOR,
        timeframe="15m",
        comparator="gte",
        left_operand={"kind": "indicator", "name": "volume_ratio"},
        right_operand={"kind": "constant", "value": 1.5},
        required_value=Decimal("1.5"),
        weight=Decimal("1"),
        sequence=0,
        is_required=True,
    )
    session.add(condition)
    job = ScanJob(
        strategy_version_id=version.id,
        idempotency_key=f"observability-{strategy.id}",
        status=ScanJobStatus.SUCCEEDED,
        scheduled_for=now,
        started_at=now,
        completed_at=now,
        symbols_planned=1,
        symbols_scanned=1,
    )
    session.add(job)
    await session.flush()
    scan = ScanResult(
        scan_job_id=job.id,
        strategy_version_id=version.id,
        exchange="binance",
        symbol="SOL/USDT",
        timeframe="15m",
        direction="long",
        outcome=ScanOutcome.NEAR_MISS,
        completion_score=Decimal("80"),
        candle_closed_at=now,
        evaluated_at=now,
        data_freshness_ms=300,
        is_candle_complete=True,
        proof_summary={},
    )
    session.add(scan)
    await session.flush()
    setup = SetupInstance(
        user_id=user.id,
        strategy_version_id=version.id,
        latest_scan_result_id=scan.id,
        exchange="binance",
        symbol="SOL/USDT",
        timeframe="15m",
        direction="long",
        setup_key=f"setup-{strategy.id}",
        state=SetupLifecycleState.NEAR_CONFIRMATION,
        completion_score=Decimal("80"),
        first_detected_at=now - timedelta(minutes=15),
        last_evaluated_at=now,
    )
    session.add(setup)
    await session.flush()
    result = SetupConditionResult(
        setup_instance_id=setup.id,
        scan_result_id=scan.id,
        strategy_condition_id=condition.id,
        condition_key=condition.condition_key,
        outcome=ConditionOutcome.FAILED,
        required_value={"value": 1.5},
        actual_value={"value": 1.27},
        distance_to_pass=Decimal("0.23"),
        contribution_score=Decimal("84.66"),
        candle_timestamp=now,
        evaluated_at=now,
        data_freshness_ms=300,
    )
    session.add(result)
    session.add(
        SetupLifecycleEvent(
            setup_instance_id=setup.id,
            from_state=SetupLifecycleState.FORMING,
            to_state=SetupLifecycleState.NEAR_CONFIRMATION,
            reason_code="one_required_condition_remaining",
            evidence={"scan_result_id": str(scan.id)},
            occurred_at=now,
        )
    )
    await session.flush()
    return condition, job, scan, setup, result


async def test_opportunity_card_uses_retained_conditions_and_prior_score(test_context):
    async with test_context["session_factory"]() as session:
        user, strategy, version = await _seed_monitor(session)
        _, _, scan, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        passed_definition = StrategyCondition(
            strategy_version_id=version.id,
            condition_key="price_above_ema",
            label="Price above EMA 200",
            node_type="condition",
            condition_type=ConditionType.INDICATOR,
            timeframe="15m",
            comparator="gt",
            left_operand={"kind": "price", "field": "close"},
            right_operand={"kind": "indicator", "name": "ema", "period": 200},
            required_value=Decimal("100"),
            weight=Decimal("1"),
            sequence=1,
            is_required=True,
        )
        session.add(passed_definition)
        await session.flush()
        session.add_all(
            [
                SetupConditionResult(
                    setup_instance_id=setup.id,
                    scan_result_id=scan.id,
                    strategy_condition_id=passed_definition.id,
                    condition_key=passed_definition.condition_key,
                    outcome=ConditionOutcome.PASSED,
                    required_value={"value": 100},
                    actual_value={"value": 102},
                    distance_to_pass=Decimal("0"),
                    contribution_score=Decimal("100"),
                    candle_timestamp=setup.last_evaluated_at,
                    evaluated_at=setup.last_evaluated_at,
                    data_freshness_ms=300,
                ),
                NearMissSnapshot(
                    scan_result_id=scan.id,
                    setup_instance_id=setup.id,
                    strategy_version_id=version.id,
                    exchange=setup.exchange,
                    symbol=setup.symbol,
                    timeframe=setup.timeframe,
                    completion_score=Decimal("80"),
                    previous_score=Decimal("70"),
                    trend="improving",
                    passed_condition_keys=["price_above_ema"],
                    missing_conditions=[{"condition_id": "volume_ratio"}],
                    captured_at=setup.last_evaluated_at,
                ),
            ]
        )
        await session.flush()
        assessment = AssetAssessmentSummary(
            id=uuid4(),
            canonical_asset="SOL",
            asset_name="Solana",
            methodology_id=uuid4(),
            methodology_name="Reviewed method",
            methodology_version="1.0",
            status=ShariaAssetStatus.ELIGIBLE,
            status_label="Eligible",
            summary="Reviewer-approved asset summary.",
            qualifications=[],
            reviewed_by="Qualified reviewer",
            reviewed_at=datetime.now(UTC),
            valid_from=datetime.now(UTC) - timedelta(days=1),
            valid_until=None,
        )

        card = await OpportunityCardReadService(session).for_setup(
            setup=setup,
            assessment=assessment,
            strategy_name=strategy.name,
        )

        assert card["present_conditions"] == ("Price above EMA 200",)
        assert card["missing_requirement"] == (
            "Still missing: Volume confirmation - Current 1.27 - Required 1.5"
        )
        assert card["direction"] == "Getting closer"
        assert card["data_freshness"] == "0.3s at evaluation"
        assert card["market_availability"] == "Exact active spot mapping unavailable"
        assert card["can_create_watch_plan"] is False


async def test_radar_is_tenant_isolated_and_filterable(test_context):
    async with test_context["session_factory"]() as session:
        user, strategy, version = await _seed_monitor(session)
        other, other_strategy, other_version = await _seed_monitor(session, name="Other")
        _, _, scan, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        _, _, other_scan, other_setup, _ = await _seed_lifecycle(
            session, other, other_strategy, other_version
        )
        for owner, monitor, monitor_version, item_scan, item_setup in (
            (user, strategy, version, scan, setup),
            (other, other_strategy, other_version, other_scan, other_setup),
        ):
            session.add(
                CandidateReadinessSnapshot(
                    user_id=owner.id,
                    strategy_id=monitor.id,
                    strategy_version_id=monitor_version.id,
                    setup_instance_id=item_setup.id,
                    scan_result_id=item_scan.id,
                    exchange="binance",
                    symbol="SOL/USDT",
                    timeframe="15m",
                    direction="long",
                    lifecycle_state="confirmation_pending",
                    stage_rank=3,
                    required_total=1,
                    required_passed=0,
                    optional_total=0,
                    optional_passed=0,
                    blocker_key="volume_ratio",
                    blocker_label="Volume confirmation",
                    blocker_outcome="failed",
                    blocker_actual={"value": 1.27},
                    blocker_required={"value": 1.5},
                    blocker_distance=Decimal("0.23"),
                    blocker_unit="absolute",
                    most_recent_change="Current blocker changed to Volume confirmation.",
                    last_changed_at=datetime.now(UTC),
                    last_evaluated_at=datetime.now(UTC),
                    data_freshness_ms=300,
                    data_health="healthy",
                    notification_status="not_attempted",
                    condition_tree={},
                    latest_values=[],
                )
            )
        await session.flush()
        payload = await SetupObservabilityService(session, test_context["settings"]).radar(
            user.id, monitor_id=strategy.id, state="confirmation_pending"
        )
        assert payload["total"] == 1
        assert payload["items"][0]["monitor_name"] == "RSI Readiness"
        assert payload["items"][0]["blocker"]["actual"] == 1.27


async def test_no_alert_investigation_uses_stored_condition_and_delivery_evidence(test_context):
    async with test_context["session_factory"]() as session:
        user, strategy, version = await _seed_monitor(session)
        _, _, _, setup, _ = await _seed_lifecycle(session, user, strategy, version)
        alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.CONFIRMED,
            deduplication_key=f"investigation-{setup.id}",
            title="Research match",
            body="Evidence",
            proof_receipt={},
        )
        session.add(alert)
        await session.flush()
        delivery = AlertDelivery(
            alert_id=alert.id,
            channel=DeliveryChannel.TELEGRAM,
            destination_key="chat:123",
            status=DeliveryStatus.FAILED_PERMANENT,
            attempt_count=5,
            last_error_code="telegram_forbidden",
            last_error_detail="Bot was blocked",
        )
        session.add(delivery)
        await session.flush()
        evidence = await SetupObservabilityService(session, test_context["settings"]).investigation(
            user.id, setup.id
        )
        assert evidence["primary_category"] == "notification_delivery_failure"
        assert evidence["conditions"][0]["status"] == "failed"
        assert evidence["conditions"][0]["actual"] == 1.27
        assert evidence["actions"]["retry_delivery_id"] == str(delivery.id)


async def test_no_alert_investigation_separates_provider_and_suppression_causes(test_context):
    async with test_context["session_factory"]() as session:
        user, strategy, version = await _seed_monitor(session)
        _, _, _, setup, result = await _seed_lifecycle(session, user, strategy, version)
        result.outcome = ConditionOutcome.UNAVAILABLE
        result.actual_value = {}
        result.explanation_code = "provider_data_unavailable"
        await session.flush()

        service = SetupObservabilityService(session, test_context["settings"])
        provider_evidence = await service.investigation(user.id, setup.id)
        assert provider_evidence["primary_category"] == "data_provider_issue"
        assert provider_evidence["conditions"][0]["status"] == "data_unavailable"
        assert provider_evidence["conditions"][0]["actual"] is None

        session.add(
            SetupLifecycleEvent(
                setup_instance_id=setup.id,
                from_state=SetupLifecycleState.CONFIRMED,
                to_state=SetupLifecycleState.SUPPRESSED,
                reason_code="cooldown_active",
                evidence={"cooldown_seconds_remaining": 120},
                occurred_at=datetime.now(UTC),
            )
        )
        await session.flush()

        suppressed_evidence = await service.investigation(user.id, setup.id)
        assert suppressed_evidence["primary_category"] == "cooldown_or_exclusion"
        assert "cooldown active" in suppressed_evidence["primary_reason"]


async def test_bottleneck_aggregation_marks_low_sample_and_counterfactual_boundaries(test_context):
    test_context["settings"].observability_minimum_sample_size = 1
    async with test_context["session_factory"]() as session:
        user, strategy, version = await _seed_monitor(session)
        _, job, _, _, _ = await _seed_lifecycle(session, user, strategy, version)
        now = datetime.now(UTC)
        for symbol in ("ETH/USDT", "LINK/USDT"):
            session.add(
                ScanResult(
                    scan_job_id=job.id,
                    strategy_version_id=version.id,
                    exchange="binance",
                    symbol=symbol,
                    timeframe="15m",
                    direction="long",
                    outcome=ScanOutcome.NEAR_MISS,
                    completion_score=Decimal("80"),
                    candle_closed_at=now,
                    evaluated_at=now,
                    data_freshness_ms=200,
                    is_candle_complete=True,
                    proof_summary={},
                )
            )
        await session.flush()
        service = SetupObservabilityService(session, test_context["settings"])
        aggregates = await service.aggregate_version(strategy, version)
        volume = next(item for item in aggregates if item.condition_key == "volume_ratio")
        assert volume.evaluation_count == 1
        assert volume.final_blocker_count == 1
        assert volume.sample_status == "sufficient"
        payload = await service.bottlenecks(user.id, monitor_id=strategy.id)
        item = next(value for value in payload if value["condition_key"] == "volume_ratio")
        assert item["counterfactual"]["preview_only"] is True
        assert "not a performance prediction" in item["counterfactual"]["message"]
        health = await session.scalar(
            select(MonitorHealthSummary).where(
                MonitorHealthSummary.strategy_version_id == version.id
            )
        )
        assert health.strategy_status == "too_strict"
        assert health.technical_status == "misconfigured"
        assert any(
            cause["code"] == "notification_channel_missing" for cause in health.technical_causes
        )


async def test_ai_explanation_fallback_repeats_only_grounded_reason(test_context):
    payload = {
        "primary_reason": "No alert was sent because Volume confirmation failed.",
        "symbol": "SOL/USDT",
        "timeframe": "15m",
    }
    explanation = await GroundedObservabilityExplainer(test_context["settings"]).explain(payload)
    assert payload["primary_reason"] in explanation
    assert "not a trading recommendation" in explanation
