from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_market_monitor.core.config import get_settings

settings = get_settings()


def build_engine(database_url: str) -> AsyncEngine:
    """Create the engine, and explain a missing driver in terms of ``DATABASE_URL``.

    SQLAlchemy imports the driver named in the URL scheme, so a URL the environment
    cannot satisfy surfaces as a bare ``ModuleNotFoundError`` for a package the operator
    has never heard of. ``database_url`` defaults to ``sqlite+aiosqlite``, and
    ``aiosqlite`` is a development-only dependency, so starting a release image without
    ``DATABASE_URL`` used to fail with "No module named 'aiosqlite'" — which names
    neither the setting that was wrong nor what to do about it.

    The URL itself is never repeated back: it carries the database password. Only the
    scheme and the missing module name go into the message.
    """
    try:
        return create_async_engine(database_url, pool_pre_ping=True, echo=False)
    except ModuleNotFoundError as missing_driver:
        scheme = database_url.split("://", 1)[0] or "(empty)"
        raise RuntimeError(
            f"DATABASE_URL asks for {scheme!r}, but its database driver "
            f"{missing_driver.name!r} is not installed here. Set DATABASE_URL to the "
            "database this deployment should use — the release images ship the "
            "PostgreSQL driver — or install the missing driver for local work."
        ) from missing_driver


engine: AsyncEngine = build_engine(settings.database_url)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
