"""The reviewer's page for a new-coin report.

The report is shown with its red flags; the reviewer approves or rejects, and either one
publishes the coin's Passport under the Hilal Markets Methodology — but only with reasons
the reviewer confirmed. The AI can draft those reasons; it can never send them.
"""

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
async def test_the_report_page_shows_the_findings_and_offers_approve_and_reject(
    test_context, held
):
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
    assert 'data-testid="coin-report-red-flags"' in html
    assert 'value="approve_new_coin"' in html and "Reject &amp; publish" in html
    assert 'data-testid="public-reasons"' in html and "data-draft-reasons" in html
    # The old buttons that left a decided coin with no Passport are gone.
    assert "Keep it out" not in html and ">Release<" not in html
    # Still no authority-style approval: no criteria to mark passed.
    assert "Mark all as passed" not in html
    assert "not a Shariah ruling" in html
    if held:
        assert "Held back" in html
        assert "The team is not named anywhere." in html
        assert "The code is open source." in html
        assert "AI reader" in html
    else:
        assert "None found on the project" in html
        assert "No red flags found." in html


def _csrf(test_context, user_id) -> str:
    from ai_market_monitor.api.routers.system_brain import _csrf as make

    return make(test_context["settings"], user_id)


async def test_approve_without_confirmed_reasons_is_refused_and_records_nothing(test_context):
    from sqlalchemy import func, select

    from ai_market_monitor.db.models import AssetShariaAssessment, ReviewDecision

    admin = await _admin(test_context)
    case_id = await _case_id(test_context, AIReview(state="completed", model="m"))
    response = await test_context["client"].post(
        f"/dashboard/system-brain/cases/{case_id}/decision",
        headers={"X-User-ID": str(admin.id)},
        data={
            "action": "approve_new_coin",
            "reason": "pages are fine, payments only",
            "public_reasons": "",
            "csrf_token": _csrf(test_context, admin.id),
        },
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    async with test_context["session_factory"]() as session:
        assert await session.scalar(select(func.count(ReviewDecision.id))) == 0
        assert await session.scalar(select(func.count(AssetShariaAssessment.id))) == 0


@pytest.mark.parametrize("action", ["approve_new_coin", "reject_and_store"])
async def test_approve_or_reject_with_confirmed_reasons_publishes_the_passport(
    test_context, action
):
    admin = await _admin(test_context)
    case_id = await _case_id(test_context, AIReview(state="completed", model="m"))
    response = await test_context["client"].post(
        f"/dashboard/system-brain/cases/{case_id}/decision",
        headers={"X-User-ID": str(admin.id)},
        data={
            "action": action,
            "reason": "pages are fine, payments only",
            "public_reasons": "The project runs a payments network.\nIts code is public.",
            "csrf_token": _csrf(test_context, admin.id),
        },
    )
    assert response.status_code == 303
    assert "success=" in response.headers["location"]
    passport = await test_context["client"].get("/passports/newx")
    assert passport.status_code == 200
    assert "The project runs a payments network." in passport.text
    assert "Hilal Markets reviewer" in passport.text
    from ai_market_monitor.services.automated_research_reader import (
        AutomatedResearchReader,
    )

    async with test_context["session_factory"]() as session:
        listed = (await AutomatedResearchReader(session).page())["research_rows"]
        assert [row["symbol"] for row in listed] == ["NEWX"]


async def test_the_draft_endpoint_offers_the_reviewers_own_sentences_without_a_model(
    test_context,
):
    admin = await _admin(test_context)
    case_id = await _case_id(test_context, AIReview(state="completed", model="m"))
    response = await test_context["client"].post(
        f"/dashboard/system-brain/cases/{case_id}/public-reasons-draft",
        headers={"X-User-ID": str(admin.id)},
        data={
            "reason": "The pages describe payments only. The code is public.",
            "csrf_token": _csrf(test_context, admin.id),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reasons"] == ["The pages describe payments only.", "The code is public."]
    assert body["by_ai"] is False and body["note"]
