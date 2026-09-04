"""Checking the waiting coins again, on demand, must visit the coins a person can see.

A reviewer looking at "Pages not found" presses one button and expects the coins **in
that list** to be read again now. Three things can quietly make that untrue, and each one
looks like success from the outside:

* a second idea of what "waiting" means — the button visits a set built by different
  code from the set the page draws, so a coin on screen is never touched;
* the recheck calendar — every address was fetched inside the window, so each one is
  skipped and the run reports "completed, 0 checked" having done nothing;
* the shallow layers only — the cheap guesses are re-run and the layer that actually
  finds a page (the project's own homepage, the search engine) never runs.

All three are pinned here, against the database rather than against a mock, because all
three are about which rows are selected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.base import Base
from ai_market_monitor.db.models import CanonicalAsset, ReviewCase
from ai_market_monitor.db.models.enums import ReviewCaseType
from ai_market_monitor.services.sharia_source_resolution import (
    SourceResolutionService,
    pending_asset_ids,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _settings(**overrides) -> Settings:
    fields = {
        "database_url": "sqlite+aiosqlite://",
        "sharia_source_resolution_enabled": True,
        **overrides,
    }
    return Settings(**fields)


async def _asset(session: AsyncSession, symbol: str, *, verified: bool = True) -> CanonicalAsset:
    asset = CanonicalAsset(
        id=uuid4(),
        symbol=symbol,
        name=f"{symbol} Project",
        asset_type="token",
        identity_hash=f"hash-{symbol.lower()}",
        mapping_state="verified" if verified else "candidate",
        official_website=f"https://{symbol.lower()}.test",
    )
    session.add(asset)
    await session.flush()
    return asset


async def _gap_case(
    session: AsyncSession, asset: CanonicalAsset, *, done: bool = False
) -> ReviewCase:
    case = ReviewCase(
        case_reference=f"SRC-{asset.symbol}",
        case_type=ReviewCaseType.OFFICIAL_SOURCE_GAP,
        state="needs_evidence",
        publication_state="unpublished",
        canonical_asset_id=asset.id,
        title=f"Find an official news page for {asset.name}",
        priority="normal",
        risk_severity="low",
        human_review_reason="No working news page.",
        idempotency_key=f"official-source-gap:{asset.id}",
        done_at=NOW if done else None,
    )
    session.add(case)
    await session.flush()
    return case


# ---------------------------------------------------------------------------
# One owner for "which coins are waiting"
# ---------------------------------------------------------------------------


async def test_waiting_coins_are_read_from_the_open_cases_a_person_sees(
    session: AsyncSession,
) -> None:
    waiting = await _asset(session, "AAA")
    settled = await _asset(session, "BBB")
    never_asked = await _asset(session, "CCC")
    await _gap_case(session, waiting)
    await _gap_case(session, settled, done=True)

    found = await pending_asset_ids(session)

    assert found == {waiting.id}
    assert settled.id not in found, "a closed task is not something a person is waiting on"
    assert never_asked.id not in found


async def test_only_the_pages_not_found_kind_counts(session: AsyncSession) -> None:
    """A coin waiting for a different reason is not a coin waiting for an address."""

    other = await _asset(session, "DDD")
    session.add(
        ReviewCase(
            case_reference="REV-DDD",
            case_type=ReviewCaseType.INITIAL_ASSET_REVIEW,
            state="needs_evidence",
            publication_state="unpublished",
            canonical_asset_id=other.id,
            title="Initial review",
            priority="normal",
            risk_severity="low",
            human_review_reason="A first look at this coin.",
            idempotency_key=f"initial:{other.id}",
        )
    )
    await session.flush()

    assert await pending_asset_ids(session) == set()


async def test_the_script_and_the_button_share_one_definition() -> None:
    """The operator's script must not carry a second copy of the selection rule.

    Two copies is how "the coins in this list" and "the coins the script picks" drift
    into different sets while both keep reporting success.
    """

    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "recheck_official_sources.py"
    ).read_text(encoding="utf-8")
    assert "pending_asset_ids" in script
    assert "async def _pending_asset_ids" not in script, (
        "the script defines its own copy again; import the one owner from "
        "services/sharia_source_resolution.py instead"
    )


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------


class _RecordingService(SourceResolutionService):
    """Records which coins were visited and how, without touching the network."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.visited: list[tuple[str, bool]] = []

    async def resolve_asset(self, asset: CanonicalAsset, *, deep: bool = False):
        self.visited.append((asset.symbol, deep))
        from ai_market_monitor.services.sharia_source_resolution import AssetSourceOutcome

        return AssetSourceOutcome(asset_id=asset.id, symbol=asset.symbol)


class _CountingProvider:
    """A CoinMarketCap stub that records how many calls each sweep really makes."""

    enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def coin_links(self, symbols):
        self.calls.append(tuple(symbols))
        return {}


@pytest.mark.parametrize("sweep_name", ["resolve_open_cases", "resolve_pending"])
async def test_one_provider_call_covers_the_whole_sweep(
    session: AsyncSession, sweep_name: str
) -> None:
    """Both sweeps, not just the one that was reported.

    ``coin_links`` carries up to a hundred symbols in one request. Asking for them one at
    a time still works, which is exactly why nothing caught it: ``preload_provider_links``
    was written for this and then never called from anywhere. With 157 coins waiting, the
    button spent 157 provider credits and 157 round trips before fetching a single page,
    and the daily sweep did the same for its whole batch.

    Asserted as "one call, and it carries every symbol", so a future caller that loops
    per coin fails here rather than quietly costing a hundred times more.
    """

    for symbol in ("AAA", "BBB", "CCC"):
        asset = await _asset(session, symbol)
        await _gap_case(session, asset)
    await session.flush()

    provider = _CountingProvider()
    service = _RecordingService(
        session, _settings(), force_recheck=True, coinmarketcap=provider
    )
    await getattr(service, sweep_name)()

    assert len(provider.calls) == 1, (
        f"{sweep_name} made {len(provider.calls)} provider calls for 3 coins; "
        "it should batch them into one"
    )
    assert set(provider.calls[0]) == {"AAA", "BBB", "CCC"}


async def test_it_visits_every_waiting_coin_and_nothing_else(session: AsyncSession) -> None:
    waiting_one = await _asset(session, "AAA")
    waiting_two = await _asset(session, "BBB")
    quiet = await _asset(session, "CCC")
    await _gap_case(session, waiting_one)
    await _gap_case(session, waiting_two)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    sweep = await service.resolve_open_cases()

    assert sorted(symbol for symbol, _ in service.visited) == ["AAA", "BBB"]
    assert quiet.symbol not in [symbol for symbol, _ in service.visited]
    assert len(sweep.assets) == 2


async def test_a_named_selection_visits_only_the_coins_that_were_named(
    session: AsyncSession,
) -> None:
    """"Run research" on three ticked coins must fetch three coins, not a hundred.

    Both buttons send the same task and they mean different sets. Without a named set the
    ticked-coins button did the whole waiting list: minutes of fetching against other
    people's servers that nobody asked for, under a message that said "1 coin is being
    looked up again".
    """

    ticked = await _asset(session, "AAA")
    also_waiting = await _asset(session, "BBB")
    await _gap_case(session, ticked)
    await _gap_case(session, also_waiting)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    sweep = await service.resolve_open_cases(only={ticked.id})

    assert [symbol for symbol, _ in service.visited] == ["AAA"]
    assert len(sweep.assets) == 1


async def test_a_named_selection_can_never_widen_the_sweep(session: AsyncSession) -> None:
    """It narrows. A coin with no open task is not reached by naming it.

    The one rule this sweep rests on is that a coin is only ever re-read because a person
    is being asked about it. A caller that could add coins would break that rule from the
    outside, and a stale id from a page loaded ten minutes ago is the ordinary way it
    would happen.
    """

    waiting = await _asset(session, "AAA")
    never_asked = await _asset(session, "CCC")
    await _gap_case(session, waiting)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    sweep = await service.resolve_open_cases(only={waiting.id, never_asked.id, uuid4()})

    assert [symbol for symbol, _ in service.visited] == ["AAA"]
    assert len(sweep.assets) == 1


async def test_naming_nothing_still_means_every_waiting_coin(session: AsyncSession) -> None:
    """The whole-list button sends no names, and must keep the whole list."""

    for symbol in ("AAA", "BBB"):
        await _gap_case(session, await _asset(session, symbol))
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    await service.resolve_open_cases(only=None)

    assert sorted(symbol for symbol, _ in service.visited) == ["AAA", "BBB"]


async def test_every_layer_runs_so_a_new_page_can_actually_be_found(
    session: AsyncSession,
) -> None:
    """``deep`` is not optional here. Re-running only the cheap guesses finds nothing new."""

    asset = await _asset(session, "AAA")
    await _gap_case(session, asset)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    await service.resolve_open_cases()

    assert service.visited == [("AAA", True)]


async def test_the_recheck_calendar_is_ignored(session: AsyncSession) -> None:
    """Without this the button re-reads stored answers and changes nothing."""

    asset = await _asset(session, "AAA")
    await _gap_case(session, asset)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    assert service.force_recheck is True
    # A row fetched one minute ago is still due, because the operator asked.
    row = type("Row", (), {"last_checked_at": datetime.now(UTC) - timedelta(minutes=1)})()
    assert service._due_for_recheck(row) is True


async def test_an_unverified_coin_is_never_visited(session: AsyncSession) -> None:
    """The same rule the scheduled sweep obeys: no unapproved identity is derived from."""

    asset = await _asset(session, "AAA", verified=False)
    await _gap_case(session, asset)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    sweep = await service.resolve_open_cases()

    assert service.visited == []
    assert sweep.assets == []


async def test_nothing_waiting_means_nothing_fetched(session: AsyncSession) -> None:
    await _asset(session, "AAA")
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    sweep = await service.resolve_open_cases()

    assert service.visited == []
    assert sweep.assets == []


async def test_it_does_nothing_at_all_when_the_feature_is_off(session: AsyncSession) -> None:
    asset = await _asset(session, "AAA")
    await _gap_case(session, asset)
    await session.flush()

    service = _RecordingService(
        session, _settings(sharia_source_resolution_enabled=False), force_recheck=True
    )
    sweep = await service.resolve_open_cases()

    assert service.visited == []
    assert sweep.assets == []


async def test_the_closed_cases_stay_closed(session: AsyncSession) -> None:
    """A task somebody already settled must not be reopened by pressing the button."""

    asset = await _asset(session, "AAA")
    await _gap_case(session, asset, done=True)
    await session.flush()

    service = _RecordingService(session, _settings(), force_recheck=True)
    await service.resolve_open_cases()

    case = await session.scalar(
        select(ReviewCase).where(ReviewCase.canonical_asset_id == asset.id)
    )
    assert case is not None
    assert case.done_at is not None
    assert service.visited == []


# ---------------------------------------------------------------------------
# One press, one sweep
# ---------------------------------------------------------------------------


class _Redis:
    """A stand-in for the shared lock, with the real ``SET NX`` answer shape."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.closed = False
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in self.keys:
            return None  # what redis really returns when NX refuses
        self.keys[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        return int(self.keys.pop(key, None) is not None)

    async def aclose(self) -> None:
        self.closed = True


async def _run_with(monkeypatch, client, *, app_env: str = "production") -> bool:
    """Take the lock through the real context manager and report whether it was held."""

    from ai_market_monitor import worker as worker_module

    monkeypatch.setattr(
        worker_module.settings, "app_env", app_env, raising=False
    )
    monkeypatch.setattr(
        worker_module.settings, "redis_url", "redis://localhost:6379/0", raising=False
    )

    class _Factory:
        @staticmethod
        def from_url(_url: str):
            if client is None:
                raise RuntimeError("no redis here")
            return client

    import sys
    import types

    module = types.ModuleType("redis.asyncio")
    module.Redis = _Factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis.asyncio", module)

    async with worker_module._only_one_recheck() as held:
        return bool(held)


async def test_a_second_press_while_one_is_running_is_refused(monkeypatch) -> None:
    """Two sweeps at once would visit the same site twice at the same moment."""

    from ai_market_monitor import worker as worker_module

    client = _Redis()
    client.keys[worker_module.RECHECK_LOCK_KEY] = "1"  # one is already running

    assert await _run_with(monkeypatch, client) is False
    # The refused run must not release the lock the running one is holding.
    assert client.deleted == []


async def test_the_lock_is_released_when_the_sweep_finishes(monkeypatch) -> None:
    from ai_market_monitor import worker as worker_module

    client = _Redis()

    assert await _run_with(monkeypatch, client) is True
    assert client.deleted == [worker_module.RECHECK_LOCK_KEY]
    assert client.closed is True


async def test_no_redis_means_the_sweep_still_runs(monkeypatch) -> None:
    """A missing guard is not a reason to refuse work, or the button dies silently."""

    assert await _run_with(monkeypatch, None) is True


async def test_the_lock_expires_on_its_own(monkeypatch) -> None:
    """A worker killed mid-run must not block the button until somebody clears a key."""

    from ai_market_monitor import worker as worker_module

    recorded: dict[str, int | None] = {}

    class _Recording(_Redis):
        async def set(self, key, value, *, nx=False, ex=None):
            recorded["ex"] = ex
            return await super().set(key, value, nx=nx, ex=ex)

    await _run_with(monkeypatch, _Recording())

    assert recorded["ex"] == worker_module.RECHECK_LOCK_SECONDS
    assert worker_module.RECHECK_LOCK_SECONDS > 0


# ---------------------------------------------------------------------------
# The route and the button
# ---------------------------------------------------------------------------


def test_the_page_offers_the_button_and_the_route_exists() -> None:
    """The button, its address, and the worker task are one chain with no missing link."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    template = (
        root / "src" / "ai_market_monitor" / "templates" / "system_brain.html"
    ).read_text(encoding="utf-8")
    router = (
        root / "src" / "ai_market_monitor" / "api" / "routers" / "system_brain.py"
    ).read_text(encoding="utf-8")
    worker = (root / "src" / "ai_market_monitor" / "worker.py").read_text(encoding="utf-8")

    assert "/dashboard/system-brain/cases/recheck-sources" in template
    assert "/dashboard/system-brain/cases/recheck-sources" in router
    # The form must carry a CSRF token like every other posting form on the page.
    assert 'class="brain-recheck-bar"' in template
    assert "csrf_token" in template
    assert "_verify_csrf" in router
    # And the route must hand the work to the task that really does it.
    assert "ai_market_monitor.recheck_official_sources_for_open_cases" in router
    assert "ai_market_monitor.recheck_official_sources_for_open_cases" in worker
    assert "resolve_open_cases" in worker


#: Words that would make a button read as a religious verdict. None may appear on one
#: that only re-reads web addresses.
FORBIDDEN_WORDS = (
    "halal",
    "haram",
    "haraam",
    "permissible",
    "impermissible",
    "compliant",
    "verdict",
    "ruling",
)


def _recheck_form_block() -> str:
    from pathlib import Path

    template = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ai_market_monitor"
        / "templates"
        / "system_brain.html"
    ).read_text(encoding="utf-8")
    start = template.index('class="brain-recheck-bar"')
    return template[start : template.index("</form>", start)].casefold()


@pytest.mark.parametrize("word", FORBIDDEN_WORDS)
def test_the_button_never_reads_as_a_sharia_decision(word: str) -> None:
    """It re-reads addresses. Nothing about it may sound like a religious answer."""

    assert word not in _recheck_form_block(), f"the recheck button must not say {word!r}"


def test_the_button_says_out_loud_that_it_decides_nothing() -> None:
    """The reassurance is the copy, not an omission a reader has to infer."""

    block = _recheck_form_block()
    assert "nothing is decided" in block
    assert "nothing is published" in block
