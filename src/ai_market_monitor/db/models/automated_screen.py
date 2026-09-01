"""What the automated screen read, and what it concluded. A proposal, never a status.

Two tables, and the split between them is the point.

:class:`CoinEvidenceDocument` is the receipt for one page: which address was read, what
kind of page it was, how much text it had, when it was read. :class:`AutomatedScreenRun`
is the conclusion drawn from all of them, with the sentence supporting every claim.

**Nothing here is a Shariah status and nothing here may become one.** A row records what
the Hilal Markets Methodology proposed after reading a project's own pages. It is marked
as reviewed by nobody, it never joins an ``AssetShariaAssessment``, and ``published``
exists only so that a route with an approval gate has somewhere to write once a person
has decided. No code in the automated path is allowed to set it.

**The page text is deliberately not stored.** Twelve pages of a documentation site is
around a megabyte per coin; across a thousand coins that is a table nothing can query,
and this product has already met that failure once — five list views loading full
evidence JSON turned a twelve-row page into 1.6 GB of reads. What is kept is the
quotation behind each finding, which is what an auditor actually needs, and it is a few
hundred bytes. Re-reading a whole page means fetching it again, which is cheap and
returns the *current* text rather than last month's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AutomatedScreenRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One reading of one coin by the Hilal Markets Methodology."""

    __tablename__ = "automated_screen_runs"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_automated_screen_run_symbol"),
        Index("ix_automated_screen_run_verdict", "verdict", "decided_at"),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(180), default="", nullable=False)

    #: ``eligible`` | ``not_eligible`` | ``not_enough_data``. Owned by
    #: ``services.sharia_evidence_screen.EvidenceVerdict``; a value not in that enum is
    #: a bug, not a new state.
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Each reason with the sentence and the page address behind it. This is what makes
    #: an automated verdict answerable: a reader who disagrees can open the page.
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    activities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    blocking_activities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Every phrase match, with its quotation. Bounded by construction — one quotation
    #: per activity per page.
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    holder_return: Mapped[str | None] = mapped_column(String(40))
    holder_return_basis: Mapped[str | None] = mapped_column(Text)
    #: What the project's own pages never answered. Shown to a reviewer as the work
    #: that would settle the coin.
    open_questions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: Codes of the approved screening conditions that refused this coin.
    matched_conditions: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    #: Codes of conditions that matched but are not approved, so they changed nothing.
    #: Stored so the owner can be shown, on real coins, what approving each would do.
    proposed_matches: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    documents_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_documents_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set **only** by the application's own approval route, after a person decided.
    #: Nothing in the automated path writes this, and
    #: ``tests/unit/test_invariant_automated_screen_never_publishes.py`` fails if it does.
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CoinEvidenceDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One page that was read for one coin. The receipt, not the page."""

    __tablename__ = "coin_evidence_documents"
    __table_args__ = (
        UniqueConstraint("symbol", "url", name="uq_coin_evidence_document_symbol_url"),
        Index("ix_coin_evidence_document_symbol", "symbol", "category"),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[Any | None] = mapped_column(
        ForeignKey("automated_screen_runs.id", ondelete="CASCADE")
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    #: One of ``sharia_source_catalog``'s categories. Not a new vocabulary.
    category: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    #: How much readable text the page had. Kept instead of the text itself.
    characters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Named by a provider or a reviewer rather than found by following a link.
    seeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Carries the project's own description of itself, so a refusal may rest on it.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why a page could not be read, when it could not. ``None`` on a page that was.
    failure_code: Mapped[str | None] = mapped_column(String(60))


__all__ = ["AutomatedScreenRun", "CoinEvidenceDocument"]
