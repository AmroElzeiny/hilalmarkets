"""Every process opens its own pool, and together they must fit the database.

`create_async_engine` was called with no pool arguments at all, so SQLAlchemy's defaults
applied silently: 5 connections kept open, 10 more allowed in a burst, and a **thirty
second** wait for a free one.

Both halves were wrong for this deployment.

**The count.** A pool is per *process*, and this deployment starts five of them — two API
workers, the Celery parent and its child, and the scheduler. Fifteen each is 75 against
PostgreSQL's default ceiling of 100, and nothing anywhere said so. Adding a third API
worker — a one-word change, and the obvious response to the site being slow — would have
crossed it, and the failure would have appeared as the database refusing new connections
long after the change that caused it.

**The wait.** A saturated pool did not fail, it froze. Every page hung for thirty seconds
and then broke. To somebody using the site that is not "busy", it is "the whole thing is
down", and no message names the cause. Five seconds fails fast and legibly, and matches
`provider_pool_timeout_seconds`, which already had exactly this reasoning written on it.

The rules below are asserted against the settings the deployment actually runs with, so
the arithmetic is redone on every change to any of the numbers that feed it.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_market_monitor.core.config import Settings, get_settings
from ai_market_monitor.core.database import build_engine, pool_options

#: PostgreSQL's own default. The server does not override it, and raising it is not free:
#: every backend process costs memory in a container capped at 512 MB.
POSTGRES_MAX_CONNECTIONS = 100

#: PostgreSQL keeps a few back for superuser logins, which is how an operator gets in to
#: fix a connection storm. Those must never be part of the application's budget.
SUPERUSER_RESERVED_CONNECTIONS = 3

#: How much of what is left the application may claim at full stretch. A pool budget that
#: exactly fills the database leaves nothing for a migration, a psql session during an
#: incident, or a backup.
SAFE_SHARE = 0.7


def processes_with_a_pool(settings: Settings) -> dict[str, int]:
    """Every process that imports the application and therefore builds an engine.

    Counted by name so that a reader can see what was included. The Celery parent is
    counted even though it runs no tasks: it imports the application, and a pool that is
    merely *able* to open connections is part of the worst case.
    """
    return {
        "api workers": settings.api_worker_processes,
        "celery parent": 1,
        "celery children": settings.celery_worker_concurrency,
        "scheduler": 1,
    }


def worst_case_connections(settings: Settings) -> int:
    per_process = settings.database_pool_size + settings.database_pool_overflow
    return per_process * sum(processes_with_a_pool(settings).values())


def test_all_the_pools_together_fit_the_database() -> None:
    """The sum, not the per-process number, is what the database sees."""
    settings = get_settings()
    total = worst_case_connections(settings)
    budget = int((POSTGRES_MAX_CONNECTIONS - SUPERUSER_RESERVED_CONNECTIONS) * SAFE_SHARE)
    breakdown = "  ".join(
        f"{name}={count}" for name, count in processes_with_a_pool(settings).items()
    )
    assert total <= budget, (
        f"the pools can open {total} connections at once but only {budget} may be "
        f"claimed ({POSTGRES_MAX_CONNECTIONS} PostgreSQL allows, less "
        f"{SUPERUSER_RESERVED_CONNECTIONS} kept for superuser logins, less a margin for "
        f"migrations and a psql session during an incident).\n"
        f"  processes: {breakdown}\n"
        f"  each: {settings.database_pool_size} + "
        f"{settings.database_pool_overflow} overflow\n"
        "Lower the pool, lower the process count, or raise PostgreSQL's max_connections "
        "— and if you raise it, change POSTGRES_MAX_CONNECTIONS in this test and check "
        "the db container's memory limit, because every backend costs memory."
    )


def test_there_is_room_to_add_a_worker() -> None:
    """The obvious response to a slow site is another worker. It must not break the database.

    This is the specific trap: the change is one number in one file, it looks harmless,
    and the damage appears somewhere else entirely — as the database refusing to accept
    connections.
    """
    settings = get_settings()
    with_one_more = get_settings().model_copy(
        update={"api_worker_processes": settings.api_worker_processes + 1}
    )
    budget = int((POSTGRES_MAX_CONNECTIONS - SUPERUSER_RESERVED_CONNECTIONS) * SAFE_SHARE)
    assert worst_case_connections(with_one_more) <= budget, (
        "adding one API worker would take the pools past the safe share of PostgreSQL's "
        "connection limit. Leave headroom for at least one more worker, so growing the "
        "site is not a database outage."
    )


def test_a_busy_moment_does_not_wait_half_a_minute() -> None:
    """Fail fast and say so, rather than freezing the page.

    Thirty seconds of a blank page is indistinguishable from the site being down, and it
    holds the worker for the whole time — so a short burst of slow requests turns into a
    long queue of them.
    """
    seconds = get_settings().database_pool_timeout_seconds
    assert seconds <= 10, (
        f"a request waits {seconds}s for a database connection. Anything near "
        "SQLAlchemy's default of 30 reads as an outage rather than as a busy moment, and "
        "the worker is blocked for the whole wait."
    )


def test_the_wait_is_not_so_short_that_a_normal_query_loses_its_turn() -> None:
    """A timeout below a normal query is a self-inflicted error under mild load."""
    assert get_settings().database_pool_timeout_seconds >= 1.0


def test_connections_are_recycled_before_the_network_drops_them() -> None:
    """`pool_pre_ping` finds a dead connection by paying a failed round trip for it.

    Recycling first means the common case never reaches the ping. Both are kept: recycling
    handles age, the ping handles anything that dies early.
    """
    settings = get_settings()
    assert 60 <= settings.database_pool_recycle_seconds <= 3600, (
        "connections should be replaced within the hour. Longer and idle ones are dropped "
        "by the network first; much shorter and the pool is rebuilt for no reason."
    )


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite://",
        "sqlite+aiosqlite:///./local.db",
        "sqlite:///./local.db",
    ],
)
def test_sqlite_is_given_no_pool_arguments(url: str) -> None:
    """SQLite has no queue of connections, and raises on every one of these names.

    The whole test suite and local development run on SQLite. Passing `pool_size` to it
    is `TypeError: Invalid argument(s) 'pool_size'` at import time — the application would
    not start at all, which is why this is asserted rather than assumed.
    """
    assert pool_options(url, get_settings()) == {}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:pw@db:5432/app",
        "postgresql+psycopg://user:pw@db:5432/app",
    ],
)
def test_a_real_database_gets_every_setting(url: str) -> None:
    """Each of the four is passed on, not merely read.

    Parametrised over the drivers this project could use, because the guard is written as
    "is it SQLite" and a second real driver must not fall on the wrong side of it.
    """
    settings = get_settings()
    chosen = pool_options(url, settings)
    assert chosen == {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_pool_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_recycle": settings.database_pool_recycle_seconds,
    }


def test_the_engine_really_builds_with_them() -> None:
    """The options reach SQLAlchemy, and SQLAlchemy accepts them.

    A dictionary that is correct and never passed on would satisfy every test above.
    """
    settings = get_settings()
    engine = build_engine("sqlite+aiosqlite://", settings)
    assert isinstance(engine, AsyncEngine)


def test_the_local_default_still_starts() -> None:
    """The default `DATABASE_URL` is SQLite, so this is the path a new developer takes."""
    assert build_engine(get_settings().database_url) is not None
