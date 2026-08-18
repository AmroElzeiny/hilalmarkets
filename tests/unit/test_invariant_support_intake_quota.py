"""One rule decides who may send a support message, and both doors obey it.

The product takes support messages through two forms — the public ``/contact`` page and
the dashboard's own support form. Neither counted anything, so a script could open a
thousand tickets, and "two per email" could only ever have meant two *per form*.

These tests assert the rule rather than one door:

* every limit is read from settings, so an operator changes a number in the
  environment and never in the code;
* every limit is counted from one ledger, so the two doors cannot disagree about how
  many messages a person has already sent;
* each of the three limits fires on its own, and the person is told which one and when
  to come back;
* an incoherent pair of numbers is refused at startup rather than discovered by the
  second customer of the hour.

A third door added later fails here unless it uses the same module.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.base import Base
from ai_market_monitor.db.models import SupportIntakeRecord
from ai_market_monitor.services.support_intake import (
    SUPPORT_INTAKE_DOORS,
    SupportIntakeGuard,
    support_intake_limits,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "ai_market_monitor"

#: The three numbers, and the environment variable each one comes from.
LIMIT_SETTINGS = (
    ("per_email", "support_intake_max_per_email", "SUPPORT_INTAKE_MAX_PER_EMAIL", 2),
    ("per_client", "support_intake_max_per_client", "SUPPORT_INTAKE_MAX_PER_CLIENT", 2),
    ("per_hour", "support_intake_max_per_hour", "SUPPORT_INTAKE_MAX_PER_HOUR", 20),
)


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "database_url": "sqlite+aiosqlite://",
        "public_base_url": "https://hilal.example",
    }
    values.update(overrides)
    return Settings(**values)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


# --------------------------------------------------------------------------- #
#  The numbers live in the environment                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field,setting,variable,default", LIMIT_SETTINGS)
def test_every_limit_is_a_setting_with_the_documented_default(
    field: str, setting: str, variable: str, default: int
) -> None:
    assert getattr(support_intake_limits(_settings()), field) == default
    assert getattr(_settings(), setting) == default
    assert variable == setting.upper()


@pytest.mark.parametrize("field,setting,variable,default", LIMIT_SETTINGS)
def test_every_limit_is_documented_in_both_environment_examples(
    field: str, setting: str, variable: str, default: int
) -> None:
    """A number an operator cannot find is a number an operator cannot change."""

    for name in (".env.example", ".env.production.example"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(rf"^{variable}={default}$", text, re.M), f"{variable} missing from {name}"
    assert getattr(support_intake_limits(_settings()), field) == default
    assert setting in Settings.model_fields


def test_the_window_is_a_setting_too() -> None:
    for name in (".env.example", ".env.production.example"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "SUPPORT_INTAKE_WINDOW_SECONDS=3600" in text, name
    assert support_intake_limits(_settings()).window == timedelta(hours=1)


@pytest.mark.parametrize(
    "setting",
    ["support_intake_max_per_email", "support_intake_max_per_client"],
)
def test_a_personal_allowance_above_the_flood_ceiling_is_refused(setting: str) -> None:
    """Otherwise the first person of the hour spends everybody else's allowance."""

    with pytest.raises(ValueError, match="cannot exceed"):
        _settings(**{setting: 30, "support_intake_max_per_hour": 20})


# --------------------------------------------------------------------------- #
#  Each limit fires on its own                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("door", sorted(SUPPORT_INTAKE_DOORS))
async def test_the_email_limit_stops_the_third_message_whichever_door_sent_them(
    session: AsyncSession, door: str
) -> None:
    guard = SupportIntakeGuard(session, _settings())
    for _ in range(2):
        assert (
            await guard.check(email="a@example.com", client_fingerprint=f"client-{_}")
        ).allowed
        await guard.record(door=door, email="a@example.com", client_fingerprint=f"client-{_}")

    refused = await guard.check(email="a@example.com", client_fingerprint="client-fresh")
    assert refused.allowed is False
    assert refused.reason == "per_email"
    assert refused.remaining_for_email == 0
    assert "already sent 2 messages" in refused.message()


async def test_the_email_limit_is_shared_between_the_two_doors(session: AsyncSession) -> None:
    """Two per email means two, not two at each form.

    This is the whole reason the ledger exists. Counting each form's own table would
    have given the same person a fresh allowance simply by changing page.
    """

    guard = SupportIntakeGuard(session, _settings())
    await guard.record(door="contact", email="a@example.com", client_fingerprint="one")
    await guard.record(door="dashboard", email="a@example.com", client_fingerprint="two")

    refused = await guard.check(email="a@example.com", client_fingerprint="three")
    assert refused.allowed is False
    assert refused.reason == "per_email"


@pytest.mark.parametrize(
    "written,read",
    [
        ("a@example.com", "A@Example.com"),
        ("  a@example.com  ", "a@example.com"),
        ("A@EXAMPLE.COM", "a@example.com"),
    ],
)
async def test_one_address_is_one_person_however_it_is_typed(
    session: AsyncSession, written: str, read: str
) -> None:
    """Capital letters and stray spaces are not a second person."""

    guard = SupportIntakeGuard(session, _settings())
    for _ in range(2):
        await guard.record(door="contact", email=written, client_fingerprint=f"c{_}")

    refused = await guard.check(email=read, client_fingerprint="fresh")
    assert refused.allowed is False
    assert refused.reason == "per_email"


async def test_the_client_limit_stops_a_fresh_address_from_the_same_browser(
    session: AsyncSession,
) -> None:
    guard = SupportIntakeGuard(session, _settings())
    for index in range(2):
        await guard.record(
            door="contact", email=f"person{index}@example.com", client_fingerprint="same-browser"
        )

    refused = await guard.check(
        email="person-new@example.com", client_fingerprint="same-browser"
    )
    assert refused.allowed is False
    assert refused.reason == "per_client"
    assert "from this device" in refused.message()


async def test_the_flood_ceiling_stops_a_crowd_of_fresh_addresses(
    session: AsyncSession,
) -> None:
    """Twenty different people, twenty different browsers, one ceiling."""

    settings = _settings()
    guard = SupportIntakeGuard(session, settings)
    for index in range(settings.support_intake_max_per_hour):
        await guard.record(
            door="contact",
            email=f"person{index}@example.com",
            client_fingerprint=f"browser-{index}",
        )

    refused = await guard.check(email="one-more@example.com", client_fingerprint="browser-new")
    assert refused.allowed is False
    assert refused.reason == "per_hour"
    assert "unusual number of messages" in refused.message()


async def test_the_narrowest_reason_is_the_one_reported(session: AsyncSession) -> None:
    """"We are busy" would be true and about the wrong thing.

    When a person has used their own allowance, telling them the product is busy sends
    them back to try again in a minute, which will not work. They are told what
    actually stopped them.
    """

    settings = _settings(support_intake_max_per_hour=2)
    guard = SupportIntakeGuard(session, settings)
    for _ in range(2):
        await guard.record(door="contact", email="a@example.com", client_fingerprint="one")

    refused = await guard.check(email="a@example.com", client_fingerprint="one")
    assert refused.reason == "per_email"


# --------------------------------------------------------------------------- #
#  The window really is a window                                               #
# --------------------------------------------------------------------------- #
async def test_an_allowance_returns_once_the_window_has_passed(
    session: AsyncSession,
) -> None:
    guard = SupportIntakeGuard(session, _settings())
    old = datetime.now(UTC) - timedelta(hours=2)
    for _ in range(2):
        await guard.record(
            door="contact", email="a@example.com", client_fingerprint="one", now=old
        )

    assert (await guard.check(email="a@example.com", client_fingerprint="one")).allowed


async def test_the_wait_is_measured_rather_than_guessed(session: AsyncSession) -> None:
    """The person is told when they can send, from the oldest message still counted."""

    guard = SupportIntakeGuard(session, _settings())
    now = datetime.now(UTC)
    await guard.record(
        door="contact",
        email="a@example.com",
        client_fingerprint="one",
        now=now - timedelta(minutes=50),
    )
    await guard.record(
        door="contact",
        email="a@example.com",
        client_fingerprint="one",
        now=now - timedelta(minutes=5),
    )

    refused = await guard.check(email="a@example.com", client_fingerprint="one", now=now)
    # The first message leaves the hour in ten minutes, so that is the wait.
    assert 9 * 60 <= refused.retry_after_seconds <= 11 * 60
    assert "about 10 minutes" in refused.message()


async def test_a_refusal_always_names_a_next_step(session: AsyncSession) -> None:
    """No bare error. Every refusal says when, and what else to do."""

    guard = SupportIntakeGuard(session, _settings())
    for index in range(2):
        await guard.record(
            door="contact", email="a@example.com", client_fingerprint=f"c{index}"
        )
    refused = await guard.check(email="a@example.com", client_fingerprint="c-new")
    message = refused.message()
    assert "try again" in message or "send another" in message
    assert re.search(r"about \d+ minutes?", message)


async def test_an_accepted_message_reports_what_is_left(session: AsyncSession) -> None:
    guard = SupportIntakeGuard(session, _settings())
    first = await guard.check(email="a@example.com", client_fingerprint="one")
    assert first.allowed and first.remaining_for_email == 2

    await guard.record(door="contact", email="a@example.com", client_fingerprint="one")
    after = await guard.check(email="a@example.com", client_fingerprint="one")
    assert after.allowed and after.remaining_for_email == 1


async def test_an_unknown_door_is_refused_rather_than_recorded(
    session: AsyncSession,
) -> None:
    guard = SupportIntakeGuard(session, _settings())
    with pytest.raises(ValueError, match="Unknown support door"):
        await guard.record(
            door="telegram",  # type: ignore[arg-type]
            email="a@example.com",
            client_fingerprint="one",
        )


def test_the_ledger_stores_no_address() -> None:
    """The counter must not become a second copy of everybody's email address."""

    columns = set(SupportIntakeRecord.__table__.columns.keys())
    assert columns == {"id", "door", "email_hash", "client_hash", "accepted_at"}


def test_the_hash_cannot_be_reversed_without_the_application_secret() -> None:
    """Two deployments with different secrets produce different values for one address."""

    one = SupportIntakeGuard(None, _settings())  # type: ignore[arg-type]
    two = SupportIntakeGuard(
        None,  # type: ignore[arg-type]
        _settings(app_secret_key="another-secret-key-with-at-least-thirty-two-chars"),
    )
    assert one.email_hash("a@example.com") != two.email_hash("a@example.com")
    assert "a@example.com" not in one.email_hash("a@example.com")


# --------------------------------------------------------------------------- #
#  Both doors call the one owner                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "door_file",
    [
        "api/routers/public_forms.py",
        "api/routers/dashboard_api.py",
    ],
)
def test_every_door_asks_the_one_owner(door_file: str) -> None:
    """A door that counts for itself is a door that will drift from the other."""

    text = (SOURCE / door_file).read_text(encoding="utf-8")
    assert "SupportIntakeGuard" in text, door_file
    assert "guard.check(" in text, door_file
    assert "guard.record(" in text, door_file


@pytest.mark.parametrize(
    "door_file",
    [
        "api/routers/public_forms.py",
        "api/routers/dashboard_api.py",
    ],
)
def test_no_door_writes_its_own_limit(door_file: str) -> None:
    """The numbers belong to settings. A literal here is a second authority."""

    text = (SOURCE / door_file).read_text(encoding="utf-8")
    for invented in ("max_per_email", "MAX_PER_EMAIL = ", "per_hour = 20"):
        assert invented not in text, f"{door_file} holds its own limit: {invented}"


@pytest.mark.parametrize(
    "door_file",
    [
        "api/routers/public_forms.py",
        "api/routers/dashboard_api.py",
    ],
)
def test_every_door_identifies_the_caller_the_same_way(door_file: str) -> None:
    """One definition of "this browser session or address", shared by every guard."""

    text = (SOURCE / door_file).read_text(encoding="utf-8")
    assert "from ai_market_monitor.api.request_guards import client_fingerprint" in text, door_file


@pytest.mark.parametrize(
    "page",
    # One help page now. The older one at this address was deleted when the redesigned
    # page took over `/dashboard/support`; there is nothing left for a second entry here.
    ["templates/hilal/dashboard_test/support.html"],
)
def test_every_form_states_the_limit_before_somebody_meets_it(page: str) -> None:
    """A limit discovered by being refused is a limit nobody was told about.

    The number is read from the enforcing module rather than typed into the page, so a
    change in the environment moves both together. A hard-coded "2" here would go on
    promising two after the operator set it to five.
    """

    text = (SOURCE / page).read_text(encoding="utf-8")
    assert "support_limits.per_email" in text, page
    assert "reply to our email" in text, page


@pytest.mark.parametrize(
    "route_file,handler",
    # One help page, one router. The older `support_page` in `dashboard.py` was deleted
    # when the redesigned page took over `/dashboard/support`.
    [("api/routers/dashboard_test.py", "hilal/dashboard_test/support.html")],
)
def test_every_support_page_reads_the_limit_from_the_one_owner(
    route_file: str, handler: str
) -> None:
    text = (SOURCE / route_file).read_text(encoding="utf-8")
    assert "support_intake_limits" in text, route_file
    assert "support_limits=support_intake_limits(settings)" in text, route_file
    assert handler in text, route_file


def test_the_public_page_is_told_the_limit_by_the_server() -> None:
    """The contact page shows a number it was given, never one it decided."""

    router = (SOURCE / "api/routers/public_forms.py").read_text(encoding="utf-8")
    page = (
        ROOT / "Hilal-Markets-Website" / "src" / "pages" / "ContactPage.tsx"
    ).read_text(encoding="utf-8")
    forms = (
        ROOT / "Hilal-Markets-Website" / "src" / "publicForms.ts"
    ).read_text(encoding="utf-8")

    assert '"contact_limits"' in router
    assert "contact_limits" in forms
    assert "contactLimits()" in page
    # No number of its own anywhere on the page.
    assert "per_email: 2" not in page
    assert "return config.contact_limits ?? null" in forms
