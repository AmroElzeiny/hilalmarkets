from __future__ import annotations

import base64
import json
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    AuditEvent,
    CustomerConversationEvent,
    HilalChatConversation,
    HilalChatMessage,
    HilalChatMessageReport,
    HilalChatRating,
    PublicChatAnswerEvent,
    PublicChatConversation,
    PublicChatMessage,
    PublicChatTurn,
    User,
    UserIdentity,
)
from ai_market_monitor.db.models.enums import IdentityProvider, UserStatus
from ai_market_monitor.schemas.system_brain import (
    AdminConversationMessage,
    AdminConversationPage,
    AdminConversationSource,
    AdminConversationSummary,
    AdminConversationTimeline,
)
from ai_market_monitor.services.system_brain_privacy import redact_customer_text


class AdminConversationNotFound(LookupError):
    pass


class AdminConversationExplorer:
    """One privacy-aware, read-only authority over persisted customer transcripts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_conversations(
        self,
        *,
        search: str | None = None,
        source: AdminConversationSource | None = None,
        lifecycle: str | None = None,
        error_only: bool = False,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        identity: Literal["authenticated", "anonymous"] | None = None,
        approval_state: str | None = None,
        cursor: str | None = None,
        limit: int = 30,
        seen_event_id: int = 0,
    ) -> AdminConversationPage:
        limit = max(1, min(limit, 100))
        cursor_at, cursor_id = _decode_cursor(cursor)
        candidates: list[AdminConversationSummary] = []
        if source in {None, "dashboard_hilal_agent"}:
            candidates.extend(
                await self._hilal_summaries(
                    search=search,
                    lifecycle=lifecycle,
                    error_only=error_only,
                    date_from=date_from,
                    date_to=date_to,
                    identity=identity,
                    approval_state=approval_state,
                    limit=limit * 3,
                    seen_event_id=seen_event_id,
                )
            )
        if source in {None, "public_site_chat"}:
            candidates.extend(
                await self._public_summaries(
                    search=search,
                    lifecycle=lifecycle,
                    error_only=error_only,
                    date_from=date_from,
                    date_to=date_to,
                    identity=identity,
                    limit=limit * 3,
                    seen_event_id=seen_event_id,
                )
            )
        candidates.sort(
            key=lambda item: (item.last_message_at or item.updated_at, item.conversation_id),
            reverse=True,
        )
        if cursor_at is not None and cursor_id is not None:
            candidates = [
                item
                for item in candidates
                if (item.last_message_at or item.updated_at, item.conversation_id)
                < (cursor_at, cursor_id)
            ]
        page = candidates[:limit]
        next_cursor = None
        if len(candidates) > limit and page:
            last = page[-1]
            next_cursor = _encode_cursor(
                last.last_message_at or last.updated_at, last.conversation_id
            )
        latest_event_id = int(
            await self.session.scalar(select(func.max(CustomerConversationEvent.id))) or 0
        )
        return AdminConversationPage(
            items=page,
            next_cursor=next_cursor,
            latest_event_id=latest_event_id,
        )

    async def conversation(
        self,
        conversation_id: UUID,
        *,
        source: AdminConversationSource,
        admin_user_id: UUID,
        access_reason: str,
        request_id: str | None,
        ip_hash: str | None,
    ) -> AdminConversationTimeline:
        reason = access_reason.strip()
        if len(reason) < 3:
            raise ValueError("An access reason or category is required.")
        if source == "dashboard_hilal_agent":
            result = await self._hilal_timeline(conversation_id)
        else:
            result = await self._public_timeline(conversation_id)
        self.session.add(
            AuditEvent(
                actor_user_id=admin_user_id,
                actor_type="system_brain_admin",
                action="system_brain.customer_conversation.view",
                target_type=source,
                target_id=str(conversation_id),
                request_id=request_id,
                ip_hash=ip_hash,
                metadata_redacted={"access_reason": reason[:120]},
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return result

    async def events(
        self,
        *,
        after_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = list(
            (
                await self.session.scalars(
                    select(CustomerConversationEvent)
                    .where(CustomerConversationEvent.id > max(0, after_id))
                    .order_by(CustomerConversationEvent.id.asc())
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        )
        return [
            {
                "event_id": row.id,
                "source_type": row.source_type,
                "conversation_id": str(row.conversation_id),
                "event_type": row.event_type,
                "message_id": str(row.message_id) if row.message_id else None,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ]

    async def _hilal_summaries(self, **filters: Any) -> list[AdminConversationSummary]:
        """Conversations with Hilal, the assistant inside the signed-in dashboard.

        One row per person, for ever: Hilal keeps one running conversation rather than a
        session per visit, so "last message" is what orders this list and the message
        count is the whole history.
        """

        now = datetime.now(UTC)
        statement = select(HilalChatConversation).order_by(
            HilalChatConversation.updated_at.desc()
        )
        statement = _date_filters(
            statement,
            HilalChatConversation.created_at,
            filters.get("date_from"),
            filters.get("date_to"),
        )
        if filters.get("conversation_id"):
            statement = statement.where(HilalChatConversation.id == filters["conversation_id"])
        identity = filters.get("identity")
        if identity == "anonymous":
            # Hilal only ever answers a signed-in customer, so this filter selects
            # nothing rather than quietly selecting everybody.
            return []
        search = str(filters.get("search") or "").strip()
        if search:
            user_ids = await self._matching_user_ids(search)
            message_match = select(HilalChatMessage.id).where(
                HilalChatMessage.conversation_id == HilalChatConversation.id,
                HilalChatMessage.retain_until > now,
                HilalChatMessage.content.ilike(f"%{_escape_like(search)}%", escape="\\"),
            )
            clauses: list[Any] = [message_match.exists()]
            with suppress(ValueError):
                clauses.append(HilalChatConversation.id == UUID(search))
                clauses.append(HilalChatConversation.user_id == UUID(search))
            if user_ids:
                clauses.append(HilalChatConversation.user_id.in_(user_ids))
            statement = statement.where(or_(*clauses))
        rows = list(
            (
                await self.session.scalars(statement.limit(max(1, min(int(filters["limit"]), 300))))
            ).all()
        )
        users, identities = await self._user_maps({item.user_id for item in rows})
        chat_ids = {item.id for item in rows}
        message_counts = {
            row[0]: int(row[1])
            for row in (
                await self.session.execute(
                    select(HilalChatMessage.conversation_id, func.count(HilalChatMessage.id))
                    .where(
                        HilalChatMessage.conversation_id.in_(chat_ids),
                        HilalChatMessage.retain_until > now,
                    )
                    .group_by(HilalChatMessage.conversation_id)
                )
            ).all()
        }
        last_sequence = (
            select(
                HilalChatMessage.conversation_id.label("conversation_id"),
                func.max(HilalChatMessage.sequence).label("sequence"),
            )
            .where(
                HilalChatMessage.conversation_id.in_(chat_ids),
                HilalChatMessage.retain_until > now,
            )
            .group_by(HilalChatMessage.conversation_id)
            .subquery()
        )
        last_messages = {
            row.conversation_id: row
            for row in (
                await self.session.scalars(
                    select(HilalChatMessage).join(
                        last_sequence,
                        (HilalChatMessage.conversation_id == last_sequence.c.conversation_id)
                        & (HilalChatMessage.sequence == last_sequence.c.sequence),
                    )
                )
            ).all()
        }
        costs = {
            row[0]: Decimal(row[1] or 0)
            for row in (
                await self.session.execute(
                    select(
                        HilalChatMessage.conversation_id,
                        func.coalesce(func.sum(HilalChatMessage.estimated_cost_usd), 0),
                    )
                    .where(HilalChatMessage.conversation_id.in_(chat_ids))
                    .group_by(HilalChatMessage.conversation_id)
                )
            ).all()
        }
        # "Somebody said this answer was wrong" is the only failure this assistant has.
        # There is no failed-turn table for it, so a reported answer is what `has_error`
        # means here — and the Errors-only filter selects exactly those conversations.
        reported = {
            row[0]: str(row[1])
            for row in (
                await self.session.execute(
                    select(
                        HilalChatMessageReport.conversation_id,
                        func.min(HilalChatMessageReport.reason),
                    )
                    .where(HilalChatMessageReport.conversation_id.in_(chat_ids))
                    .group_by(HilalChatMessageReport.conversation_id)
                )
            ).all()
        }
        ratings = {
            row[0]: int(row[1])
            for row in (
                await self.session.execute(
                    select(
                        HilalChatRating.conversation_id,
                        func.max(HilalChatRating.stars),
                    )
                    .where(HilalChatRating.conversation_id.in_(chat_ids))
                    .group_by(HilalChatRating.conversation_id)
                )
            ).all()
        }
        unread_ids = await self._unread_ids(chat_ids, int(filters.get("seen_event_id") or 0))
        lifecycle = str(filters.get("lifecycle") or "").strip().casefold()
        approval_state = filters.get("approval_state")
        summaries: list[AdminConversationSummary] = []
        for item in rows:
            report_reason = reported.get(item.id)
            if filters.get("error_only") and report_reason is None:
                continue
            last = last_messages.get(item.id)
            stage = (last.mode if last else "no_messages").casefold()
            if lifecycle and lifecycle not in stage:
                continue
            if approval_state in {"approved", "unapproved"}:
                # Nothing in a Hilal conversation is approved or unapproved: this
                # assistant answers questions, it does not gate anything. The filter
                # selects nothing rather than pretending a state exists.
                continue
            user = users.get(item.user_id)
            deleted = user is not None and user.status == UserStatus.DELETED
            email = None if deleted else identities.get(item.user_id)
            stars = ratings.get(item.id)
            summaries.append(
                AdminConversationSummary(
                    conversation_id=item.id,
                    source_type="dashboard_hilal_agent",
                    user_id=None if deleted else item.user_id,
                    user_email=email,
                    display_name=(
                        "Deleted user"
                        if deleted
                        else (
                            user.display_name
                            if user and user.display_name
                            else email or "Registered user"
                        )
                    ),
                    anonymous=False,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    last_message_at=item.last_message_at or (last.created_at if last else None),
                    message_count=message_counts.get(item.id, item.message_count),
                    title_or_topic=(
                        f"Rated {stars}/5" if stars else "Dashboard help"
                    ),
                    lifecycle_or_stage=stage,
                    last_message_excerpt=(
                        None
                        if deleted or last is None
                        else redact_customer_text(last.content, limit=180)
                    ),
                    has_error=report_reason is not None,
                    last_error_code=report_reason,
                    approval_state=None,
                    model=last.model if last else None,
                    estimated_ai_cost=costs.get(item.id, Decimal("0")),
                    unread=item.id in unread_ids,
                )
            )
        return summaries

    async def _public_summaries(self, **filters: Any) -> list[AdminConversationSummary]:
        now = datetime.now(UTC)
        statement = (
            select(PublicChatConversation)
            .where(PublicChatConversation.expires_at > now)
            .order_by(PublicChatConversation.updated_at.desc())
        )
        statement = _date_filters(
            statement,
            PublicChatConversation.created_at,
            filters.get("date_from"),
            filters.get("date_to"),
        )
        lifecycle = filters.get("lifecycle")
        if filters.get("conversation_id"):
            statement = statement.where(PublicChatConversation.id == filters["conversation_id"])
        if lifecycle:
            statement = statement.where(PublicChatConversation.stage == lifecycle)
        identity = filters.get("identity")
        if identity == "authenticated":
            statement = statement.where(PublicChatConversation.user_id.is_not(None))
        elif identity == "anonymous":
            statement = statement.where(PublicChatConversation.user_id.is_(None))
        search = str(filters.get("search") or "").strip()
        if search:
            user_ids = await self._matching_user_ids(search)
            message_match = select(PublicChatMessage.id).where(
                PublicChatMessage.conversation_id == PublicChatConversation.id,
                PublicChatMessage.retain_until > now,
                PublicChatMessage.content.ilike(f"%{_escape_like(search)}%", escape="\\"),
            )
            clauses: list[Any] = [message_match.exists()]
            with suppress(ValueError):
                clauses.append(PublicChatConversation.id == UUID(search))
                clauses.append(PublicChatConversation.user_id == UUID(search))
            if user_ids:
                clauses.append(PublicChatConversation.user_id.in_(user_ids))
            statement = statement.where(or_(*clauses))
        rows = list(
            (
                await self.session.scalars(statement.limit(max(1, min(int(filters["limit"]), 300))))
            ).all()
        )
        users, identities = await self._user_maps(
            {item.user_id for item in rows if item.user_id is not None}
        )
        chat_ids = {item.id for item in rows}
        failures: dict[UUID, PublicChatTurn] = {}
        if chat_ids:
            failure_rows = list(
                (
                    await self.session.scalars(
                        select(PublicChatTurn)
                        .where(
                            PublicChatTurn.conversation_id.in_(chat_ids),
                            PublicChatTurn.error_type.is_not(None),
                        )
                        .order_by(
                            PublicChatTurn.conversation_id,
                            PublicChatTurn.updated_at.desc(),
                        )
                    )
                ).all()
            )
            for row in failure_rows:
                failures.setdefault(row.conversation_id, row)
        message_counts = {
            row[0]: int(row[1])
            for row in (
                await self.session.execute(
                    select(PublicChatMessage.conversation_id, func.count(PublicChatMessage.id))
                    .where(
                        PublicChatMessage.conversation_id.in_(chat_ids),
                        PublicChatMessage.retain_until > now,
                    )
                    .group_by(PublicChatMessage.conversation_id)
                )
            ).all()
        }
        last_sequence = (
            select(
                PublicChatMessage.conversation_id.label("conversation_id"),
                func.max(PublicChatMessage.sequence).label("sequence"),
            )
            .where(
                PublicChatMessage.conversation_id.in_(chat_ids),
                PublicChatMessage.retain_until > now,
            )
            .group_by(PublicChatMessage.conversation_id)
            .subquery()
        )
        last_messages = {
            row.conversation_id: row
            for row in (
                await self.session.scalars(
                    select(PublicChatMessage).join(
                        last_sequence,
                        (PublicChatMessage.conversation_id == last_sequence.c.conversation_id)
                        & (PublicChatMessage.sequence == last_sequence.c.sequence),
                    )
                )
            ).all()
        }
        costs = {
            row[0]: Decimal(row[1] or 0)
            for row in (
                await self.session.execute(
                    select(
                        PublicChatAnswerEvent.conversation_id,
                        func.coalesce(func.sum(PublicChatAnswerEvent.estimated_cost_usd), 0),
                    )
                    .where(
                        PublicChatAnswerEvent.conversation_id.in_(chat_ids),
                        PublicChatAnswerEvent.retain_until > now,
                    )
                    .group_by(PublicChatAnswerEvent.conversation_id)
                )
            ).all()
        }
        unread_ids = await self._unread_ids(chat_ids, int(filters.get("seen_event_id") or 0))
        summaries: list[AdminConversationSummary] = []
        for item in rows:
            failed = failures.get(item.id)
            if filters.get("error_only") and failed is None:
                continue
            last = last_messages.get(item.id)
            user = users.get(item.user_id) if item.user_id else None
            deleted = user is not None and user.status == UserStatus.DELETED
            email = None if deleted or item.user_id is None else identities.get(item.user_id)
            anonymous = item.user_id is None or deleted
            summaries.append(
                AdminConversationSummary(
                    conversation_id=item.id,
                    source_type="public_site_chat",
                    user_id=None if anonymous else item.user_id,
                    user_email=email,
                    display_name=(
                        "Anonymous visitor"
                        if item.user_id is None
                        else "Deleted user"
                        if deleted
                        else (
                            user.display_name
                            if user and user.display_name
                            else email or "Registered user"
                        )
                    ),
                    anonymous=anonymous,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    last_message_at=last.created_at if last else None,
                    message_count=message_counts.get(item.id, 0),
                    title_or_topic=str(
                        (item.state_json or {}).get("current_topic") or "Public product chat"
                    ),
                    lifecycle_or_stage=item.stage,
                    last_message_excerpt=(
                        None
                        if deleted or last is None
                        else redact_customer_text(last.content, limit=180)
                    ),
                    has_error=failed is not None,
                    last_error_code=failed.error_type if failed else None,
                    approval_state=None,
                    model=item.model,
                    estimated_ai_cost=costs.get(item.id, Decimal("0")),
                    unread=item.id in unread_ids,
                )
            )
        return summaries

    async def _hilal_timeline(self, conversation_id: UUID) -> AdminConversationTimeline:
        chat = await self.session.get(HilalChatConversation, conversation_id)
        now = datetime.now(UTC)
        if chat is None:
            raise AdminConversationNotFound(str(conversation_id))
        page = await self._hilal_summaries(
            conversation_id=conversation_id, limit=1, seen_event_id=0
        )
        summary = next((item for item in page if item.conversation_id == conversation_id), None)
        if summary is None:
            raise AdminConversationNotFound(str(conversation_id))
        user = await self.session.get(User, chat.user_id)
        deleted = user is not None and user.status == UserStatus.DELETED
        rows = list(
            (
                await self.session.scalars(
                    select(HilalChatMessage)
                    .where(
                        HilalChatMessage.conversation_id == conversation_id,
                        HilalChatMessage.retain_until > now,
                    )
                    .order_by(HilalChatMessage.sequence.asc())
                    .limit(1000)
                )
            ).all()
        )
        reports = {
            row.message_id: row
            for row in (
                await self.session.scalars(
                    select(HilalChatMessageReport).where(
                        HilalChatMessageReport.conversation_id == conversation_id
                    )
                )
            ).all()
        }
        messages = [
            AdminConversationMessage(
                message_id=row.id,
                sequence=row.sequence,
                role="user" if row.role == "user" else "assistant",
                # Hilal records what kind of answer it gave — grounded, refusal, or a
                # question back. That is the message type an admin needs to see.
                message_type=row.mode.casefold(),
                content=(
                    "[Content unavailable after account deletion]"
                    if deleted
                    else redact_customer_text(row.content)
                ),
                created_at=row.created_at,
                lifecycle=row.page,
                error_code=(reports[row.id].reason if row.id in reports else None),
                model=row.model,
                latency_ms=row.latency_ms or None,
                input_tokens=row.input_tokens or None,
                output_tokens=row.output_tokens or None,
                estimated_cost_usd=row.estimated_cost_usd,
            )
            for row in rows
        ]
        # A conversation older than the retention window keeps its counters but not its
        # words. Saying so is the honest answer; reconstructing the text is not.
        complete = len(rows) >= chat.message_count
        return AdminConversationTimeline(
            summary=summary,
            messages=messages,
            lifecycle_events=await self._timeline_events(conversation_id),
            telemetry_available=any(row.model for row in rows),
            transcript_complete=complete,
            transcript_limitation=(
                None
                if complete
                else (
                    "Some messages in this conversation have passed their retention date "
                    "and were removed. Nothing was reconstructed from the counters."
                )
            ),
            related_links=(
                [
                    {
                        "label": "Customer",
                        "href": f"/dashboard/system-brain/users?user_id={chat.user_id}",
                    }
                ]
                if not deleted
                else []
            ),
        )

    async def _public_timeline(self, conversation_id: UUID) -> AdminConversationTimeline:
        chat = await self.session.get(PublicChatConversation, conversation_id)
        now = datetime.now(UTC)
        if chat is None or _as_utc(chat.expires_at) <= now:
            raise AdminConversationNotFound(str(conversation_id))
        page = await self._public_summaries(
            conversation_id=conversation_id, limit=1, seen_event_id=0
        )
        summary = next((item for item in page if item.conversation_id == conversation_id), None)
        if summary is None:
            summary = await self._public_summary_direct(chat)
        user = await self.session.get(User, chat.user_id) if chat.user_id else None
        deleted = user is not None and user.status == UserStatus.DELETED
        rows = list(
            (
                await self.session.scalars(
                    select(PublicChatMessage)
                    .where(
                        PublicChatMessage.conversation_id == conversation_id,
                        PublicChatMessage.retain_until > now,
                    )
                    .order_by(PublicChatMessage.sequence.asc())
                    .limit(1000)
                )
            ).all()
        )
        messages = [
            AdminConversationMessage(
                message_id=row.id,
                sequence=row.sequence,
                role="user" if row.role == "user" else "assistant",
                message_type=row.message_type,
                content=(
                    "[Content unavailable after account deletion]"
                    if deleted
                    else redact_customer_text(row.content)
                ),
                created_at=row.created_at,
                model=(row.telemetry_redacted or {}).get("model"),
                latency_ms=_int_or_none((row.telemetry_redacted or {}).get("latency_ms")),
                input_tokens=_int_or_none((row.telemetry_redacted or {}).get("input_tokens")),
                output_tokens=_int_or_none((row.telemetry_redacted or {}).get("output_tokens")),
                reasoning_tokens=_int_or_none(
                    (row.telemetry_redacted or {}).get("reasoning_tokens")
                ),
                estimated_cost_usd=_decimal_or_none(
                    (row.telemetry_redacted or {}).get("estimated_cost_usd")
                ),
            )
            for row in rows
        ]
        complete = bool(rows) or chat.message_count == 0
        return AdminConversationTimeline(
            summary=summary,
            messages=messages,
            lifecycle_events=await self._timeline_events(conversation_id),
            telemetry_available=any(row.telemetry_redacted for row in rows),
            transcript_complete=complete,
            transcript_limitation=(
                None
                if complete
                else (
                    "This conversation predates complete Public Chat transcript storage. "
                    "Historical text was not reconstructed from hashes or bounded state."
                )
            ),
            related_links=(
                [
                    {
                        "label": "Customer",
                        "href": f"/dashboard/system-brain/users?user_id={chat.user_id}",
                    }
                ]
                if chat.user_id and not deleted
                else []
            ),
        )

    async def _public_summary_direct(
        self, chat: PublicChatConversation
    ) -> AdminConversationSummary:
        rows = await self._public_summaries(conversation_id=chat.id, limit=1, seen_event_id=0)
        found = next((item for item in rows if item.conversation_id == chat.id), None)
        if found is None:
            raise AdminConversationNotFound(str(chat.id))
        return found

    async def _matching_user_ids(self, search: str) -> set[UUID]:
        escaped = _escape_like(search)
        return set(
            (
                await self.session.scalars(
                    select(UserIdentity.user_id)
                    .where(
                        UserIdentity.provider == IdentityProvider.EMAIL,
                        or_(
                            UserIdentity.normalized_identifier.ilike(f"%{escaped}%", escape="\\"),
                            UserIdentity.display_identifier.ilike(f"%{escaped}%", escape="\\"),
                        ),
                    )
                    .limit(100)
                )
            ).all()
        )

    async def _user_maps(
        self, user_ids: set[UUID | None]
    ) -> tuple[dict[UUID, User], dict[UUID, str]]:
        ids = {item for item in user_ids if item is not None}
        if not ids:
            return {}, {}
        users = {
            item.id: item
            for item in (await self.session.scalars(select(User).where(User.id.in_(ids)))).all()
        }
        rows = list(
            (
                await self.session.scalars(
                    select(UserIdentity)
                    .where(
                        UserIdentity.user_id.in_(ids),
                        UserIdentity.provider == IdentityProvider.EMAIL,
                        UserIdentity.normalized_identifier.is_not(None),
                    )
                    .order_by(UserIdentity.is_primary.desc(), UserIdentity.created_at.asc())
                )
            ).all()
        )
        emails: dict[UUID, str] = {}
        for row in rows:
            emails.setdefault(
                row.user_id, row.display_identifier or row.normalized_identifier or ""
            )
        return users, emails

    async def _unread_ids(self, conversation_ids: set[UUID], seen_event_id: int) -> set[UUID]:
        if seen_event_id <= 0:
            return set()
        return set(
            (
                await self.session.scalars(
                    select(CustomerConversationEvent.conversation_id)
                    .where(
                        CustomerConversationEvent.conversation_id.in_(conversation_ids),
                        CustomerConversationEvent.id > seen_event_id,
                    )
                    .distinct()
                )
            ).all()
        )

    async def _timeline_events(self, conversation_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            (
                await self.session.scalars(
                    select(CustomerConversationEvent)
                    .where(CustomerConversationEvent.conversation_id == conversation_id)
                    .order_by(CustomerConversationEvent.id.asc())
                    .limit(200)
                )
            ).all()
        )
        return [
            {
                "event_id": row.id,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at.isoformat(),
                "message_id": str(row.message_id) if row.message_id else None,
            }
            for row in rows
        ]


def _date_filters(statement: Any, column: Any, date_from: Any, date_to: Any) -> Any:
    if date_from:
        statement = statement.where(column >= date_from)
    if date_to:
        statement = statement.where(column <= date_to)
    return statement


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _encode_cursor(at: datetime, row_id: UUID) -> str:
    raw = json.dumps({"at": at.isoformat(), "id": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, UUID | None]:
    if not value:
        return None, None
    try:
        padding = "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(value + padding))
        return datetime.fromisoformat(data["at"]), UUID(data["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ValueError("Invalid conversation cursor.") from None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
