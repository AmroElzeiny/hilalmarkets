"""Which coins have a public Passport, and which standard each one opens on.

One rule, two readers. The Passport page (``/passports/<coin>``) opens a coin with
:func:`open_passport`; the sitemap lists every coin :func:`public_passport_assets` finds,
and that list is made by asking :func:`open_passport` about each coin. So a coin is in
the sitemap exactly when its page opens: the sitemap can never send a search engine to a
page that answers "not found", nor leave out a page that is open to everyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.sharia import (
    AssetPassportResponse,
    MethodologyComparisonResponse,
)
from ai_market_monitor.services.hilal_methodology import is_automated
from ai_market_monitor.services.sharia_passports import ShariaPassportReadService
from ai_market_monitor.services.sharia_screening import (
    AGGREGATE_METHODOLOGY_CODE,
    ShariaScreeningError,
    ShariaScreeningService,
)

#: How long the list of public Passports is reused. Opening every coin's Passport to
#: build it takes a few hundred database reads, and a search engine may ask for the
#: sitemap often. A coin published or withdrawn reaches the sitemap within this time.
_PUBLIC_PASSPORTS_SECONDS: Final[float] = 600.0


async def open_passport(
    session: AsyncSession,
    settings: Settings,
    asset: str,
    methodology_id: UUID | None,
) -> tuple[AssetPassportResponse, MethodologyComparisonResponse]:
    """The coin's Passport under the standard asked for — or, asked for none, its own.

    **One Passport per coin**, so a coin some standard reviewed always has a page. With
    no standard named it opens on the product's default standard; when that standard
    never reviewed this coin, on the first standard that did. The machine-made standard
    is never chosen this way: it is a standard a person picks on purpose, never one put
    in front of somebody who did not (`services/sharia_screening.py`). A standard named
    in the address is honoured exactly, or refused — never swapped for another.

    Raises :class:`ShariaScreeningError` when the coin has no Passport to open.
    """

    reader = ShariaPassportReadService(session, settings)
    comparison = await ShariaScreeningService(session, settings).methodology_comparison(asset)
    try:
        return await reader.current(asset, methodology_id=methodology_id), comparison
    except ShariaScreeningError as refused:
        if methodology_id is not None:
            raise
        for item in comparison.results:
            if item.status is None or is_automated(item.methodology.code):
                continue
            try:
                passport = await reader.current(asset, methodology_id=item.methodology.id)
            except ShariaScreeningError:
                continue
            return passport, comparison
        # Last, and only when no other standard covers the coin: a result under the
        # Hilal Markets Methodology that a Hilal Markets reviewer decided. That is a
        # person's decision, not the machine's, so it may open on its own — the rule
        # above keeps out only what nobody reviewed.
        for item in comparison.results:
            if item.status is None or not is_automated(item.methodology.code):
                continue
            try:
                passport = await reader.current(asset, methodology_id=item.methodology.id)
            except ShariaScreeningError:
                continue
            if passport.decision_record is not None:
                return passport, comparison
        raise refused from None


@dataclass(slots=True)
class _Cached:
    value: tuple[str, ...]
    expires_at: float


#: One list per database. Keyed by the engine itself, not its ``id()``: a test suite
#: builds a new database per test, and a reused ``id()`` would hand one database's coins
#: to another.
_cache: WeakKeyDictionary[object, _Cached] = WeakKeyDictionary()


def clear_cache() -> None:
    _cache.clear()


async def public_passport_assets(session: AsyncSession, settings: Settings) -> tuple[str, ...]:
    """Every coin whose Passport opens for a visitor, by symbol, in alphabetical order.

    The coins asked about are every coin with a current result under any standard in
    force — no coin outside that set can have a Passport. Each one is then opened with
    :func:`open_passport`, the page's own rule, and kept only when it opens.
    """

    key = session.bind
    cached = _cache.get(key) if key is not None else None
    now = monotonic()
    if cached is not None and cached.expires_at > now:
        return cached.value
    screening = ShariaScreeningService(session, settings)
    candidates: set[str] = set()
    for methodology in await screening.executable_methodologies():
        if methodology.code == AGGREGATE_METHODOLOGY_CODE:
            continue
        candidates |= await screening.assessed_assets(methodology.id)
    opened: set[str] = set()
    for asset in sorted(candidates):
        try:
            passport, _comparison = await open_passport(session, settings, asset, None)
        except ShariaScreeningError:
            continue
        opened.add(passport.assessment.canonical_asset)
    value = tuple(sorted(opened))
    if key is not None:
        _cache[key] = _Cached(value=value, expires_at=now + _PUBLIC_PASSPORTS_SECONDS)
    return value
