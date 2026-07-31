"""Content identity for an approved Favorites list.

The list's version used to be ``watchlist.updated_at.isoformat()``. A timestamp is not
an identity of *contents*:

* membership lives in ``approved_watchlist_assets``, a different table, so adding or
  removing an asset need not touch the parent row's ``updated_at`` at all
* two edits inside the same clock tick produce the same string
* restoring a previously removed asset returns the list to a state whose identity has
  moved on, and a changed identity that means nothing is as bad as an unchanged one

So an approval could be bound to a "version" while the assets it governs had changed
underneath it. This module computes identity from the members themselves: change the
membership and the hash changes; change nothing and it does not.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models.sharia import ApprovedWatchlist, ApprovedWatchlistAsset

#: Prefix so a content hash can never be mistaken for the old timestamp identity, and so
#: a stored legacy value is recognisable on sight.
SNAPSHOT_PREFIX = "wlv1:"


class WatchlistSnapshot(BaseModel):
    """An immutable record of exactly which assets a Favorites list held."""

    model_config = ConfigDict(extra="forbid")

    watchlist_id: UUID
    name: str = Field(max_length=160)
    #: Canonical asset ids, sorted. Sorted because membership is a set: the order rows
    #: come back in is not part of what the user chose.
    ordered_asset_ids: list[str] = Field(default_factory=list, max_length=100000)
    #: The market symbols those assets resolve to under the current quote scope.
    market_symbols: list[str] = Field(default_factory=list, max_length=100000)
    #: `active` today; the field exists so a future soft-delete changes the hash.
    membership_state: str = Field(default="active", max_length=32)
    created_at: datetime

    @property
    def content_hash(self) -> str:
        """``wlv1:<sha256>`` over the membership, and nothing time-varying."""
        payload = {
            "watchlist_id": str(self.watchlist_id),
            "assets": sorted(self.ordered_asset_ids),
            "symbols": sorted(self.market_symbols),
            "membership_state": self.membership_state,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{SNAPSHOT_PREFIX}{hashlib.sha256(encoded).hexdigest()}"

    def evidence(self) -> dict[str, object]:
        return {
            "watchlist_id": str(self.watchlist_id),
            "watchlist_name": self.name,
            "asset_count": len(self.ordered_asset_ids),
            "content_hash": self.content_hash,
            "created_at": self.created_at.isoformat(),
        }


def is_content_identity(value: str | None) -> bool:
    """True when a stored version is a content hash rather than a legacy timestamp."""

    return bool(value) and str(value).startswith(SNAPSHOT_PREFIX)


async def build_watchlist_snapshot(
    session: AsyncSession,
    watchlist: ApprovedWatchlist,
    *,
    quote_currencies: list[str] | None = None,
) -> WatchlistSnapshot:
    """Read the list's current membership and give it a content identity."""

    rows = list(
        await session.scalars(
            select(ApprovedWatchlistAsset)
            .where(ApprovedWatchlistAsset.watchlist_id == watchlist.id)
            .order_by(ApprovedWatchlistAsset.canonical_asset.asc())
        )
    )
    assets = sorted({row.canonical_asset.strip().upper() for row in rows if row.canonical_asset})
    quotes = [item.strip().upper() for item in (quote_currencies or ["USDT"]) if item.strip()]
    symbols = sorted({f"{asset}/{quote}" for asset in assets for quote in quotes})
    return WatchlistSnapshot(
        watchlist_id=watchlist.id,
        name=watchlist.name,
        ordered_asset_ids=assets,
        market_symbols=symbols,
        created_at=datetime.now(UTC),
    )


async def watchlist_content_hash(
    session: AsyncSession,
    watchlist: ApprovedWatchlist,
    *,
    quote_currencies: list[str] | None = None,
) -> str:
    snapshot = await build_watchlist_snapshot(
        session, watchlist, quote_currencies=quote_currencies
    )
    return snapshot.content_hash


async def watchlist_identity_changed(
    session: AsyncSession,
    watchlist_id: UUID | None,
    reviewed_hash: str | None,
    *,
    quote_currencies: list[str] | None = None,
) -> bool:
    """Has the list changed since the identity the user reviewed?

    Fail-closed in two directions. A missing list is a change, and a *legacy timestamp*
    identity is treated as changed too — it cannot be compared to content, and silently
    accepting it would carry the exact defect this replaces into approval.
    """
    if watchlist_id is None:
        return reviewed_hash is not None
    if not is_content_identity(reviewed_hash):
        return True
    watchlist = await session.get(ApprovedWatchlist, watchlist_id)
    if watchlist is None:
        return True
    current = await watchlist_content_hash(
        session, watchlist, quote_currencies=quote_currencies
    )
    return current != reviewed_hash
