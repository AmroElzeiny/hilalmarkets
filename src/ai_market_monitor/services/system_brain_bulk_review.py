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

"Mark every condition as passed" is a **human** judgement stated once and applied to the
selected cases. It is refused, per case, when the methodology does not allow ``pass`` on
one of its criteria: the honest answer there is that this case needs opening, not that
its criterion should be recorded as something it is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import ReviewActionBatch, ReviewCase, ReviewDecision
from ai_market_monitor.services.sharia_governance import (
    ShariaGovernanceError,
    ShariaGovernanceService,
)

#: The outcome a reviewer means by "this condition is met", and the scope decision that
#: means "this use is covered". Named once so the button, the request and the recorded
#: decision cannot mean three different things.
PASS_OUTCOME = "pass"
COVERED_DECISION = "covered"

#: The two quick decisions the page offers.
BULK_ACTIONS = ("approve", "reject")

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


@dataclass(slots=True)
class BatchOutcome:
    batch_id: UUID | None
    action: str
    results: list[CaseOutcome] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return sum(1 for item in self.results if item.applied)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if not item.applied)

    def message(self) -> str:
        if not self.results:
            return "No case was selected."
        verb = "approved" if self.action == "approve" else "rejected and stored"
        if not self.failed:
            return f"{self.applied} case(s) {verb}. Use Undo if this was a mistake."
        return (
            f"{self.applied} case(s) {verb}. {self.failed} could not be decided this way "
            "and are listed below — open them one by one."
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
        if applied:
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
        previous_state = case.state
        previous_publication_state = case.publication_state
        try:
            if action == "reject":
                decision = await self.governance.reject_and_store(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                )
            else:
                criteria, use_cases = await self._all_conditions_pass(case, reason=reason)
                decision = await self.governance.approve_for_publication(
                    case_id,
                    admin_user_id=admin_user_id,
                    reason=reason,
                    criterion_decisions=criteria,
                    use_case_decisions=use_cases,
                )
        except (ShariaGovernanceError, BulkReviewError) as exc:
            # One case refusing must not roll back the ones already recorded, so the
            # failure is reported next to its case and the loop continues.
            return CaseOutcome(case_id, reference, False, str(exc))
        return CaseOutcome(
            case_id,
            reference,
            True,
            "Recorded.",
            previous_state=previous_state,
            previous_publication_state=previous_publication_state,
            decision_id=decision.id,
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
            await self.governance.undo_decision(
                case_id,
                admin_user_id=admin_user_id,
                reason=reason,
                decision_id=UUID(str(decision_id)),
                previous_state=str(item.get("previous_state") or ""),
                previous_publication_state=str(item.get("previous_publication_state") or ""),
            )
        except ShariaGovernanceError as exc:
            return CaseOutcome(case_id, reference, False, str(exc))
        return CaseOutcome(case_id, reference, True, "Put back.")


async def latest_decision_id(session: AsyncSession, case_id: UUID) -> UUID | None:
    row = await session.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.review_case_id == case_id)
        .order_by(ReviewDecision.decision_version.desc())
        .limit(1)
    )
    return row.id if row else None
