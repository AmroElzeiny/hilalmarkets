"""What the Guided Builder may offer for "which coins" and "under which method".

The Builder already had the *actions* — ``select_universe``, ``select_watchlist``,
``select_methodology``, ``set_explicit_assets`` — but nothing told it what the legal
answers were. A client that does not know the choices either hard-codes them or asks the
assistant, and both are wrong: a hard-coded methodology id is a Sharia status decided in
JavaScript, and needing the assistant to pick a screening method is the same AI-only
dependency the Builder exists to remove.

This module is the one place those options come from. Everything is read from the
governed tables:

* methodologies come from the ``sharia_methodologies`` table, **active only**, so a draft
  or archived method can never be selected;
* watchlists are scoped to the signed-in user, so one person's list is never offered to
  another;
* the currently selected values are read back from the draft, so the Builder highlights
  what is actually stored rather than what was last clicked.

Nothing here assigns a Sharia status, and nothing here mutates. Choosing a methodology
selects a **governed record by id**; it does not create, alter or infer a ruling. The
selection is applied through the same option path the assistant uses, so every screening
gate runs unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import (
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    ShariaMethodology,
)
from ai_market_monitor.db.models.enums import ShariaMethodologyStatus
from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2


@dataclass(frozen=True, slots=True)
class MethodologyOption:
    """One screening method a person may choose, in the platform's own words."""

    methodology_id: str
    code: str
    name: str
    version: str
    #: Plain-language description from the governed record. Never generated here.
    explanation: str
    governing_body: str | None
    reviewer_group: str | None
    effective_from: str | None
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "methodology_id": self.methodology_id,
            "code": self.code,
            "name": self.name,
            "version": self.version,
            "label": f"{self.name} (v{self.version})",
            "explanation": self.explanation,
            "governing_body": self.governing_body,
            "reviewer_group": self.reviewer_group,
            "effective_from": self.effective_from,
            "selected": self.selected,
        }


@dataclass(frozen=True, slots=True)
class WatchlistOption:
    """One of the person's own approved lists."""

    watchlist_id: str
    name: str
    asset_count: int
    is_default: bool
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "watchlist_id": self.watchlist_id,
            "name": self.name,
            "asset_count": self.asset_count,
            "is_default": self.is_default,
            "selected": self.selected,
            "empty_reason": (
                "This list has no coins in it yet." if self.asset_count == 0 else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BuilderUniverseOptions:
    """Everything the "which coins" step needs, decided by the server."""

    methodologies: tuple[MethodologyOption, ...] = ()
    watchlists: tuple[WatchlistOption, ...] = ()
    #: The coins typed in by hand on this draft, if that is the chosen mode.
    explicit_assets: tuple[str, ...] = ()
    universe_mode: str | None = None
    #: Why a step cannot be completed yet, in words a beginner can act on. Empty when
    #: nothing is blocking.
    notices: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "methodologies": [item.to_dict() for item in self.methodologies],
            "watchlists": [item.to_dict() for item in self.watchlists],
            "explicit_assets": list(self.explicit_assets),
            "universe_mode": self.universe_mode,
            "notices": list(self.notices),
        }


async def builder_universe_options(
    session: AsyncSession,
    *,
    user_id: UUID,
    draft: StrategyDraftV2,
) -> BuilderUniverseOptions:
    """Read the governed choices for one person and one draft.

    A pure read. It never writes, never creates a default watchlist and never publishes a
    methodology — a Builder step that could bring a screening method into existence would
    be assigning Sharia status from the UI.
    """

    policy = draft.sharia_policy
    selected_methodology = str(policy.methodology_id) if policy.methodology_id else None
    selected_watchlist = (
        str(policy.approved_watchlist_id) if policy.approved_watchlist_id else None
    )

    methodology_rows = (
        (
            await session.execute(
                select(ShariaMethodology)
                # Active only. A draft method has not been approved by the reviewers and
                # an archived one has been withdrawn; offering either would let a person
                # monitor under a ruling the platform does not stand behind.
                .where(ShariaMethodology.status == ShariaMethodologyStatus.ACTIVE.value)
                .order_by(ShariaMethodology.name.asc(), ShariaMethodology.version.asc())
            )
        )
        .scalars()
        .all()
    )
    methodologies = tuple(
        MethodologyOption(
            methodology_id=str(row.id),
            code=row.code,
            name=row.name,
            version=row.version,
            explanation=row.description or "",
            governing_body=row.governing_body,
            reviewer_group=row.reviewer_group,
            effective_from=(
                row.effective_from.strftime("%d %b %Y") if row.effective_from else None
            ),
            selected=str(row.id) == selected_methodology,
        )
        for row in methodology_rows
    )

    count_rows = (
        await session.execute(
            select(
                ApprovedWatchlistAsset.watchlist_id,
                func.count(ApprovedWatchlistAsset.id),
            )
            .join(
                ApprovedWatchlist,
                ApprovedWatchlist.id == ApprovedWatchlistAsset.watchlist_id,
            )
            .where(ApprovedWatchlist.user_id == user_id)
            .group_by(ApprovedWatchlistAsset.watchlist_id)
        )
    ).all()
    counts: dict[UUID, int] = {row[0]: int(row[1]) for row in count_rows}
    watchlist_rows = (
        (
            await session.execute(
                select(ApprovedWatchlist)
                # Scoped to the signed-in person. Ownership is not a display concern:
                # offering somebody else's list would leak both its name and its coins.
                .where(ApprovedWatchlist.user_id == user_id)
                .order_by(
                    ApprovedWatchlist.is_default.desc(),
                    ApprovedWatchlist.name.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    watchlists = tuple(
        WatchlistOption(
            watchlist_id=str(row.id),
            name=row.name,
            asset_count=int(counts.get(row.id, 0)),
            is_default=bool(row.is_default),
            selected=str(row.id) == selected_watchlist,
        )
        for row in watchlist_rows
    )

    mode = policy.universe_mode.value if policy.universe_mode else None
    notices: list[str] = []
    if not methodologies:
        notices.append(
            "No screening method is published yet, so nothing can be monitored until one "
            "is. This is not something you can fix from here."
        )
    if mode == "approved_watchlist" and not watchlists:
        notices.append(
            "You have no Favorites lists yet. Make one from Halal Assets, or choose "
            "every eligible coin instead."
        )
    if mode == "explicit_assets" and not policy.explicit_symbols:
        notices.append("Type in at least one coin, or choose a different set of coins.")

    return BuilderUniverseOptions(
        methodologies=methodologies,
        watchlists=watchlists,
        explicit_assets=tuple(policy.explicit_symbols),
        universe_mode=mode,
        notices=tuple(notices),
    )
