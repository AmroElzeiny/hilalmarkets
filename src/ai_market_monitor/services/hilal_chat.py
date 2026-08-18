"""Hilal, end to end: what it may spend, what it knows, and what it remembers.

The order of a turn is the order of the questions that decide it, and each one is asked
exactly once:

1. **Is this something Hilal must not answer?** Checked first because it is free. A
   request for a strategy or for financial advice is refused before any money is
   promised and before the provider is contacted at all — the person gets an honest
   answer in a few milliseconds and pays nothing for it.
2. **Is there allowance left?** :mod:`ai_spend` decides, against a window that belongs
   to this assistant alone. Refused means the box locks and says why.
3. **What is actually known?** :mod:`hilal_chat_knowledge` gathers rows. Nothing else
   reaches the model.
4. **What did it cost?** The reservation is settled with the provider's real usage, so
   the number the person sees ticking down is the number that was really spent.

Everything said in either direction is written down, keyed to the person rather than to
their browser, because "history from all the sessions" is only true if there is one
place it accumulates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    HilalChatConversation,
    HilalChatMessage,
    HilalChatMessageReport,
    HilalChatRating,
    User,
)
from ai_market_monitor.schemas.hilal_chat import (
    HILAL_CHAT_MODES,
    HilalChatAsk,
    HilalChatMode,
    HilalChatRatingInput,
    HilalChatReply,
    HilalChatReport,
)
from ai_market_monitor.services.ai_availability import availability_for
from ai_market_monitor.services.ai_budget import AIBudgetService, limits_from_settings
from ai_market_monitor.services.ai_spend import AISpendGuard, AISpendRefused
from ai_market_monitor.services.entitlements import EntitlementService
from ai_market_monitor.services.feature_control import Feature
from ai_market_monitor.services.hilal_chat_agent import (
    HilalChatAgent,
    HilalChatUnavailable,
    refusal_for,
)
from ai_market_monitor.services.hilal_chat_knowledge import HilalChatKnowledge

logger = structlog.get_logger(__name__)

#: Which feature this assistant spends under. Named once, used by the reserve path and
#: by the status the browser polls, so both look at the same counter row.
HILAL_FEATURE = Feature.FREE_TEXT_AI


class HilalChatError(RuntimeError):
    """Something a person is told about, in their own words."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Turn:
    """One question and its answer, ready to show."""

    message_id: str
    reply: HilalChatReply
    status: dict[str, Any]


def daily_allowance(settings: Settings, *, paying: bool) -> Decimal:
    """What this person may spend on Hilal in one UTC day.

    One reader. The enforcement path and the figure shown in the window both come
    through here, so they cannot disagree about what the allowance is.
    """

    free = Decimal(str(settings.hilal_chat_free_daily_usd))
    if not paying:
        return free
    return free * Decimal(int(settings.hilal_chat_paid_daily_multiplier))


class HilalChatService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        agent: HilalChatAgent | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.agent = agent or HilalChatAgent(settings)
        self.knowledge = HilalChatKnowledge(session, settings)

    # -- who is asking ----------------------------------------------------

    async def _is_paying(self, user_id: UUID) -> bool:
        """Whether this person is on a paid plan.

        Read from the plan's own price rather than from a list of plan codes here. A new
        paid plan then arrives with the right allowance instead of silently getting the
        free one until somebody remembers to add it.
        """

        entitlement = await EntitlementService(self.session).current(user_id)
        return Decimal(str(entitlement.plan.monthly_price)) > 0

    async def _guard(self, user_id: UUID) -> AISpendGuard:
        paying = await self._is_paying(user_id)
        limits = limits_from_settings(self.settings).for_feature(
            str(HILAL_FEATURE), daily_allowance(self.settings, paying=paying)
        )
        return AISpendGuard(self.session, self.settings, limits=limits)

    # -- the status the window watches ------------------------------------

    async def status_for(self, user_id: UUID) -> dict[str, Any]:
        """What the chat box needs to know about itself, right now.

        Polled every second while the window is open, so it is deliberately one indexed
        row read plus the plan lookup. It carries the reset time as an absolute instant
        rather than "seconds remaining": the browser can count down from an instant
        without asking again, and a countdown computed here would be wrong by however
        long the response took to arrive.
        """

        paying = await self._is_paying(user_id)
        allowance = daily_allowance(self.settings, paying=paying)
        limits = limits_from_settings(self.settings).for_feature(
            str(HILAL_FEATURE), allowance
        )
        availability = availability_for(self.settings, user_id=user_id)
        summary = await AIBudgetService(self.session, limits).summary_for(
            user_id,
            ai_available=availability.assistant_available and self.settings.hilal_chat_enabled,
            unavailable_reason=(
                None if self.settings.hilal_chat_enabled else "Hilal is switched off right now."
            ),
            feature=str(HILAL_FEATURE),
        )
        payload = summary.to_dict()
        payload["paying"] = paying
        payload["can_send"] = bool(summary.ai_available)
        # The words a person reads when the box locks. Written here rather than in the
        # browser so the reason and the state can never disagree.
        if not summary.ai_available:
            payload["locked_reason"] = (
                summary.unavailable_reason
                or "You have used today's messages with Hilal."
            )
            payload["offer_upgrade"] = not paying
        else:
            payload["locked_reason"] = None
            payload["offer_upgrade"] = False
        return payload

    # -- history ----------------------------------------------------------

    async def conversation_for(self, user_id: UUID) -> HilalChatConversation:
        found = await self.session.scalar(
            select(HilalChatConversation).where(HilalChatConversation.user_id == user_id)
        )
        if found is not None:
            return found
        conversation = HilalChatConversation(user_id=user_id, message_count=0, next_sequence=1)
        try:
            async with self.session.begin_nested():
                self.session.add(conversation)
                await self.session.flush()
        except IntegrityError:
            # Two tabs opened at once. One of them made it; this is the other.
            again = await self.session.scalar(
                select(HilalChatConversation).where(HilalChatConversation.user_id == user_id)
            )
            if again is None:
                raise
            return again
        return conversation

    async def history_for(self, user_id: UUID, *, limit: int = 200) -> list[dict[str, Any]]:
        """Everything this person and Hilal have said, oldest first."""

        conversation = await self.conversation_for(user_id)
        rows = (
            (
                await self.session.execute(
                    select(HilalChatMessage)
                    .where(HilalChatMessage.conversation_id == conversation.id)
                    .order_by(HilalChatMessage.sequence.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "role": row.role,
                "text": row.content,
                "mode": row.mode,
                "suggestions": list(row.suggestions or []),
                "at": row.created_at.isoformat(),
            }
            for row in reversed(rows)
        ]

    # -- one turn ---------------------------------------------------------

    async def ask(self, *, user: User, ask: HilalChatAsk) -> Turn:
        if not self.settings.hilal_chat_enabled:
            raise HilalChatError("HILAL_DISABLED", "Hilal is switched off right now.")

        message = " ".join(ask.message.split())
        if not message:
            raise HilalChatError("EMPTY_MESSAGE", "Write something and Hilal will answer.")
        if len(message) > self.settings.hilal_chat_message_max_length:
            raise HilalChatError(
                "MESSAGE_TOO_LONG",
                "That message is longer than Hilal can read in one go. "
                "Try asking one thing at a time.",
            )

        conversation = await self.conversation_for(user.id)

        # Was this exact message already sent? Three outcomes, and all three matter:
        #
        #   * asked and answered — hand back the answer it already got;
        #   * asked but never answered — the first attempt failed on the way, so answer
        #     it now and do *not* write the question down a second time;
        #   * never asked — record it and carry on.
        #
        # Missing the middle case is how a retry crashed: the question was already in
        # the table under this id, and writing it again broke the unique constraint. So
        # the one situation a person actually retries in, a failure, was the one that
        # turned that failure into a five-hundred.
        page = ask.view.page if ask.view else None

        asked = await self._already_asked(conversation, ask.client_message_id)
        if asked is not None:
            answer = await self._answer_after(conversation, asked)
            if answer is not None:
                return await self._turn_from(answer, conversation)
        else:
            await self._write(
                conversation,
                role="user",
                content=message,
                mode="ASK",
                page=page,
                client_message_id=ask.client_message_id,
            )

        # 1. The things Hilal must never do. Free, immediate, and no provider call.
        refusal = refusal_for(message)
        if refusal is not None:
            stored = await self._write(
                conversation, role="assistant", content=refusal.reply, mode=refusal.mode,
                page=page, suggestions=refusal.suggestions,
            )
            return Turn(
                message_id=str(stored.id),
                reply=refusal,
                status=await self.status_for(user.id),
            )

        # 2. May this turn spend anything?
        guard = await self._guard(user.id)
        model, _ = self.agent.model_for_turn()
        try:
            spend = await guard.open_turn(
                user_id=user.id,
                feature=HILAL_FEATURE,
                idempotency_key=f"hilal:{conversation.id}:{ask.client_message_id or uuid4()}",
                model=model,
                service_tier=None,
                max_input_tokens=6000,
                max_output_tokens=self.settings.hilal_chat_ai_max_output_tokens,
                estimated_cost_usd=Decimal(
                    str(self.settings.hilal_chat_ai_max_estimated_cost_usd_per_turn)
                ),
            )
        except AISpendRefused as refused:
            raise HilalChatError(refused.code, refused.message) from refused

        # 3. What is actually known.
        #
        # The conversation is read for coin names as well as the new message. "Did you
        # mean Litecoin?" / "yes" is one question asked over two turns, and looking only
        # at the second one found no coin at all — so Hilal answered that Litecoin was
        # not listed here, about a coin this platform has.
        history = await self._recent(conversation)
        evidence = await self.knowledge.gather(
            message=message,
            view=ask.view,
            earlier=[str(item["said"]) for item in history],
        )

        try:
            call = await self.agent.answer(
                question=message,
                history=history,
                evidence=evidence.to_payload(),
                first_time=conversation.message_count <= 1,
                display_name=_first_name(user),
            )
        except HilalChatUnavailable as failure:
            # Nothing was produced, so nothing is charged. The person is told plainly
            # rather than shown a half answer.
            await guard.release_turn(spend, outcome="refused")
            raise HilalChatError(
                "HILAL_UNAVAILABLE",
                "Hilal could not answer that one just now. Please try again in a moment.",
            ) from failure

        # 4. What it really cost.
        await guard.settle_turn(
            spend,
            actual_cost_usd=Decimal(str(call.estimated_cost_usd)),
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            outcome="completed",
        )

        stored = await self._write(
            conversation,
            role="assistant",
            content=call.reply.reply,
            mode=call.reply.mode,
            page=page,
            language=call.reply.language,
            suggestions=call.reply.suggestions,
            model=call.model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cost=Decimal(str(call.estimated_cost_usd)),
            latency_ms=call.latency_ms,
        )
        logger.info(
            "hilal_chat_answered",
            mode=call.reply.mode,
            model=call.model,
            grounded_in=len(call.reply.grounded_in),
            cost_usd=str(call.estimated_cost_usd),
        )
        return Turn(
            message_id=str(stored.id),
            reply=call.reply,
            status=await self.status_for(user.id),
        )

    async def _already_asked(
        self, conversation: HilalChatConversation, client_message_id: str | None
    ) -> HilalChatMessage | None:
        """This exact question, if it is already written down.

        A dropped connection makes a person press send again. Without this the same
        question is asked of the provider twice and charged twice, and the transcript
        shows it twice.
        """

        if not client_message_id:
            return None
        return await self.session.scalar(
            select(HilalChatMessage).where(
                HilalChatMessage.conversation_id == conversation.id,
                HilalChatMessage.client_message_id == client_message_id,
            )
        )

    async def _answer_after(
        self, conversation: HilalChatConversation, asked: HilalChatMessage
    ) -> HilalChatMessage | None:
        """The reply that followed one question, if it ever arrived."""

        return await self.session.scalar(
            select(HilalChatMessage)
            .where(
                HilalChatMessage.conversation_id == conversation.id,
                HilalChatMessage.role == "assistant",
                HilalChatMessage.sequence > asked.sequence,
            )
            .order_by(HilalChatMessage.sequence)
            .limit(1)
        )

    async def _turn_from(
        self, answer: HilalChatMessage, conversation: HilalChatConversation
    ) -> Turn:
        """Redraw a stored answer exactly as it was shown."""

        # A mode that is no longer one of the declared ones is read as a plain answer
        # rather than refused: this row is a message the person has already been shown,
        # and refusing to redraw it would lose it from their transcript.
        mode = cast(
            HilalChatMode, answer.mode if answer.mode in HILAL_CHAT_MODES else "ANSWER"
        )
        return Turn(
            message_id=str(answer.id),
            reply=HilalChatReply(
                mode=mode,
                reply=answer.content,
                language=answer.language or "English",
                suggestions=list(answer.suggestions or []),
            ),
            status=await self.status_for(conversation.user_id),
        )

    async def _recent(self, conversation: HilalChatConversation) -> list[dict[str, str]]:
        rows = (
            (
                await self.session.execute(
                    select(HilalChatMessage)
                    .where(HilalChatMessage.conversation_id == conversation.id)
                    .order_by(HilalChatMessage.sequence.desc())
                    .limit(self.settings.hilal_chat_ai_max_history_messages)
                )
            )
            .scalars()
            .all()
        )
        return [
            {"who": "them" if row.role == "user" else "you", "said": row.content}
            for row in reversed(rows)
        ]

    async def _write(
        self,
        conversation: HilalChatConversation,
        *,
        role: str,
        content: str,
        mode: str,
        page: str | None = None,
        language: str | None = None,
        suggestions: list[str] | None = None,
        client_message_id: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: Decimal = Decimal("0"),
        latency_ms: int = 0,
    ) -> HilalChatMessage:
        now = datetime.now(UTC)
        sequence = conversation.next_sequence
        conversation.next_sequence = sequence + 1
        conversation.message_count += 1
        conversation.last_message_at = now
        row = HilalChatMessage(
            conversation_id=conversation.id,
            sequence=sequence,
            role=role,
            content=content,
            mode=mode,
            language=language,
            page=page,
            client_message_id=client_message_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            latency_ms=latency_ms,
            suggestions=list(suggestions or []),
            created_at=now,
            retain_until=now + timedelta(days=self.settings.hilal_chat_retention_days),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    # -- reporting and rating ---------------------------------------------

    async def report(self, *, user: User, report: HilalChatReport) -> None:
        conversation = await self.conversation_for(user.id)
        try:
            message_id = UUID(report.message_id)
        except ValueError as error:
            raise HilalChatError("UNKNOWN_MESSAGE", "That message could not be found.") from error
        message = await self.session.scalar(
            select(HilalChatMessage).where(
                HilalChatMessage.id == message_id,
                HilalChatMessage.conversation_id == conversation.id,
            )
        )
        if message is None:
            raise HilalChatError("UNKNOWN_MESSAGE", "That message could not be found.")
        existing = await self.session.scalar(
            select(HilalChatMessageReport).where(
                HilalChatMessageReport.message_id == message.id,
                HilalChatMessageReport.user_id == user.id,
            )
        )
        if existing is not None:
            # Already reported. Saying "thank you" again is kinder than an error about
            # something the person has no reason to care about.
            return
        self.session.add(
            HilalChatMessageReport(
                message_id=message.id,
                conversation_id=conversation.id,
                user_id=user.id,
                reason=report.reason,
                note=(report.note or "").strip()[
                    : self.settings.hilal_chat_comment_max_length
                ]
                or None,
                reported_content=message.content,
                context={"page": message.page, "mode": message.mode, "model": message.model},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        logger.info("hilal_chat_reported", reason=report.reason, mode=message.mode)

    async def rate(self, *, user: User, rating: HilalChatRatingInput) -> None:
        conversation = await self.conversation_for(user.id)
        self.session.add(
            HilalChatRating(
                conversation_id=conversation.id,
                user_id=user.id,
                stars=rating.stars,
                comment=(rating.comment or "").strip()[
                    : self.settings.hilal_chat_comment_max_length
                ]
                or None,
                message_count=conversation.message_count,
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        logger.info("hilal_chat_rated", stars=rating.stars)


def _first_name(user: User) -> str | None:
    """Something to call them, if we have anything.

    Never their email address. Greeting somebody by the local part of their email reads
    as a machine that has their details rather than a person who knows them.
    """

    full = (user.display_name or "").strip()
    if not full:
        return None
    return full.split()[0][:40]
