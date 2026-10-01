"""The reviewer's page for a new-coin report: the report is shown, and no status can be
approved from it."""

from __future__ import annotations

import pytest

from ai_market_monitor.db.models import User, UserIdentity
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.services.automated_screen_pipeline import AutomatedScreenPipeline
from ai_market_monitor.services.coin_terms_ai_review import AIReview
from tests.coin_review_fakes import FakeAI, FakeCrawler, FakeProvider
from tests.coin_review_fakes import held_review as _held_review


async def _admin(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="reviewer", role=UserRole.ADMIN)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject="reviewer@example.com",
                normalized_identifier="reviewer@example.com",
                display_identifier="reviewer@example.com",
                is_verified=True,
                is_primary=True,
            )
        )
        await session.commit()
        return user


async def _case_id(test_context, review: AIReview):
    from sqlalchemy import select

    from ai_market_monitor.db.models import AutomatedScreenRun

    async with test_context["session_factory"]() as session:
        pipeline = AutomatedScreenPipeline(
            session,
            test_context["settings"],
            coinmarketcap=FakeProvider(),
            crawler=FakeCrawler(),
            ai_reviewer=FakeAI(review, test_context["settings"]),
        )
        await pipeline.run(["NEWX"])
        await session.commit()
        run = await session.scalar(select(AutomatedScreenRun))
        return run.review_case_id


@pytest.mark.parametrize("held", [True, False])
async def test_the_report_page_shows_the_findings_and_offers_no_approval(test_context, held):
    admin = await _admin(test_context)
    case_id = await _case_id(
        test_context, _held_review() if held else AIReview(state="completed", model="m")
    )
    page = await test_context["client"].get(
        f"/dashboard/system-brain/cases/{case_id}",
        headers={"X-User-ID": str(admin.id)},
    )
    assert page.status_code == 200
    html = page.text
    assert 'data-testid="coin-report"' in html
    assert 'data-testid="coin-report-decision"' in html
    assert "Keep it out" in html and "Release" in html
    assert "Approve &amp; publish" not in html
    assert "Mark all as passed" not in html
    assert "not a Shariah ruling" in html
    if held:
        assert "Held back" in html
        assert "The team is not named anywhere." in html
        assert "The code is open source." in html
        assert "AI reader" in html
    else:
        assert "None found on the project" in html
