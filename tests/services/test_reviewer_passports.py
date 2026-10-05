"""Every coin a Hilal Markets reviewer decides has a Passport, and only those are listed.

The owner's rules from 5 October 2026, tested against the real models with the network
replaced:

* a reviewer's approve **or** reject on a new coin writes the coin's Passport under the
  Hilal Markets Methodology, saying a Hilal Markets reviewer checked it;
* the reasons on it are the ones the reviewer confirmed, never the AI's on its own;
* the research page lists only coins a person decided — never a machine verdict;
* the AI never approves: an approval without confirmed public reasons records nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AssetShariaAssessment,
    PublishedAssetAssessment,
    ReviewCase,
    ReviewDecision,
    ShariaEvidenceSource,
    User,
)
from ai_market_monitor.db.models.enums import ReviewCaseType, ShariaAssetStatus, UserRole
from ai_market_monitor.services.automated_research_reader import AutomatedResearchReader
from ai_market_monitor.services.coin_terms_ai_review import AIReview, red_flag
from ai_market_monitor.services.hilal_methodology import (
    Admission,
    admitted_by,
    ensure_methodology,
    publish,
)
from ai_market_monitor.services.meme_coins import owner_list
from ai_market_monitor.services.reviewer_passports import (
    REVIEWER_QUALIFICATION,
    CitedPage,
    ReviewerPassportError,
    write_reviewer_passport,
)
from ai_market_monitor.services.sharia_admin_dashboard import ShariaAdminDashboardService
from ai_market_monitor.services.sharia_case_tags import RED_FLAGS
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from tests.coin_review_fakes import FakeAI, FakeCrawler, FakeProvider
from tests.coin_review_fakes import held_review as _held_review

REASONS = ["The project's own pages describe a payments network.", "Its code is public."]


async def _run(session, settings, review: AIReview, symbol: str = "NEWX"):
    from ai_market_monitor.db.models import AutomatedScreenRun
    from ai_market_monitor.services.automated_screen_pipeline import AutomatedScreenPipeline

    pipeline = AutomatedScreenPipeline(
        session,
        settings,
        coinmarketcap=FakeProvider(),
        crawler=FakeCrawler(),
        ai_reviewer=FakeAI(review, settings),
    )
    await pipeline.run([symbol])
    await session.flush()
    run = await session.scalar(
        select(AutomatedScreenRun).where(AutomatedScreenRun.symbol == symbol)
    )
    return run, await session.get(ReviewCase, run.review_case_id)


async def _reviewer(session) -> User:
    admin = User(display_name="Reviewer", role=UserRole.ADMIN)
    session.add(admin)
    await session.flush()
    return admin


def _clean_review() -> AIReview:
    return AIReview(state="completed", model="m", reasoning_effort="high")


# --- Approve and reject both publish ------------------------------------------------


async def test_approving_a_new_coin_publishes_its_passport_under_the_hilal_standard(
    test_context,
):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        _run_row, case = await _run(session, settings, _clean_review())
        admin = await _reviewer(session)
        decision = await ShariaGovernanceService(session, settings).approve_new_coin(
            case.id,
            admin_user_id=admin.id,
            reason="pages fine, payments only, code public",
            public_reasons=REASONS,
        )
        await session.flush()

        assert case.state == "published" and case.publication_state == "published"
        assert decision.public_reasons == REASONS
        row = await session.scalar(
            select(AssetShariaAssessment).where(AssetShariaAssessment.valid_until.is_(None))
        )
        # Never plain "Eligible": a Hilal Markets reviewer is not an outside authority.
        assert row.status == ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS
        assert row.qualifications == [REVIEWER_QUALIFICATION]
        assert row.reviewed_by == "Hilal Markets reviewer"
        assert "@" not in row.reviewed_by
        assert row.evidence_snapshot["review_decision_id"] == str(decision.id)
        sources = await session.scalar(
            select(func.count(ShariaEvidenceSource.id)).where(
                ShariaEvidenceSource.assessment_id == row.id
            )
        )
        assert sources == 2  # the two pages the fake crawler read
        assert await session.scalar(select(func.count(PublishedAssetAssessment.id))) == 0

        methodology, _ = await ensure_methodology(session)
        passport = await ShariaPassportReadService(session, settings).current(
            "NEWX", methodology_id=methodology.id
        )
        assert passport.decision_record is not None
        assert passport.decision_record.public_reasons == REASONS


@pytest.mark.parametrize("public_reasons", [None, [], ["", "   "], ["100% halal coin"]])
async def test_an_approval_without_confirmed_reasons_records_nothing(
    test_context, public_reasons
):
    """The AI never approves. Without reasons the reviewer confirmed — or with only
    reasons that break the forbidden-claims rule — nothing is written at all."""

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        _run_row, case = await _run(session, settings, _clean_review())
        admin = await _reviewer(session)
        with pytest.raises(ShariaGovernanceError) as refused:
            await ShariaGovernanceService(session, settings).approve_new_coin(
                case.id,
                admin_user_id=admin.id,
                reason="pages fine, payments only",
                public_reasons=public_reasons,
            )
        assert refused.value.code == "public_reasons_required"
        assert await session.scalar(select(func.count(ReviewDecision.id))) == 0
        assert await session.scalar(select(func.count(AssetShariaAssessment.id))) == 0
        assert case.state == "ready_for_review"


async def test_the_new_coin_approval_is_refused_for_a_full_review_case(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        admin = await _reviewer(session)
        case = ReviewCase(
            case_reference="X-1",
            case_type=ReviewCaseType.INITIAL_ASSET_REVIEW,
            state="ready_for_review",
            publication_state="unpublished",
            title="Initial review",
            human_review_reason="",
            requested_evidence=[],
            idempotency_key="x-1",
        )
        session.add(case)
        await session.flush()
        with pytest.raises(ShariaGovernanceError) as refused:
            await ShariaGovernanceService(session, settings).approve_new_coin(
                case.id, admin_user_id=admin.id, reason="looks fine to me", public_reasons=REASONS
            )
        assert refused.value.code == "new_coin_approval_wrong_case"


async def test_undo_takes_the_reviewer_passport_down_and_brings_back_the_old_one(
    test_context,
):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        _run_row, case = await _run(session, settings, _held_review())
        admin = await _reviewer(session)
        service = ShariaGovernanceService(session, settings)
        decision = await service.reject_and_store(
            case.id,
            admin_user_id=admin.id,
            reason="It lends money, keep it out.",
            public_reasons=["It lends money to its users."],
        )
        await session.flush()
        live = select(func.count(AssetShariaAssessment.id)).where(
            AssetShariaAssessment.valid_until.is_(None)
        )
        assert await session.scalar(live) == 1
        await service.undo_decision(
            case.id,
            admin_user_id=admin.id,
            reason="Wrong coin, undoing this.",
            decision_id=decision.id,
            previous_state="ready_for_review",
            previous_publication_state="unpublished",
        )
        assert await session.scalar(live) == 0
        assert case.state == "ready_for_review"


# --- What the owner of the Passport refuses ------------------------------------------


def _decision() -> ReviewDecision:
    return ReviewDecision(
        id=uuid4(), review_case_id=uuid4(), admin_user_id=uuid4(), public_reasons=REASONS
    )


PAGE = CitedPage(
    url="https://coin.example/about", title="About", category="website", own=True,
    read_at=datetime.now(UTC),
)


@pytest.mark.parametrize("symbol", sorted(owner_list())[:5])
async def test_a_meme_coin_can_never_be_approved(test_context, symbol):
    async with test_context["session_factory"]() as session:
        with pytest.raises(ReviewerPassportError) as refused:
            await write_reviewer_passport(
                session, symbol=symbol, name=symbol, approved=True, decision=_decision(),
                reviewer_label="Hilal Markets reviewer", pages=[PAGE],
            )
        assert refused.value.code == "reviewer_passport_meme_coin"


async def test_an_approval_that_cites_no_page_is_refused(test_context):
    async with test_context["session_factory"]() as session:
        with pytest.raises(ReviewerPassportError) as refused:
            await write_reviewer_passport(
                session, symbol="NEWX", name="New X", approved=True, decision=_decision(),
                reviewer_label="Hilal Markets reviewer", pages=[],
            )
        assert refused.value.code == "reviewer_passport_no_source"


@pytest.mark.parametrize(
    "symbol", [item.symbol for item in admitted_by(Admission.REGULATOR_FLOOR)]
)
async def test_a_regulator_floor_coin_is_never_written_as_not_approved(test_context, symbol):
    async with test_context["session_factory"]() as session:
        result = await write_reviewer_passport(
            session, symbol=symbol, name=symbol, approved=False, decision=_decision(),
            reviewer_label="Hilal Markets reviewer", pages=[PAGE],
        )
        assert result.assessment is None
        assert "regulator" in result.skipped_reason


async def test_the_methodology_file_never_replaces_a_reviewer_decision(test_context):
    symbol = admitted_by(Admission.REGULATOR_FLOOR)[0].symbol
    async with test_context["session_factory"]() as session:
        await publish(session)
        result = await write_reviewer_passport(
            session, symbol=symbol, name=symbol, approved=True, decision=_decision(),
            reviewer_label="Hilal Markets reviewer", pages=[PAGE],
        )
        outcome = await publish(session)
        current = await session.scalar(
            select(AssetShariaAssessment).where(
                AssetShariaAssessment.canonical_asset == symbol,
                AssetShariaAssessment.valid_until.is_(None),
            )
        )
        assert current.id == result.assessment.id
        assert outcome.assessments_written == 0


# --- Red flags reach the task -----------------------------------------------------


async def test_red_flags_raise_the_task_and_get_their_own_tag(test_context):
    review = _clean_review()
    review.red_flags = [red_flag("It hints at lending.", source="ai", url="https://x.example")]
    async with test_context["session_factory"]() as session:
        run, case = await _run(session, test_context["settings"], review)
        assert run.review_report["red_flags"][0]["text"] == "It hints at lending."
        assert run.hold_state == "for_review"
        assert case.priority == "high" and case.risk_severity == "medium"
        assert case.requested_evidence[0] == "Red flag: It hints at lending."
        detail = await ShariaAdminDashboardService(session).case_detail(case.id)
        assert detail["tag"].tag == RED_FLAGS


# --- The research page lists only what a person decided --------------------------


async def test_the_research_page_lists_only_coins_a_reviewer_decided(test_context):
    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        await _run(session, settings, _clean_review(), symbol="UNDEC")
        _run_row, approved_case = await _run(session, settings, _clean_review(), symbol="GOOD")
        _run_row, rejected_case = await _run(session, settings, _held_review(), symbol="BADX")
        admin = await _reviewer(session)
        service = ShariaGovernanceService(session, settings)
        await service.approve_new_coin(
            approved_case.id, admin_user_id=admin.id, reason="all fine here", public_reasons=REASONS
        )
        await service.reject_and_store(
            rejected_case.id,
            admin_user_id=admin.id,
            reason="It lends money.",
            public_reasons=["It lends money."],
        )
        await session.flush()

        reader = AutomatedResearchReader(session)
        page = await reader.page()
        listed = {row["symbol"]: row["verdict"] for row in page["research_rows"]}
        assert listed == {"GOOD": "approved", "BADX": "not_approved"}
        assert page["research_counts"] == {"all": 2, "approved": 1, "not_approved": 1}
        assert await reader.detail("UNDEC") is None
        detail = await reader.detail("BADX")
        assert detail["reasons"] == ["It lends money."]
        assert detail["passport_url"] == "/passports/badx"


# --- Quick decisions on many cases at once ------------------------------------------


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("approve", ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS),
        ("reject", ShariaAssetStatus.EXCLUDED),
    ],
)
async def test_a_quick_decision_on_a_new_coin_publishes_its_passport(
    test_context, action, status
):
    from ai_market_monitor.services.system_brain_bulk_review import BulkReviewService

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        _run_row, case = await _run(session, settings, _clean_review())
        admin = await _reviewer(session)
        await session.commit()
        outcome = await BulkReviewService(session, settings).apply(
            [case.id],
            action=action,
            reason="Read the report. The pages describe payments only.",
            admin_user_id=admin.id,
        )
        assert [item.applied for item in outcome.results] == [True]
        row = await session.scalar(
            select(AssetShariaAssessment).where(AssetShariaAssessment.valid_until.is_(None))
        )
        assert row.status == status
        # One reason typed for many cases: the reviewer's own sentences, never an AI's.
        assert row.evidence_snapshot["reasons"] == [
            "Read the report.",
            "The pages describe payments only.",
        ]


# --- Machine results in the methodology file go to a reviewer -----------------------


def test_the_methodology_file_can_never_publish_a_machine_reading():
    from ai_market_monitor.services.hilal_methodology import (
        AdmissionError,
        AdmittedAsset,
        Outcome,
        Source,
        admitted_assets,
    )

    for outcome in (Outcome.ADMITTED, Outcome.REFUSED, Outcome.NOT_ENOUGH_DATA):
        with pytest.raises(AdmissionError) as refused:
            AdmittedAsset(
                symbol="ZZZ",
                name="Zzz",
                admission=Admission.AUTOMATED_SCREEN,
                outcome=outcome,
                decided_on=datetime.now(UTC).date(),
                reasons=("A machine read it.",),
                sources=(
                    Source("https://zzz.example/", "Zzz", "website", datetime.now(UTC).date()),
                ),
            )
        assert refused.value.code == "machine_route_needs_a_reviewer"
    # What is left in the file: the regulator floor, and the owner's meme refusals.
    for item in admitted_assets():
        assert item.admission is Admission.REGULATOR_FLOOR or item.is_meme_refusal, item.symbol


async def test_a_result_the_file_dropped_is_taken_down_and_sent_to_a_reviewer(test_context):
    from ai_market_monitor.services.automated_screen_pipeline import AutomatedScreenPipeline

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        methodology, _ = await ensure_methodology(session)
        # A machine result an older file published, for a coin the machine has a report on.
        _run_row, case = await _run(session, settings, _clean_review(), symbol="OLDX")
        session.add(
            AssetShariaAssessment(
                canonical_asset="OLDX",
                asset_name="Old X",
                methodology_id=methodology.id,
                status=ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
                summary="Read by machine.",
                qualifications=[],
                exclusion_reasons=[],
                evidence_snapshot={"human_reviewed": False, "fingerprint": "old"},
                reviewed_by="Hilal Markets automated screen (no human reviewer)",
                reviewed_at=datetime.now(UTC),
                valid_from=datetime.now(UTC),
            )
        )
        await session.flush()
        result = await publish(session)
        assert result.withdrawn == ["OLDX"]
        live = await session.scalar(
            select(func.count(AssetShariaAssessment.id)).where(
                AssetShariaAssessment.canonical_asset == "OLDX",
                AssetShariaAssessment.valid_until.is_(None),
            )
        )
        assert live == 0

        outcome = await AutomatedScreenPipeline(session, settings).send_to_review(
            ["OLDX", "NEVERREAD"]
        )
        assert outcome == {"OLDX": "filed", "NEVERREAD": "never_read"}
        assert case.state == "ready_for_review" and case.done_at is None


async def test_a_coin_a_person_already_decided_is_left_alone(test_context):
    from ai_market_monitor.services.automated_screen_pipeline import AutomatedScreenPipeline

    settings = test_context["settings"]
    async with test_context["session_factory"]() as session:
        _run_row, case = await _run(session, settings, _held_review(), symbol="DONEX")
        admin = await _reviewer(session)
        await ShariaGovernanceService(session, settings).reject_and_store(
            case.id, admin_user_id=admin.id, reason="It lends money.", public_reasons=["It lends."]
        )
        outcome = await AutomatedScreenPipeline(session, settings).send_to_review(["DONEX"])
        assert outcome == {"DONEX": "already_decided"}
        assert case.state == "rejected"
