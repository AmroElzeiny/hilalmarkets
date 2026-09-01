from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from ai_market_monitor.core.csrf import csrf_token
from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    SetupInstance,
    Strategy,
    StrategyUniverse,
    StrategyVersion,
    User,
)
from ai_market_monitor.db.models.enums import (
    MarketType,
    SetupLifecycleState,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    StrategyStatus,
    StrategyVersionStatus,
    TriggerMode,
)
from ai_market_monitor.schemas.sharia import (
    AssessmentCreateRequest,
    EvidenceSourceInput,
    MethodologyCreateRequest,
)
from ai_market_monitor.services.sharia_screening import (
    DEVELOPMENT_METHODOLOGY_PREFIX,
    ShariaScreeningService,
)
from tests.factories import (
    load_strategy,
    methodology_evidence_requirements,
    methodology_rules,
)


async def _signup(test_context, email: str) -> None:
    response = await test_context["client"].post(
        "/signup/password",
        data={
            "email": email,
            "display_name": "Screened market user",
            "password": "CorrectHorse123!",
            "repeat_password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    code = test_context["settings"].email_test_outbox[-1]["code"]
    verified = await test_context["client"].post(
        "/signup/verify",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert verified.status_code == 303


def _methodology_payload(
    *,
    code: str,
    name: str,
    status: ShariaMethodologyStatus,
) -> MethodologyCreateRequest:
    active = status == ShariaMethodologyStatus.ACTIVE
    return MethodologyCreateRequest(
        code=code,
        name=name,
        version="1.0",
        description=(
            "A test methodology record used to verify authenticated governance exposure."
        ),
        status=status,
        governing_body="Qualified test governance" if active else None,
        reviewer_group="Qualified test reviewers" if active else None,
        effective_from=datetime.now(UTC) - timedelta(days=1) if active else None,
        rules=methodology_rules(source_family=code.casefold()) if active else {},
        evidence_requirements=methodology_evidence_requirements() if active else {},
    )


async def test_user_sharia_api_hides_non_executable_governance_records(test_context):
    await _signup(test_context, "sharia-api-user@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        service = ShariaScreeningService(session, test_context["settings"])
        approved = await service.create_methodology(
            _methodology_payload(
                code="API_APPROVED_METHOD",
                name="API approved methodology",
                status=ShariaMethodologyStatus.ACTIVE,
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        draft = await service.create_methodology(
            _methodology_payload(
                code="API_DRAFT_METHOD",
                name="API draft methodology",
                status=ShariaMethodologyStatus.DRAFT,
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        await service.create_methodology(
            _methodology_payload(
                code=f"{DEVELOPMENT_METHODOLOGY_PREFIX}API",
                name="API development seed",
                status=ShariaMethodologyStatus.ACTIVE,
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        forbidden_assessment = AssessmentCreateRequest(
            canonical_asset="BTC",
            methodology_id=approved.id,
            status=ShariaAssetStatus.ELIGIBLE,
            summary=(
                "This valid-shaped payload must still be rejected for an ordinary user."
            ),
            evidence_sources=[
                EvidenceSourceInput(
                    source_type="official_disclosure",
                    title="Official test disclosure",
                    publisher="Project documentation",
                    source_url="https://example.com/btc-evidence",
                    retrieved_at=datetime.now(UTC),
                    evidence_category="primary_activity",
                    evidence_summary="Evidence payload used only to test authorization.",
                )
            ],
            reviewed_by="Unprivileged user",
            reviewed_at=datetime.now(UTC),
            valid_from=datetime.now(UTC),
            reason_code="authorization_test",
            reason_summary="This request must fail before an assessment is created.",
        )
        await session.commit()

    listed = await test_context["client"].get("/api/v1/sharia/methodologies")
    guessed_draft = await test_context["client"].get(
        f"/api/v1/sharia/methodologies/{draft.id}"
    )
    requested_admin_view = await test_context["client"].get(
        "/api/v1/sharia/methodologies?include_non_active=true"
    )
    approved_detail = await test_context["client"].get(
        f"/api/v1/sharia/methodologies/{approved.id}"
    )
    forbidden_write = await test_context["client"].post(
        "/api/v1/sharia/admin/assessments",
        json=forbidden_assessment.model_dump(mode="json"),
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [str(approved.id)]
    assert guessed_draft.status_code == 409
    assert guessed_draft.json()["detail"]["code"] == "methodology_not_active"
    assert requested_admin_view.status_code == 403
    assert approved_detail.status_code == 200
    assert forbidden_write.status_code == 403


async def test_screened_market_opportunity_filter_uses_persisted_user_lifecycle(test_context):
    await _signup(test_context, "screened-opportunity@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        service = ShariaScreeningService(session, test_context["settings"])
        methodology = await service.create_methodology(
            _methodology_payload(
                code="OPPORTUNITY_CARD_METHOD",
                name="Opportunity card methodology",
                status=ShariaMethodologyStatus.ACTIVE,
            ),
            actor_user_id=user.id,
            actor_identity="test-admin",
        )
        assessment = await service.create_assessment(
            AssessmentCreateRequest(
                canonical_asset="SOL",
                asset_name="Solana",
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE,
                summary=(
                    "A qualified test reviewer recorded this evidence-backed assessment."
                ),
                evidence_snapshot={"reviewed_dimensions": []},
                evidence_sources=[
                    EvidenceSourceInput(
                        source_type="official_disclosure",
                        title="Official project disclosure",
                        publisher="Project documentation",
                        source_url="https://example.com/sol-evidence",
                        retrieved_at=datetime.now(UTC),
                        evidence_category="primary_activity",
                        evidence_summary="Evidence retained for the test assessment.",
                    )
                ],
                reviewed_by="Qualified test reviewer",
                reviewed_at=datetime.now(UTC) - timedelta(hours=1),
                valid_from=datetime.now(UTC) - timedelta(hours=1),
                reason_code="review_complete",
                reason_summary="Qualified evidence review completed for this asset.",
            ),
            actor_user_id=user.id,
        )
        strategy = Strategy(
            user_id=user.id,
            name="Opportunity Watchlist",
            status=StrategyStatus.ACTIVE,
        )
        session.add(strategy)
        await session.flush()
        definition = load_strategy().model_dump(mode="json")
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=1,
            status=StrategyVersionStatus.ACTIVE,
            source_type="test",
            source_text="screened opportunity",
            schema_json=definition,
            schema_hash="screened-opportunity-card",
        )
        session.add(version)
        await session.flush()
        strategy.active_version_id = version.id
        session.add(
            SetupInstance(
                user_id=user.id,
                strategy_version_id=version.id,
                exchange="binance",
                symbol="SOL/USDT",
                timeframe="15m",
                direction="long",
                setup_key="screened-opportunity-card",
                state=SetupLifecycleState.FORMING,
                completion_score=Decimal("80"),
                first_detected_at=datetime.now(UTC) - timedelta(minutes=5),
                last_evaluated_at=datetime.now(UTC),
                sharia_methodology_id=methodology.id,
                sharia_methodology_version=methodology.version,
                sharia_status_at_detection=ShariaAssetStatus.ELIGIBLE.value,
                sharia_assessment_id=assessment.id,
            )
        )
        await session.commit()

    page = await test_context["client"].get("/dashboard/market?view=opportunities")
    forming = await test_context["client"].get(
        "/api/v1/sharia/assets?opportunity_type=forming"
    )
    ended = await test_context["client"].get(
        "/api/v1/sharia/assets?opportunity_type=ended"
    )

    assert page.status_code == 200
    assert 'class="opportunity-card" data-status="eligible"' not in page.text
    assert "All screened assets" not in page.text
    assert forming.status_code == 200
    assert forming.json()["total"] == 1
    assert forming.json()["items"][0]["canonical_asset"] == "SOL"
    assert ended.status_code == 200
    assert ended.json()["total"] == 0


async def test_saved_asset_removal_requires_review_of_active_watch_plans(test_context):
    await _signup(test_context, "saved-asset-impact@example.com")
    async with test_context["session_factory"]() as session:
        user = await session.scalar(select(User))
        assert user is not None
        watchlist = ApprovedWatchlist(
            user_id=user.id,
            name="Core screened assets",
            is_default=True,
        )
        session.add(watchlist)
        await session.flush()
        saved_asset = ApprovedWatchlistAsset(
            watchlist_id=watchlist.id,
            canonical_asset="SOL",
            added_at=datetime.now(UTC),
        )
        strategy = Strategy(
            user_id=user.id,
            name="SOL structure Watchlist",
            status=StrategyStatus.ACTIVE,
        )
        session.add_all([saved_asset, strategy])
        await session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id,
            version_number=3,
            status=StrategyVersionStatus.ACTIVE,
            source_type="test",
            source_text="watch SOL structure",
            schema_json=load_strategy().model_dump(mode="json"),
            schema_hash="saved-asset-removal-impact",
        )
        session.add(version)
        await session.flush()
        strategy.active_version_id = version.id
        session.add(
            StrategyUniverse(
                strategy_version_id=version.id,
                exchange="binance",
                market_type=MarketType.SPOT,
                quote_currencies=["USDT"],
                include_symbols=[],
                exclude_symbols=[],
                timeframes=["15m"],
                trigger_mode=TriggerMode.CANDLE_CLOSE,
                approved_watchlist_id=watchlist.id,
            )
        )
        await session.commit()
        watchlist_id = watchlist.id
        token = csrf_token(test_context["settings"], user.id)

    endpoint = f"/api/v1/sharia/watchlists/{watchlist_id}/assets/SOL"
    impact = await test_context["client"].get(f"{endpoint}/removal-impact")
    missing_csrf = await test_context["client"].delete(endpoint)
    unconfirmed = await test_context["client"].delete(
        endpoint,
        headers={"X-CSRF-Token": token},
    )

    assert impact.status_code == 200
    assert impact.json()["requires_confirmation"] is True
    assert impact.json()["affected_watch_plans"] == [
        {
            "strategy_id": str(strategy.id),
            "name": "SOL structure Watchlist",
            "status": "active",
            "strategy_version_id": str(version.id),
            "strategy_version_number": 3,
        }
    ]
    assert missing_csrf.status_code == 403
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["detail"]["code"] == "watchlist_asset_in_use"

    removed = await test_context["client"].delete(
        f"{endpoint}?confirmed=true",
        headers={"X-CSRF-Token": token},
    )
    assert removed.status_code == 204
    async with test_context["session_factory"]() as session:
        assert await session.scalar(
            select(ApprovedWatchlistAsset.id).where(
                ApprovedWatchlistAsset.watchlist_id == watchlist_id,
                ApprovedWatchlistAsset.canonical_asset == "SOL",
            )
        ) is None
