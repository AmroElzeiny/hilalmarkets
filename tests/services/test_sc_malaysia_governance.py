import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from ai_market_monitor.core.security import hash_password
from ai_market_monitor.db.models import (
    AIAnalysisSnapshot,
    AssetResearchDossier,
    AssetShariaAssessment,
    AuditEvent,
    CanonicalAsset,
    ExternalAssessment,
    PublishedAssetAssessment,
    ReviewCase,
    ShariaMethodology,
    ShariaMonitoringRun,
    SourceChangeEvent,
    SourceSnapshot,
    TelegramNotificationAttempt,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import (
    IdentityProvider,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    UserRole,
)
from ai_market_monitor.services.sc_malaysia_import import (
    FetchedSource,
    SCMalaysiaImporter,
    SCMalaysiaParser,
)
from ai_market_monitor.services.sharia_governance import (
    SC_METHODOLOGY_CODE,
    ShariaAdminTelegramService,
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_identity import (
    PILOT_ASSET_CANDIDATES,
    AssetIdentityError,
    CanonicalAssetMappingService,
)
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_research import (
    AIAnalysisResult,
    ShariaFactualAnalysis,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from ai_market_monitor.services.sharia_source_monitoring import (
    ShariaSourceMonitoringService,
)
from ai_market_monitor.telegram.adapter import (
    TelegramDeliveryError,
    TelegramDeliveryResult,
)

SC_HTML = """
<html><body><table>
  <thead><tr><th>No.</th><th>Tradeable Digital Asset</th><th>Shariah Status</th></tr></thead>
  <tbody>
    <tr><td>1</td><td>Bitcoin (BTC)</td><td><strong>Shariah-compliant</strong>
      Resolved at the 256th SAC Meeting (29 June 2023)</td></tr>
    <tr><td>2</td><td>Ethereum (ETH)</td><td>Assessment pending</td></tr>
    <tr><td>16</td><td>Synthetix (SNX)</td><td>Effective 30 March 2026, operators
      must obtain SAC endorsement before trading.</td></tr>
  </tbody>
</table></body></html>
"""


class StaticSCFetcher:
    async def fetch(self, url: str) -> FetchedSource:
        return FetchedSource(
            url=url,
            status_code=200,
            content=SC_HTML,
            headers={"etag": '"sc-fixture"'},
            retrieved_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
        )


def test_sc_parser_requires_an_explicit_status_meeting_and_date():
    parsed = SCMalaysiaParser().parse(SC_HTML, url="https://www.sc.com.my/digital-assets")

    assert [(row.name, row.symbol) for row in parsed.rows] == [("Bitcoin", "BTC")]
    assert {item["asset"] for item in parsed.excluded_rows} == {
        "Ethereum (ETH)",
        "Synthetix (SNX)",
    }


async def test_sc_import_is_idempotent_and_never_publishes(test_context):
    async with test_context["session_factory"]() as session:
        importer = SCMalaysiaImporter(
            session,
            test_context["settings"],
            fetcher=StaticSCFetcher(),
        )
        first = await importer.import_latest()
        await session.commit()
        second = await importer.import_latest()

        external_count = await session.scalar(select(func.count(ExternalAssessment.id)))
        public_count = await session.scalar(select(func.count(AssetShariaAssessment.id)))

    assert first.created_assessments == 1
    assert second.idempotent_replay is True
    assert external_count == 1
    assert public_count == 0


async def _external(session, *, name: str = "Bitcoin", symbol: str = "BTC"):
    run = ShariaMonitoringRun(
        run_kind="test_import",
        idempotency_key=f"test-import:{name}:{symbol}",
        status="completed",
        source_url="https://www.sc.com.my/digital-assets",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    source = SourceSnapshot(
        monitoring_run_id=run.id,
        source_url="https://www.sc.com.my/digital-assets",
        retrieved_at=datetime.now(UTC),
        http_status=200,
        response_headers={},
        raw_content="official row",
        normalized_text="official row",
        title="SC Malaysia Digital Assets",
        headings=["Shariah Status"],
        content_hash=f"{symbol.lower():0<64}",
        meaningful_diff={},
        fetch_status="success",
        scraper_version="test-v1",
        parser_result={"explicit_rows": 1},
    )
    session.add(source)
    await session.flush()
    external = ExternalAssessment(
        source_snapshot_id=source.id,
        source_authority="Shariah Advisory Council of the Securities Commission Malaysia",
        source_url="https://www.sc.com.my/digital-assets",
        asset_name=name,
        asset_symbol=symbol,
        exact_status_wording="Shariah-compliant",
        sac_meeting_number="256th",
        decision_date=date(2023, 6, 29),
        regulatory_scope="SC Malaysia regulated digital-assets framework",
        retrieval_date=datetime.now(UTC),
        exact_row_text=f"{name} ({symbol}) Shariah-compliant",
        import_hash=f"import-{name}-{symbol}",
        mapping_state="unresolved",
    )
    session.add(external)
    await session.flush()
    return external, run, source


async def test_canonical_mapping_rejects_ticker_only_match(test_context):
    async with test_context["session_factory"]() as session:
        external, _, _ = await _external(session, name="Not Bitcoin", symbol="BTC")

        with pytest.raises(AssetIdentityError, match="ambiguous"):
            await CanonicalAssetMappingService(session).map_candidate(
                external,
                PILOT_ASSET_CANDIDATES["BTC"],
                verified_exchange_symbols={"BTC/USDT"},
            )
        await session.flush()

        conflict = await session.scalar(
            select(ReviewCase).where(ReviewCase.case_type == "source_identity_conflict")
        )
        asset_count = await session.scalar(select(func.count(CanonicalAsset.id)))

    assert external.mapping_state == "conflict"
    assert conflict is not None
    assert asset_count == 0


async def _ready_case(session):
    external, run, official_snapshot = await _external(session)
    candidate = PILOT_ASSET_CANDIDATES["BTC"]
    asset = await CanonicalAssetMappingService(session).map_candidate(
        external,
        candidate,
        verified_exchange_symbols={"BTC/USDT"},
    )
    methodology = ShariaMethodology(
        code=SC_METHODOLOGY_CODE,
        name="SC Malaysia SAC Reference",
        version="2026.1",
        description="Versioned reference publication workflow.",
        status=ShariaMethodologyStatus.ACTIVE,
        governing_body="Securities Commission Malaysia SAC",
        reviewer_group="HilalMarkets authenticated administrators",
        published_at=datetime.now(UTC),
        effective_from=datetime.now(UTC),
        rules_json={"requires_admin_publication": True},
        evidence_requirements_json={"official_sc_row": True},
    )
    session.add(methodology)
    await session.flush()
    dossier = AssetResearchDossier(
        canonical_asset_id=asset.id,
        external_assessment_id=external.id,
        monitoring_run_id=run.id,
        run_key=f"research:{asset.id}",
        state="completed",
        source_snapshot_ids=[str(official_snapshot.id)],
        evidence_completeness=1.0,
        missing_information_count=0,
        contradiction_count=0,
        evidence_package_hash="e" * 64,
        factual_profile={
            "project_identity": "Bitcoin native network asset.",
            "primary_activity": "Peer-to-peer value transfer and settlement.",
            "token_role": "Native unit used for transfers and network fees.",
            "staking": "Bitcoin does not use native proof-of-stake rewards.",
            "lending_and_yield": "Third-party products are separate from the asset.",
            "derivatives": "Derivative products are outside the platform's spot scope.",
            "treasury_and_governance": "No protocol treasury was established.",
            "tokenomics_and_backing": "Protocol-defined issuance; not asset-backed.",
        },
        limitations=["Official project evidence does not determine a religious ruling."],
        completed_at=datetime.now(UTC),
    )
    session.add(dossier)
    await session.flush()
    output = {
        "canonical_identity_conclusion": "confirmed",
        "profile": dossier.factual_profile,
        "relevant_activity_categories": ["native_asset"],
        "evidence_references": [
            {
                "snapshot_id": str(official_snapshot.id),
                "category": "official_reference",
                "finding": "The retained row contains the explicit official wording.",
            }
        ],
        "missing_evidence": [],
        "contradictions": [],
        "change_type": "initial_research",
        "potential_impact_severity": "none",
        "potentially_affected_methodology_areas": [],
        "human_review_required": True,
        "human_review_reason": "Publication always requires authenticated admin review.",
        "recommended_next_action": "human_review",
        "confidence": 0.95,
        "explicit_limitations": [
            "The analysis is factual and does not issue a religious ruling."
        ],
    }
    session.add(
        AIAnalysisSnapshot(
            dossier_id=dossier.id,
            analysis_version=1,
            model="gpt-5.4-nano",
            reasoning_effort="low",
            requested_service_tier="flex",
            returned_service_tier="flex",
            prompt_version="test-v1",
            input_snapshot_ids=[str(official_snapshot.id)],
            input_hash="i" * 64,
            output=output,
            usage={},
            status="completed",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
    )
    case = ReviewCase(
        case_reference="SC-BTC-TEST",
        case_type="initial_asset_review",
        state="ready_for_review",
        publication_state="unpublished",
        canonical_asset_id=asset.id,
        external_assessment_id=external.id,
        dossier_id=dossier.id,
        methodology_id=methodology.id,
        title="Review Bitcoin",
        priority="normal",
        risk_severity="none",
        human_review_reason="Authenticated publication review is mandatory.",
        idempotency_key="review:btc:test",
        next_reminder_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    return case, methodology


async def test_only_admin_can_publish_and_passport_keeps_two_layers(test_context):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = "1261328718"
    async with test_context["session_factory"]() as session:
        case, methodology = await _ready_case(session)
        ordinary = User(display_name="Ordinary", role=UserRole.USER)
        admin = User(display_name="Governance Admin", role=UserRole.ADMIN)
        session.add_all([ordinary, admin])
        await session.flush()

        assert await session.scalar(select(func.count(AssetShariaAssessment.id))) == 0
        with pytest.raises(ShariaGovernanceError, match="Administrator"):
            await ShariaGovernanceService(session, settings).approve_and_publish(
                case.id,
                admin_user_id=ordinary.id,
                reason="This user must not be allowed to publish anything.",
            )

        publication = await ShariaGovernanceService(
            session, settings
        ).approve_and_publish(
            case.id,
            admin_user_id=admin.id,
            reason="Official row, canonical identity, evidence, and limitations were reviewed.",
        )
        await session.commit()
        passport = await ShariaScreeningService(session, settings).passport(
            "BTC", methodology_id=methodology.id
        )
        attempt_count = await session.scalar(
            select(func.count(TelegramNotificationAttempt.id))
        )

    assert publication.is_active is True
    assert passport.official_sc_malaysia_reference["label"] == (
        "SC Malaysia SAC reference: Shariah-compliant"
    )
    assert "not SC Malaysia's unpublished reasoning" in (
        passport.hilalmarkets_factual_information_profile["notice"]
    )
    assert passport.separate_use_status["third_party_lending"] == (
        "not_covered_by_asset_reference"
    )
    assert attempt_count == 1


async def test_review_publication_hold_and_restore_are_separate_immutable_actions(
    test_context,
):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        admin = User(display_name="Governance owner", role=UserRole.ADMIN)
        ordinary = User(display_name="Ordinary customer", role=UserRole.USER)
        session.add_all([admin, ordinary])
        await session.flush()
        session.add(
            UserIdentity(
                user_id=admin.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="passport-history@example.com",
                normalized_identifier="passport-history@example.com",
                display_identifier="passport-history@example.com",
                password_hash=hash_password("CorrectHorse123!"),
                is_verified=True,
                is_primary=True,
            )
        )
        service = ShariaGovernanceService(session, test_context["settings"])

        await service.approve_for_publication(
            case.id,
            admin_user_id=admin.id,
            reason="The complete evidence package and every criterion were reviewed.",
        )
        assert case.state == "approved"
        assert case.publication_state == "approved_not_published"
        assert await session.scalar(select(func.count(PublishedAssetAssessment.id))) == 0

        first = await service.publish_approved(
            case.id,
            admin_user_id=admin.id,
            reason="Publish the separately approved evidence record for customer review.",
        )
        first_snapshot = dict(first.passport_snapshot)
        first_integrity_hash = first.integrity_hash

        with pytest.raises(ShariaGovernanceError) as denied:
            await service.place_safety_hold(
                case.id,
                admin_user_id=ordinary.id,
                reason="An ordinary customer cannot place a public safety hold.",
            )
        assert denied.value.code == "admin_required"

        held = await service.place_safety_hold(
            case.id,
            admin_user_id=admin.id,
            reason="A material concern requires the public Passport to fail closed.",
        )
        replay = await service.place_safety_hold(
            case.id,
            admin_user_id=admin.id,
            reason="A repeated request must be idempotent and keep the same hold.",
        )
        assert held is replay
        assert case.state == "safety_hold"
        assert first.is_active is False
        assert first.publication_state == "safety_hold"

        await service.request_safety_hold_removal(
            case.id,
            admin_user_id=admin.id,
            reason="Fresh evidence is available and must pass a new human review.",
        )
        assert case.state == "ready_for_review"
        assert case.publication_state == "safety_hold_pending_review"
        assert first.is_active is False

        await service.approve_for_publication(
            case.id,
            admin_user_id=admin.id,
            reason="The fresh evidence and prior safety concern were reviewed again.",
            with_qualifications=True,
            qualifications=[
                "The restored record includes a new use-specific qualification."
            ],
        )
        second = await service.publish_approved(
            case.id,
            admin_user_id=admin.id,
            reason="Publish a new version after the completed hold-removal review.",
        )
        await session.flush()
        historical = await ShariaPassportReadService(
            session, test_context["settings"]
        ).historical(
            canonical_asset_id=first.canonical_asset_id,
            passport_version_id=first.id,
            event_time=first.published_at,
            user_id=admin.id,
        )
        current = await ShariaPassportReadService(
            session, test_context["settings"]
        ).current("BTC", methodology_id=case.methodology_id, user_id=admin.id)

        audit_actions = list(
            (
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.target_id.in_([str(first.id), str(second.id)])
                        | AuditEvent.action.in_(
                            {
                                "sharia.review_decision_approved",
                                "sharia.safety_hold_removal_review_requested",
                            }
                        )
                    )
                )
            ).all()
        )
        await session.commit()

    signed_in = await test_context["client"].post(
        "/signin",
        data={
            "email": "passport-history@example.com",
            "password": "CorrectHorse123!",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    headers = {"X-User-ID": str(admin.id)}
    historical_api = await test_context["client"].get(
        f"/api/v1/sharia/passports/{first.canonical_asset_id}/versions/{first.id}",
        params={"event_time": first.published_at.isoformat()},
        headers=headers,
    )
    historical_page = await test_context["client"].get(
        f"/passports/{first.canonical_asset_id}/versions/{first.id}",
        params={"event_time": first.published_at.isoformat()},
    )

    assert second.version == first.version + 1
    assert second.supersedes_publication_id == first.id
    assert second.is_active is True
    assert first.passport_snapshot == first_snapshot
    assert first.integrity_hash == first_integrity_hash
    assert historical.passport_version_id == first.id
    assert historical.assessment.status == ShariaAssetStatus.ELIGIBLE
    assert historical.historical.current_status == (
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
    )
    assert current.passport_version_id == second.id
    assert current.assessment.status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
    assert historical_api.status_code == 200
    assert historical_api.json()["passport_version_id"] == str(first.id)
    assert historical_api.json()["historical"]["is_historical"] is True
    assert historical_page.status_code == 200
    assert "Passport used at alert time" in historical_page.text
    assert "Current status:" in historical_page.text
    assert audit_actions.count("sharia.publication_safety_hold_placed") == 1
    assert audit_actions.count("sharia.publication_completed") == 2
    assert audit_actions.count("sharia.review_decision_approved") == 2
    assert audit_actions.count("sharia.safety_hold_removal_review_requested") == 1


async def test_false_positive_dismissal_is_audited_without_changing_public_data(
    test_context,
):
    async with test_context["session_factory"]() as session:
        admin = User(display_name="Review admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        case = ReviewCase(
            case_reference="SC-FALSE-POSITIVE",
            case_type="material_source_change",
            state="ready_for_review",
            publication_state="change_under_review",
            title="Review a reported source change",
            priority="normal",
            risk_severity="low",
            human_review_reason="A reported source change requires a human decision.",
            idempotency_key="review:false-positive:test",
        )
        session.add(case)
        await session.flush()

        await ShariaGovernanceService(
            session, test_context["settings"]
        ).dismiss_false_positive(
            case.id,
            admin_user_id=admin.id,
            reason="The source content is unchanged and the report is not material.",
        )
        await session.flush()
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "sharia.review_false_positive_dismissed"
            )
        )
        publication_count = await session.scalar(
            select(func.count(PublishedAssetAssessment.id))
        )

    assert case.state == "superseded"
    assert case.publication_state == "published_unchanged"
    assert case.done_at is not None
    assert publication_count == 0
    assert event is not None
    assert event.metadata_redacted["published_assessment_changed"] is False


async def test_optional_four_eyes_requires_a_different_publisher(test_context):
    settings = test_context["settings"]
    settings.require_second_reviewer = True
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = User(display_name="First reviewer", role=UserRole.ADMIN)
        publisher = User(display_name="Second publisher", role=UserRole.ADMIN)
        session.add_all([reviewer, publisher])
        await session.flush()
        service = ShariaGovernanceService(session, settings)

        await service.approve_for_publication(
            case.id,
            admin_user_id=reviewer.id,
            reason="The first reviewer completed and recorded the evidence decision.",
        )
        assert case.publication_state == "awaiting_second_approval"

        with pytest.raises(ShariaGovernanceError) as same_actor:
            await service.publish_approved(
                case.id,
                admin_user_id=reviewer.id,
                reason="The same reviewer must not satisfy the optional second approval.",
            )
        assert same_actor.value.code == "second_reviewer_required"

        publication = await service.publish_approved(
            case.id,
            admin_user_id=publisher.id,
            reason="A separate publisher verified the recorded review before publication.",
        )

    assert publication.published_by_user_id == publisher.id
    assert publication.is_active is True
    assert case.state == "published"


async def test_rejected_case_is_retained_without_public_assessment(test_context):
    async with test_context["session_factory"]() as session:
        external, _, _ = await _external(session, name="Ethereum", symbol="ETH")
        admin = User(display_name="Admin", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        case = ReviewCase(
            case_reference="SC-ETH-REJECT",
            case_type="initial_asset_review",
            state="ready_for_review",
            publication_state="unpublished",
            external_assessment_id=external.id,
            title="Review Ethereum",
            priority="normal",
            risk_severity="none",
            human_review_reason="Evidence requires a human publication decision.",
            idempotency_key="review:eth:reject",
        )
        session.add(case)
        await session.flush()

        decision = await ShariaGovernanceService(
            session, test_context["settings"]
        ).reject_and_store(
            case.id,
            admin_user_id=admin.id,
            reason="The evidence package is not approved for customer publication.",
        )
        await session.commit()
        publication_count = await session.scalar(
            select(func.count(PublishedAssetAssessment.id))
        )
        assessment_count = await session.scalar(select(func.count(AssetShariaAssessment.id)))

    assert decision.decision == "reject_and_store"
    assert case.state == "rejected"
    assert case.publication_state == "stored_not_published"
    assert publication_count == 0
    assert assessment_count == 0


def test_ai_schema_cannot_issue_or_change_a_sharia_status():
    payload = {
        "canonical_identity_conclusion": "confirmed",
        "profile": {
            "project_identity": "Example asset",
            "primary_activity": "Example network",
            "token_role": "Example utility",
            "staking": "No evidence",
            "lending_and_yield": "No evidence",
            "derivatives": "No evidence",
            "treasury_and_governance": "No evidence",
            "tokenomics_and_backing": "No evidence",
        },
        "relevant_activity_categories": [],
        "evidence_references": [],
        "missing_evidence": [],
        "contradictions": [],
        "change_type": "initial_research",
        "potential_impact_severity": "none",
        "potentially_affected_methodology_areas": [],
        "human_review_required": True,
        "human_review_reason": "A person must review this.",
        "recommended_next_action": "human_review",
        "confidence": 0.5,
        "explicit_limitations": [],
        "sharia_status": "shariah_compliant",
    }
    with pytest.raises(ValidationError):
        ShariaFactualAnalysis.model_validate(payload)


async def test_reminders_are_idempotent_and_stop_after_terminal_decision(test_context):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = "1261328718"
    async with test_context["session_factory"]() as session:
        case = ReviewCase(
            case_reference="SC-REMINDER",
            case_type="initial_asset_review",
            state="ready_for_review",
            publication_state="unpublished",
            title="Review reminder test",
            priority="normal",
            risk_severity="none",
            human_review_reason="A test case is waiting for review.",
            idempotency_key="review:reminder:test",
            next_reminder_at=datetime.now(UTC),
        )
        session.add(case)
        await session.flush()
        service = ShariaAdminTelegramService(session, settings)

        immediate = await service.enqueue(
            case,
            notification_type="new_review_required",
            idempotency_key=f"new-review:{case.id}",
        )
        replay = await service.enqueue(
            case,
            notification_type="new_review_required",
            idempotency_key=f"new-review:{case.id}",
        )
        assert immediate is replay
        assert case.next_reminder_at > datetime.now(UTC) + timedelta(hours=5)

        case.next_reminder_at = datetime.now(UTC) - timedelta(seconds=1)
        created = await service.enqueue_due_reminders()
        repeated = await service.enqueue_due_reminders()
        case.state = "approved"
        case.done_at = datetime.now(UTC)
        case.next_reminder_at = datetime.now(UTC) - timedelta(seconds=1)
        after_terminal = await service.enqueue_due_reminders()
        attempts = await session.scalar(select(func.count(TelegramNotificationAttempt.id)))

    assert created == 1
    assert repeated == 0
    assert after_terminal == 0
    assert attempts == 2


class RetryOnceTelegramAdapter:
    def __init__(self):
        self.calls = 0

    async def deliver(self, message):
        self.calls += 1
        if self.calls == 1:
            raise TelegramDeliveryError(
                "temporary_test_failure",
                "Temporary Telegram failure.",
                retryable=True,
            )
        return TelegramDeliveryResult(message_ids=["telegram-message-1"])


async def test_telegram_retry_updates_one_durable_attempt_without_duplication(test_context):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = "1261328718"
    settings.telegram_enabled = True
    settings.telegram_adapter = "http"
    settings.telegram_bot_token = "test-telegram-token"
    async with test_context["session_factory"]() as session:
        case = ReviewCase(
            case_reference="SC-TELEGRAM-RETRY",
            case_type="initial_asset_review",
            state="ready_for_review",
            publication_state="unpublished",
            title="Telegram retry case",
            priority="normal",
            risk_severity="none",
            human_review_reason="A human decision remains required.",
            idempotency_key="review:telegram:retry",
        )
        session.add(case)
        await session.flush()
        adapter = RetryOnceTelegramAdapter()
        service = ShariaAdminTelegramService(session, settings, adapter=adapter)
        first = await service.enqueue(
            case,
            notification_type="new_review_required",
            idempotency_key=f"new-review:{case.id}",
        )
        replay = await service.enqueue(
            case,
            notification_type="new_review_required",
            idempotency_key=f"new-review:{case.id}",
        )

        await service.process_due()
        assert first.status == "retryable"
        first.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await service.process_due()
        row_count = await session.scalar(
            select(func.count(TelegramNotificationAttempt.id))
        )

    assert first is replay
    assert row_count == 1
    assert first.attempt_count == 2
    assert first.status == "sent"
    assert first.provider_message_id == "telegram-message-1"


class SnapshotPipeline:
    def __init__(self, session, *, material: bool):
        self.session = session
        self.material = material
        self.calls: list[int] = []
        self.in_flight = False

    async def _fetch_source(self, run, source):
        if self.in_flight:
            raise AssertionError("Official sources were fetched concurrently")
        self.in_flight = True
        self.calls.append(source.priority)
        await asyncio.sleep(0)
        snapshot = SourceSnapshot(
            monitoring_run_id=run.id,
            official_source_id=source.id,
            source_url=source.source_url,
            retrieved_at=datetime.now(UTC),
            http_status=200,
            response_headers={},
            raw_content=f"Current content for {source.title}",
            normalized_text=f"Current content for {source.title}",
            title=source.title,
            headings=[source.title],
            content_hash=(str(source.id).replace("-", "") * 2)[:64],
            meaningful_diff=(
                {"added": ["A potentially material source statement changed."], "removed": []}
                if self.material
                else {}
            ),
            is_material_change=self.material,
            fetch_status="success",
            scraper_version="test-monitor-v1",
            parser_result={"fixture": True},
        )
        self.session.add(snapshot)
        await self.session.flush()
        self.in_flight = False
        return snapshot

    def _record_usage(self, result):
        return None


class NoCallAI:
    async def analyze(self, package):
        raise AssertionError("AI must not run when no source changed")


class MaterialChangeAI:
    def __init__(self):
        self.calls = 0
        self.changed_source_counts: list[int] = []

    async def analyze(self, package):
        self.calls += 1
        self.changed_source_counts.append(len(package["changed_sources"]))
        analysis = ShariaFactualAnalysis.model_validate(
            {
                "canonical_identity_conclusion": "confirmed",
                "profile": {
                    "project_identity": "Bitcoin native network asset.",
                    "primary_activity": "Peer-to-peer settlement.",
                    "token_role": "Native network unit.",
                    "staking": "No native proof-of-stake.",
                    "lending_and_yield": "Third-party uses are separate.",
                    "derivatives": "Outside the platform's spot scope.",
                    "treasury_and_governance": "No new conclusion.",
                    "tokenomics_and_backing": "Protocol-defined issuance.",
                },
                "relevant_activity_categories": ["source_change"],
                "evidence_references": [],
                "missing_evidence": [],
                "contradictions": [],
                "change_type": "potential_material_change",
                "potential_impact_severity": "high",
                "potentially_affected_methodology_areas": ["primary_activity"],
                "human_review_required": True,
                "human_review_reason": (
                    "A verified official source changed and requires human review."
                ),
                "recommended_next_action": "human_review",
                "confidence": 0.9,
                "explicit_limitations": ["AI cannot change the published status."],
            }
        )
        return AIAnalysisResult(
            analysis=analysis,
            usage={"input_tokens": 20, "output_tokens": 10},
            returned_service_tier="flex",
            retry_count=0,
        )


async def _publish_for_monitoring(test_context, session):
    case, _ = await _ready_case(session)
    admin = User(display_name="Monitoring admin", role=UserRole.ADMIN)
    session.add(admin)
    await session.flush()
    publication = await ShariaGovernanceService(
        session, test_context["settings"]
    ).approve_and_publish(
        case.id,
        admin_user_id=admin.id,
        reason="The evidence was reviewed before enabling source monitoring.",
    )
    await session.flush()
    return publication


async def test_unchanged_published_sources_use_no_ai_and_run_once_per_window(test_context):
    async with test_context["session_factory"]() as session:
        await _publish_for_monitoring(test_context, session)
        pipeline = SnapshotPipeline(session, material=False)
        service = ShariaSourceMonitoringService(
            session,
            test_context["settings"],
            research_pipeline=pipeline,
            ai_client=NoCallAI(),
        )

        first = await service.run_due()
        replay = await service.run_due()
        monitoring_run = await session.scalar(
            select(ShariaMonitoringRun).where(
                ShariaMonitoringRun.run_kind == "source_monitor"
            )
        )

    assert pipeline.calls == sorted(pipeline.calls)
    assert first["assets_monitored"] == 1
    assert first["assets_changed"] == 0
    assert replay["assets_monitored"] == 0
    assert monitoring_run.result_summary["ai_calls"] == 0


async def test_all_changed_links_make_one_ai_call_and_create_review(test_context):
    settings = test_context["settings"]
    settings.sharia_admin_telegram_chat_id = "1261328718"
    async with test_context["session_factory"]() as session:
        publication = await _publish_for_monitoring(test_context, session)
        pipeline = SnapshotPipeline(session, material=True)
        ai = MaterialChangeAI()
        result = await ShariaSourceMonitoringService(
            session,
            settings,
            research_pipeline=pipeline,
            ai_client=ai,
        ).run_due()
        await session.flush()
        review = await session.scalar(
            select(ReviewCase).where(ReviewCase.case_type == "material_source_change")
        )
        changes = int(
            await session.scalar(select(func.count(SourceChangeEvent.id))) or 0
        )
        stored_publication = await session.get(PublishedAssetAssessment, publication.id)

    assert pipeline.calls == sorted(pipeline.calls)
    assert ai.calls == 1
    assert ai.changed_source_counts == [len(pipeline.calls)]
    assert result["review_cases_created"] == 1
    assert changes == len(pipeline.calls)
    assert review is not None
    assert review.publication_state == "change_under_review"
    assert stored_publication.publication_state == "published"
    assert stored_publication.is_active is True
