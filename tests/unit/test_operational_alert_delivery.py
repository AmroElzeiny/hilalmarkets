"""Page delivery: at most once, never through the broken thing, nothing secret in it.

These assert the rules, not one alert. Where a property has to hold for every rule,
the test walks every rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.base import Base
from ai_market_monitor.db.models.operations import OperationalAlertDelivery
from ai_market_monitor.observability.alert_delivery import (
    OperationalAlertDispatcher,
    RouteUnavailable,
    delivery_idempotency_key,
    render_alert_message,
)
from ai_market_monitor.observability.alerts import (
    ALERT_RULES,
    DELIVERY_ROUTE_DEPENDENCIES,
    AlertRule,
    AlertRuleError,
    SLOBreachTrigger,
    validate_alert_rules,
)
from ai_market_monitor.observability.durable_metrics import DurableMetricsStore
from ai_market_monitor.observability.metrics import MetricsRecorder

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

PAGE_RULES = [rule for rule in ALERT_RULES if rule.severity == "page"]
TICKET_RULES = [rule for rule in ALERT_RULES if rule.severity == "ticket"]


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
    base = {
        "operational_alert_telegram_chat_id": "-100200300",
        "operational_alert_email": "ops@example.com",
        "operational_alert_repeat_minutes": 30,
    }
    base.update(overrides)
    return Settings(**base)


async def _break_the_api(session: AsyncSession, settings: Settings) -> None:
    """Record enough server errors that the availability objective is breached."""

    recorder = MetricsRecorder()
    for _ in range(40):
        recorder.record(
            "http_requests_total", route="/dashboard", method="GET", status_class="5xx"
        )
    await DurableMetricsStore(
        session, policy=settings.metric_retention_policy, writer="host:a:1"
    ).flush(recorder, now=NOW)


# ---------------------------------------------------------------------------
# 1. The rules themselves stay deliverable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", PAGE_RULES, ids=lambda rule: rule.name)
def test_every_page_names_two_routes_that_can_fail_independently(rule: AlertRule) -> None:
    assert rule.primary_route is not None
    assert rule.fallback_route is not None
    primary = DELIVERY_ROUTE_DEPENDENCIES[rule.primary_route]
    fallback = DELIVERY_ROUTE_DEPENDENCIES[rule.fallback_route]
    assert not (primary & fallback), (
        f"{rule.name}: both routes depend on {sorted(primary & fallback)}"
    )


@pytest.mark.parametrize("rule", PAGE_RULES, ids=lambda rule: rule.name)
def test_no_page_travels_through_the_service_it_watches(rule: AlertRule) -> None:
    assert rule.primary_route is not None and rule.fallback_route is not None
    for route in (rule.primary_route, rule.fallback_route):
        assert rule.watched_service not in DELIVERY_ROUTE_DEPENDENCIES[route], (
            f"{rule.name} would be delivered through {route}, which depends on "
            f"{rule.watched_service}"
        )


@pytest.mark.parametrize("rule", TICKET_RULES, ids=lambda rule: rule.name)
def test_a_ticket_names_no_route_because_it_is_not_delivered(rule: AlertRule) -> None:
    assert rule.primary_route is None
    assert rule.fallback_route is None
    assert rule.delivered is False


def test_the_route_check_is_not_a_no_op() -> None:
    """A rule that pages through what it watches must be refused."""

    broken = AlertRule(
        name="broken",
        trigger=SLOBreachTrigger("alert_delivery_success"),
        severity="page",
        what_broke="x",
        blast_radius="y",
        first_mitigation="z",
        runbook_anchor="#alert-delivery-failing",
        primary_route="ops_telegram",
        fallback_route="ops_email",
    )
    with pytest.raises(AlertRuleError, match="depends on it"):
        validate_alert_rules((broken,))


def test_a_page_with_no_fallback_is_refused() -> None:
    broken = AlertRule(
        name="broken",
        trigger=SLOBreachTrigger("api_availability"),
        severity="page",
        what_broke="x",
        blast_radius="y",
        first_mitigation="z",
        runbook_anchor="#api-availability",
        primary_route="ops_telegram",
    )
    with pytest.raises(AlertRuleError, match="no fallback"):
        validate_alert_rules((broken,))


def test_a_page_whose_two_routes_share_a_dependency_is_refused() -> None:
    broken = AlertRule(
        name="broken",
        trigger=SLOBreachTrigger("api_availability"),
        severity="page",
        what_broke="x",
        blast_radius="y",
        first_mitigation="z",
        runbook_anchor="#api-availability",
        primary_route="ops_email",
        fallback_route="ops_email",
    )
    with pytest.raises(AlertRuleError):
        validate_alert_rules((broken,))


def test_a_ticket_that_names_a_route_is_refused() -> None:
    """A route on a ticket reads as a promise the system does not keep."""

    broken = AlertRule(
        name="broken",
        trigger=SLOBreachTrigger("api_latency_p95"),
        severity="ticket",
        what_broke="x",
        blast_radius="y",
        first_mitigation="z",
        runbook_anchor="#api-latency",
        primary_route="ops_telegram",
    )
    with pytest.raises(AlertRuleError, match="not delivered"):
        validate_alert_rules((broken,))


# ---------------------------------------------------------------------------
# 2. At most one message per problem per window.
# ---------------------------------------------------------------------------


def test_the_same_problem_in_the_same_window_claims_the_same_key() -> None:
    first = delivery_idempotency_key(
        "api_unavailable", "alert:api_unavailable:api", moment=NOW, repeat_minutes=30
    )
    later = delivery_idempotency_key(
        "api_unavailable",
        "alert:api_unavailable:api",
        moment=NOW + timedelta(minutes=29),
        repeat_minutes=30,
    )
    assert first == later


def test_a_new_window_claims_a_new_key_so_a_long_outage_pages_again() -> None:
    first = delivery_idempotency_key(
        "api_unavailable", "alert:api_unavailable:api", moment=NOW, repeat_minutes=30
    )
    much_later = delivery_idempotency_key(
        "api_unavailable",
        "alert:api_unavailable:api",
        moment=NOW + timedelta(hours=2),
        repeat_minutes=30,
    )
    assert first != much_later


def test_two_different_problems_never_share_a_key() -> None:
    left = delivery_idempotency_key("a", "alert:a:api", moment=NOW, repeat_minutes=30)
    right = delivery_idempotency_key("b", "alert:b:api", moment=NOW, repeat_minutes=30)
    assert left != right


@pytest.mark.asyncio
async def test_a_firing_rule_evaluated_repeatedly_produces_exactly_one_page(
    session: AsyncSession,
) -> None:
    """The property that decides whether the channel stays readable."""

    settings = _settings()
    await _break_the_api(session, settings)
    dispatcher = OperationalAlertDispatcher(session, settings)

    first = await dispatcher.dispatch_due(now=NOW)
    assert first["paged"] >= 1

    for minute in (1, 2, 5, 29):
        again = await dispatcher.dispatch_due(now=NOW + timedelta(minutes=minute))
        assert again["paged"] == 0
        assert again["already_claimed"] >= 1

    rows = (
        await session.scalars(
            select(OperationalAlertDelivery).where(
                OperationalAlertDelivery.rule_name == "api_unavailable"
            )
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_same_outage_pages_again_after_the_repeat_window(
    session: AsyncSession,
) -> None:
    settings = _settings(operational_alert_repeat_minutes=30)
    await _break_the_api(session, settings)
    dispatcher = OperationalAlertDispatcher(session, settings)

    await dispatcher.dispatch_due(now=NOW)
    await dispatcher.dispatch_due(now=NOW + timedelta(minutes=45))

    rows = (
        await session.scalars(
            select(OperationalAlertDelivery).where(
                OperationalAlertDelivery.rule_name == "api_unavailable"
            )
        )
    ).all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_a_ticket_worthy_alert_is_recorded_and_never_delivered(
    session: AsyncSession,
) -> None:
    settings = _settings()
    recorder = MetricsRecorder()
    for _ in range(50):
        recorder.record("screening_refusals_total", refusal_reason="capability_unsupported")
    for _ in range(30):
        recorder.record("http_request_duration_ms", 60_000.0, route="/x", method="GET")
    await DurableMetricsStore(
        session, policy=settings.metric_retention_policy, writer="host:a:1"
    ).flush(recorder, now=NOW)

    result = await OperationalAlertDispatcher(session, settings).dispatch_due(now=NOW)
    assert result["ticketed"] >= 1

    delivered_names = {
        row.rule_name
        for row in (await session.scalars(select(OperationalAlertDelivery))).all()
    }
    for rule in TICKET_RULES:
        assert rule.name not in delivered_names


# ---------------------------------------------------------------------------
# 3. When the primary refuses, the fallback carries it, and the row says so.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unconfigured_primary_moves_the_page_to_the_fallback(
    session: AsyncSession,
) -> None:
    settings = _settings(operational_alert_telegram_chat_id=None)
    await _break_the_api(session, settings)
    dispatcher = OperationalAlertDispatcher(session, settings)
    await dispatcher.dispatch_due(now=NOW)

    sent: list[tuple[str, str]] = []

    async def _record_email(subject: str, body: str) -> None:
        sent.append((subject, body))

    dispatcher._send_email = _record_email  # type: ignore[method-assign]
    result = await dispatcher.process_due(now=NOW)
    assert result["fell_back"] >= 1

    result = await dispatcher.process_due(now=NOW + timedelta(seconds=1))
    assert result["sent"] >= 1
    assert sent

    row = await session.scalar(
        select(OperationalAlertDelivery).where(
            OperationalAlertDelivery.rule_name == "api_unavailable"
        )
    )
    assert row is not None
    assert row.used_fallback is True
    assert row.route == row.fallback_route
    assert row.status == "sent"
    assert "telegram_not_configured" not in (row.last_error or "")


@pytest.mark.asyncio
async def test_a_page_only_falls_back_once_and_then_stops_retrying(
    session: AsyncSession,
) -> None:
    settings = _settings(operational_alert_max_attempts=2)
    await _break_the_api(session, settings)
    dispatcher = OperationalAlertDispatcher(session, settings)
    await dispatcher.dispatch_due(now=NOW)

    async def _refuse(subject: str, body: str) -> None:
        raise RouteUnavailable("nope", "Not configured.", retryable=False)

    dispatcher._send_telegram = _refuse  # type: ignore[method-assign]
    dispatcher._send_email = _refuse  # type: ignore[method-assign]

    await dispatcher.process_due(now=NOW)
    await dispatcher.process_due(now=NOW + timedelta(seconds=1))

    row = await session.scalar(
        select(OperationalAlertDelivery).where(
            OperationalAlertDelivery.rule_name == "api_unavailable"
        )
    )
    assert row is not None
    assert row.used_fallback is True
    assert row.status == "failed"
    assert row.next_retry_at is None


# ---------------------------------------------------------------------------
# 4. Nothing secret, nothing customer-owned, nothing unbounded in the record.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule", list(ALERT_RULES), ids=lambda rule: rule.name)
def test_a_page_body_only_repeats_the_rule_s_own_sentences(rule: AlertRule) -> None:
    subject, body = render_alert_message(rule, 0.5)
    assert rule.what_broke in body
    assert rule.blast_radius in body
    assert rule.first_mitigation in body
    assert rule.runbook_anchor in body
    assert rule.name in subject
    assert len(body) <= 1200


@pytest.mark.parametrize(
    "forbidden",
    ["sk-", "Bearer ", "password", "@gmail.com", "seed phrase"],
)
@pytest.mark.parametrize("rule", list(ALERT_RULES), ids=lambda rule: rule.name)
def test_no_page_body_can_carry_a_credential_shape(rule: AlertRule, forbidden: str) -> None:
    _, body = render_alert_message(rule, None)
    assert forbidden not in body


@pytest.mark.asyncio
async def test_a_transport_error_is_stored_redacted_and_length_capped(
    session: AsyncSession,
) -> None:
    settings = _settings()
    await _break_the_api(session, settings)
    dispatcher = OperationalAlertDispatcher(session, settings)
    await dispatcher.dispatch_due(now=NOW)

    async def _explode(subject: str, body: str) -> None:
        raise RouteUnavailable("boom", "x" * 4000, retryable=True)

    dispatcher._send_telegram = _explode  # type: ignore[method-assign]
    dispatcher._send_email = _explode  # type: ignore[method-assign]
    await dispatcher.process_due(now=NOW)

    row = await session.scalar(
        select(OperationalAlertDelivery).where(
            OperationalAlertDelivery.rule_name == "api_unavailable"
        )
    )
    assert row is not None
    assert row.last_error is not None
    # Capped, not raised on. A length limit that throws while the system is
    # reporting a failure turns the report into a second failure.
    assert len(row.last_error) <= 240


@pytest.mark.asyncio
async def test_a_measured_reading_never_reaches_the_stored_payload_as_prose(
    session: AsyncSession,
) -> None:
    settings = _settings()
    await _break_the_api(session, settings)
    await OperationalAlertDispatcher(session, settings).dispatch_due(now=NOW)

    rows = (await session.scalars(select(OperationalAlertDelivery))).all()
    assert rows
    for row in rows:
        assert set(row.payload) == {
            "subject",
            "body",
            "runbook_anchor",
            "watched_service",
            "rules_version",
        }


# ---------------------------------------------------------------------------
# 5. Delivery never touches product state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_writes_only_its_own_two_tables(session: AsyncSession) -> None:
    settings = _settings()
    await _break_the_api(session, settings)
    await OperationalAlertDispatcher(session, settings).dispatch_due(now=NOW)

    from ai_market_monitor.db.models import Strategy, User

    assert await session.scalar(select(func.count()).select_from(User)) == 0
    assert await session.scalar(select(func.count()).select_from(Strategy)) == 0
    assert (
        await session.scalar(select(func.count()).select_from(OperationalAlertDelivery))
    ) >= 1
