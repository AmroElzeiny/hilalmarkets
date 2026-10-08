"""Which coins have a public Passport, and which standard each one opens on.

One rule, two readers. The Passport page (``/passports/<coin>``) opens a coin with
:func:`open_passport`; the sitemap lists every coin :func:`public_passport_assets` finds,
and that list is made by asking :func:`open_passport` about each coin. So a coin is in
the sitemap exactly when its page opens: the sitemap can never send a search engine to a
page that answers "not found", nor leave out a page that is open to everyone. The links
from one Passport to others (:func:`public_passports`) come from the same list, under the
same rule.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from ai_market_monitor.core.asset_kinds import KIND_NOT_RECORDED
from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.sharia import (
    AssetPassportResponse,
    MethodologyComparisonResponse,
)
from ai_market_monitor.services.hilal_methodology import is_automated
from ai_market_monitor.services.passport_page import PublicPassport, passport_headline
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

#: The longest a Passport page waits for its links when no list exists yet.
_LINKS_WAIT_SECONDS: Final[float] = 1.5

logger = logging.getLogger(__name__)


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
    value: tuple[PublicPassport, ...]
    expires_at: float


#: One list per database. Keyed by the engine itself, not its ``id()``: a test suite
#: builds a new database per test, and a reused ``id()`` would hand one database's coins
#: to another.
_cache: WeakKeyDictionary[object, _Cached] = WeakKeyDictionary()
#: The refresh running for each database, so two readers never build the list twice.
_building: WeakKeyDictionary[object, asyncio.Task[tuple[PublicPassport, ...]]] = (
    WeakKeyDictionary()
)


def clear_cache() -> None:
    _cache.clear()
    _building.clear()


async def public_passport_assets(session: AsyncSession, settings: Settings) -> tuple[str, ...]:
    """Every coin whose Passport opens for a visitor, by symbol, in alphabetical order."""

    return tuple(item.symbol for item in await public_passports(session, settings))


async def public_passports(
    session: AsyncSession, settings: Settings
) -> tuple[PublicPassport, ...]:
    """Every coin whose Passport opens for a visitor, in alphabetical order by symbol.

    The coins asked about are every coin with a current result under any standard in
    force — no coin outside that set can have a Passport. Each one is then opened with
    :func:`open_passport`, the page's own rule, and kept only when it opens. The sitemap
    lists these, and each Passport links to some of them, so a Passport never links to
    a page that would not open.
    """

    key = session.bind
    cached = _cache.get(key) if key is not None else None
    now = monotonic()
    if cached is not None and cached.expires_at > now:
        return cached.value
    value = await _collect(session, settings)
    if key is not None:
        _cache[key] = _Cached(value=value, expires_at=now + _PUBLIC_PASSPORTS_SECONDS)
    return value


async def linkable_passports(
    session: AsyncSession,
    settings: Settings,
    *,
    wait: float | None = None,
) -> tuple[PublicPassport, ...]:
    """The same list, for a Passport page to link from — without making a reader wait.

    Building the list opens every coin's Passport: about eight seconds on the live site.
    The sitemap may take that time; a person opening a Passport may not. So a page uses
    the list it already has, even a few minutes old, and a fresh one is built beside it
    in its own database session. Only when there is no list at all — a new worker
    process — does the page wait, and then for ``wait`` seconds at most (by default
    :data:`_LINKS_WAIT_SECONDS`); past that it is sent without the links, which come
    with the next request.
    """

    key = session.bind
    if key is None:
        return ()
    cached = _cache.get(key)
    if cached is not None and cached.expires_at > monotonic():
        return cached.value
    task = _building.get(key)
    if task is None:
        task = asyncio.create_task(_refresh(key, settings))
        _building[key] = task
        task.add_done_callback(lambda _done: _building.pop(key, None))
    if cached is not None:
        return cached.value
    try:
        return await asyncio.wait_for(
            asyncio.shield(task), timeout=_LINKS_WAIT_SECONDS if wait is None else wait
        )
    except TimeoutError:
        return ()


async def _refresh(
    bind: AsyncEngine | AsyncConnection, settings: Settings
) -> tuple[PublicPassport, ...]:
    """Build the list in a session of its own, and keep it. Never raises."""

    started = monotonic()
    try:
        async with AsyncSession(bind=bind, expire_on_commit=False) as session:
            value = await _collect(session, settings)
    except Exception:  # A failed refresh keeps the old list; the next request retries.
        logger.exception("public_passports_refresh_failed")
        return ()
    _cache[bind] = _Cached(value=value, expires_at=started + _PUBLIC_PASSPORTS_SECONDS)
    return value


async def _collect(session: AsyncSession, settings: Settings) -> tuple[PublicPassport, ...]:
    """Ask every candidate coin's Passport whether it opens, and keep the ones that do."""

    screening = ShariaScreeningService(session, settings)
    candidates: set[str] = set()
    for methodology in await screening.executable_methodologies():
        if methodology.code == AGGREGATE_METHODOLOGY_CODE:
            continue
        candidates |= await screening.assessed_assets(methodology.id)
    opened: dict[str, PublicPassport] = {}
    for asset in sorted(candidates):
        try:
            passport, _comparison = await open_passport(session, settings, asset, None)
        except ShariaScreeningError:
            continue
        headline = passport_headline(passport)
        opened[passport.assessment.canonical_asset] = PublicPassport(
            symbol=passport.assessment.canonical_asset,
            name=headline.name,
            kind=passport.identity.kind_label if passport.identity else KIND_NOT_RECORDED,
            network=passport.identity.network_label if passport.identity else None,
        )
    return tuple(opened[symbol] for symbol in sorted(opened))
