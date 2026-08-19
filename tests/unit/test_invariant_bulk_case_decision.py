"""Deciding several cases at once, and taking it back.

The Cases page can now tick several cases and record one decision on all of them, and
undo that decision if it was a mistake. Both are conveniences over a governed record, so
the rules they must not break are the interesting part:

* a quick decision is **not** a shortcut around review. Every case goes through the same
  ``ShariaGovernanceService`` call a single case goes through;
* "mark every condition as passed" **fails closed**. A methodology that does not allow
  ``pass`` on a condition refuses that case and names it; nothing near it is substituted;
* one case refusing must not undo the cases already recorded;
* undo **restores state, it never deletes a decision**. The record of what was decided,
  on what evidence, stays for ever;
* undo refuses when a newer decision has been recorded, and refuses on a published
  Passport — the recorded way back from a published one is a safety hold.

The fixtures come from ``tests/services/test_sc_malaysia_governance.py`` rather than
being written again here: a second copy of a methodology fixture is a second definition
of what a valid review looks like, and the two would drift.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from ai_market_monitor.db.models import AuditEvent, ReviewCase, ReviewDecision, User
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.services.sharia_governance import (
    ALLOWED_UNDO_STATES,
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.system_brain_bulk_review import (
    BULK_ACTIONS,
    COVERED_DECISION,
    MAX_BATCH_SIZE,
    MIN_REASON_LENGTH,
    PASS_OUTCOME,
    BulkReviewError,
    BulkReviewService,
)
from tests.services.test_sc_malaysia_governance import (
    TEST_CRITERIA,
    TEST_USE_CASES,
    _ready_case,
)

REASON = "Every condition is met by the retained official evidence for this asset."

#: How many fields the web framework will accept in one posted form. Read out of
#: Starlette rather than written down, so an upgrade that changes it changes this too
#: instead of leaving a number here that quietly stopped being true.
STARLETTE_MAX_FORM_FIELDS = inspect.signature(Request.form).parameters["max_fields"].default


async def _reviewer(session) -> User:
    reviewer = User(display_name="Quick reviewer", role=UserRole.ADMIN)
    session.add(reviewer)
    await session.flush()
    return reviewer


# --------------------------------------------------------------------------------
# The quick decision goes through the normal path.
# --------------------------------------------------------------------------------


async def test_a_bulk_approval_records_every_condition_as_passed(test_context):
    """Marked passed, with the reviewer's own sentence against each one.

    Nothing is generated. The audit trail has to contain what a person said, and one
    sentence said once and applied deliberately to a selection is exactly that.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id],
            action="approve",
            reason=REASON,
            admin_user_id=reviewer.id,
        )
        await session.commit()
        decision = await session.scalar(
            select(ReviewDecision).where(ReviewDecision.review_case_id == case.id)
        )
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.applied == 1
    assert outcome.failed == 0
    assert refreshed is not None
    # Approving publishes. A reviewer approving an asset is asking for it to be in front
    # of customers, so the approval and the publication run together — two recorded
    # governed steps, one press. Leaving cases at "approved, not published" is what let a
    # whole queue be approved and nothing reach anybody.
    assert refreshed.state == "published"
    assert refreshed.publication_state == "published"
    assert outcome.published == 1
    assert decision is not None
    assert {row["outcome"] for row in decision.criterion_decisions} == {PASS_OUTCOME}
    assert {row["decision"] for row in decision.use_case_decisions} == {COVERED_DECISION}
    assert all(
        row["reviewer_explanation"] == REASON for row in decision.criterion_decisions
    )
    assert all(row["reason"] == REASON for row in decision.use_case_decisions)


async def test_a_bulk_rejection_keeps_the_evidence(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id],
            action="reject",
            reason="The official source does not cover this asset at all.",
            admin_user_id=reviewer.id,
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.applied == 1
    assert refreshed is not None
    assert refreshed.state == "rejected"
    assert refreshed.publication_state == "stored_not_published"


@pytest.mark.parametrize("action", BULK_ACTIONS)
async def test_a_reason_shorter_than_the_floor_is_refused_for_every_action(
    test_context, action
):
    """A bulk decision is not a smaller decision, so it uses the same floor."""

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        with pytest.raises(BulkReviewError) as refused:
            await BulkReviewService(session, test_context["settings"]).apply(
                [case.id],
                action=action,
                reason="x" * (MIN_REASON_LENGTH - 1),
                admin_user_id=reviewer.id,
            )
        await session.rollback()

    assert refused.value.code == "reason_required"


async def test_an_unknown_action_is_refused_rather_than_guessed(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        with pytest.raises(BulkReviewError) as refused:
            await BulkReviewService(session, test_context["settings"]).apply(
                [case.id],
                action="publish",
                reason=REASON,
                admin_user_id=reviewer.id,
            )
        await session.rollback()

    assert refused.value.code == "unknown_action"


async def test_nothing_selected_is_refused_rather_than_treated_as_everything(
    test_context,
):
    async with test_context["session_factory"]() as session:
        reviewer = await _reviewer(session)
        with pytest.raises(BulkReviewError) as refused:
            await BulkReviewService(session, test_context["settings"]).apply(
                [],
                action="approve",
                reason=REASON,
                admin_user_id=reviewer.id,
            )

    assert refused.value.code == "nothing_selected"


async def test_more_cases_than_the_ceiling_is_refused(test_context):
    """One over the ceiling is refused, whatever the ceiling currently is."""

    async with test_context["session_factory"]() as session:
        reviewer = await _reviewer(session)
        with pytest.raises(BulkReviewError) as refused:
            await BulkReviewService(session, test_context["settings"]).apply(
                [uuid4() for _ in range(MAX_BATCH_SIZE + 1)],
                action="approve",
                reason=REASON,
                admin_user_id=reviewer.id,
            )

    assert refused.value.code == "too_many_cases"


async def test_exactly_the_ceiling_is_not_refused_for_being_too_many(test_context):
    """The ceiling is inclusive, and off-by-one at the boundary is a real failure.

    Selecting exactly the maximum has to be a decision the service will take. The ids
    here belong to no case, so each one comes back "this case no longer exists" — which
    is the point: the batch was *accepted* and judged case by case, rather than thrown
    away whole for being one too large.
    """

    async with test_context["session_factory"]() as session:
        reviewer = await _reviewer(session)
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [uuid4() for _ in range(MAX_BATCH_SIZE)],
            action="approve",
            reason=REASON,
            admin_user_id=reviewer.id,
        )

    assert len(outcome.results) == MAX_BATCH_SIZE
    assert outcome.applied == 0


def test_the_ceiling_survives_the_form_the_page_posts_it_in():
    """A ceiling the request cannot carry is not a ceiling, it is a hidden failure.

    The decision is posted as an ordinary form, one field per selected case. Starlette
    refuses a form with more than 1000 fields *before* any Hilal Markets code runs, so a
    ceiling above that turns a large selection into a bare HTTP 400 with no message a
    reviewer could act on.

    This test is why the page still lists at most 300 cases. Raising that list past what
    one request can carry means packing the ids into a single field first — the note on
    ``MAX_BATCH_SIZE`` says so, and this test is what stops it being forgotten.
    """

    from ai_market_monitor.api.routers import system_brain as router_module

    listed = router_module.CASES_PAGE_LIMIT
    # Three more fields ride along with the ids: the action, the reason and the token.
    assert listed + 3 <= STARLETTE_MAX_FORM_FIELDS
    assert listed <= MAX_BATCH_SIZE


async def test_a_case_that_cannot_be_decided_this_way_is_named_and_the_rest_go_through(
    test_context,
):
    """One refusal must not roll back what was already recorded.

    A missing case stands in for every per-case refusal — a blocked criterion, stale
    evidence, a case in the wrong state. All of them come back the same way: reported
    against that case, with the others untouched.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        missing = uuid4()
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id, missing],
            action="approve",
            reason=REASON,
            admin_user_id=reviewer.id,
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)

    assert outcome.applied == 1
    assert outcome.failed == 1
    assert refreshed is not None and refreshed.state == "published"
    refused = [item for item in outcome.results if not item.applied]
    assert refused[0].case_id == missing
    assert refused[0].message
    # The message names the case, so a reviewer knows which one to open.
    assert str(missing) in outcome.message() or refused[0].reference


async def test_the_same_case_selected_twice_is_decided_once(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        outcome = await BulkReviewService(session, test_context["settings"]).apply(
            [case.id, case.id],
            action="approve",
            reason=REASON,
            admin_user_id=reviewer.id,
        )
        await session.commit()
        decisions = int(
            await session.scalar(
                select(func.count(ReviewDecision.id)).where(
                    ReviewDecision.review_case_id == case.id
                )
            )
            or 0
        )

    assert len(outcome.results) == 1
    assert decisions == 1


# --------------------------------------------------------------------------------
# Undo.
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("action", BULK_ACTIONS)
async def test_undo_puts_every_case_back_and_keeps_the_decision_in_the_history(
    test_context, action
):
    """The way back from a mistake, not an eraser.

    Asserted for both quick decisions: an undo that worked for one and silently did
    nothing for the other would be the same defect wearing a different name.
    """

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        service = BulkReviewService(session, test_context["settings"])
        before_state = case.state
        before_publication = case.publication_state
        applied = await service.apply(
            [case.id],
            action=action,
            reason=REASON,
            admin_user_id=reviewer.id,
        )
        await session.commit()
        assert applied.batch_id is not None

        undone = await service.undo(
            applied.batch_id,
            admin_user_id=reviewer.id,
            reason="Selected the wrong rows on the list.",
        )
        await session.commit()
        refreshed = await session.get(ReviewCase, case.id)
        decisions = int(
            await session.scalar(
                select(func.count(ReviewDecision.id)).where(
                    ReviewDecision.review_case_id == case.id
                )
            )
            or 0
        )
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "sharia.review_decision_undone",
                AuditEvent.target_id == str(case.id),
            )
        )

    assert undone.applied == 1
    assert refreshed is not None
    assert refreshed.state == before_state
    assert refreshed.publication_state == before_publication
    assert refreshed.done_at is None
    # The decision that was undone is still there, whole.
    assert decisions == 1
    assert audit is not None
    assert audit.metadata_redacted["decision_record_retained"] is True
    assert audit.metadata_redacted["restored_to"] == before_state


async def test_the_same_batch_cannot_be_undone_twice(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        service = BulkReviewService(session, test_context["settings"])
        applied = await service.apply(
            [case.id], action="approve", reason=REASON, admin_user_id=reviewer.id
        )
        await session.commit()
        assert applied.batch_id is not None
        await service.undo(
            applied.batch_id, admin_user_id=reviewer.id, reason="Wrong rows."
        )
        await session.commit()
        with pytest.raises(BulkReviewError) as refused:
            await service.undo(
                applied.batch_id, admin_user_id=reviewer.id, reason="Wrong rows again."
            )

    assert refused.value.code == "already_undone"


async def test_one_reviewer_cannot_undo_another_reviewers_decision(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        other = User(display_name="Someone else", role=UserRole.ADMIN)
        session.add(other)
        await session.flush()
        service = BulkReviewService(session, test_context["settings"])
        applied = await service.apply(
            [case.id], action="approve", reason=REASON, admin_user_id=reviewer.id
        )
        await session.commit()
        assert applied.batch_id is not None
        with pytest.raises(BulkReviewError) as refused:
            await service.undo(
                applied.batch_id, admin_user_id=other.id, reason="Not mine to undo."
            )

    assert refused.value.code == "batch_not_found"


async def test_an_undo_is_refused_once_a_newer_decision_exists(test_context):
    """An undo must never silently reverse somebody else's later work."""

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        service = BulkReviewService(session, test_context["settings"])
        applied = await service.apply(
            [case.id], action="reject", reason=REASON, admin_user_id=reviewer.id
        )
        await session.commit()
        assert applied.batch_id is not None

        # Somebody reopens the case and records a further decision on it.
        governance = ShariaGovernanceService(session, test_context["settings"])
        await governance.reopen_case(
            case.id,
            admin_user_id=reviewer.id,
            reason="New official evidence arrived for this asset.",
        )
        await governance.request_more_evidence(
            case.id,
            admin_user_id=reviewer.id,
            reason="The newer official page has to be captured first.",
            requested_evidence=["Current official listing page"],
        )
        await session.commit()

        undone = await service.undo(
            applied.batch_id, admin_user_id=reviewer.id, reason="Changed my mind."
        )

    assert undone.applied == 0
    # Said in the plain words every screen uses for this refusal, and it names the one
    # thing the reviewer can do instead.
    assert "newer decision was recorded" in undone.results[0].message
    assert "Reopen the case" in undone.results[0].message


async def test_a_published_passport_is_never_undone_here(test_context):
    """It is already in front of customers. The recorded way back is a safety hold."""

    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        governance = ShariaGovernanceService(session, test_context["settings"])
        decision = await governance.approve_for_publication(
            case.id,
            admin_user_id=reviewer.id,
            reason=REASON,
            criterion_decisions=[
                {"key": item["key"], "outcome": PASS_OUTCOME, "reviewer_explanation": REASON}
                for item in TEST_CRITERIA
            ],
            use_case_decisions=[
                {"key": item["key"], "decision": COVERED_DECISION, "reason": REASON}
                for item in TEST_USE_CASES
            ],
        )
        await governance.publish_approved(
            case.id, admin_user_id=reviewer.id, reason="Publishing the approved version."
        )
        await session.commit()

        with pytest.raises(ShariaGovernanceError) as refused:
            await governance.undo_decision(
                case.id,
                admin_user_id=reviewer.id,
                reason="I want to take the publication back.",
                decision_id=decision.id,
                previous_state="ready_for_review",
                previous_publication_state="unpublished",
            )

    assert refused.value.code in {"undo_published_blocked", "undo_superseded"}


@pytest.mark.parametrize(
    "restored", ["published", "approved", "superseded", "rejected", "stored", "nonsense"]
)
async def test_an_undo_may_only_restore_a_state_a_case_can_wait_in(test_context, restored):
    """Undo is a way back from a decision taken too quickly.

    Restoring a case into a *different* finished state would let one mistake be
    corrected into another one, so only waiting states are allowed.
    """

    assert restored not in ALLOWED_UNDO_STATES
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        service = BulkReviewService(session, test_context["settings"])
        applied = await service.apply(
            [case.id], action="reject", reason=REASON, admin_user_id=reviewer.id
        )
        await session.commit()
        decision_id = applied.results[0].decision_id
        assert decision_id is not None

        with pytest.raises(ShariaGovernanceError) as refused:
            await ShariaGovernanceService(
                session, test_context["settings"]
            ).undo_decision(
                case.id,
                admin_user_id=reviewer.id,
                reason="Putting this somewhere it does not belong.",
                decision_id=decision_id,
                previous_state=restored,
                previous_publication_state="unpublished",
            )

    assert refused.value.code == "undo_state_unknown"


async def test_the_latest_undoable_batch_is_the_one_the_page_offers(test_context):
    async with test_context["session_factory"]() as session:
        case, _ = await _ready_case(session)
        reviewer = await _reviewer(session)
        service = BulkReviewService(session, test_context["settings"])
        assert await service.latest_undoable_batch(reviewer.id) is None

        applied = await service.apply(
            [case.id], action="approve", reason=REASON, admin_user_id=reviewer.id
        )
        await session.commit()
        offered = await service.latest_undoable_batch(reviewer.id)
        assert offered is not None and offered.id == applied.batch_id

        assert applied.batch_id is not None
        await service.undo(
            applied.batch_id, admin_user_id=reviewer.id, reason="Wrong rows."
        )
        await session.commit()
        # Once taken back, it is no longer offered.
        assert await service.latest_undoable_batch(reviewer.id) is None
