"""Finish the turns that a crash left half-done.

A Setup Chat turn writes at several points: the user's message, the plan, the executed
draft, the reply. A process that dies between two of them leaves a row saying "still
working" forever. The user sees their message with no answer, and the session stays
locked because that turn still holds the claim.

This module is the only thing allowed to settle those. It reads one rule per state from
:mod:`ai_market_monitor.engine.setup_turn_lifecycle` — it does not have its own opinion
about what a state means — and it obeys two hard limits:

* **It never re-applies a committed mutation.** Anything from ``EXECUTING`` onward is
  reconciled against the canonical draft, never re-run.
* **It never makes a paid call.** Recovery composes from stored facts. A turn that
  already paid for its plan does not pay again to be told what it did.

One owner at a time, through a lease. Two workers cannot both claim the same turn,
because claiming is a conditional UPDATE that only one of them can win.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import getpid
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    SetupChatOperationalIssue,
    SetupChatTurn,
)
from ai_market_monitor.engine.setup_turn_lifecycle import (
    MAX_RECOVERY_ATTEMPTS,
    NON_TERMINAL_STATUSES,
    RecoveryAction,
    TurnStatus,
    lease_seconds,
    recovery_policy,
)
from ai_market_monitor.schemas.setup_agent import SetupTurnExecutionResult

#: How many stalled turns one cycle will settle. Bounded so a bad deploy that stalls
#: thousands of turns cannot turn recovery itself into the outage.
RECOVERY_BATCH_LIMIT = 50


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What one cycle did, for metrics and for the operator queue."""

    examined: int = 0
    recovered: int = 0
    abandoned: int = 0
    escalated: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "examined": self.examined,
            "recovered": self.recovered,
            "abandoned": self.abandoned,
            "escalated": self.escalated,
            "skipped": self.skipped,
        }


def worker_identity() -> str:
    """A name for this recovery owner that is unique per process and per run."""

    return f"{socket.gethostname()[:32]}:{getpid()}:{uuid4().hex[:8]}"


class SetupChatRecoveryService:
    """Claim stalled turns one at a time and settle each by its own rule."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.worker_id = worker_identity()

    async def run_once(self, session: AsyncSession) -> RecoveryOutcome:
        """One bounded pass. Safe to call again immediately, and safe to run twice.

        Restarting mid-pass loses nothing: every turn is claimed and settled in its own
        transaction, so a turn is either untouched or fully settled.
        """

        if self.settings.setup_chat_recovery_disabled:
            # The emergency switch stops new recovery work. It never hides a committed
            # result: replays keep answering from the stored record either way.
            return RecoveryOutcome(skipped=1)

        examined = recovered = abandoned = escalated = 0
        for turn_id in await self._stalled_turn_ids(session):
            claimed = await self._claim(session, turn_id)
            if claimed is None:
                # Another worker won it. That is the lease doing its job.
                continue
            examined += 1
            try:
                disposition = await self._settle(session, claimed)
            except Exception:
                await session.rollback()
                await self._escalate(session, turn_id)
                escalated += 1
                continue
            if disposition in {RecoveryAction.ABANDON, RecoveryAction.ABANDON_AMBIGUOUS}:
                abandoned += 1
            elif disposition is RecoveryAction.NONE:
                pass
            else:
                recovered += 1
            await session.commit()
        return RecoveryOutcome(
            examined=examined,
            recovered=recovered,
            abandoned=abandoned,
            escalated=escalated,
        )

    async def _stalled_turn_ids(self, session: AsyncSession) -> list[object]:
        """Turns that are still owed work and have been silent past their lease."""

        now = datetime.now(UTC)
        rows = await session.scalars(
            select(SetupChatTurn.id)
            .where(
                SetupChatTurn.status.in_([str(item) for item in NON_TERMINAL_STATUSES]),
                or_(
                    SetupChatTurn.lease_expires_at.is_(None),
                    SetupChatTurn.lease_expires_at <= now,
                ),
            )
            .order_by(SetupChatTurn.created_at.asc())
            .limit(RECOVERY_BATCH_LIMIT)
        )
        return list(rows)

    async def _claim(self, session: AsyncSession, turn_id: object) -> SetupChatTurn | None:
        """Take ownership of one turn, or return ``None`` if somebody else did.

        The claim is a conditional UPDATE: it only matches a row whose lease is still
        expired. Two workers running the same query both try, and exactly one changes a
        row. There is no window where both believe they own it.
        """

        now = datetime.now(UTC)
        result = await session.execute(
            update(SetupChatTurn)
            .where(
                SetupChatTurn.id == turn_id,
                SetupChatTurn.status.in_([str(item) for item in NON_TERMINAL_STATUSES]),
                or_(
                    SetupChatTurn.lease_expires_at.is_(None),
                    SetupChatTurn.lease_expires_at <= now,
                ),
            )
            .values(
                lease_owner=self.worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds(TurnStatus.RECOVERING)),
                recovery_attempts=SetupChatTurn.recovery_attempts + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            return None
        turn = await session.get(SetupChatTurn, turn_id)
        if turn is None:
            return None
        # The update went straight to the database, so an instance this session had
        # already loaded still carries the old lease. Reading that stale copy made the
        # winning worker believe it had lost, and the turn was never recovered at all.
        # Refreshing makes the check read what the database actually holds.
        await session.refresh(turn)
        if turn.lease_owner != self.worker_id:
            return None
        return turn

    async def _settle(self, session: AsyncSession, turn: SetupChatTurn) -> RecoveryAction:
        """Apply the one rule that belongs to this turn's state."""

        if turn.recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            # Repeatedly unsettleable. Stop trying and let a person look at it, rather
            # than looping forever and never telling anyone.
            await self._escalate(session, turn.id)
            self._finish(turn, TurnStatus.PERMANENT_FAILURE, "recovery_exhausted")
            turn.failure_code = turn.failure_code or "RECOVERY_EXHAUSTED"
            return RecoveryAction.NONE

        policy = recovery_policy(turn.status)
        action = policy.action

        if action in {RecoveryAction.ABANDON, RecoveryAction.ABANDON_AMBIGUOUS}:
            # Nothing was committed. The same client message id may be sent again, and
            # the session goes back to the user immediately.
            if action is RecoveryAction.ABANDON_AMBIGUOUS:
                # A provider call may have completed without its answer being stored.
                # That is recorded as unknown rather than counted as a call that did or
                # did not happen — the cost report must not invent either.
                usage = dict(turn.recovery_usage_json or {})
                usage["planner_result_unknown"] = True
                turn.recovery_usage_json = usage
            self._finish(turn, TurnStatus.ABANDONED, str(action))
            return action

        if action is RecoveryAction.RECLAIM_LEASE:
            # A previous owner died mid-recovery. Put the turn back to the state its
            # stored evidence supports and let the next cycle apply the real rule.
            turn.status = (
                TurnStatus.EXECUTED.value
                if isinstance(turn.execution_result_json, dict)
                else TurnStatus.RECEIVED.value
            )
            turn.lease_expires_at = datetime.now(UTC)
            turn.recovery_disposition = str(action)
            return action

        if action is RecoveryAction.REVALIDATE_AND_EXECUTE:
            # An authorized plan exists but nothing ran. Executing it now would need the
            # draft to be provably unchanged, and proving that is the request path's job
            # with its own lock. The safe settlement is to release it for the user to
            # retry: the plan is stored, so nothing is lost, and no mutation is guessed.
            self._finish(turn, TurnStatus.ABANDONED, str(action))
            return RecoveryAction.ABANDON

        if action in {
            RecoveryAction.RECONCILE_EXECUTION,
            RecoveryAction.COMPOSE_DETERMINISTIC,
        }:
            return await self._finish_committed_turn(session, turn, action)

        turn.recovery_disposition = str(RecoveryAction.NONE)
        return RecoveryAction.NONE

    async def _finish_committed_turn(
        self,
        session: AsyncSession,
        turn: SetupChatTurn,
        action: RecoveryAction,
    ) -> RecoveryAction:
        """Complete a turn whose mutation may already be in the draft.

        The mutation is never run again. Either the stored execution result is there —
        in which case the draft already carries it and only the reply is missing — or it
        is not, in which case nothing committed and the turn is released.
        """

        stored = turn.execution_result_json
        if not isinstance(stored, dict) or not isinstance(stored.get("execution_result"), dict):
            # ``EXECUTING`` with no stored result means the transaction that would have
            # written both the draft and this record never committed. Nothing was
            # applied, so releasing the turn is safe and re-running is the user's choice.
            self._finish(turn, TurnStatus.ABANDONED, str(action))
            return RecoveryAction.ABANDON

        if turn.assistant_message_id is not None:
            # Draft and reply both landed; only the status was never updated.
            self._finish(turn, TurnStatus.COMPLETED, str(action))
            return action

        chat = await session.get(AISetupChatSession, turn.chat_session_id)
        if chat is None:
            self._finish(turn, TurnStatus.ABANDONED, "chat_deleted")
            return RecoveryAction.ABANDON

        result = SetupTurnExecutionResult.model_validate(stored["execution_result"])
        message = _stored_recovery_reply(stored, result)
        sequence = await self._next_sequence(session, chat)
        assistant = AISetupChatMessage(
            session_id=chat.id,
            sequence=sequence,
            role="assistant",
            message_type="draft_updated" if result.strategy_mutated else "assistant_reply",
            content=message,
            payload={
                "execution_result": stored["execution_result"],
                "draft_v2": stored.get("draft_after"),
                "recovered_from_committed_turn": True,
                # Recovery is free. Saying so stops a recovered turn from being counted
                # as a second paid call against the user's quota.
                "model_call_count": 0,
            },
            created_at=datetime.now(UTC),
        )
        session.add(assistant)
        await session.flush()
        turn.assistant_message_id = assistant.id
        turn.reply_json = {"message": message, "execution_result": stored["execution_result"]}
        usage = dict(turn.recovery_usage_json or {})
        usage["model_calls"] = 0
        usage["composed"] = "deterministic"
        turn.recovery_usage_json = usage
        self._finish(turn, TurnStatus.COMPLETED, str(action))
        return action

    @staticmethod
    async def _next_sequence(session: AsyncSession, chat: AISetupChatSession) -> int:
        highest = await session.scalar(
            select(AISetupChatMessage.sequence)
            .where(AISetupChatMessage.session_id == chat.id)
            .order_by(AISetupChatMessage.sequence.desc())
            .limit(1)
        )
        return int(highest or 0) + 1

    @staticmethod
    def _finish(turn: SetupChatTurn, status: TurnStatus, disposition: str) -> None:
        """Settle a turn and give the session back. Always both, never one."""

        turn.status = status.value
        turn.recovery_disposition = disposition[:48]
        turn.completed_at = turn.completed_at or datetime.now(UTC)
        turn.session_claim = None
        turn.lease_owner = None
        turn.lease_expires_at = None
        stamps = dict(turn.stage_timestamps_json or {})
        stamps[status.value] = datetime.now(UTC).isoformat()
        turn.stage_timestamps_json = stamps

    async def _escalate(self, session: AsyncSession, turn_id: object) -> None:
        """Tell an operator, once per distinct problem.

        A turn nobody can settle is a product fault, not a user fault. It goes to the
        same deduplicated queue the compiler faults use so it is seen rather than
        counted.
        """

        turn = await session.get(SetupChatTurn, turn_id)
        if turn is None:
            return
        fingerprint = f"recovery:{turn.id}"
        existing = await session.scalar(
            select(SetupChatOperationalIssue).where(
                SetupChatOperationalIssue.fingerprint == fingerprint
            )
        )
        now = datetime.now(UTC)
        if existing is None:
            session.add(
                SetupChatOperationalIssue(
                    chat_session_id=turn.chat_session_id,
                    setup_chat_turn_id=turn.id,
                    fingerprint=fingerprint,
                    issue_kind="turn_recovery",
                    failure_class=str(turn.status or "UNKNOWN"),
                    status="open",
                    occurrence_count=1,
                    semantic_paths=[],
                    safe_source_excerpt="",
                    support_reference=fingerprint[:64],
                    failure_proof={
                        "status": str(turn.status or ""),
                        "recovery_attempts": turn.recovery_attempts,
                        "mutation_committed": bool(turn.mutation_committed),
                    },
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            existing.occurrence_count += 1
            existing.last_seen_at = now
            existing.status = "open"
        await session.flush()
        await session.commit()


def _stored_recovery_reply(stored: dict[str, object], result: SetupTurnExecutionResult) -> str:
    """The reply the committing transaction already wrote, read back.

    Regenerating it here would let two recoveries of one message produce two different
    answers for one unchanged result.
    """

    from ai_market_monitor.services.setup_chat_launch import RECOVERY_REPLY_KEY

    written = stored.get(RECOVERY_REPLY_KEY)
    if isinstance(written, str) and written.strip():
        return written
    from ai_market_monitor.services.setup_chat_agent import deterministic_summary

    conversation = stored.get("conversation_after")
    language = (
        str(conversation.get("active_language") or "en")
        if isinstance(conversation, dict)
        else "en"
    )
    return deterministic_summary(result, language=language)
