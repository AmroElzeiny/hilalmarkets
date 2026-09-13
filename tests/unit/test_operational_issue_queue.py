"""The operational issue queue: dedupe, state machine, and what it refuses to hold."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ai_market_monitor.db.base import Base
from ai_market_monitor.observability.alerts import ALERT_RULES, FiredAlert
from ai_market_monitor.observability.issues import (
    _DEDUPE_KEY_PATTERN,
    _EVIDENCE_REF_PATTERN,
    IssueQueueError,
    OperationalIssueService,
    _sanitize_evidence_ref,
    dedupe_key_for_alert,
    sanitize_dedupe_key,
)
from ai_market_monitor.observability.labels import MAX_RECORD_VALUE_LENGTH, SensitiveValueError

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


async def test_the_queue_and_the_content_check_hold_one_length_limit(
    session: AsyncSession,
) -> None:
    """One limit for a summary and a reason, and it is the content check's own.

    The queue allowed 240 characters, then ran a content check that refuses anything
    over 200. A summary of 201 to 240 characters passed the first rule and failed the
    second, and ``record_fired_alert``, which cut an alert's text to 240, would have
    failed at the moment the alert fired — the alert becoming the failure.
    """

    service = OperationalIssueService(session)
    at_the_limit = ("Scan 7 is late. " * 13)[:MAX_RECORD_VALUE_LENGTH]
    assert len(at_the_limit) == MAX_RECORD_VALUE_LENGTH

    issue = await _record(service, summary=at_the_limit)
    assert issue.summary == at_the_limit
    with pytest.raises(IssueQueueError, match=f"at most {MAX_RECORD_VALUE_LENGTH} characters"):
        await _record(service, dedupe_key="alert:too_long:api", summary=at_the_limit + "x")

    await service.transition(
        issue_id=issue.id, to_state="acknowledged", actor="amroe", reason=at_the_limit
    )
    with pytest.raises(IssueQueueError, match="short sentence"):
        await service.transition(
            issue_id=issue.id, to_state="resolved", actor="amroe", reason=at_the_limit + "x"
        )

    long_rule = replace(ALERT_RULES[0], what_broke="The API is slow. " * 20)
    fired = await service.record_fired_alert(FiredAlert(rule=long_rule, measured=0.5))
    assert fired.summary == long_rule.what_broke[:MAX_RECORD_VALUE_LENGTH]


async def test_an_evidence_ref_with_provider_colons_is_sanitized_not_lost(
    session: AsyncSession,
) -> None:
    """A NOWPayments event id contains ``:``; the alert must still be written.

    The queue pattern forbids ``:`` in the tail of an evidence ref. Sanitizing at the
    queue boundary keeps the pointer intact and keeps the dedupe key unchanged, so one
    recurring failure still collapses into one issue row.
    """

    service = OperationalIssueService(session)
    raw_ref = "billing_event:nowpayments:90313222:finished"
    dedupe_key = "billing:plan-move-failed:nowpayments:90313222:finished"
    issue = await _record(
        service,
        dedupe_key=dedupe_key,
        evidence_refs=(raw_ref,),
    )
    assert issue.dedupe_key == dedupe_key
    assert issue.evidence_refs == ["billing_event:nowpayments-90313222-finished"]
    assert all(
        _EVIDENCE_REF_PATTERN.match(ref) for ref in issue.evidence_refs
    ), issue.evidence_refs


@pytest.mark.parametrize(
    "bad_ref",
    [
        "no-colon",
        "",
        "9digits:first",
        "prefix:",
    ],
)
async def test_a_malformed_evidence_ref_is_still_refused(
    session: AsyncSession, bad_ref: str
) -> None:
    """Sanitizing is not a free pass: refs that cannot be made safe keep raising."""

    service = OperationalIssueService(session)
    with pytest.raises(IssueQueueError, match="pointer"):
        await _record(service, evidence_refs=(bad_ref,))


#: Provider event ids, in the shapes the providers actually write them. Each pair is
#: the raw id and the exact key `record_occurrence` must end up storing for
#: `billing:plan-move-failed:{id}` — the billing alert this key belongs to.
PROVIDER_ID_SHAPES: list[tuple[str, str]] = [
    # NOWPayments: colons, already allowed after the first character — unchanged.
    (
        "nowpayments:90313222:finished",
        "billing:plan-move-failed:nowpayments:90313222:finished",
    ),
    # Stripe: mixed case — casefolded, nothing else touched.
    ("evt_01JABCdefGHI", "billing:plan-move-failed:evt_01jabcdefghi"),
    # A slash is allowed; the uppercase around it is not.
    ("sub/ABC-def_123", "billing:plan-move-failed:sub/abc-def_123"),
    # Longer than the queue can store — cut to the limit, lowercase intact.
    (
        "evt_" + "A" * 200,
        ("billing:plan-move-failed:evt_" + "a" * 200)[:160],
    ),
]


@pytest.mark.parametrize(("raw_id", "stored_key"), PROVIDER_ID_SHAPES)
async def test_a_provider_event_id_shape_never_loses_the_alert(
    session: AsyncSession, raw_id: str, stored_key: str
) -> None:
    """A dedupe key built from a provider id must survive the queue, whatever shape
    the provider gives the id.

    Stripe writes uppercase, NOWPayments writes colons, and some ids run past the 160
    characters the column holds. Rejecting those keys threw away the critical billing
    alert before its commit — the same loss the evidence-ref sanitizer exists to
    prevent, reached through the key instead. Normalising at the queue owner keeps the
    alert, keeps one row per problem, and keeps the lookup in
    ``_close_replacement_failure`` hitting the same row.
    """

    service = OperationalIssueService(session)
    raw_key = f"billing:plan-move-failed:{raw_id}"
    issue = await _record(
        service,
        dedupe_key=raw_key,
        category="billing",
        severity="critical",
        summary="A customer paid for a new plan, but the move could not be finished.",
        affected_scope="billing.plan_replacement",
    )
    assert issue.dedupe_key == stored_key
    assert _DEDUPE_KEY_PATTERN.match(issue.dedupe_key)
    again = await _record(
        service,
        dedupe_key=raw_key,
        category="billing",
        severity="critical",
        summary="A customer paid for a new plan, but the move could not be finished.",
        affected_scope="billing.plan_replacement",
    )
    assert again.id == issue.id
    assert issue.occurrence_count == 2
    summary = await service.summary()
    assert summary.open == 1


@pytest.mark.parametrize(
    "raw",
    [
        "billing:plan-move-failed:evt_01JABCdefGHI",
        "_evt_01ABC",
        ".hidden:key",
        "-leading-dash-9",
        "Ünicode-evt/1",
        "evt+plus/42",
        "ABC",
        "9lives",
        "k" * 240,
        "billing:subscription-resurrected:evt_XYZ",
    ],
)
async def test_sanitize_dedupe_key_always_lands_inside_the_pattern(raw: str) -> None:
    """Every input shape produces a key the queue accepts, and cleaning twice is the
    same as cleaning once — the write and the later read must agree."""

    safe = sanitize_dedupe_key(raw)
    assert _DEDUPE_KEY_PATTERN.match(safe), safe
    assert len(safe) <= 160
    assert sanitize_dedupe_key(safe) == safe


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "alert at 2026-08-12T09:00:00+00:00",
        "a key with spaces",
        "___",
        ":::",
        ".-_-.",
    ],
)
async def test_sanitize_dedupe_key_refuses_nothing_safe_remains(raw: str) -> None:
    """Sanitising is not a free pass. A key with whitespace is prose or a timestamp —
    the volatile keys the queue has always refused — and a key with no alphanumeric
    character to start from cannot be made safe."""

    with pytest.raises(IssueQueueError, match="stable low-cardinality key"):
        sanitize_dedupe_key(raw)


# ---------------------------------------------------------------------------
# An evidence ref is a pointer, never a payload (defect class D4)
# ---------------------------------------------------------------------------

#: The stored form of a ref is ``prefix:tail``, and the tail must look like a name —
#: not like a sentence, a JSON body, a stack line, an address or a URL. These are the
#: identifier shapes the real callers write, and the exact string each must produce.
#: A ``:`` inside a provider id is rewritten to ``-`` because the queue's own pattern
#: holds colons in reserve for the prefix separator.
POINTER_SHAPED_REFS: list[tuple[str, str]] = [
    # Stripe: mixed case and an underscore, stored as the provider wrote it.
    ("billing_event:evt_01JABCdefGHI", "billing_event:evt_01JABCdefGHI"),
    # NOWPayments: the id carries its own colons.
    (
        "billing_event:nowpayments:90313222:finished",
        "billing_event:nowpayments-90313222-finished",
    ),
    # Creem: mixed case with a slash inside the id.
    (
        "billing_event:creem/sub_2026-08-12_001",
        "billing_event:creem/sub_2026-08-12_001",
    ),
    # A money-owed row id: a UUID, which is a name, not a payload.
    (
        "plan_move_money_owed:6f1c2b3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
        "plan_move_money_owed:6f1c2b3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
    ),
    # The queue's own oldest pointer shapes.
    ("runbook:#api-availability", "runbook:#api-availability"),
    ("slo:api_availability", "slo:api_availability"),
    # Longer than the pattern can hold: cut to its allowance, never refused.
    ("billing_event:" + "X" * 300, "billing_event:" + "X" * 120),
    # The same over-length case behind a short prefix, where the total budget would
    # otherwise let 130 characters through and the queue's own pattern would refuse them.
    ("slo:" + "b" * 300, "slo:" + "b" * 120),
]

#: Payloads — content, not names. The first is the reviewer's own probe. Each pair is
#: the ref and the words that must not survive into the stored row.
PAYLOAD_SHAPED_REFS: list[tuple[str, tuple[str, ...]]] = [
    (
        "billing_event:the provider returned a 500 body",
        ("provider", "returned", "body"),
    ),
    (
        'billing_event:{"code":500,"msg":"old_subscription_cancel_failed"}',
        ("code", "msg", "subscription"),
    ),
    (
        'billing_event:File "x.py", line 5, in process_event',
        ("File", "line", "process_event"),
    ),
    ("billing_event:support@example.com", ("support", "example")),
    (
        "billing_event:https://api.provider.test/cb?token=abc123def456",
        ("token", "api.provider.test"),
    ),
]

#: The hash the queue uses for a ref that is not an identifier: the first 16 characters
#: of the ref's SHA-256, lowercase. Named here so the test states the rule the owner
#: must implement, rather than accepting whatever the owner happens to produce.
_EVIDENCE_POINTER_HASH_LENGTH = 16
_HASHED_TAIL_PATTERN = re.compile(r"^[0-9a-f]{16}$")


def _expected_pointer(raw_ref: str) -> str:
    prefix = raw_ref.split(":", 1)[0]
    digest = hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:_EVIDENCE_POINTER_HASH_LENGTH]}"


@pytest.mark.parametrize(("raw_ref", "stored_ref"), POINTER_SHAPED_REFS)
async def test_a_pointer_shaped_ref_is_stored_as_the_pointer_it_names(
    session: AsyncSession, raw_ref: str, stored_ref: str
) -> None:
    """A real identifier must survive unchanged (apart from the ``:`` rewrite and the
    length the pattern allows), and the same id twice must still be one row."""

    service = OperationalIssueService(session)
    issue = await _record(
        service,
        dedupe_key="billing:plan-move-failed:evt_1",
        evidence_refs=(raw_ref,),
    )
    assert issue.evidence_refs == [stored_ref]
    assert _EVIDENCE_REF_PATTERN.match(stored_ref), stored_ref
    # Self-check: the stored tail is the name, not a digest of it. A sanitizer that
    # hashed everything would pass a "nothing is stored verbatim" test alone.
    assert not _HASHED_TAIL_PATTERN.match(stored_ref.split(":", 1)[1]), stored_ref
    assert _sanitize_evidence_ref(stored_ref) == stored_ref

    again = await _record(
        service, dedupe_key="billing:plan-move-failed:evt_1", evidence_refs=(raw_ref,)
    )
    assert again.id == issue.id
    assert issue.occurrence_count == 2
    assert again.evidence_refs == [stored_ref]
    summary = await service.summary()
    assert summary.open == 1


@pytest.mark.parametrize(("raw_ref", "forbidden"), PAYLOAD_SHAPED_REFS)
async def test_a_payload_shaped_ref_is_reduced_to_a_pointer_not_stored(
    session: AsyncSession, raw_ref: str, forbidden: tuple[str, ...]
) -> None:
    """Content never becomes a record, and never becomes a failure either.

    The reviewer stored ``billing_event:the-provider-returned-a-500-body`` by rewriting
    the forbidden characters to ``-``: the payload survived, only readable to someone
    who knew what to look for. A ref that is not an identifier shape is replaced by the
    prefix plus a short deterministic hash of it — a pointer that says *this same
    content again* without holding the content.
    """

    service = OperationalIssueService(session)
    issue = await _record(
        service,
        dedupe_key="billing:plan-move-failed:evt_2",
        evidence_refs=(raw_ref,),
    )
    stored = list(issue.evidence_refs)
    assert stored == [_expected_pointer(raw_ref)]
    assert _EVIDENCE_REF_PATTERN.match(stored[0]), stored
    assert not _HASHED_TAIL_PATTERN.match(raw_ref.split(":", 1)[1]), raw_ref
    joined = " ".join(stored)
    for word in forbidden:
        assert word not in joined, (raw_ref, stored, word)
    assert raw_ref not in joined

    # Read the row back from the database: what is stored, not what the caller handed
    # over, is the thing a reviewer eventually sees.
    rows = await service.list_issues(states=("open", "resolved", "suppressed"))
    assert [row.evidence_refs for row in rows] == [stored]

    again = await _record(
        service, dedupe_key="billing:plan-move-failed:evt_2", evidence_refs=(raw_ref,)
    )
    assert again.id == issue.id
    assert issue.occurrence_count == 2
    # The same payload points at the same ref; a different payload does not.
    assert again.evidence_refs == stored


async def test_payload_reduction_is_deterministic_and_tells_payloads_apart() -> None:
    """A pure-function statement of the reduce-to-hash rule.

    Same content twice must give one pointer, or the queue loses its dedupe property;
    two different bodies must give two pointers, or it merges problems that are not
    the same problem. Neither holds if the sanitizer stores the text, and neither
    holds if it throws the payload away without a trace.
    """

    prose = "billing_event:the provider returned a 500 body"
    other = "billing_event:the provider returned a 402 body"
    assert _sanitize_evidence_ref(prose) == _sanitize_evidence_ref(prose)
    assert _sanitize_evidence_ref(prose) != _sanitize_evidence_ref(other)
    assert _sanitize_evidence_ref(prose) == _expected_pointer(prose)
    # Cleaning a reduced pointer again changes nothing: the write and the read agree.
    assert _sanitize_evidence_ref(_sanitize_evidence_ref(prose)) == _sanitize_evidence_ref(prose)
    # A usable prefix never ends the write, not even one far longer than any the
    # product has: the pointer shrinks behind it instead of the alert disappearing.
    for huge_prefix in ("a" * 200, "billing_" * 30):
        for tail in ("a sentence about the failure", "evt_01JABCdefGHI_and_longer_still"):
            reduced = _sanitize_evidence_ref(f"{huge_prefix}:{tail}")
            assert _EVIDENCE_REF_PATTERN.match(reduced), reduced
            assert "sentence" not in reduced


async def test_the_pointer_route_is_not_applied_to_identifiers() -> None:
    """Self-check for the family above: no real identifier may be hashed.

    Without this, a sanitizer that hashed every tail would satisfy "the payload is not
    stored verbatim" while silently destroying every pointer a reviewer needs to trace
    a failure back to a provider event.
    """

    for raw_ref, stored_ref in POINTER_SHAPED_REFS:
        assert _sanitize_evidence_ref(raw_ref) == stored_ref, raw_ref
        tail = stored_ref.split(":", 1)[1]
        assert not _HASHED_TAIL_PATTERN.match(tail), raw_ref


@pytest.mark.parametrize(
    "raw_ref",
    [
        "billing_event:sk-abcdefghijklmnopqrstuvwxyz012345",
        "billing_event:eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123",
    ],
)
async def test_a_credential_inside_an_identifier_shaped_ref_is_reduced_not_refused(
    session: AsyncSession, raw_ref: str
) -> None:
    """A credential written where an identifier belongs is never stored — and never
    costs the alert.

    It used to be kept as a pointer and then refused by the content check, which threw
    away the whole issue: a critical billing alert lost because one pointer looked like a
    key. It now takes the digest route, like prose does. The secret is not stored, the
    issue is written, and the same ref always reduces to the same pointer, so a repeat
    still collapses into one row.
    """

    service = OperationalIssueService(session)
    expected = "billing_event:" + hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()[:16]

    issue = await _record(service, evidence_refs=(raw_ref,))
    again = await _record(service, evidence_refs=(raw_ref,))

    assert issue.evidence_refs == [expected]
    assert again.id == issue.id
    secret = raw_ref.split(":", 1)[1]
    assert all(secret not in ref for ref in issue.evidence_refs)
    assert _sanitize_evidence_ref(raw_ref) == expected

