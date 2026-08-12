"""The operational issue queue: dedupe, state machine, and what it refuses to hold."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.db.base import Base
from ai_market_monitor.observability.alerts import ALERT_RULES, FiredAlert
from ai_market_monitor.observability.issues import (
    IssueQueueError,
    OperationalIssueService,
    dedupe_key_for_alert,
)
from ai_market_monitor.observability.labels import SensitiveValueError

pytestmark = pytest.mark.asyncio


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


async def _record(service: OperationalIssueService, **overrides):
    payload = {
        "dedupe_key": "alert:api_unavailable:api",
        "category": "api",
        "severity": "page",
        "summary": "The API is returning server errors.",
        "affected_scope": "api",
        "evidence_refs": ("slo:api_availability", "runbook:#api-availability"),
        "runbook_anchor": "#api-availability",
    }
    payload.update(overrides)
    return await service.record_occurrence(**payload)


async def test_repeat_occurrences_collapse_into_one_row(session: AsyncSession) -> None:
    """The property the whole queue depends on.

    A provider failing four thousand times overnight must produce one row with a
    count, not four thousand rows nobody can read.
    """

    service = OperationalIssueService(session)
    first = await _record(service)
    for _ in range(9):
        await _record(service)
    assert first.occurrence_count == 10

    summary = await service.summary()
    assert summary.open == 1
    assert summary.needs_attention == 1


async def test_first_seen_is_kept_while_last_seen_moves(session: AsyncSession) -> None:
    """How long this has been happening is the answer the queue exists to give."""

    service = OperationalIssueService(session)
    start = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    issue = await _record(service, now=start)
    later = start + timedelta(hours=6)
    await _record(service, now=later)
    assert issue.first_seen_at == start
    assert issue.last_seen_at == later


async def test_a_different_problem_gets_its_own_row(session: AsyncSession) -> None:
    service = OperationalIssueService(session)
    await _record(service)
    await _record(service, dedupe_key="alert:scans_delayed:scanner", category="scanner",
                  affected_scope="scanner", summary="Scans are late.")
    summary = await service.summary()
    assert summary.open == 2


async def test_every_alert_rule_produces_a_stable_key(session: AsyncSession) -> None:
    """Parametrised over the real rules, so a new rule cannot invent a volatile key."""

    keys = set()
    for rule in ALERT_RULES:
        alert = FiredAlert(rule=rule, measured=0.0)
        key = dedupe_key_for_alert(alert)
        assert key == dedupe_key_for_alert(FiredAlert(rule=rule, measured=999.0))
        keys.add(key)
    assert len(keys) == len(ALERT_RULES)


async def test_recording_the_same_alert_twice_does_not_create_two_rows(
    session: AsyncSession,
) -> None:
    service = OperationalIssueService(session)
    alert = FiredAlert(rule=ALERT_RULES[0], measured=0.5)
    issue = await service.record_fired_alert(alert)
    await service.record_fired_alert(alert)
    assert issue.occurrence_count == 2


async def test_a_resolved_problem_that_returns_reopens_the_original_row(
    session: AsyncSession,
) -> None:
    """A fresh row would throw away the history of a recurring problem."""

    service = OperationalIssueService(session)
    issue = await _record(service)
    await service.transition(
        issue_id=issue.id, to_state="resolved", actor="amroe", reason="Rolled back."
    )
    assert issue.state == "resolved"

    await _record(service)
    assert issue.state == "open"
    assert issue.occurrence_count == 2
    events = await service.events(issue.id)
    assert [event.to_state for event in events] == ["open", "resolved", "open"]


async def test_an_expired_suppression_stops_suppressing(session: AsyncSession) -> None:
    """Otherwise a known problem stops being reported and then stops being known."""

    service = OperationalIssueService(session)
    start = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    issue = await _record(service, now=start)
    await service.transition(
        issue_id=issue.id,
        to_state="suppressed",
        actor="amroe",
        reason="Known provider maintenance.",
        suppressed_for=timedelta(hours=2),
        now=start,
    )
    assert issue.state == "suppressed"

    await _record(service, now=start + timedelta(minutes=30))
    assert issue.state == "suppressed"

    await _record(service, now=start + timedelta(hours=3))
    assert issue.state == "open"


async def test_a_suppression_without_an_end_is_refused(session: AsyncSession) -> None:
    service = OperationalIssueService(session)
    issue = await _record(service)
    with pytest.raises(IssueQueueError, match="when it ends"):
        await service.transition(
            issue_id=issue.id, to_state="suppressed", actor="amroe"
        )


@pytest.mark.parametrize(
    "start,target",
    [
        ("resolved", "mitigated"),
        ("resolved", "acknowledged"),
        ("resolved", "suppressed"),
    ],
)
async def test_an_illegal_state_move_fails_closed(
    session: AsyncSession, start: str, target: str
) -> None:
    service = OperationalIssueService(session)
    issue = await _record(service)
    await service.transition(issue_id=issue.id, to_state="resolved", actor="amroe")
    with pytest.raises(IssueQueueError, match="cannot move"):
        await service.transition(issue_id=issue.id, to_state=target, actor="amroe")


async def test_every_state_change_is_recorded_with_its_actor(
    session: AsyncSession,
) -> None:
    """"This was fixed" and "somebody closed it" must stay distinguishable."""

    service = OperationalIssueService(session)
    issue = await _record(service)
    await service.transition(
        issue_id=issue.id, to_state="acknowledged", actor="amroe", reason="Looking."
    )
    await service.transition(
        issue_id=issue.id, to_state="resolved", actor="amroe", reason="Provider fixed."
    )
    events = await service.events(issue.id)
    assert [event.actor for event in events] == ["system", "amroe", "amroe"]
    assert [event.to_state for event in events] == ["open", "acknowledged", "resolved"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("summary", "Provider rejected key sk-abcdefghijklmnopqrstuvwxyz012345"),
        ("summary", "Customer wrote: alert me when bitcoin drops five percent " * 4),
        ("affected_scope", "user@example.com"),
    ],
)
async def test_customer_content_and_secrets_never_enter_an_issue(
    session: AsyncSession, field: str, value: str
) -> None:
    service = OperationalIssueService(session)
    with pytest.raises((IssueQueueError, SensitiveValueError)):
        await _record(service, **{field: value})


async def test_evidence_is_a_pointer_never_a_payload(session: AsyncSession) -> None:
    service = OperationalIssueService(session)
    with pytest.raises(IssueQueueError, match="pointer"):
        await _record(service, evidence_refs=("the provider returned a 500 body",))


async def test_a_volatile_dedupe_key_is_refused(session: AsyncSession) -> None:
    """A key containing a timestamp is the same as having no key at all."""

    service = OperationalIssueService(session)
    with pytest.raises(IssueQueueError, match="stable low-cardinality key"):
        await _record(service, dedupe_key="alert at 2026-08-12T09:00:00+00:00")
