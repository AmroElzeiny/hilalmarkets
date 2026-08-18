"""Where the dashboard assistant's conversations live.

One conversation per person, kept on the server rather than in their browser. That is
the whole point of it: the request was for history "from all the sessions with the
user", and a transcript held in ``localStorage`` is gone the moment they use a second
device, clear their browser, or sign in somewhere else.

Deliberately separate from ``public_chat`` even though the shapes rhyme. That assistant
answers an anonymous visitor and is keyed by a hashed session cookie; this one answers a
signed-in customer and is keyed by their user id. Sharing one table would mean one
retention rule, one limit and one set of instructions for two different jobs, and the
first change to either would have to be made carefully in both.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class HilalChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One person's running conversation with Hilal.

    One row per user, for ever. There is no "new session": closing the window and
    coming back a month later continues the same conversation, which is what a person
    expects from something that greets them by name.
    """

    __tablename__ = "hilal_chat_conversations"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_hilal_chat_conversation_user"),
        Index("ix_hilal_chat_conversation_updated", "updated_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The next value of ``HilalChatMessage.sequence``. Held here rather than counted
    #: with ``MAX(sequence) + 1`` so two messages arriving together cannot be handed the
    #: same number — the row is locked while it is read and incremented.
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HilalChatMessage(UUIDPrimaryKeyMixin, Base):
    """One line of the transcript, as it was shown.

    ``client_message_id`` is unique per conversation so a retry — a flaky connection, a
    double tap on send — finds the message it already wrote instead of asking the model
    the same question twice and charging for it twice.
    """

    __tablename__ = "hilal_chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_hilal_chat_message_sequence"),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_hilal_chat_message_client_id",
        ),
        Index("ix_hilal_chat_message_conversation_created", "conversation_id", "created_at"),
        Index("ix_hilal_chat_message_retain_until", "retain_until"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("hilal_chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which kind of answer this was: a grounded answer, a refusal, a clarification, or
    #: something outside what Hilal knows. Stored so a refusal rate can be measured
    #: without re-reading anybody's words.
    mode: Mapped[str] = mapped_column(String(32), default="ANSWER", nullable=False)
    #: The language tag the model replied in, as it reported it.
    language: Mapped[str | None] = mapped_column(String(20))
    #: Which page the person was on when they wrote this. Context for the answer, and
    #: for anybody later reading a report about it.
    page: Mapped[str | None] = mapped_column(String(120))
    client_message_id: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), default=Decimal("0"), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The short follow-up questions offered under the answer, if any.
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HilalChatMessageReport(UUIDPrimaryKeyMixin, Base):
    """Somebody said an answer was wrong.

    One report per person per message: reporting the same answer twice does not make it
    twice as wrong, and counting it that way would make the numbers useless.
    """

    __tablename__ = "hilal_chat_message_reports"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_hilal_chat_report_message_user",
        ),
        Index("ix_hilal_chat_report_created", "created_at"),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("hilal_chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("hilal_chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    #: The answer exactly as it was reported, copied rather than referenced. A later
    #: retention sweep removes the transcript; a report about it has to stay readable.
    reported_content: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HilalChatRating(UUIDPrimaryKeyMixin, Base):
    """How the conversation felt, asked when the window is closed.

    Kept as one row per rating rather than one per conversation. A person who comes back
    next week and closes the chat again is rating that visit, not editing last week's
    answer, and averaging a moving single value would hide every visit but the last.
    """

    __tablename__ = "hilal_chat_ratings"
    __table_args__ = (Index("ix_hilal_chat_rating_created", "created_at", "stars"),)

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("hilal_chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    #: How many messages had been exchanged when the rating was given, so a five-star
    #: rating after one greeting is not read as the same signal as one after ten turns.
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
