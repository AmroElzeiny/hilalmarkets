"""What a market-data provider knows about a coin, before anybody has screened it.

This table exists for a gap the product had no home for: a coin that is **listed and
tradeable on Binance or Bybit but carries no Shariah result at all**. It is not halal
here, it is not haram here — no authority has looked at it, so the product has nothing
to say. Until now there was nowhere to keep what is factually known about such a coin,
so nothing was known, and the first step of ever reviewing it started from zero.

**Nothing in this table is a Shariah status, and nothing in it may become one.** There
is deliberately no status column, no eligibility flag, and no link to a methodology or a
review case. A row here says "CoinMarketCap publishes this website and this whitepaper
for this symbol, and Binance lists it" and stops. Turning any of that into a ruling
still requires an authority, an assessment and a person, exactly as it does today.

Why it is separate from ``AssetResearchDossier``: that table requires an
``external_assessment_id``, because it holds the research supporting a specific
authority's verdict. These coins have no verdict to support. Reusing it would have meant
inventing an assessment for a coin nobody has assessed, which is the precise thing the
product must never do.

Why it is separate from ``CanonicalAsset``: identity is approved by a reviewer, and an
approved identity is what the Sharia pipeline treats as real. A provider's record is a
**proposal** for that, gathered in advance so the reviewer starts with the project's own
website, whitepaper and repository already in front of them instead of an empty form.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_market_monitor.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProviderCoinProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One coin's provider record. Facts only."""

    __tablename__ = "provider_coin_profiles"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "symbol",
            name="uq_provider_coin_profile_provider_symbol",
        ),
        Index(
            "ix_provider_coin_profile_research_state",
            "research_state",
            "refreshed_at",
        ),
        Index("ix_provider_coin_profile_symbol", "symbol"),
    )

    #: Which provider this record came from. A second provider gets its own rows rather
    #: than overwriting the first, so two records can be compared instead of merged.
    provider: Mapped[str] = mapped_column(String(40), default="coinmarketcap", nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(180), default="", nullable=False)
    slug: Mapped[str] = mapped_column(String(180), default="", nullable=False)

    #: The addresses a Shariah reviewer needs before they can start.
    official_website: Mapped[str | None] = mapped_column(Text)
    whitepaper_url: Mapped[str | None] = mapped_column(Text)
    source_code_url: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    #: Everything else the provider published, kept whole so a later reader is not
    #: limited to the fields that seemed useful today.
    links: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    category: Mapped[str | None] = mapped_column(String(80))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    date_added: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Which exchanges list it for spot, as observed. The reason the coin is worth
    #: researching at all: a coin nobody can trade is not urgent.
    exchanges: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    #: Market numbers, so the queue can be worked in the order that helps most users.
    market_cap_usd: Mapped[float | None] = mapped_column(Float)
    volume_24h_usd: Mapped[float | None] = mapped_column(Float)
    provider_rank: Mapped[int | None] = mapped_column(Integer)

    #: The rest of what the provider publishes about size and movement — value if every
    #: coin ever minted existed, movement over a week, a month and a quarter, supply.
    #:
    #: One JSON column rather than eight typed ones, because nothing here is filtered in
    #: the database. The Market page filters and sorts in the browser over the rows it
    #: has already loaded, so these only need to *reach* the payload — and a provider
    #: that adds a field next year should not need a migration to carry it.
    market_numbers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: When the numbers above were last read. Shown to a reader, because a market number
    #: from last week is a different claim from one read this morning.
    market_numbers_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: ``pending`` — on an exchange, no Shariah result, not yet researched.
    #: ``researched`` — provider facts gathered.
    #: ``linked`` — a canonical asset now exists for it.
    #: ``skipped`` — deliberately not researched; ``skip_reason`` says why.
    research_state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set once a reviewer's approved identity exists. Until then this is a proposal.
    canonical_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("canonical_assets.id", ondelete="SET NULL")
    )
    #: The provider's own warning that a listing is untrustworthy or delisted. Carried
    #: so a researcher sees it before spending time on the coin.
    provider_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_notice: Mapped[str | None] = mapped_column(Text)


__all__ = ["ProviderCoinProfile"]
