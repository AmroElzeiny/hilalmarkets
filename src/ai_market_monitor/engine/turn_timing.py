"""One clock for a Setup Chat turn: what it spent, and how long it has left.

Timing and the deadline live in the same module on purpose. They are two readings of
one monotonic clock, and two clocks would eventually disagree about whether a stage had
time to start — the disagreement that turns a bounded turn into a client timeout.

Nothing here changes behaviour on its own. It measures, and it answers one question:
*is there enough time left to start this?* Optimising before measuring is how a slow
path acquires a larger timeout instead of less work.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

#: Every stage a turn can spend time in. One vocabulary, so a stage cannot be recorded
#: under two spellings and appear twice in the ranking, or be optimised in one place and
#: still measured in another.
STAGES: Final[tuple[str, ...]] = (
    "request_acceptance",
    "context_selection",
    "planner_schema_serialization",
    "planner_payload_serialization",
    "planner_provider_wait",
    "intent_deserialization",
    "intent_validation",
    "intent_normalization",
    "semantic_compilation",
    "canonical_operation_validation",
    "dry_validation",
    "repair_context_build",
    "repair_provider_wait",
    "repair_delta_application",
    "semantic_recompilation",
    "canonical_execution",
    "compilation",
    "screening",
    "provider_validation",
    "runtime_preflight",
    "response_composition",
    "persistence",
    "total_turn",
)

_STAGE_SET: Final[frozenset[str]] = frozenset(STAGES)

#: Stages that wait on something outside this process. Their sum is the floor under any
#: latency claim: no amount of server-side tidying removes provider or network time.
EXTERNAL_WAIT_STAGES: Final[frozenset[str]] = frozenset(
    {"planner_provider_wait", "repair_provider_wait", "response_composition"}
)


def estimated_tokens(characters: int) -> int:
    """Tokens a payload of this size costs, to the same rule the cost guard uses.

    Deliberately the same four-characters-per-token estimate as
    ``openai_structured_call.estimate_structured_call_cost``. A second estimate here
    would let the budget and the telemetry disagree about the same call.
    """

    return max(1, (characters + 3) // 4)


class TurnDeadlineExceeded(Exception):
    """There is not enough time left to start the work that was about to begin."""

    def __init__(self, stage: str, *, remaining_ms: float, needed_ms: float) -> None:
        super().__init__(f"{stage} needs {needed_ms:.0f} ms and {remaining_ms:.0f} ms remain")
        self.stage = stage
        self.remaining_ms = remaining_ms
        self.needed_ms = needed_ms


@dataclass(frozen=True, slots=True)
class TurnDeadline:
    """How much of the turn's budget is left, from one monotonic start.

    Created once at the authenticated request boundary and passed down. A stage asks
    before it starts, not after it has already waited: a call that cannot finish in the
    time left must never be started, because starting it is what produces a client
    timeout and a paid response nobody reads.
    """

    started_at: float
    budget_seconds: float

    @classmethod
    def start(cls, budget_seconds: float) -> TurnDeadline:
        return cls(started_at=time.monotonic(), budget_seconds=max(0.0, budget_seconds))

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.budget_seconds - self.elapsed_seconds)

    @property
    def expired(self) -> bool:
        return self.remaining_seconds <= 0.0

    def allows(self, needed_seconds: float) -> bool:
        """Is there room to start work that could take this long?"""
        return self.remaining_seconds >= max(0.0, needed_seconds)

    def require(self, stage: str, needed_seconds: float) -> None:
        if not self.allows(needed_seconds):
            raise TurnDeadlineExceeded(
                stage,
                remaining_ms=self.remaining_seconds * 1000,
                needed_ms=needed_seconds * 1000,
            )

    def timeout_for(self, requested_seconds: float, *, reserve_seconds: float = 0.0) -> float:
        """The shorter of what a stage asked for and what the turn can still afford.

        A stage never gets to extend the turn. Reserving time leaves room for the work
        that has to happen after it — persisting the result is not optional.
        """

        affordable = max(0.0, self.remaining_seconds - max(0.0, reserve_seconds))
        return min(max(0.0, requested_seconds), affordable)


@dataclass(slots=True)
class TurnTelemetry:
    """What one turn actually spent, by stage, payload section and model call.

    Every number here is measured, never estimated from a plan: a ranking built from
    what the code was expected to do cannot show which stage is actually slow.
    """

    deadline: TurnDeadline
    stage_ms: dict[str, float] = field(default_factory=dict)
    stage_counts: dict[str, int] = field(default_factory=dict)
    payload_chars: dict[str, int] = field(default_factory=dict)
    output_chars: dict[str, int] = field(default_factory=dict)
    model_calls: int = 0
    model_call_stages: list[str] = field(default_factory=list)
    provider_calls: int = 0
    db_queries: int = 0
    db_query_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    concurrent_wait_ms: float = 0.0
    concurrent_serial_ms: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, budget_seconds: float) -> TurnTelemetry:
        return cls(deadline=TurnDeadline.start(budget_seconds))

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time one stage. Re-entering the same stage adds to it rather than replacing."""

        if name not in _STAGE_SET:
            raise ValueError(f"unknown turn stage: {name}")
        if name == "total_turn":
            raise ValueError("total_turn is derived from the request clock")
        began = time.monotonic()
        try:
            yield
        finally:
            elapsed = (time.monotonic() - began) * 1000
            self.stage_ms[name] = self.stage_ms.get(name, 0.0) + elapsed
            self.stage_counts[name] = self.stage_counts.get(name, 0) + 1

    def record_payload(self, section: str, characters: int) -> None:
        self.payload_chars[section] = self.payload_chars.get(section, 0) + max(0, characters)

    def record_output(self, section: str, characters: int) -> None:
        self.output_chars[section] = self.output_chars.get(section, 0) + max(0, characters)

    def record_model_call(self, stage: str) -> None:
        self.model_calls += 1
        self.model_call_stages.append(stage)

    def record_provider_call(self) -> None:
        self.provider_calls += 1

    def record_db(self, queries: int, milliseconds: float) -> None:
        self.db_queries += max(0, queries)
        self.db_query_ms += max(0.0, milliseconds)

    def record_cache(self, *, hit: bool) -> None:
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_concurrency(self, *, wall_ms: float, serial_ms: float) -> None:
        """One concurrent group: how long it took, and how long it would have taken."""
        self.concurrent_wait_ms += max(0.0, wall_ms)
        self.concurrent_serial_ms += max(0.0, serial_ms)

    @property
    def total_ms(self) -> float:
        return self.deadline.elapsed_seconds * 1000

    @property
    def external_wait_ms(self) -> float:
        return sum(self.stage_ms.get(name, 0.0) for name in EXTERNAL_WAIT_STAGES)

    @property
    def server_ms(self) -> float:
        return max(0.0, self.total_ms - self.external_wait_ms)

    @property
    def concurrency_saved_ms(self) -> float:
        """Wait removed by running independent reads together, not by doing less."""
        return max(0.0, self.concurrent_serial_ms - self.concurrent_wait_ms)

    def ranked_stages(self) -> tuple[tuple[str, float], ...]:
        """Stages by measured contribution, slowest first. The order to optimise in."""
        return tuple(sorted(self.stage_ms.items(), key=lambda row: row[1], reverse=True))

    def payload_total_chars(self) -> int:
        return sum(self.payload_chars.values())

    def to_payload(self) -> dict[str, Any]:
        """The persisted record. Rounded, because sub-millisecond precision is noise."""

        stage_ms = {name: round(value, 1) for name, value in self.stage_ms.items()}
        stage_ms["total_turn"] = round(self.total_ms, 1)
        stage_counts = dict(self.stage_counts)
        stage_counts["total_turn"] = 1
        return {
            "total_ms": round(self.total_ms, 1),
            "server_ms": round(self.server_ms, 1),
            "external_wait_ms": round(self.external_wait_ms, 1),
            "stage_ms": stage_ms,
            "stage_counts": stage_counts,
            "ranked_stages": [
                {"stage": name, "ms": round(value, 1)} for name, value in self.ranked_stages()
            ],
            "payload_chars": dict(self.payload_chars),
            "payload_tokens": {
                name: estimated_tokens(value) for name, value in self.payload_chars.items()
            },
            "payload_total_chars": self.payload_total_chars(),
            "output_chars": dict(self.output_chars),
            "output_tokens": {
                name: estimated_tokens(value) for name, value in self.output_chars.items()
            },
            "model_calls": self.model_calls,
            "model_call_stages": list(self.model_call_stages),
            "provider_calls": self.provider_calls,
            "db_queries": self.db_queries,
            "db_query_ms": round(self.db_query_ms, 1),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "concurrent_wait_ms": round(self.concurrent_wait_ms, 1),
            "concurrent_serial_ms": round(self.concurrent_serial_ms, 1),
            "concurrency_saved_ms": round(self.concurrency_saved_ms, 1),
            "deadline_budget_ms": round(self.deadline.budget_seconds * 1000, 1),
            "deadline_remaining_ms": round(self.deadline.remaining_seconds * 1000, 1),
            "notes": dict(self.notes),
        }


#: A telemetry object that records nothing, for call sites with no turn in scope.
#: Handing back a real recorder there would attribute another turn's time to this one.
class _NullTelemetry(TurnTelemetry):
    __slots__ = ()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in _STAGE_SET:
            raise ValueError(f"unknown turn stage: {name}")
        if name == "total_turn":
            raise ValueError("total_turn is derived from the request clock")
        yield


def null_telemetry(budget_seconds: float = 0.0) -> TurnTelemetry:
    return _NullTelemetry(deadline=TurnDeadline.start(budget_seconds))


EMPTY_NOTES: Final[MappingProxyType[str, Any]] = MappingProxyType({})
