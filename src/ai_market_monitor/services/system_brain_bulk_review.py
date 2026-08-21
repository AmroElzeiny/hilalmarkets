"""One quick decision over several cases, and the way back from it.

The Cases page lets a reviewer tick several cases and record one decision on all of them.
This is the only place that happens, and it is deliberately **not** a shortcut around the
review rules:

* every case goes through the same ``ShariaGovernanceService`` call a single case goes
  through, with the same validation, the same evidence checks and the same audit record;
* nothing is approved that the methodology would not allow — a criterion that cannot be
  passed makes that case fail, with its reason shown, while the others go through;
* the reviewer's own written reason is what is recorded on every case. Nothing is
  written by a model, and nothing is filled in with a default.

**Approve means published.** A reviewer approving an asset is asking for it to be in
front of customers, so the approval and the publication run together, as two recorded
governed steps rather than two clicks. Where publication legitimately has to wait — a
second reviewer is required, or written permission for a provider's content has not been
recorded — the approval is still kept and the case says what it is waiting for. That is
an outcome, not a failure.

Undo takes both steps back: a Passport that reached customers is withdrawn through a
safety hold, which is the recorded way to withdraw one, and only then does the case
return to where it was. Nothing is ever deleted.

"Mark every condition as passed" is a **human** judgement stated once and applied to the
selected cases. It is refused, per case, when the methodology does not allow ``pass`` on
one of its criteria: the honest answer there is that this case needs opening, not that
its criterion should be recorded as something it is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ReviewActionBatch, ReviewCase, ReviewDecision
from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)
from ai_market_monitor.services.sharia_review_blockers import explain_error

logger = logging.getLogger(__name__)

#: The outcome a reviewer means by "this condition is met", and the scope decision that
#: means "this use is covered". Named once so the button, the request and the recorded
#: decision cannot mean three different things.
PASS_OUTCOME = "pass"
COVERED_DECISION = "covered"

#: The quick actions the page offers on a selection.
#:
#: ``start_research`` is not a decision — it is the way out of the refusal every other
#: bulk action gives on a case that has no evidence yet. An imported case arrives with an
#: asset identity and a provider row but **no research folder**, and the approval needs
#: all three, so a reviewer selecting a hundred imported cases got a hundred copies of
#: "This case is missing part of its evidence… Open it and run research." Opening a
#: hundred cases one at a time to press the same button is not review work.
BULK_ACTIONS = ("approve", "reject", "start_research")

#: The actions that record a governed decision on the case. ``start_research`` asks for
#: evidence to be gathered and decides nothing, so it is deliberately not one of them:
#: it writes no ``ReviewDecision``, and it is not undoable, because there is no decision
#: to take back.
BULK_DECISION_ACTIONS = ("approve", "reject")

#: Case types that carry no verdict at all. They are jobs for a person — go and find
#: something — so approving, rejecting or researching one is meaningless rather than
#: merely blocked, and the reviewer is told so in words that name the actual job.
UNDECIDABLE_CASE_TYPES = frozenset({ReviewCaseType.OFFICIAL_SOURCE_GAP})

#: The shortest reason the page will accept. The same floor the single-case decision
#: uses, because a bulk decision is not a smaller decision.
MIN_REASON_LENGTH = 10

#: How many cases one click may decide.
#:
#: This number is the **one owner** of the ceiling. The Cases page reads it and stops the
#: browser from ever ticking more than this, so a selection that the server would refuse
#: can no longer be built. Hard-coding it a second time in the template or in the script
#: is what let the page offer a selection the endpoint then threw away whole.
#:
#: Two limits sit above this one and are the real ceiling in practice. Whoever raises
#: either of them should read this note first:
#:
#: * the Cases page lists at most 300 cases (``limit=300`` in the reviews route), so one
#:   click can never cover more than that many today;
#: * the decision is posted as an ordinary form, and Starlette refuses a form with more
#:   than 1000 fields before any of this code runs. One field per selected case plus
#:   three leaves room for roughly 997 cases per request. Raising the page's list past
#:   that means the ids have to be posted as one packed field instead of one field each.
MAX_BATCH_SIZE = 5000


@dataclass(slots=True)
class CaseOutcome:
    case_id: UUID
    reference: str
    applied: bool
    message: str
    previous_state: str = ""
    previous_publication_state: str = ""
    decision_id: UUID | None = None
    publication_id: UUID | None = None


@dataclass(slots=True)
class BatchOutcome:
    batch_id: UUID | None
    action: str
    results: list[CaseOutcome] = field(default_factory=list)
    #: Whether the research sweep was actually handed to the worker. False means the
    #: cases are marked and waiting, and the sweep will pick them up on its next run —
    #: said plainly rather than reported as a success nobody can see.
    research_queued: bool = True

    @property
    def applied(self) -> int:
        return sum(1 for item in self.results if item.applied)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if not item.applied)

    @property
    def published(self) -> int:
        return sum(1 for item in self.results if item.publication_id is not None)

    def message(self) -> str:
        if not self.results:
            return "No case was selected."
        if self.action.startswith("undo_"):
            return (
                f"{self.applied} case(s) put back. The earlier decision stays in the "
                "history."
            )
        if self.action == "start_research":
            when = (
                "Research is running now."
                if self.research_queued
                else "The cases are marked; research starts on the next sweep."
            )
            done = f"{self.applied} case(s) sent for research. {when}"
            if not self.failed:
                return f"{done} Come back when it finishes, then decide them."
            return (
                f"{done} {self.failed} could not be sent and are listed below — "
                "open them one by one."
            )
        if self.action == "reject":
            done = f"{self.applied} case(s) rejected and stored"
        elif self.published == self.applied:
            done = f"{self.applied} case(s) approved and published"
        else:
            waiting = self.applied - self.published
            done = (
                f"{self.applied} case(s) approved, {self.published} published, "
                f"{waiting} waiting to be published"
            )
        if not self.failed:
            return f"{done}. Use Undo if this was a mistake."
        return (
            f"{done}. {self.failed} could not be decided this way and are listed below "
            "— open them one by one."
        )


class BulkReviewError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BulkReviewService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.governance = ShariaGovernanceService(session, settings)

    async def apply(
        self,
        case_ids: list[UUID],
        *,
        action: str,
        reason: str,
        admin_user_id: UUID,
    ) -> BatchOutcome:
        if action not in BULK_ACTIONS:
            raise BulkReviewError("unknown_action", "That quick decision is not available.")
        if len(reason.strip()) < MIN_REASON_LENGTH:
            raise BulkReviewError(
                "reason_required",
                "Write one sentence saying why, in at least 10 characters. "
                "It is recorded on every selected case.",
            )
        unique_ids = list(dict.fromkeys(case_ids))
        if not unique_ids:
            raise BulkReviewError("nothing_selected", "Select at least one case first.")
        if len(unique_ids) > MAX_BATCH_SIZE:
            raise BulkReviewError(
                "too_many_cases",
                f"Select at most {MAX_BATCH_SIZE} cases so each decision stays a review.",
            )

        outcome = BatchOutcome(batch_id=None, action=action)
        for case_id in unique_ids:
            outcome.results.append(
                await self._apply_one(
                    case_id,
                    action=action,
                    reason=reason,
                    admin_user_id=admin_user_id,
                )
            )

        applied = [item for item in outcome.results if item.applied]
        if action == "start_research":
            # Deliberately does **not** queue the worker here. This runs inside the
            # caller's transaction, and the sweep reads the cases from the database — a
            # task sent now can start before the commit and find the old states. The
            # caller queues it with `queue_research_sweep` once the commit has landed.
            outcome.research_queued = False
            return outcome
        if applied and action in BULK_DECISION_ACTIONS:
            batch = ReviewActionBatch(
                actor_user_id=admin_user_id,
                action=action,
                reason=reason.strip(),
                items=[
                    {
                        "case_id": str(item.case_id),
                        "reference": item.reference,
                        "decision_id": str(item.decision_id) if item.decision_id else None,
                        "previous_state": item.previous_state,
                        "previous_publication_state": item.previous_publication_state,
                        # Kept so Undo knows this one reached customers and has to be
                        # taken off the public Passport before the case goes back.
                        "publication_id": (
                            str(item.publication_id) if item.publication_id else None
                        ),
                    }
                    for item in applied
                ],
                applied_count=len(applied),
                failed_count=outcome.failed,
                created_at=datetime.now(UTC),
            )
            self.session.add(batch)
            await self.session.flush()
            outcome.batch_id = batch.id
        return outcome

    def queue_research_sweep(self) -> bool:
        """Ask the worker to run its research sweep now instead of on its own timer.

        Call this **after** the selection has been committed. One send for the whole
        selection, not one per case: the task walks its own queue, so a hundred sends
        would be a hundred copies of the same sweep competing for the same provider
        hosts and the same model budget.

        Reported rather than raised. The cases have already been marked and committed by
        the time this runs, so a worker that cannot be reached means "it starts later",
        not "nothing happened" — and telling the reviewer it failed would send them
        looking for a problem that is not theirs.
        """

        try:
            from ai_market_monitor.worker import app as worker_app

            worker_app.send_task("ai_market_monitor.process_sharia_authority_imports")
        except Exception:  # noqa: BLE001 - reported to the reviewer, never raised
            logger.warning("Research sweep could not be queued; it runs on its next turn.")
            return False
        return True

    async def _apply_one(
        self,
        case_id: UUID,
        *,
        action: str,
        reason: str,
        admin_user_id: UUID,
    ) -> CaseOutcome:
        case = await self.session.get(ReviewCase, case_id)
        if case is None:
            return CaseOutcome(case_id, str(case_id), False, "This case no longer exists.")
        reference = case.case_reference
        if case.case_type in UNDECIDABLE_CASE_TYPES:
            # A job for a person, not a case with a verdict. Without this guard the
            # decision path refuses it much later and much worse: the reviewer is told
            # the case "is missing part of its evidence: the asset identity, the
            # official source record, or the research folder", which is true of every
            # blocked case and tells them nothing about what this one actually wants.
            return CaseOutcome(
                case_id,
                reference,
                False,
                "This is a job to find a missing link, not a case to decide. "
                "Open it and add the address.",
            )
        previous_state = case.state
        previous_publication_state = case.publication_state
        publication_id: UUID | None = None
        message = "Recorded."
        try:
            if action == "start_research":
                # Asks for the evidence; decides nothing. Returns early because there is
                # no ReviewDecision to record and nothing for Undo to take back.
                await self.governance.start_research(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                )
                return CaseOutcome(
                    case_id,
                    reference,
                    True,
                    "Queued for research.",
                    previous_state=previous_state,
                    previous_publication_state=previous_publication_state,
                )
            if action == "reject":
                decision = await self.governance.reject_and_store(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                )
            else:
                criteria, use_cases = await self._all_conditions_pass(case, reason=reason)
                outcome = await self.governance.approve_and_publish(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                    criterion_decisions=criteria,
                    use_case_decisions=use_cases,
                )
                decision = outcome.decision
                publication_id = (
                    outcome.publication.id if outcome.publication is not None else None
                )
                message = (
                    "Approved and published."
                    if outcome.published
                    else (outcome.publication_pending_reason or "Approved.")
                )
        except (ShariaGovernanceError, BulkReviewError) as exc:
            # One case refusing must not roll back the ones already recorded, so the
            # failure is reported next to its case and the loop continues — in the plain
            # words every screen uses for that refusal, never the raw rule sentence.
            return CaseOutcome(
                case_id, reference, False, explain_error(exc).sentence()
            )
        return CaseOutcome(
            case_id,
            reference,
            True,
            message,
            previous_state=previous_state,
            previous_publication_state=previous_publication_state,
            decision_id=decision.id,
            publication_id=publication_id,
        )

    async def _all_conditions_pass(
        self,
        case: ReviewCase,
        *,
        reason: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Every methodology condition marked as met, and every use scope as covered.

        The reviewer's own sentence is what is recorded against each condition. Nothing
        is written by a model and nothing is filled in with a stock phrase: the audit
        trail has to contain what a person actually said, and one sentence said once and
        applied deliberately to a selection is exactly that.

        Fails closed. When a criterion does not allow ``pass`` at all, nothing near it is
        substituted — this case is refused and the reviewer opens it. Recording the
        nearest allowed outcome would put a decision in the record nobody made.
        """

        rules = (await self.governance.review_contract(case)).rules
        explanation = reason.strip()
        criteria: list[dict[str, Any]] = []
        for item in rules.required_criteria:
            if PASS_OUTCOME not in item.allowed_outcomes:
                raise BulkReviewError(
                    "criterion_cannot_pass",
                    f'"{item.label}" cannot simply be marked as met. Open this case.',
                )
            criteria.append(
                {
                    "key": item.key,
                    "outcome": PASS_OUTCOME,
                    "reviewer_explanation": explanation,
                }
            )
        use_cases: list[dict[str, Any]] = []
        for use_definition in rules.use_cases:
            if COVERED_DECISION not in use_definition.allowed_decisions:
                raise BulkReviewError(
                    "use_scope_cannot_pass",
                    f'"{use_definition.label}" cannot simply be marked as covered. '
                    "Open this case.",
                )
            use_cases.append(
                {
                    "key": use_definition.key,
                    "decision": COVERED_DECISION,
                    "reason": explanation,
                    "scope": use_definition.default_scope,
                }
            )
        return criteria, use_cases

    async def latest_undoable_batch(self, admin_user_id: UUID) -> ReviewActionBatch | None:
        """The most recent quick decision this reviewer can still take back."""

        return await self.session.scalar(
            select(ReviewActionBatch)
            .where(
                ReviewActionBatch.actor_user_id == admin_user_id,
                ReviewActionBatch.undone_at.is_(None),
                ReviewActionBatch.applied_count > 0,
            )
            .order_by(ReviewActionBatch.created_at.desc())
            .limit(1)
        )

    async def undo(
        self,
        batch_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str,
    ) -> BatchOutcome:
        """Put every case in one batch back where it was.

        Nothing is deleted. Each case gets a further recorded action that restores its
        state, and the decision it is undoing stays in the review history with its
        evidence — which is what makes the history worth reading.
        """

        batch = await self.session.get(ReviewActionBatch, batch_id)
        if batch is None or batch.actor_user_id != admin_user_id:
            raise BulkReviewError("batch_not_found", "That decision could not be found.")
        if batch.undone_at is not None:
            raise BulkReviewError("already_undone", "This decision was already undone.")
        text = reason.strip() or f"Undo of a bulk {batch.action} taken by mistake."
        outcome = BatchOutcome(batch_id=batch.id, action=f"undo_{batch.action}")
        for item in batch.items:
            outcome.results.append(await self._undo_one(item, admin_user_id, text))
        if outcome.applied:
            batch.undone_at = datetime.now(UTC)
            batch.undo_reason = text
        await self.session.flush()
        return outcome

    async def _undo_one(
        self,
        item: dict[str, Any],
        admin_user_id: UUID,
        reason: str,
    ) -> CaseOutcome:
        case_id = UUID(str(item["case_id"]))
        reference = str(item.get("reference") or case_id)
        decision_id = item.get("decision_id")
        if not decision_id:
            return CaseOutcome(
                case_id, reference, False, "No recorded decision to undo on this case."
            )
        try:
            # A quick decision that published a Passport put it in front of customers.
            # Taking that back is a safety hold — the recorded, audited way to withdraw a
            # published Passport — and only then does the case go back where it was.
            # Nothing is deleted: the publication stays in the history, held.
            if item.get("publication_id"):
                await self.governance.place_safety_hold(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                )
            await self.governance.undo_decision(
                case_id,
                admin_user_id=admin_user_id,
                reason=reason,
                decision_id=UUID(str(decision_id)),
                previous_state=str(item.get("previous_state") or ""),
                previous_publication_state=str(item.get("previous_publication_state") or ""),
            )
        except ShariaGovernanceError as exc:
            return CaseOutcome(
                case_id, reference, False, explain_error(exc).sentence()
            )
        return CaseOutcome(case_id, reference, True, "Put back.")


async def latest_decision_id(session: AsyncSession, case_id: UUID) -> UUID | None:
    row = await session.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.review_case_id == case_id)
        .order_by(ReviewDecision.decision_version.desc())
        .limit(1)
    )
    return row.id if row else None
