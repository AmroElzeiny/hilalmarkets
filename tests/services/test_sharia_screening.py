from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    Alert,
    AlertDelivery,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    AssetShariaStatusHistory,
    ComplianceChange,
    ComplianceDriftNotification,
    DashboardNotification,
    DashboardPreference,
    MonitorShariaAssetState,
    OperationalMetric,
    SetupInstance,
    ShariaMethodology,
    ShariaUniverseSnapshot,
    TelegramConnection,
    User,
)
from ai_market_monitor.db.models.enums import (
    AlertType,
    ComplianceChangeBehavior,
    ComplianceChangeSeverity,
    ComplianceChangeStatus,
    ComplianceReviewDecision,
    ConnectionStatus,
    DeliveryChannel,
    HealthStatus,
    MonitorShariaAssetStatus,
    SetupLifecycleState,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    ShariaUniverseMode,
    StrategyStatus,
)
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    ComplianceChangeIngestRequest,
    ComplianceReviewRequest,
    EvidenceSourceInput,
    MethodologyCreateRequest,
)
from ai_market_monitor.schemas.strategy import (
    InterpretationPreview,
    ShariaPolicyDefinition,
)
from ai_market_monitor.services import on_demand_scans, scanner
from ai_market_monitor.services.alert_presentation import AlertPresentation
from ai_market_monitor.services.compliance_watch import (
    ComplianceDigestService,
    ComplianceWatchError,
    ComplianceWatchService,
)
from ai_market_monitor.services.product_language import readiness_copy
from ai_market_monitor.services.sharia_screening import (
    AGGREGATE_METHODOLOGY_CODE,
    DEVELOPMENT_METHODOLOGY_PREFIX,
    ShariaScreeningError,
    ShariaScreeningService,
    sharia_evidence_from_proof,
)
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.strategy import StrategyService
from tests.factories import (
    load_strategy,
    methodology_evidence_requirements,
    methodology_rules,
)


class ScreeningProvider:
    async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
        return ["SOL/USDT", "BAD/USDT", "UNKNOWN/USDT", "SOL/USDC"]


def screening_settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_screening_enforced=True,
        sharia_allow_legacy_unscreened_local=False,
        sharia_default_methodology_code=None,
        openai_explanation_enabled=False,
    )


async def active_methodology(session, user_id):
    now = datetime.now(UTC)
    payload = MethodologyCreateRequest(
        code="REVIEWED_TEST_METHOD",
        name="Reviewed test methodology",
        version="1.0",
        description="Test-only methodology with explicit governance and evidence requirements.",
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Qualified test governance",
        reviewer_group="Qualified test reviewers",
        effective_from=now - timedelta(days=30),
        rules=methodology_rules(),
        evidence_requirements=methodology_evidence_requirements(),
    )
    return await ShariaScreeningService(session, screening_settings()).create_methodology(
        payload,
        actor_user_id=user_id,
        actor_identity="test-admin",
    )


def evidence(title: str = "Official project disclosure") -> EvidenceSourceInput:
    return EvidenceSourceInput(
        source_type="official_disclosure",
        title=title,
        publisher="Project documentation",
        source_url="https://example.com/evidence",
        published_at=datetime.now(UTC) - timedelta(days=2),
        retrieved_at=datetime.now(UTC) - timedelta(days=1),
        evidence_category="primary_activity",
        evidence_summary="Structured evidence reviewed by the configured reviewer workflow.",
    )


def test_assessment_schema_accepts_real_one_character_asset_symbols():
    request = AssessmentCreateRequest(
        canonical_asset="s",
        asset_name="Sonic",
        methodology_id="00000000-0000-0000-0000-000000000001",
        status=ShariaAssetStatus.ELIGIBLE,
        summary="An external authority record supports this methodology-specific status.",
        evidence_sources=[evidence()],
        reviewed_by="External reference automation",
        reviewed_at=datetime.now(UTC),
        valid_from=datetime.now(UTC),
        reason_summary="Imported from an explicit compliant authority record.",
    )

    assert request.canonical_asset == "S"


async def assess(
    session,
    methodology_id,
    user_id,
    asset: str,
    status: ShariaAssetStatus,
    *,
    valid_from: datetime | None = None,
):
    return await ShariaScreeningService(session, screening_settings()).create_assessment(
        AssessmentCreateRequest(
            canonical_asset=asset,
            asset_name=f"{asset} asset",
            methodology_id=methodology_id,
            status=status,
            summary=(
                "A qualified reviewer recorded this test assessment against the versioned "
                "test methodology and its evidence requirements."
            ),
            qualifications=(
                ["Review the stated qualification before use."]
                if status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
                else []
            ),
            evidence_snapshot={
                "reviewed_dimensions": [{"name": "Primary activity", "result": "reviewed"}],
                "methodology_result": {"passed": ["test rule"]},
            },
            evidence_sources=[evidence()],
            reviewed_by="Qualified test reviewer",
            reviewed_at=valid_from or datetime.now(UTC) - timedelta(days=1),
            valid_from=valid_from or datetime.now(UTC) - timedelta(days=1),
            reason_code="test_review",
            reason_summary="Test governance review completed with evidence.",
        ),
        actor_user_id=user_id,
    )


def screened_strategy(methodology_id):
    strategy = load_strategy()
    policy = ShariaPolicyDefinition(
        universe_mode=ShariaUniverseMode.ELIGIBLE_MARKET,
        methodology_id=methodology_id,
    )
    universe = strategy.universe.model_copy(
        update={
            "include_symbols": [],
            "exclude_symbols": [],
            "quote_currencies": ["USDT"],
            "max_symbols": 100,
            "sharia_policy": policy,
        }
    )
    return strategy.model_copy(update={"universe": universe})


async def test_effective_status_and_screened_universe_fail_closed(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Screened user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        eligible = await assess(
            session, methodology.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE
        )
        await assess(
            session, methodology.id, user.id, "BAD", ShariaAssetStatus.UNDER_REVIEW
        )
        strategy = screened_strategy(methodology.id)

        first = await ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            screening_settings(),
        ).resolve(strategy, user_id=user.id)
        second = await ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            screening_settings(),
        ).resolve(strategy, user_id=user.id)

        assert first.included_symbols == ["SOL/USDT"]
        assert first.considered_count == 3
        assert first.excluded_by_policy_count == 2
        assert first.insufficient_information_count == 1
        assert first.included[0].assessment_id == eligible.id
        assert {item.canonical_asset for item in first.excluded} == {"BAD", "UNKNOWN"}
        assert second.snapshot_id == first.snapshot_id
        assert second.snapshot_hash == first.snapshot_hash
        assert await session.scalar(select(func.count(ShariaUniverseSnapshot.id))) == 1


async def test_universe_resolution_records_fail_closed_and_abnormal_exclusion_metrics(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Screening telemetry user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        await assess(session, methodology.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        await assess(
            session,
            methodology.id,
            user.id,
            "BAD",
            ShariaAssetStatus.UNDER_REVIEW,
        )
        settings = screening_settings().model_copy(
            update={
                "sharia_abnormal_exclusion_rate_threshold": 0.5,
                "sharia_abnormal_exclusion_minimum_assets": 1,
            }
        )

        resolution = await ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            settings,
        ).resolve(screened_strategy(methodology.id), user_id=user.id)

        metrics = list(
            (
                await session.scalars(
                    select(OperationalMetric).where(
                        OperationalMetric.component == "sharia_universe"
                    )
                )
            ).all()
        )
        names = {metric.metric_name for metric in metrics}
        assert resolution.included_count == 1
        assert {
            "sharia_fail_closed_total",
            "sharia_universe_resolution_seconds",
            "sharia_universe_included_count",
            "sharia_universe_excluded_count",
            "sharia_universe_exclusion_rate",
        }.issubset(names)
        exclusion_rate = next(
            metric
            for metric in metrics
            if metric.metric_name == "sharia_universe_exclusion_rate"
        )
        assert exclusion_rate.status == HealthStatus.DEGRADED
        assert exclusion_rate.dimensions["abnormal"] is True
        assert {
            metric.dimensions.get("reason")
            for metric in metrics
            if metric.metric_name == "sharia_fail_closed_total"
        } == {"excluded_status", "missing_assessment"}


async def test_provider_timeout_is_normalized_and_recorded_fail_closed(test_context):
    class TimeoutProvider:
        async def list_symbols(self, exchange: str, quote_currencies: list[str]) -> list[str]:
            raise TimeoutError("provider did not respond")

    async with test_context["session_factory"]() as session:
        user = User(display_name="Provider timeout user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        with pytest.raises(ShariaUniverseError) as exc:
            await ShariaUniverseResolver(
                session,
                TimeoutProvider(),
                screening_settings(),
            ).resolve(screened_strategy(methodology.id), user_id=user.id)

        assert exc.value.code == "universe_dependency_timeout"
        metric = await session.scalar(
            select(OperationalMetric).where(
                OperationalMetric.metric_name == "sharia_fail_closed_total"
            )
        )
        assert metric is not None
        assert metric.status == HealthStatus.DOWN
        assert metric.dimensions == {"reason": "universe_dependency_timeout"}


async def test_missing_policy_is_rejected_when_screening_is_enforced(test_context):
    async with test_context["session_factory"]() as session:
        with pytest.raises(ShariaUniverseError) as exc:
            await ShariaUniverseResolver(
                session,
                ScreeningProvider(),
                screening_settings(),
            ).resolve(load_strategy())
        assert exc.value.code == "sharia_policy_required"


async def test_explicit_asset_mode_never_falls_back_to_full_provider_universe(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Explicit asset user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        definition = screened_strategy(methodology.id)
        policy = definition.universe.sharia_policy.model_copy(
            update={"universe_mode": ShariaUniverseMode.EXPLICIT_ASSETS}
        )
        definition = definition.model_copy(
            update={
                "universe": definition.universe.model_copy(
                    update={"include_symbols": [], "sharia_policy": policy}
                )
            }
        )

        with pytest.raises(ShariaUniverseError) as exc:
            await ShariaUniverseResolver(
                session,
                ScreeningProvider(),
                screening_settings(),
            ).resolve(definition, user_id=user.id)

        assert exc.value.code == "explicit_assets_required"


async def test_whole_monitor_policy_stops_scan_after_included_asset_changes(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Whole monitor policy user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        await assess(session, methodology.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        definition = screened_strategy(methodology.id)
        policy = definition.universe.sharia_policy.model_copy(
            update={
                "compliance_change_behavior": (
                    ComplianceChangeBehavior.PAUSE_MONITOR_IF_ANY_ASSET_CHANGES
                )
            }
        )
        definition = definition.model_copy(
            update={
                "universe": definition.universe.model_copy(
                    update={
                        "include_symbols": ["SOL/USDT"],
                        "sharia_policy": policy,
                    }
                )
            }
        )
        strategy, version = await StrategyService(
            session, "test-disclaimer"
        ).create_from_interpretation(
            user.id,
            InterpretationPreview(strategy=definition, interpreter="test"),
            source_text="Pause this Watchlist if SOL leaves the screened policy",
        )
        strategy.status = StrategyStatus.ACTIVE
        resolver = ShariaUniverseResolver(session, ScreeningProvider(), screening_settings())
        first = await resolver.resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        await assess(
            session,
            methodology.id,
            user.id,
            "SOL",
            ShariaAssetStatus.EXCLUDED,
            valid_from=datetime.now(UTC),
        )

        changed = await resolver.resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )

        assert first.monitor_paused_for_compliance is False
        assert changed.monitor_paused_for_compliance is True
        assert strategy.status == StrategyStatus.PAUSED


async def test_methodology_comparison_keeps_conflicting_results_separate(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Comparison admin")
        session.add(user)
        await session.flush()
        first = await active_methodology(session, user.id)
        service = ShariaScreeningService(session, screening_settings())
        second = await service.create_methodology(
            MethodologyCreateRequest(
                code="SECOND_REVIEWED_METHOD",
                name="Second reviewed methodology",
                version="2.0",
                description="A separate test methodology with its own explicit conclusion.",
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Second test governance",
                reviewer_group="Second test reviewers",
                effective_from=datetime.now(UTC) - timedelta(days=20),
                rules=methodology_rules(source_family="second_qualified_test_source"),
                evidence_requirements=methodology_evidence_requirements(),
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        await assess(session, first.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        await assess(session, second.id, user.id, "SOL", ShariaAssetStatus.UNDER_REVIEW)

        comparison = await service.methodology_comparison("SOL/USDT")

        assert len(comparison.results) == 2
        assert {item.status for item in comparison.results} == {
            ShariaAssetStatus.ELIGIBLE,
            ShariaAssetStatus.UNDER_REVIEW,
        }
        assert "false consensus" in comparison.notice


async def test_all_methodology_is_first_deduplicates_and_never_exposes_drafts(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Aggregate methodology user")
        session.add(user)
        await session.flush()
        service = ShariaScreeningService(session, screening_settings())
        now = datetime.now(UTC)

        async def create_method(code: str, name: str):
            return await service.create_methodology(
                MethodologyCreateRequest(
                    code=code,
                    name=name,
                    version="1.0",
                    description=(
                        "An evidence-backed methodology used to verify the aggregate market."
                    ),
                    status=ShariaMethodologyStatus.ACTIVE,
                    governing_body="Qualified test governance",
                    reviewer_group="Qualified test reviewers",
                    effective_from=now - timedelta(days=1),
                    rules=methodology_rules(source_family=code.casefold()),
                    evidence_requirements=methodology_evidence_requirements(),
                ),
                actor_user_id=user.id,
                actor_identity="test-admin",
            )

        all_method = await create_method(AGGREGATE_METHODOLOGY_CODE, "All")
        sc_method = await create_method("SC_MALAYSIA_SAC_REFERENCE", "SC Malaysia")
        fasset_method = await create_method("FASSET_SHARIAH_REPORTS", "Fasset")
        sc_btc = await assess(
            session,
            sc_method.id,
            user.id,
            "BTC",
            ShariaAssetStatus.ELIGIBLE,
        )
        await assess(
            session,
            fasset_method.id,
            user.id,
            "BTC",
            ShariaAssetStatus.ELIGIBLE,
        )
        await assess(
            session,
            fasset_method.id,
            user.id,
            "SOL",
            ShariaAssetStatus.ELIGIBLE,
        )
        await session.flush()

        methods = await service.selectable_market_methodologies()
        effective = await service.effective_assessments(all_method.id)
        listed = await service.list_screened_assets(
            methodology_id=all_method.id,
            statuses={
                ShariaAssetStatus.ELIGIBLE,
                ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
            },
        )

        assert methods[0].code == AGGREGATE_METHODOLOGY_CODE
        assert set(effective) == {"BTC", "SOL"}
        assert effective["BTC"].id == sc_btc.id
        assert effective["SOL"].methodology_id == fasset_method.id
        assert listed.total == 0
        assert listed.status_counts == {}
        assert "no active published Passports" in (listed.warning or "")


async def test_methodology_versions_are_immutable_and_unique(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Methodology version admin")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        service = ShariaScreeningService(session, screening_settings())

        with pytest.raises(ShariaScreeningError) as exc:
            await service.create_methodology(
                MethodologyCreateRequest(
                    code=methodology.code,
                    name=methodology.name,
                    version=methodology.version,
                    description=methodology.description,
                    status=ShariaMethodologyStatus.ACTIVE,
                    governing_body=methodology.governing_body,
                    reviewer_group=methodology.reviewer_group,
                    effective_from=methodology.effective_from,
                    rules=methodology.rules_json,
                    evidence_requirements=methodology.evidence_requirements_json,
                ),
                actor_user_id=user.id,
                actor_identity="test-admin",
            )

        assert exc.value.code == "methodology_version_exists"


async def test_active_methodology_contract_cannot_be_bypassed_in_service_or_database(
    test_context,
):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Methodology contract admin")
        session.add(user)
        await session.flush()
        service = ShariaScreeningService(session, screening_settings())
        with pytest.raises(ShariaScreeningError) as creation:
            await service.create_methodology(
                MethodologyCreateRequest(
                    code="INCOMPLETE_ACTIVE_METHOD",
                    name="Incomplete active methodology",
                    version="1.0",
                    description=(
                        "An incomplete contract must never become executable screening policy."
                    ),
                    status=ShariaMethodologyStatus.ACTIVE,
                    governing_body="Qualified test governance",
                    reviewer_group="Qualified test reviewers",
                    effective_from=datetime.now(UTC) - timedelta(days=1),
                    rules={"versioned": True},
                    evidence_requirements={"minimum_sources": 1},
                ),
                actor_user_id=user.id,
                actor_identity="test-admin",
            )
        assert creation.value.code == "methodology_contract_invalid"

        bypassed = ShariaMethodology(
            code="DIRECT_DATABASE_BYPASS",
            name="Direct database bypass",
            version="1.0",
            description="A deliberately invalid directly inserted methodology contract.",
            status=ShariaMethodologyStatus.ACTIVE,
            governing_body="Qualified test governance",
            reviewer_group="Qualified test reviewers",
            published_at=datetime.now(UTC),
            effective_from=datetime.now(UTC) - timedelta(days=1),
            rules_json={"versioned": True},
            evidence_requirements_json={"minimum_sources": 1},
        )
        session.add(bypassed)
        await session.flush()
        with pytest.raises(ShariaScreeningError) as execution:
            await service.methodology(bypassed.id, require_active=True)
        assert execution.value.code == "methodology_contract_invalid"
        assert bypassed not in await service.executable_methodologies()


async def test_approved_compliance_review_pauses_asset_and_deduplicates_alert(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Compliance user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        await assess(session, methodology.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        definition = screened_strategy(methodology.id)
        strategy, version = await StrategyService(
            session, "test-disclaimer"
        ).create_from_interpretation(
            user.id,
            InterpretationPreview(strategy=definition, interpreter="test"),
            source_text="Watch an eligible SOL setup",
        )
        resolution = await ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            screening_settings(),
        ).resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        assert resolution.included_symbols == ["SOL/USDT"]

        service = ComplianceWatchService(session, screening_settings())
        change, created = await service.ingest_change(
            ComplianceChangeIngestRequest(
                canonical_asset="SOL",
                change_type="lending_or_borrowing_added",
                severity=ComplianceChangeSeverity.REVIEW_REQUIRED,
                source_reference="https://example.com/change",
                title="New lending feature disclosed",
                summary="An official disclosure added a lending feature that requires review.",
                structured_change={"feature": "lending"},
                detected_at=datetime.now(UTC),
                detection_method="official_source_monitor",
                confidence_label="verified_source",
            ),
            actor_user_id=user.id,
        )
        assert created is True
        duplicate, created_again = await service.ingest_change(
            ComplianceChangeIngestRequest(
                canonical_asset="SOL",
                change_type="lending_or_borrowing_added",
                severity=ComplianceChangeSeverity.REVIEW_REQUIRED,
                source_reference="https://example.com/change",
                title="New lending feature disclosed",
                summary="An official disclosure added a lending feature that requires review.",
                structured_change={"feature": "lending"},
                detected_at=change.detected_at,
                detection_method="official_source_monitor",
                confidence_label="verified_source",
            ),
            actor_user_id=user.id,
        )
        assert duplicate.id == change.id
        assert created_again is False

        review, assessment_id, affected = await service.review_change(
            change.id,
            ComplianceReviewRequest(
                methodology_id=methodology.id,
                decision=ComplianceReviewDecision.APPROVED,
                proposed_status=ShariaAssetStatus.UNDER_REVIEW,
                reviewer_notes=(
                    "The new lending feature requires additional evidence under this "
                    "methodology, so the asset is moved to under review."
                ),
                reviewed_by="Qualified test reviewer",
                assessment_summary=(
                    "The approved review found a material change that requires further "
                    "evidence before the prior status can continue."
                ),
                evidence_sources=[evidence("Official lending disclosure")],
            ),
            reviewer_user_id=user.id,
        )
        await session.flush()

        state = await session.scalar(
            select(MonitorShariaAssetState).where(
                MonitorShariaAssetState.strategy_id == strategy.id,
                MonitorShariaAssetState.canonical_asset == "SOL",
            )
        )
        assert assessment_id is not None
        assert review.final_status == ShariaAssetStatus.UNDER_REVIEW
        assert affected == 1
        assert state is not None and state.state == MonitorShariaAssetStatus.PAUSED
        assert state.sharia_status == ShariaAssetStatus.UNDER_REVIEW
        assert await session.scalar(select(func.count(ComplianceDriftNotification.id))) == 1
        assert await session.scalar(
            select(func.count(Alert.id)).where(Alert.alert_type == AlertType.COMPLIANCE)
        ) == 1
        compliance_alert = await session.scalar(
            select(Alert).where(Alert.alert_type == AlertType.COMPLIANCE)
        )
        assert compliance_alert is not None
        assert compliance_alert.proof_receipt["previous_status"] == "eligible"
        assert compliance_alert.proof_receipt["new_status"] == "under_review"
        assert compliance_alert.proof_receipt["methodology_id"] == str(methodology.id)
        assert compliance_alert.proof_receipt["methodology_version"] == methodology.version
        # The immutable alert records the immediate safety hold, before the
        # reviewer decision. Approval is retained in ComplianceReview rather
        # than rewriting or duplicating this historical notification.
        assert compliance_alert.proof_receipt["review_state"] == "awaiting_review"
        assert compliance_alert.proof_receipt["provisional_safety_hold"] is True
        assert compliance_alert.proof_receipt["automatic_watch_plan_action"] == "paused"
        assert compliance_alert.proof_receipt["affected_watch_plans"] == [strategy.name]
        assert "Review the updated Passport" in (
            compliance_alert.proof_receipt["next_user_action"]
        )
        assert "Automatic Watch Plan action: paused" in compliance_alert.body
        assert compliance_alert.proof_receipt["evidence_passport_path"] == (
            "/dashboard/market/sol"
        )
        assert compliance_alert.proof_receipt["ai_generated_ruling"] is False
        assert await session.scalar(select(func.count(DashboardNotification.id))) == 1
        assert await session.scalar(select(func.count(AssetShariaStatusHistory.id))) == 2
        snapshot = await session.get(ShariaUniverseSnapshot, resolution.snapshot_id)
        assert snapshot is not None and snapshot.invalidated_at is not None

        with pytest.raises(ComplianceWatchError) as exc:
            await service.review_change(
                change.id,
                ComplianceReviewRequest(
                    methodology_id=methodology.id,
                    decision=ComplianceReviewDecision.DISMISSED,
                    reviewer_notes="This duplicate decision should be rejected by the workflow.",
                    reviewed_by="Qualified test reviewer",
                ),
                reviewer_user_id=user.id,
            )
        assert exc.value.code == "change_already_decided"


async def test_daily_compliance_digest_enqueues_external_summary_once(test_context):
    now = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
    async with test_context["session_factory"]() as session:
        user = User(display_name="Daily compliance user")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                DashboardPreference(
                    user_id=user.id,
                    theme="light",
                    default_timezone="UTC",
                    default_dashboard_path="/dashboard",
                    notification_preferences={
                        "alert_channels": ["telegram"],
                        "compliance_alert_channels": ["web", "telegram"],
                        "compliance_alert_digest": "daily",
                    },
                ),
                TelegramConnection(
                    user_id=user.id,
                    telegram_user_id="screening-digest-user",
                    chat_id="screening-digest-chat",
                    status=ConnectionStatus.ACTIVE,
                    alerts_enabled=True,
                    connected_at=now,
                ),
            ]
        )
        change = ComplianceChange(
            canonical_asset="SOL",
            change_type="qualification_updated",
            severity=ComplianceChangeSeverity.INFORMATIONAL,
            source_reference="https://example.com/qualification",
            title="Qualification evidence updated",
            summary="A qualified reviewer should inspect the updated evidence.",
            structured_change={"qualification": "updated"},
            detected_at=now - timedelta(hours=2),
            effective_at=now - timedelta(hours=2),
            status=ComplianceChangeStatus.APPROVED,
            detection_method="approved_source_ingest",
            confidence_label="verified_source",
            idempotency_key="daily-digest-change",
        )
        session.add(change)
        await session.flush()
        drift = ComplianceDriftNotification(
            user_id=user.id,
            compliance_change_id=change.id,
            strategy_id=None,
            alert_id=None,
            canonical_asset="SOL",
            previous_status=ShariaAssetStatus.ELIGIBLE,
            new_status=ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
            behavior=ComplianceChangeBehavior.NOTIFY_ONLY,
            impact={"evidence_passport_path": "/dashboard/market/sol"},
            idempotency_key="daily-digest-drift",
            created_at=now - timedelta(hours=1),
            digest_processed_at=None,
        )
        session.add(drift)
        await session.flush()

        service = ComplianceDigestService(session, screening_settings())
        first = await service.process_due(now=now)
        second = await service.process_due(now=now)

        deliveries = list(
            (
                await session.scalars(
                    select(AlertDelivery).where(
                        AlertDelivery.channel == DeliveryChannel.TELEGRAM
                    )
                )
            ).all()
        )
        assert first == {
            "users_considered": 1,
            "summaries_enqueued": 1,
            "events_processed": 1,
        }
        assert second == {
            "users_considered": 0,
            "summaries_enqueued": 0,
            "events_processed": 0,
        }
        assert drift.digest_processed_at == now
        assert len(deliveries) == 1
        assert deliveries[0].destination_key == "chat:screening-digest-chat"


async def test_development_methodology_never_becomes_executable_default(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Governance admin")
        session.add(user)
        await session.flush()
        approved = await active_methodology(session, user.id)
        service = ShariaScreeningService(session, screening_settings())
        development = await service.create_methodology(
            MethodologyCreateRequest(
                code=f"{DEVELOPMENT_METHODOLOGY_PREFIX}SEED",
                name="Development seed only",
                version="99.0",
                description=(
                    "A test-only seed that must never be represented as an approved religious "
                    "methodology or power screened execution."
                ),
                status=ShariaMethodologyStatus.ACTIVE,
                governing_body="Test fixtures only",
                reviewer_group="Automated test fixtures",
                effective_from=datetime.now(UTC) - timedelta(days=1),
                rules=methodology_rules(source_family="development_test_source"),
                evidence_requirements=methodology_evidence_requirements(),
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )

        selected = await service.default_methodology()
        executable = await service.executable_methodologies()

        assert selected is not None and selected.id == approved.id
        assert [row.id for row in executable] == [approved.id]
        with pytest.raises(ShariaScreeningError) as exc:
            await service.resolve_methodology(development.id)
        assert exc.value.code == "development_methodology_not_executable"
        with pytest.raises(ShariaScreeningError) as list_exc:
            await service.list_screened_assets(methodology_id=development.id)
        assert list_exc.value.code == "development_methodology_not_executable"


async def test_inactive_and_expired_methodologies_fail_closed(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Expired methodology user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        definition = screened_strategy(methodology.id)
        methodology.status = ShariaMethodologyStatus.ARCHIVED
        await session.flush()

        with pytest.raises(ShariaUniverseError) as inactive:
            await ShariaUniverseResolver(
                session,
                ScreeningProvider(),
                screening_settings(),
            ).resolve(definition, user_id=user.id)
        assert inactive.value.code == "methodology_not_active"

        methodology.status = ShariaMethodologyStatus.ACTIVE
        methodology.effective_to = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        with pytest.raises(ShariaUniverseError) as expired:
            await ShariaUniverseResolver(
                session,
                ScreeningProvider(),
                screening_settings(),
            ).resolve(definition, user_id=user.id)
        assert expired.value.code == "methodology_expired"


async def test_approved_watchlist_is_intersected_and_owner_checked(test_context):
    async with test_context["session_factory"]() as session:
        owner = User(display_name="Watchlist owner")
        other = User(display_name="Different user")
        session.add_all([owner, other])
        await session.flush()
        methodology = await active_methodology(session, owner.id)
        await assess(session, methodology.id, owner.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        await assess(session, methodology.id, owner.id, "BAD", ShariaAssetStatus.ELIGIBLE)
        watchlist = ApprovedWatchlist(user_id=owner.id, name="My approved assets")
        session.add(watchlist)
        await session.flush()
        session.add(
            ApprovedWatchlistAsset(
                watchlist_id=watchlist.id,
                canonical_asset="SOL",
                added_at=datetime.now(UTC),
            )
        )
        await session.flush()
        base = screened_strategy(methodology.id)
        policy = base.universe.sharia_policy.model_copy(
            update={
                "universe_mode": ShariaUniverseMode.APPROVED_WATCHLIST,
                "approved_watchlist_id": watchlist.id,
            }
        )
        definition = base.model_copy(
            update={"universe": base.universe.model_copy(update={"sharia_policy": policy})}
        )
        resolver = ShariaUniverseResolver(session, ScreeningProvider(), screening_settings())

        resolved = await resolver.resolve(definition, user_id=owner.id)

        assert resolved.included_symbols == ["SOL/USDT"]
        assert {item.canonical_asset for item in resolved.excluded} == {"BAD", "UNKNOWN"}
        assert all(item.reason_code == "not_in_watchlist" for item in resolved.excluded)
        with pytest.raises(ShariaUniverseError) as exc:
            await resolver.resolve(definition, user_id=other.id)
        assert exc.value.code == "watchlist_not_found"


async def test_pending_review_safety_hold_is_fail_closed_and_reversible(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Safety hold user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        approved = await assess(
            session,
            methodology.id,
            user.id,
            "SOL",
            ShariaAssetStatus.ELIGIBLE,
        )
        definition = screened_strategy(methodology.id)
        strategy, version = await StrategyService(
            session, "test-disclaimer"
        ).create_from_interpretation(
            user.id,
            InterpretationPreview(strategy=definition, interpreter="test"),
            source_text="Watch screened SOL",
        )
        resolver = ShariaUniverseResolver(session, ScreeningProvider(), screening_settings())
        first = await resolver.resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        setup = SetupInstance(
            user_id=user.id,
            strategy_version_id=version.id,
            exchange="binance",
            symbol="SOL/USDT",
            timeframe="15m",
            direction="long",
            setup_key="historical-screening-proof",
            state=SetupLifecycleState.FORMING,
            completion_score=Decimal("80"),
            first_detected_at=datetime.now(UTC) - timedelta(minutes=10),
            last_evaluated_at=datetime.now(UTC),
            sharia_methodology_id=methodology.id,
            sharia_methodology_version=methodology.version,
            sharia_status_at_detection=ShariaAssetStatus.ELIGIBLE.value,
            sharia_assessment_id=approved.id,
        )
        session.add(setup)
        historical_alert = Alert(
            user_id=user.id,
            strategy_version_id=version.id,
            setup_instance_id=setup.id,
            alert_type=AlertType.FORMING,
            deduplication_key="historical-screening-proof-alert",
            title="SOL/USDT forming",
            body="Historical evidence uses the status captured at evaluation time.",
            proof_receipt={
                "sharia_methodology_id": str(methodology.id),
                "sharia_methodology_version": methodology.version,
                "sharia_status_at_scan": ShariaAssetStatus.ELIGIBLE.value,
                "sharia_assessment_id": str(approved.id),
            },
            candle_timestamp=datetime.now(UTC),
        )
        session.add(historical_alert)
        await session.flush()
        compliance = ComplianceWatchService(session, screening_settings())
        change, _ = await compliance.ingest_change(
            ComplianceChangeIngestRequest(
                canonical_asset="SOL",
                change_type="primary_business_changed",
                severity=ComplianceChangeSeverity.CRITICAL,
                source_reference="https://example.com/material-change",
                title="Material activity change requires review",
                summary="A verified material change requires qualified human review.",
                structured_change={"material_change": True},
                detected_at=datetime.now(UTC),
                detection_method="official_source_monitor",
                confidence_label="verified_source",
            ),
            actor_user_id=user.id,
        )

        current = await ShariaScreeningService(
            session, screening_settings()
        ).effective_assessment(methodology.id, "SOL")
        held = await resolver.resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        state = await session.scalar(
            select(MonitorShariaAssetState).where(
                MonitorShariaAssetState.strategy_id == strategy.id,
                MonitorShariaAssetState.canonical_asset == "SOL",
            )
        )
        assert current is not None and current.id == approved.id
        assert current.status == ShariaAssetStatus.ELIGIBLE
        assert held.included_symbols == []
        sol_exclusion = next(
            item for item in held.excluded if item.canonical_asset == "SOL"
        )
        assert sol_exclusion.status == ShariaAssetStatus.UNDER_REVIEW
        assert state is not None and state.state == MonitorShariaAssetStatus.PAUSED
        assert await session.scalar(select(func.count(AssetShariaStatusHistory.id))) == 1

        await compliance.review_change(
            change.id,
            ComplianceReviewRequest(
                methodology_id=methodology.id,
                decision=ComplianceReviewDecision.DISMISSED,
                reviewer_notes=(
                    "Qualified review found no methodology status change was required."
                ),
                reviewed_by="Qualified test reviewer",
            ),
            reviewer_user_id=user.id,
        )
        restored = await resolver.resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        await session.refresh(setup)
        await session.refresh(state)
        await session.refresh(historical_alert)

        assert first.included_symbols == ["SOL/USDT"]
        assert restored.included_symbols == ["SOL/USDT"]
        assert state.state == MonitorShariaAssetStatus.ACTIVE
        assert await session.scalar(select(func.count(AssetShariaStatusHistory.id))) == 1
        assert setup.sharia_methodology_version == "1.0"
        assert setup.sharia_status_at_detection == ShariaAssetStatus.ELIGIBLE.value
        assert setup.sharia_assessment_id == approved.id
        assert historical_alert.proof_receipt["sharia_methodology_version"] == "1.0"
        assert (
            historical_alert.proof_receipt["sharia_status_at_scan"]
            == ShariaAssetStatus.ELIGIBLE.value
        )
        assert historical_alert.proof_receipt["sharia_assessment_id"] == str(approved.id)


def test_readiness_copy_keeps_peak_and_ended_separate():
    assert readiness_copy(100, SetupLifecycleState.EXPIRED) == (
        "Peak readiness 100%; Status: Ended"
    )
    assert readiness_copy(72, SetupLifecycleState.NEAR_CONFIRMATION) == "72% ready"


def test_scanner_and_persistent_monitor_share_the_same_universe_resolver():
    assert on_demand_scans.ShariaUniverseResolver is ShariaUniverseResolver
    assert scanner.ShariaUniverseResolver is ShariaUniverseResolver


def test_alert_presentation_reads_nested_immutable_screening_evidence():
    proof = {
        "symbol": "SOL/USDT",
        "strategy_version": "3",
        "scan_context": {
            "sharia_screening": {
                "methodology_id": "f522a634-2c88-493d-a5c7-d5e86f56b984",
                "methodology_code": "APPROVED_METHOD",
                "methodology_version": "2.1",
                "asset": {
                    "canonical_asset": "SOL",
                    "status": "eligible_with_qualifications",
                    "reviewed_at": "2026-07-08T10:00:00+00:00",
                },
            }
        },
    }
    alert = Alert(
        alert_type=AlertType.CONFIRMED,
        deduplication_key="screening-evidence-presentation",
        title="SOL/USDT confirmed",
        body="All required market checks passed.",
        proof_receipt=proof,
        candle_timestamp=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )

    evidence = sharia_evidence_from_proof(proof)
    presentation = AlertPresentation.from_alert(
        alert,
        public_base_url="https://app.trace-edge.com",
    )

    assert evidence["methodology_version"] == "2.1"
    assert presentation.sharia_status == "eligible_with_qualifications"
    assert presentation.sharia_methodology == "APPROVED_METHOD v2.1"
    assert presentation.sharia_passport_url == (
        "https://app.trace-edge.com/dashboard/market/sol"
    )
    assert "Screening status at evaluation" in presentation.telegram_text()
    assert "Evidence Passport" in presentation.telegram_text()


async def test_remove_asset_policy_persists_removed_monitor_state(test_context):
    async with test_context["session_factory"]() as session:
        user = User(display_name="Remove policy user")
        session.add(user)
        await session.flush()
        methodology = await active_methodology(session, user.id)
        await assess(session, methodology.id, user.id, "SOL", ShariaAssetStatus.ELIGIBLE)
        await assess(
            session,
            methodology.id,
            user.id,
            "BAD",
            ShariaAssetStatus.EXCLUDED,
        )
        definition = screened_strategy(methodology.id)
        policy = definition.universe.sharia_policy.model_copy(
            update={
                "compliance_change_behavior": ComplianceChangeBehavior.REMOVE_ASSET,
            }
        )
        definition = definition.model_copy(
            update={
                "universe": definition.universe.model_copy(
                    update={"sharia_policy": policy}
                )
            }
        )
        strategy, version = await StrategyService(
            session, "test-disclaimer"
        ).create_from_interpretation(
            user.id,
            InterpretationPreview(strategy=definition, interpreter="test"),
            source_text="Remove assets that leave the screened policy",
        )

        await ShariaUniverseResolver(
            session,
            ScreeningProvider(),
            screening_settings(),
        ).resolve(
            definition,
            user_id=user.id,
            strategy_version_id=version.id,
        )
        removed = await session.scalar(
            select(MonitorShariaAssetState).where(
                MonitorShariaAssetState.strategy_id == strategy.id,
                MonitorShariaAssetState.canonical_asset == "BAD",
            )
        )

        assert removed is not None
        assert removed.state == MonitorShariaAssetStatus.REMOVED
        assert removed.sharia_status == ShariaAssetStatus.EXCLUDED
