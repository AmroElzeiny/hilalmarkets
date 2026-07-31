"""Content identity for an approved Favorites list, from governed asset identities.

The list's version used to be ``watchlist.updated_at.isoformat()``. A timestamp is not
an identity of *contents*:

* membership lives in ``approved_watchlist_assets``, a different table, so adding or
  removing an asset need not touch the parent row's ``updated_at`` at all
* two edits inside the same clock tick produce the same string
* restoring a previously removed asset returns the list to a state whose identity has
  moved on, and a changed identity that means nothing is as bad as an unchanged one

Hashing the membership fixed that, but hashed the wrong thing: a free-text asset symbol
glued to a quote currency. ``"BTC" + "/" + "USDT"`` is a string a human typed, not an
identity the platform governs. Two different assets that share a ticker collide; an asset
that is renamed changes identity without changing; and nothing proves the market actually
exists on the exchange the setup will run against.

So membership identity now comes from the governed records themselves — the canonical
asset row and the exchange market row — and a member that cannot be bound to a *verified*
canonical asset makes the whole snapshot unusable rather than being quietly hashed as a
string. The snapshot is also written to its own immutable table, so "which markets did
this approval cover" has an answer that does not depend on rows that can still change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models.sharia import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    ApprovedWatchlistSnapshot,
)
from ai_market_monitor.db.models.sharia_governance import CanonicalAsset, ExchangeMarket

#: Prefix so a content hash can never be mistaken for the old timestamp identity, and so
#: a stored legacy value is recognisable on sight.
#:
#: ``wlv1:`` was the string-concatenation identity. It is still recognised, and always
#: treated as *changed*, so nothing approved under it silently carries forward.
SNAPSHOT_PREFIX = "wlv2:"
LEGACY_SNAPSHOT_PREFIXES = ("wlv1:",)

#: A canonical asset row may only be used for identity in this state. Anything else means
#: the platform has not yet proved which asset the symbol refers to.
VERIFIED_MAPPING_STATE = "verified"


@dataclass(frozen=True, slots=True)
class WatchlistScope:
    """Which exchange, quotes and market type a Favorites list is being read against.

    A Favorites list holds assets; a setup watches *markets*. The scope is what turns one
    into the other, and it has to be identical everywhere the identity is computed — a
    hash taken under one scope and compared against a hash taken under another is a
    guaranteed false "the list changed".
    """

    exchange: str
    quote_currencies: tuple[str, ...]
    market_type: str = "spot"

    @classmethod
    def from_parts(
        cls,
        *,
        exchange: str,
        quote_currencies: list[str] | tuple[str, ...],
        market_type: str = "spot",
    ) -> WatchlistScope:
        quotes = tuple(
            sorted({item.strip().upper() for item in quote_currencies if item.strip()})
        )
        return cls(
            exchange=exchange.strip().casefold(),
            quote_currencies=quotes or ("USDT",),
            market_type=market_type.strip().casefold() or "spot",
        )


def scope_from_definition(definition: Any) -> WatchlistScope:
    """The scope a compiled strategy reads its Favorites list under."""

    universe = definition.universe
    return WatchlistScope.from_parts(
        exchange=universe.exchange,
        quote_currencies=list(universe.quote_currencies),
        market_type=getattr(universe.market_type, "value", str(universe.market_type)),
    )


def scope_from_draft(draft: Any) -> WatchlistScope:
    """The same scope, read from the draft before it compiles.

    Kept next to :func:`scope_from_definition` on purpose. The two are the only places a
    scope is built, and they must agree — the compiler sets
    ``quote_currencies = [market_scope.quote_asset]``, so they do.
    """

    scope = draft.market_scope
    return WatchlistScope.from_parts(
        exchange=scope.exchange,
        quote_currencies=[scope.quote_asset],
        market_type=scope.market_type,
    )


class WatchlistMember(BaseModel):
    """One asset in the list, with the governed identities it resolved to."""

    model_config = ConfigDict(extra="forbid")

    #: The stored membership string. Kept for display and for diagnosing an unresolved
    #: member — never used alone as identity.
    canonical_asset: str = Field(max_length=32)
    canonical_asset_id: str | None = None
    #: One entry per market this asset trades as, inside the scope. Sorted.
    markets: list[dict[str, str]] = Field(default_factory=list, max_length=200)
    membership_state: str = Field(default="active", max_length=32)

    @property
    def resolved(self) -> bool:
        """True when this member has a verified canonical identity and a real market."""
        return bool(self.canonical_asset_id) and bool(self.markets)

    def identity(self) -> dict[str, Any]:
        """Exactly what goes into the hash for this member."""
        return {
            "canonical_asset": self.canonical_asset,
            "canonical_asset_id": self.canonical_asset_id,
            "markets": self.markets,
            "membership_state": self.membership_state,
        }


class WatchlistSnapshot(BaseModel):
    """An immutable record of exactly which markets a Favorites list held."""

    model_config = ConfigDict(extra="forbid")

    watchlist_id: UUID
    name: str = Field(max_length=160)
    exchange: str = Field(max_length=40)
    market_type: str = Field(default="spot", max_length=20)
    quote_currencies: list[str] = Field(default_factory=list, max_length=10)
    members: list[WatchlistMember] = Field(default_factory=list, max_length=100000)
    created_at: datetime

    @property
    def content_hash(self) -> str:
        """``wlv2:<sha256>`` over the governed membership, and nothing time-varying."""
        payload = {
            "watchlist_id": str(self.watchlist_id),
            "exchange": self.exchange,
            "market_type": self.market_type,
            "quote_currencies": sorted(self.quote_currencies),
            "members": sorted(
                (member.identity() for member in self.members),
                key=lambda item: str(item["canonical_asset"]),
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"{SNAPSHOT_PREFIX}{hashlib.sha256(encoded).hexdigest()}"

    @property
    def unresolved_members(self) -> list[str]:
        """Members with no verified governed identity. Any one of them fails approval."""
        return sorted(
            member.canonical_asset for member in self.members if not member.resolved
        )

    @property
    def market_symbols(self) -> list[str]:
        return sorted(
            {
                market["market_symbol"]
                for member in self.members
                for market in member.markets
                if market.get("market_symbol")
            }
        )

    def evidence(self) -> dict[str, object]:
        return {
            "watchlist_id": str(self.watchlist_id),
            "watchlist_name": self.name,
            "asset_count": len(self.members),
            "unresolved_assets": self.unresolved_members[:50],
            "content_hash": self.content_hash,
            "created_at": self.created_at.isoformat(),
        }


class WatchlistIdentityError(ValueError):
    """A Favorites list has a member the platform cannot identify. Fail closed."""

    def __init__(self, unresolved: list[str]) -> None:
        listed = ", ".join(unresolved[:5])
        super().__init__(
            "Some markets in that Favorites list are not verified yet: "
            f"{listed}. Remove them or pick another list."
        )
        self.unresolved = unresolved


def is_content_identity(value: str | None) -> bool:
    """True when a stored version is a governed content hash.

    A ``wlv1:`` value is deliberately *not* one. It was computed from concatenated
    strings, so it cannot be compared with a governed hash, and accepting it would carry
    the defect it replaces straight into approval.
    """

    return bool(value) and str(value).startswith(SNAPSHOT_PREFIX)


def is_legacy_identity(value: str | None) -> bool:
    """True for an identity from an older scheme, including the bare timestamp."""

    if not value:
        return False
    return not str(value).startswith(SNAPSHOT_PREFIX)


async def build_watchlist_snapshot(
    session: AsyncSession,
    watchlist: ApprovedWatchlist,
    *,
    scope: WatchlistScope,
) -> WatchlistSnapshot:
    """Read the list's membership and bind each member to governed identities."""

    rows = list(
        await session.scalars(
            select(ApprovedWatchlistAsset)
            .where(ApprovedWatchlistAsset.watchlist_id == watchlist.id)
            .order_by(ApprovedWatchlistAsset.canonical_asset.asc())
        )
    )
    assets = sorted(
        {row.canonical_asset.strip().upper() for row in rows if row.canonical_asset}
    )
    canonical_rows = (
        list(
            await session.scalars(
                select(CanonicalAsset).where(CanonicalAsset.symbol.in_(assets))
            )
        )
        if assets
        else []
    )
    canonical_by_symbol = {
        row.symbol.strip().upper(): row
        for row in canonical_rows
        if row.mapping_state == VERIFIED_MAPPING_STATE
    }
    asset_ids = [row.id for row in canonical_by_symbol.values()]
    market_rows = (
        list(
            await session.scalars(
                select(ExchangeMarket).where(
                    ExchangeMarket.canonical_asset_id.in_(asset_ids),
                    ExchangeMarket.exchange == scope.exchange,
                    ExchangeMarket.market_type == scope.market_type,
                    ExchangeMarket.quote_asset.in_(list(scope.quote_currencies)),
                    ExchangeMarket.is_active.is_(True),
                )
            )
        )
        if asset_ids
        else []
    )
    markets_by_asset: dict[UUID, list[dict[str, str]]] = {}
    for row in market_rows:
        markets_by_asset.setdefault(row.canonical_asset_id, []).append(
            {
                "exchange_market_id": str(row.id),
                "exchange": row.exchange,
                "market_symbol": row.market_symbol,
                "market_type": row.market_type,
                "quote_asset": row.quote_asset,
            }
        )
    members: list[WatchlistMember] = []
    for asset in assets:
        canonical = canonical_by_symbol.get(asset)
        markets = sorted(
            markets_by_asset.get(canonical.id, []) if canonical else [],
            key=lambda item: item["market_symbol"],
        )
        members.append(
            WatchlistMember(
                canonical_asset=asset,
                canonical_asset_id=str(canonical.id) if canonical else None,
                markets=markets,
            )
        )
    return WatchlistSnapshot(
        watchlist_id=watchlist.id,
        name=watchlist.name,
        exchange=scope.exchange,
        market_type=scope.market_type,
        quote_currencies=list(scope.quote_currencies),
        members=members,
        created_at=datetime.now(UTC),
    )


async def persist_watchlist_snapshot(
    session: AsyncSession,
    snapshot: WatchlistSnapshot,
) -> ApprovedWatchlistSnapshot:
    """Store the snapshot once, keyed by its own content hash.

    The same membership always produces the same hash, so re-selecting an unchanged list
    reuses the existing row instead of writing a duplicate. Written even when a member is
    unresolved: the record of "this is what the list looked like, and this part of it was
    unusable" is exactly what an audit needs.
    """

    existing = await session.scalar(
        select(ApprovedWatchlistSnapshot).where(
            ApprovedWatchlistSnapshot.content_hash == snapshot.content_hash
        )
    )
    if existing is not None:
        return existing
    record = ApprovedWatchlistSnapshot(
        watchlist_id=snapshot.watchlist_id,
        content_hash=snapshot.content_hash,
        exchange=snapshot.exchange,
        market_type=snapshot.market_type,
        quote_currencies=list(snapshot.quote_currencies),
        members=[member.model_dump(mode="json") for member in snapshot.members],
        member_count=len(snapshot.members),
        unresolved_count=len(snapshot.unresolved_members),
        captured_at=snapshot.created_at,
    )
    session.add(record)
    try:
        await session.flush()
    except IntegrityError:
        # Two turns captured the same membership at the same moment. The row is
        # content-addressed, so the one that won is the one we wanted.
        await session.rollback()
        stored = await session.scalar(
            select(ApprovedWatchlistSnapshot).where(
                ApprovedWatchlistSnapshot.content_hash == snapshot.content_hash
            )
        )
        if stored is None:
            raise
        return stored
    return record


async def load_watchlist_snapshot(
    session: AsyncSession,
    content_hash: str,
) -> ApprovedWatchlistSnapshot | None:
    """Retrieve a stored snapshot by its identity, independently of live rows."""

    return await session.scalar(
        select(ApprovedWatchlistSnapshot).where(
            ApprovedWatchlistSnapshot.content_hash == content_hash
        )
    )


async def watchlist_content_hash(
    session: AsyncSession,
    watchlist: ApprovedWatchlist,
    *,
    scope: WatchlistScope,
    persist: bool = True,
    require_resolved: bool = False,
) -> str:
    """The list's governed identity, refusing when a member cannot be identified.

    ``require_resolved`` follows the same rule the universe resolver already applies to
    published assessments: enforced where the platform is deployed, recorded but not fatal
    on a local database that has no governance rows yet. Either way the unresolved members
    are stored on the snapshot, so the gap is visible rather than assumed away.
    """

    snapshot = await build_watchlist_snapshot(session, watchlist, scope=scope)
    if require_resolved and snapshot.unresolved_members:
        raise WatchlistIdentityError(snapshot.unresolved_members)
    if persist:
        await persist_watchlist_snapshot(session, snapshot)
    return snapshot.content_hash


async def watchlist_identity_changed(
    session: AsyncSession,
    watchlist_id: UUID | None,
    reviewed_hash: str | None,
    *,
    scope: WatchlistScope,
    require_resolved: bool = False,
) -> bool:
    """Has the list changed since the identity the user reviewed?

    Fail-closed in every direction. A missing list is a change. An identity from an older
    scheme is a change, because it cannot be compared with a governed one. When
    ``require_resolved`` is on, a member with no verified governed identity is a change
    too — the approval would otherwise cover a market the platform cannot identify.
    """
    if watchlist_id is None:
        return reviewed_hash is not None
    if not is_content_identity(reviewed_hash):
        return True
    watchlist = await session.get(ApprovedWatchlist, watchlist_id)
    if watchlist is None:
        return True
    snapshot = await build_watchlist_snapshot(session, watchlist, scope=scope)
    if require_resolved and snapshot.unresolved_members:
        return True
    return snapshot.content_hash != reviewed_hash
