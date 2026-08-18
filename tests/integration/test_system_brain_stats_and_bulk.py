"""The new System Brain surfaces, driven the way a person drives them.

Three things are checked here that a unit test cannot see: the Stats page renders and
counts what was really measured, the public collector accepts a beacon from a page
nobody is signed in on, and the Cases page's quick decision and Undo work through the
real routes with the real permission and form-token checks.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import (
    ReviewActionBatch,
    ReviewCase,
    SiteVisit,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserRole
from ai_market_monitor.services.site_analytics import TAG_DEFINITIONS
from ai_market_monitor.services.system_brain_bulk_review import MAX_BATCH_SIZE

STATS_PAGES = ("/system-brain/stats", "/dashboard/system-brain/stats")


async def _admin(test_context, email: str = "stats-admin@hilalmarkets.test") -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name="Stats admin", role=UserRole.ADMIN)
        session.add(user)
        await session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=IdentityProvider.EMAIL,
                provider_subject=email,
                normalized_identifier=email,
                display_identifier=email,
                is_verified=True,
                is_primary=True,
            )
        )
        await session.commit()
        return user


def _csrf(text: str) -> str:
    found = re.search(r'name="csrf_token" value="([a-f0-9]+)"', text)
    assert found is not None
    return found.group(1)


# --------------------------------------------------------------------------------
# The public collector.
# --------------------------------------------------------------------------------


async def test_a_visitor_who_is_not_signed_in_can_report_a_visit(test_context):
    """The collector is public by design — the whole point is measuring strangers."""

    key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    opened = await test_context["client"].post(
        "/api/v1/site-analytics/collect",
        json={"event": "open", "session_key": key, "path": "/", "referrer": ""},
    )
    closed = await test_context["client"].post(
        "/api/v1/site-analytics/collect",
        json={"event": "close", "session_key": key, "path": "/", "active_ms": 42_000},
    )

    assert opened.status_code == 204
    assert closed.status_code == 204
    async with test_context["session_factory"]() as session:
        visit = await session.scalar(select(SiteVisit).where(SiteVisit.session_key == key))
    assert visit is not None
    assert visit.is_landing is True
    assert visit.active_ms == 42_000
    assert visit.ended_at is not None


@pytest.mark.parametrize(
    "body",
    [
        {"event": "open", "session_key": "short", "path": "/"},
        {"event": "open", "session_key": "!" * 32, "path": "/"},
        {"event": "open", "session_key": "z" * 32, "path": "/"},
    ],
)
async def test_a_badly_shaped_session_key_is_accepted_and_discarded(test_context, body):
    """The page is already closing; a failure is something it could never act on.

    So the answer is always 204 — and nothing is written, which is the part that
    matters. It is the primary key of a public write.
    """

    response = await test_context["client"].post(
        "/api/v1/site-analytics/collect", json=body
    )
    assert response.status_code == 204
    async with test_context["session_factory"]() as session:
        rows = list((await session.scalars(select(SiteVisit))).all())
    assert rows == []


async def test_the_collector_writes_nothing_while_measurement_is_switched_off(
    test_context,
):
    test_context["settings"].site_visit_measurement_enabled = False
    try:
        response = await test_context["client"].post(
            "/api/v1/site-analytics/collect",
            json={"event": "open", "session_key": "b" * 32, "path": "/"},
        )
        assert response.status_code == 204
        async with test_context["session_factory"]() as session:
            rows = list((await session.scalars(select(SiteVisit))).all())
        assert rows == []
    finally:
        test_context["settings"].site_visit_measurement_enabled = True


# --------------------------------------------------------------------------------
# The Stats page.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("path", STATS_PAGES)
async def test_stats_is_admin_only_at_every_address_it_answers_on(test_context, path):
    anonymous = await test_context["client"].get(path)
    assert anonymous.status_code == 401

    async with test_context["session_factory"]() as session:
        customer = User(display_name="Customer", role=UserRole.USER)
        session.add(customer)
        await session.commit()
        customer_id = customer.id
    refused = await test_context["client"].get(
        path, headers={"X-User-ID": str(customer_id)}
    )
    assert refused.status_code == 403


async def test_stats_shows_the_four_measured_numbers_and_every_tag(test_context):
    admin = await _admin(test_context)
    now = datetime.now(UTC)
    async with test_context["session_factory"]() as session:
        session.add_all(
            [
                SiteVisit(
                    visitor_key="person-one",
                    session_key="1" * 32,
                    path="/",
                    is_landing=True,
                    source="search",
                    device="phone",
                    started_at=now - timedelta(hours=2),
                    last_seen_at=now - timedelta(hours=2),
                    active_ms=45_000,
                    next_action="signup",
                    next_action_detail="/signup",
                ),
                SiteVisit(
                    visitor_key="person-two",
                    session_key="2" * 32,
                    path="/",
                    is_landing=True,
                    source="direct",
                    device="desktop",
                    started_at=now - timedelta(hours=1),
                    last_seen_at=now - timedelta(hours=1),
                    active_ms=15_000,
                ),
            ]
        )
        await session.commit()

    page = await test_context["client"].get(
        "/dashboard/system-brain/stats", headers={"X-User-ID": str(admin.id)}
    )

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store, max-age=0"
    assert 'data-testid="system-brain-stats"' in page.text
    # Two people, two page opens, and a thirty-second average of the measured visits.
    assert 'data-stat="viewers"' in page.text
    assert 'data-count-to="2"' in page.text
    assert "30s" in page.text
    assert "Went to sign up" in page.text
    # Every tag is offered, whether or not anything is behind it.
    for tag in TAG_DEFINITIONS:
        assert f"tag={tag.key}" in page.text
        assert f"{tag.label} <b>" in page.text


@pytest.mark.parametrize("tag", TAG_DEFINITIONS, ids=lambda item: item.key)
async def test_every_tag_on_the_page_can_actually_be_applied(test_context, tag):
    """A chip that returns an error is not a filter, it is a broken link."""

    admin = await _admin(test_context, email=f"tag-{tag.key.replace(':', '-')}@t.test")
    page = await test_context["client"].get(
        f"/dashboard/system-brain/stats?days=30&scope=landing&tag={tag.key}",
        headers={"X-User-ID": str(admin.id)},
    )
    assert page.status_code == 200
    assert tag.label in page.text


async def test_the_stats_link_is_in_the_menu_on_every_system_brain_page(test_context):
    admin = await _admin(test_context, email="menu-admin@hilalmarkets.test")
    home = await test_context["client"].get(
        "/dashboard/system-brain", headers={"X-User-ID": str(admin.id)}
    )
    assert home.status_code == 200
    assert 'href="/dashboard/system-brain/stats"' in home.text
    assert "<span>Stats</span>" in home.text


# --------------------------------------------------------------------------------
# The quick decision and Undo on Cases.
# --------------------------------------------------------------------------------


async def _open_case(test_context) -> ReviewCase:
    async with test_context["session_factory"]() as session:
        case = ReviewCase(
            case_reference="SB-BULK-1",
            case_type="material_source_change",
            state="ready_for_review",
            publication_state="unpublished",
            title="Bulk decision case",
            priority="normal",
            risk_severity="none",
            human_review_reason="A retained source changed and needs a human decision.",
            requested_evidence=[],
            admin_notes=[],
            idempotency_key="bulk-decision-web-test",
        )
        session.add(case)
        await session.commit()
        return case


async def test_the_cases_page_offers_selection_and_a_quick_decision(test_context):
    admin = await _admin(test_context, email="bulk-admin@hilalmarkets.test")
    await _open_case(test_context)

    page = await test_context["client"].get(
        "/dashboard/system-brain/cases", headers={"X-User-ID": str(admin.id)}
    )

    assert page.status_code == 200
    assert 'name="case_id"' in page.text
    assert "data-select-all" in page.text
    assert 'id="bulk-decision-form"' in page.text
    assert 'value="approve"' in page.text
    assert 'value="reject"' in page.text

    # The page carries the same ceiling the endpoint enforces, so "Select all" can stop
    # where the server would refuse. Before this, the limit existed only in Python: the
    # reviewer ticked every row, wrote a reason, pressed Approve, and the batch came
    # back refused whole with nothing decided and no sign a limit was ever there.
    assert f'data-bulk-max="{MAX_BATCH_SIZE}"' in page.text

    # The Inbox shares the same table but is a read-only look at what needs attention.
    # Selection there would offer a decision next to a list that is deliberately short
    # and ranked, which is not the list a reviewer should be deciding in bulk from.
    inbox = await test_context["client"].get(
        "/dashboard/system-brain", headers={"X-User-ID": str(admin.id)}
    )
    assert inbox.status_code == 200
    assert 'name="case_id"' not in inbox.text
    assert "data-select-all" not in inbox.text


async def test_a_quick_rejection_is_recorded_and_can_be_taken_back(test_context):
    admin = await _admin(test_context, email="undo-admin@hilalmarkets.test")
    case = await _open_case(test_context)
    headers = {"X-User-ID": str(admin.id)}

    page = await test_context["client"].get(
        "/dashboard/system-brain/cases", headers=headers
    )
    token = _csrf(page.text)

    recorded = await test_context["client"].post(
        "/dashboard/system-brain/cases/bulk-decision",
        headers=headers,
        data={
            "action": "reject",
            "reason": "The changed wording does not cover this asset at all.",
            "case_id": [str(case.id)],
            "csrf_token": token,
        },
    )
    assert recorded.status_code == 303
    assert "success=" in recorded.headers["location"]

    async with test_context["session_factory"]() as session:
        rejected = await session.get(ReviewCase, case.id)
        batch = await session.scalar(select(ReviewActionBatch))
    assert rejected is not None and rejected.state == "rejected"
    assert batch is not None and batch.applied_count == 1

    # The page now offers the way back, and taking it puts the case where it was.
    after = await test_context["client"].get(
        "/dashboard/system-brain/cases", headers=headers
    )
    assert "bulk-decision/undo" in after.text
    undone = await test_context["client"].post(
        "/dashboard/system-brain/cases/bulk-decision/undo",
        headers=headers,
        data={
            "batch_id": str(batch.id),
            "reason": "Selected the wrong row on the list.",
            "csrf_token": _csrf(after.text),
        },
    )
    assert undone.status_code == 303
    assert "success=" in undone.headers["location"]

    async with test_context["session_factory"]() as session:
        restored = await session.get(ReviewCase, case.id)
    assert restored is not None
    assert restored.state == "ready_for_review"
    assert restored.publication_state == "unpublished"


async def test_a_quick_decision_without_a_valid_form_token_is_refused(test_context):
    admin = await _admin(test_context, email="csrf-admin@hilalmarkets.test")
    case = await _open_case(test_context)

    refused = await test_context["client"].post(
        "/dashboard/system-brain/cases/bulk-decision",
        headers={"X-User-ID": str(admin.id)},
        data={
            "action": "reject",
            "reason": "Trying to decide without the page's own token.",
            "case_id": [str(case.id)],
            "csrf_token": "0" * 64,
        },
    )

    assert refused.status_code == 403
    async with test_context["session_factory"]() as session:
        untouched = await session.get(ReviewCase, case.id)
    assert untouched is not None and untouched.state == "ready_for_review"


async def test_a_customer_cannot_reach_the_quick_decision_at_all(test_context):
    case = await _open_case(test_context)
    async with test_context["session_factory"]() as session:
        customer = User(display_name="Customer", role=UserRole.USER)
        session.add(customer)
        await session.commit()
        customer_id = customer.id

    refused = await test_context["client"].post(
        "/dashboard/system-brain/cases/bulk-decision",
        headers={"X-User-ID": str(customer_id)},
        data={
            "action": "approve",
            "reason": "A customer should never be able to send this.",
            "case_id": [str(case.id)],
            "csrf_token": "0" * 64,
        },
    )

    assert refused.status_code == 403
