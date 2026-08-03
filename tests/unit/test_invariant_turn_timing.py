"""Rules the turn clock and the provider circuit breaker must follow.

Two defects are protected here, both measured on the real path:

* an unreachable Redis took 2.7 seconds to say so, on *every* provider call, and the
  check sat outside every timed window — so a repaired turn spent 10.8 seconds on a
  diagnostic and no latency figure showed it;
* a stage could start a provider call that could not finish inside the turn, which
  produces a client timeout and a paid answer nobody reads.

Each test asserts the rule for the whole family, not the one case that was reported.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from redis.exceptions import RedisError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.turn_timing import (
    EXTERNAL_WAIT_STAGES,
    STAGES,
    TurnDeadline,
    TurnDeadlineExceeded,
    TurnTelemetry,
    estimated_tokens,
    null_telemetry,
)
from ai_market_monitor.services.setup_chat_agent import (
    _CIRCUIT_REDIS_COOLDOWN_SECONDS,
    _CIRCUIT_REDIS_TIMEOUT_SECONDS,
    SetupChatAgent,
)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_secret_key": "test-secret-key-with-at-least-thirty-two-characters",
        "app_env": "production",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The stage vocabulary is closed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", tuple(item for item in STAGES if item != "total_turn"))
def test_every_named_stage_can_be_timed(stage: str) -> None:
    telemetry = TurnTelemetry.start(10.0)
    with telemetry.stage(stage):
        pass
    assert stage in telemetry.stage_ms


def test_total_turn_is_derived_once_from_the_request_clock() -> None:
    telemetry = TurnTelemetry.start(10.0)
    with pytest.raises(ValueError, match="derived"), telemetry.stage("total_turn"):
        pass
    payload = telemetry.to_payload()
    assert payload["stage_counts"]["total_turn"] == 1
    assert payload["stage_ms"]["total_turn"] == payload["total_ms"]


def test_an_unknown_stage_is_refused_rather_than_recorded() -> None:
    """A second spelling of a stage would appear twice in the ranking and be optimised
    in one place while still being measured in the other."""

    telemetry = TurnTelemetry.start(10.0)
    # "planner_wait" is not the canonical name for planner_provider_wait.
    with pytest.raises(ValueError, match="unknown turn stage"), telemetry.stage("planner_wait"):
        pass


def test_external_wait_stages_are_all_real_stages() -> None:
    assert set(STAGES) >= EXTERNAL_WAIT_STAGES


def test_re_entering_a_stage_adds_to_it() -> None:
    """A provider call and its breaker check are both provider wait, not two turns."""

    telemetry = TurnTelemetry.start(10.0)
    # Well above the platform clock's granularity, so the accumulation is what is
    # measured rather than the resolution of `time.monotonic`.
    for _ in range(3):
        with telemetry.stage("planner_provider_wait"):
            time.sleep(0.03)
    assert telemetry.stage_counts["planner_provider_wait"] == 3
    # Three entries of 30 ms, not the last one alone.
    assert telemetry.stage_ms["planner_provider_wait"] >= 60


def test_stages_are_ranked_by_measured_cost() -> None:
    """The ranking is the order to optimise in, so it must follow the measurement."""

    telemetry = TurnTelemetry.start(10.0)
    with telemetry.stage("compilation"):
        time.sleep(0.06)
    with telemetry.stage("screening"):
        time.sleep(0.005)
    ranked = [name for name, _ in telemetry.ranked_stages()]
    assert ranked[0] == "compilation"


def test_the_token_estimate_matches_the_cost_guard() -> None:
    """One estimate, or the budget and the telemetry disagree about the same call."""

    for characters in (0, 1, 3, 4, 5, 4000, 21720):
        assert estimated_tokens(characters) == max(1, (characters + 3) // 4)


def test_null_telemetry_records_nothing_but_still_validates_stage_names() -> None:
    telemetry = null_telemetry()
    with telemetry.stage("persistence"):
        pass
    assert telemetry.stage_ms == {}
    with pytest.raises(ValueError), telemetry.stage("not_a_stage"):
        pass


# ---------------------------------------------------------------------------
# The deadline is asked before work starts, never after.
# ---------------------------------------------------------------------------


def test_a_stage_never_gets_more_time_than_the_turn_has_left() -> None:
    deadline = TurnDeadline(started_at=time.monotonic(), budget_seconds=5.0)
    assert deadline.timeout_for(60.0) <= deadline.remaining_seconds
    assert deadline.timeout_for(1.0) == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize("reserve", (0.0, 2.0, 6.0))
def test_reserved_time_is_kept_back_for_the_work_that_must_follow(reserve: float) -> None:
    """Persisting the result is not optional, so a provider call cannot spend its time."""

    deadline = TurnDeadline(started_at=time.monotonic(), budget_seconds=10.0)
    granted = deadline.timeout_for(60.0, reserve_seconds=reserve)
    assert granted <= max(0.0, deadline.remaining_seconds - reserve) + 0.05


def test_an_expired_turn_grants_no_time_at_all() -> None:
    deadline = TurnDeadline(started_at=time.monotonic() - 30.0, budget_seconds=5.0)
    assert deadline.expired
    assert deadline.remaining_seconds == 0.0
    assert deadline.timeout_for(60.0) == 0.0
    assert not deadline.allows(0.1)


def test_requiring_more_time_than_remains_names_the_stage() -> None:
    deadline = TurnDeadline(started_at=time.monotonic() - 9.9, budget_seconds=10.0)
    with pytest.raises(TurnDeadlineExceeded) as caught:
        deadline.require("planner_provider_wait", 5.0)
    assert caught.value.stage == "planner_provider_wait"


def test_a_turn_with_no_budget_never_expires() -> None:
    """Helper and test call sites must not inherit another turn's remaining time."""

    telemetry = null_telemetry()
    assert telemetry.deadline.budget_seconds == 0.0
    assert telemetry.deadline.timeout_for(30.0) == 0.0


# ---------------------------------------------------------------------------
# The breaker is a hint. It may never cost the turn more than the outage.
# ---------------------------------------------------------------------------


class _HangingRedis:
    """Stands in for a Redis that accepts the connection and never answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def _hang(self) -> None:
        self.calls += 1
        await asyncio.sleep(30)

    def eval(self, *args: object, **kwargs: object):  # noqa: ANN201
        return self._hang()

    def delete(self, *args: object, **kwargs: object):  # noqa: ANN201
        return self._hang()


class _FailingRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def _fail(self) -> None:
        self.calls += 1
        raise RedisError("down")

    def eval(self, *args: object, **kwargs: object):  # noqa: ANN201
        return self._fail()

    def delete(self, *args: object, **kwargs: object):  # noqa: ANN201
        return self._fail()


@pytest.mark.anyio
@pytest.mark.parametrize("client_factory", (_HangingRedis, _FailingRedis))
async def test_an_unreachable_breaker_is_bounded_and_permits_the_call(
    client_factory: type,
) -> None:
    """A breaker that cannot answer must not block, and must not refuse the call.

    Coordination is an optimisation here, never the authority: an unknown breaker
    permits the provider call exactly as a RedisError already did.
    """

    agent = SetupChatAgent(_settings())
    agent._circuit_redis = client_factory()  # type: ignore[assignment]  # noqa: SLF001
    began = time.monotonic()
    await agent._before_provider_call("m")  # noqa: SLF001
    elapsed = time.monotonic() - began
    assert elapsed < _CIRCUIT_REDIS_TIMEOUT_SECONDS + 0.5


@pytest.mark.anyio
async def test_one_turn_pays_the_breaker_timeout_once_not_once_per_call() -> None:
    """Four calls × one timeout each is how an unreachable cache became ten seconds."""

    agent = SetupChatAgent(_settings())
    client = _HangingRedis()
    agent._circuit_redis = client  # type: ignore[assignment]  # noqa: SLF001

    await agent._before_provider_call("m")  # noqa: SLF001
    assert client.calls == 1

    began = time.monotonic()
    for _ in range(6):
        await agent._before_provider_call("m")  # noqa: SLF001
        await agent._provider_succeeded("m")  # noqa: SLF001
    elapsed = time.monotonic() - began
    # Remembered as unavailable, so no further attempt is made at all.
    assert client.calls == 1
    assert elapsed < 0.2


@pytest.mark.anyio
async def test_the_breaker_starts_asking_again_after_the_cooldown() -> None:
    """A permanent memo would turn a brief outage into a permanently local breaker."""

    agent = SetupChatAgent(_settings())
    client = _HangingRedis()
    agent._circuit_redis = client  # type: ignore[assignment]  # noqa: SLF001
    await agent._before_provider_call("m")  # noqa: SLF001
    assert client.calls == 1
    assert _CIRCUIT_REDIS_COOLDOWN_SECONDS > 0
    agent._redis_unavailable_until = 0.0  # noqa: SLF001  # cooldown elapsed
    await agent._before_provider_call("m")  # noqa: SLF001
    assert client.calls == 2


@pytest.mark.anyio
async def test_recording_a_provider_result_never_waits_on_the_breaker() -> None:
    """The provider result is already authoritative; the marker must not delay it."""

    agent = SetupChatAgent(_settings())
    agent._circuit_redis = _HangingRedis()  # type: ignore[assignment]  # noqa: SLF001
    began = time.monotonic()
    await agent._provider_succeeded("m")  # noqa: SLF001
    assert time.monotonic() - began < _CIRCUIT_REDIS_TIMEOUT_SECONDS + 0.5
