"""A new coin, end to end: read, AI-checked, reported, and left for a person.

The pipeline runs against the real database models with the network replaced: the
provider, the crawler and the model are fakes, so what is tested is what gets written.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from ai_market_monitor.db.models import (
    AIUsageEvent,
    AssetShariaAssessment,
    AutomatedScreenRun,
    PublishedAssetAssessment,
    ReviewCase,
    ReviewDecision,
    User,
)
from ai_market_monitor.db.models.enums import ReviewCaseType, UserRole
from ai_market_monitor.services.automated_screen_pipeline import AutomatedScreenPipeline
from ai_market_monitor.services.coin_terms_ai_review import AIReview
from ai_market_monitor.services.coinmarketcap import CoinLinks
from ai_market_monitor.services.sharia_admin_dashboard import ShariaAdminDashboardService
from ai_market_monitor.services.sharia_case_tags import HELD_BACK, NEW_COIN_REPORT
from ai_market_monitor.services.sharia_conditions import Activity
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from tests.coin_review_fakes import BLOCKED, FakeAI, FakeCrawler, FakeProvider
from tests.coin_review_fakes import held_review as _held_review


async def _run(session, settings, review: AIReview, symbol: str = "NEWX"):
    pipeline = AutomatedScreenPipeline(
        session,
        settings,
        coinmarketcap=FakeProvider(),
        crawler=FakeCrawler(),
        ai_reviewer=FakeAI(review, settings),
    )
    result = await pipeline.run([symbol])
    await session.flush()
    run = await session.scalar(
        select(AutomatedScreenRun).where(AutomatedScreenRun.symbol == symbol)
    )
    case = await session.get(ReviewCase, run.review_case_id) if run.review_case_id else None
    return result, run, case


async def _published_count(session) -> int:
    return int(
        (await session.scalar(select(func.count(AssetShariaAssessment.id))) or 0)
        + (await session.scalar(select(func.count(PublishedAssetAssessment.id))) or 0)
        + (await session.scalar(select(func.count(ReviewDecision.id))) or 0)
    )


async def test_a_term_found_by_the_ai_holds_the_coin_back_and_files_a_task(test_context):
    async with test_context["session_factory"]() as session:
        result, run, case = await _run(session, test_context["settings"], _held_review())

        assert result.held_back == 1 and result.ai_reviewed == 1
        assert run.hold_state == "held_back"
        assert run.ai_review_state == "completed"
        assert run.published is False
        report = run.review_report
        assert report["terms_found"][0]["activity"] == BLOCKED.value
        assert report["terms_found"][0]["source"] == "ai"
        assert "The team is not named anywhere." in report["doubts"]
        assert "The code is open source." in report["trust_points"]

        assert case is not None
        assert case.case_type == ReviewCaseType.AUTOMATED_COIN_REVIEW
        assert case.state == "ready_for_review"
        assert case.publication_state == "unpublished"
        assert case.priority == "high" and case.risk_severity == "high"
        assert "Held back" in case.human_review_reason
        assert "The team is not named anywhere." in case.requested_evidence

        usage = await session.scalar(
            select(AIUsageEvent).where(AIUsageEvent.operation == "coin_terms_review")
        )
        assert usage is not None and usage.provider == "opencode_go"
        assert usage.reasoning_effort == "high"

        # Held back is not a status: nothing governed was written.
        assert await _published_count(session) == 0


async def test_no_term_found_is_still_only_a_task_for_a_person(test_context):
    async with test_context["session_factory"]() as session:
        review = AIReview(state="completed", model="m", reasoning_effort="high")
        _result, run, case = await _run(session, test_context["settings"], review)

        assert run.hold_state == "for_review"
        assert case.priority == "normal" and case.risk_severity == "none"
        assert "This is not an approval" in case.human_review_reason
        assert await _published_count(session) == 0


@pytest.mark.parametrize("ai_state", ["failed", "not_configured", "disabled"])
async def test_the_report_is_filed_even_when_the_ai_did_not_run(test_context, ai_state):
    async with test_context["session_factory"]() as session:
        _result, run, case = await _run(
            session, test_context["settings"], AIReview(state=ai_state)
        )
        assert run.ai_review_state == ai_state
        assert run.ai_review_attempts == (1 if ai_state == "failed" else 0)
        assert case is not None
        assert any("only the fixed rules were applied" in d for d in run.review_report["doubts"])
        assert await session.scalar(select(func.count(AIUsageEvent.id))) == 0


async def test_a_retry_updates_the_open_task_instead_of_adding_one(test_context):
    async with test_context["session_factory"]() as session:
        _r, _first_run, first = await _run(
            session, test_context["settings"], AIReview(state="failed")
        )
        assert first.risk_severity == "none"
        _r, run, second = await _run(session, test_context["settings"], _held_review())
        assert second.id == first.id
        assert second.risk_severity == "high"
        assert run.ai_review_attempts == 2
        count = await session.scalar(
            select(func.count(ReviewCase.id)).where(
                ReviewCase.case_type == ReviewCaseType.AUTOMATED_COIN_REVIEW
            )
        )
        assert count == 1


async def _reviewer(session) -> User:
    admin = User(display_name="Reviewer", role=UserRole.ADMIN)
    session.add(admin)
    await session.flush()
    return admin


async def test_keep_it_out_stores_the_decision_and_publishes_nothing(test_context):
    async with test_context["session_factory"]() as session:
        _r, _run_row, case = await _run(session, test_context["settings"], _held_review())
        admin = await _reviewer(session)
        await ShariaGovernanceService(session, test_context["settings"]).reject_and_store(
            case.id, admin_user_id=admin.id, reason="The project lends money; keep it out."
        )
        await session.flush()
        assert case.state == "rejected" and case.done_at is not None
        assert await session.scalar(select(func.count(PublishedAssetAssessment.id))) == 0
        assert await session.scalar(select(func.count(AssetShariaAssessment.id))) == 0

        # A decided task is never reopened by a later reading.
        _r, _again, after = await _run(session, test_context["settings"], _held_review())
        assert after.id == case.id and after.state == "rejected"


async def test_release_closes_the_task_and_says_nothing_was_published(test_context):
    async with test_context["session_factory"]() as session:
        _r, _run_row, case = await _run(session, test_context["settings"], _held_review())
        admin = await _reviewer(session)
        await ShariaGovernanceService(session, test_context["settings"]).dismiss_false_positive(
            case.id, admin_user_id=admin.id, reason="The quoted sentence is about a partner."
        )
        assert case.state == "superseded"
        assert case.publication_state == "unpublished"
        assert await session.scalar(select(func.count(PublishedAssetAssessment.id))) == 0


async def test_the_task_can_never_be_approved_into_a_status(test_context):
    async with test_context["session_factory"]() as session:
        _r, _run_row, case = await _run(session, test_context["settings"], _held_review())
        blocker = await ShariaGovernanceService(
            session, test_context["settings"]
        ).review_blocker(case)
        assert isinstance(blocker, ShariaGovernanceError)


@pytest.mark.parametrize(("review", "tag"), [("held", HELD_BACK), ("clean", NEW_COIN_REPORT)])
async def test_the_review_screen_shows_the_report_under_its_own_tag(test_context, review, tag):
    chosen = _held_review() if review == "held" else AIReview(state="completed")
    async with test_context["session_factory"]() as session:
        _r, run, case = await _run(session, test_context["settings"], chosen)
        detail = await ShariaAdminDashboardService(session).case_detail(case.id)
        assert detail["coin_report"] == run.review_report
        assert detail["tag"].tag == tag


async def test_a_sweep_out_of_time_leaves_the_rest_untouched_for_the_next_one(test_context):
    async with test_context["session_factory"]() as session:
        ai = FakeAI(_held_review(), test_context["settings"])
        pipeline = AutomatedScreenPipeline(
            session,
            test_context["settings"],
            coinmarketcap=FakeProvider(),
            crawler=FakeCrawler(),
            ai_reviewer=ai,
        )
        result = await pipeline.run(["AAA", "BBB"], time_budget_seconds=0)
        assert result.deferred == ["AAA", "BBB"]
        assert ai.calls == 0
        assert await session.scalar(select(func.count(AutomatedScreenRun.id))) == 0


@pytest.mark.parametrize("field", ["name", "slug"])
@pytest.mark.parametrize(
    ("given", "existing", "expected"),
    [
        ("", None, ""),  # a record with nothing, on a row never saved
        ("", "kept", "kept"),  # nothing new: the saved value stays
        ("x" * 500, None, "x" * 180),  # longer than the column: cut, never refused
        ("fresh", "old", "fresh"),
    ],
    ids=["empty-new-row", "empty-keeps-saved", "too-long", "replaces"],
)
def test_the_one_profile_writer_survives_every_shape_of_name(field, given, existing, expected):
    from ai_market_monitor.db.models import ProviderCoinProfile
    from ai_market_monitor.services.unscreened_coin_research import apply_provider_record

    row = ProviderCoinProfile(provider="coinmarketcap", symbol="NEWX")
    setattr(row, field, existing)
    values = {"name": "Coin", "slug": "coin", field: given}
    record = CoinLinks(symbol="NEWX", cmc_id=1, **values)
    apply_provider_record(row, record, datetime.now(UTC))
    assert getattr(row, field) == expected


def test_the_case_type_is_known_to_the_one_owner():
    assert ReviewCaseType("automated_coin_review") is ReviewCaseType.AUTOMATED_COIN_REVIEW
    assert Activity(BLOCKED.value) is BLOCKED
