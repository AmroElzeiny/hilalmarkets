"""A missing database driver must name ``DATABASE_URL``, never just the module.

The release image installs only the PostgreSQL driver, while ``database_url`` defaults to
``sqlite+aiosqlite``. Starting the image without ``DATABASE_URL`` therefore failed with
``ModuleNotFoundError: No module named 'aiosqlite'``, which tells the operator nothing
about the setting that was actually wrong.

The rule is asserted for every driver a deployment can plausibly name, not only for the
one that was reported.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from types import ModuleType

import pytest

from ai_market_monitor.core.database import build_engine

# Every one of these schemes is spelled by SQLAlchemy but has no driver installed in this
# project's environments, so each reaches the same failure the release image hit.
#
# ``aiosqlite`` — the driver actually reported — cannot go in this list: it is a
# development dependency, so it is present here and absent only in the release image.
# ``test_reported_case_...`` below recreates that image's condition instead.
UNINSTALLED_DRIVERS = [
    ("postgresql+psycopg2://user:secret@db:5432/app", "psycopg2"),
    ("postgresql+pg8000://user:secret@db:5432/app", "pg8000"),
    ("mysql+aiomysql://user:secret@db:3306/app", "aiomysql"),
    ("mysql+pymysql://user:secret@db:3306/app", "pymysql"),
    ("oracle+cx_oracle://user:secret@db:1521/app", "cx_Oracle"),
]


@pytest.mark.parametrize(("database_url", "driver"), UNINSTALLED_DRIVERS)
def test_missing_driver_names_the_setting(database_url: str, driver: str) -> None:
    with pytest.raises(RuntimeError) as raised:
        build_engine(database_url)

    message = str(raised.value)
    assert "DATABASE_URL" in message, "the operator must learn which setting was wrong"
    assert database_url.split("://", 1)[0] in message, "the message must name the scheme"


@pytest.mark.parametrize(("database_url", "driver"), UNINSTALLED_DRIVERS)
def test_missing_driver_keeps_the_original_cause(database_url: str, driver: str) -> None:
    with pytest.raises(RuntimeError) as raised:
        build_engine(database_url)

    cause = raised.value.__cause__
    assert isinstance(cause, ModuleNotFoundError), "the real import error must stay attached"
    assert cause.name == driver


@pytest.mark.parametrize(("database_url", "driver"), UNINSTALLED_DRIVERS)
def test_password_never_appears_in_the_message(database_url: str, driver: str) -> None:
    with pytest.raises(RuntimeError) as raised:
        build_engine(database_url)

    assert "secret" not in str(raised.value), "a diagnostic must not leak the password"


class _HideModule:
    """Make one module look uninstalled, the way the release image leaves ``aiosqlite``."""

    def __init__(self, hidden: str) -> None:
        self.hidden = hidden

    def find_module(self, name: str, path: Sequence[str] | None = None) -> None:
        return None

    def find_spec(
        self,
        name: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> None:
        if name == self.hidden or name.startswith(f"{self.hidden}."):
            raise ModuleNotFoundError(f"No module named {self.hidden!r}", name=self.hidden)
        return None


@pytest.fixture
def without_aiosqlite() -> Iterator[None]:
    hidden = [name for name in sys.modules if name == "aiosqlite" or name.startswith("aiosqlite.")]
    saved = {name: sys.modules.pop(name) for name in hidden}
    finder = _HideModule("aiosqlite")
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.update(saved)


def test_reported_case_release_image_without_database_url(without_aiosqlite: None) -> None:
    """The exact failure: the default URL, in an environment with no ``aiosqlite``."""
    from ai_market_monitor.core.config import Settings

    default_url = Settings.model_fields["database_url"].default
    assert default_url.startswith("sqlite+aiosqlite"), "the default still names aiosqlite"

    with pytest.raises(RuntimeError) as raised:
        build_engine(default_url)

    message = str(raised.value)
    assert "DATABASE_URL" in message
    assert "aiosqlite" in message
    assert isinstance(raised.value.__cause__, ModuleNotFoundError)


def test_an_installed_driver_still_builds_an_engine() -> None:
    """The guard must only translate failures, never refuse a URL that works."""
    engine = build_engine("postgresql+psycopg://user:secret@db:5432/app")
    assert engine.url.drivername == "postgresql+psycopg"
