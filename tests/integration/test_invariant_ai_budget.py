"""A budget reservation cannot be spent twice, and a replay cannot be charged twice.

These run against a real database with real transactions, because the failure this module
exists to prevent is a race: ``SELECT SUM(cost)`` followed by an independent write lets
two workers both read "ВЈ9 of ВЈ10 spent", both decide there is room, and both spend it.
Neither statement is wrong on its own, so nothing but a concurrent test finds it.

The properties asserted here:

* actual spend + outstanding reservations can never exceed the enforced ceiling;
* two simultaneous requests cannot take the same remaining budget;
* replay and crash recovery cost nothing extra;
* a model with no configured price is refused, never guessed at.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ai_market_monitor.db.models import AIBudgetCounter, AIBudgetReservation, User
from ai_market_monitor.services.ai_budget import (
    AIBudgetService,
    BudgetError,
    BudgetLimits,
    day_window,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


def _limits(**overrides) -> BudgetLimits:
    base = {
        "per_turn_max_usd": Decimal("1.00"),
        "user_daily_usd": Decimal("1.00"),
        "user_monthly_usd": Decimal("100.00"),
        "global_daily_usd": Decimal("1000.00"),
        "global_monthly_usd": Decimal("10000.00"),
        "max_concurrent_reservations": 0,
    }
    base.update(overrides)
    return BudgetLimits(**base)


async def _user(test_context) -> User:
    async with test_context["session_factory"]() as session:
        user = User(display_name=f"Budget {uuid4().hex[:8]}")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _counter(session, scope: str, key: str, window: str) -> AIBudgetCounter | None:
    return await session.get(AIBudgetCounter, (scope, key, window))


# ---------------------------------------------------------------------------
# The ceiling holds
# ---------------------------------------------------------------------------


async def test_a_reservation_counts_against_the_ceiling_before_it_is_spent(
    test_context,
) -> None:
    """Counting only settled spend makes every in-flight call invisible to the next."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits(user_daily_usd=Decimal("1.00")))

        await service.reserve(
            user_id=user.id,
            idempotency_key=f"a{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.80"),
            now=NOW,
        )
        await session.commit()

        # Nothing has settled, but 0.80 is promised, so only 0.20 is left.
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"b{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.50"),
                now=NOW,
            )
        assert raised.value.code == "USER_DAILY_BUDGET_EXCEEDED"


async def test_spend_plus_outstanding_never_exceeds_the_enforced_budget(
    test_context,
) -> None:
    user = await _user(test_context)
    limit = Decimal("1.00")

    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits(user_daily_usd=limit))
        granted = 0
        for _ in range(10):
            try:
                await service.reserve(
                    user_id=user.id,
                    idempotency_key=f"k{uuid4().hex}",
                    feature="planner",
                    model="gpt-x",
                    estimated_cost_usd=Decimal("0.30"),
                    now=NOW,
                )
                granted += 1
            except BudgetError:
                break
        await session.commit()

        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert counter is not None
        total = Decimal(str(counter.spent_usd)) + Decimal(str(counter.reserved_usd))
        assert total <= limit
        assert granted == 3


async def test_two_simultaneous_requests_cannot_spend_the_same_remaining_budget(
    test_context,
) -> None:
    """The race the counter row exists to lose safely.

    Both callers run in their own session and their own transaction, exactly as two
    workers would. Only the amount the budget actually holds may be granted.
    """

    user = await _user(test_context)
    limit = Decimal("1.00")

    async def attempt(index: int) -> bool:
        async with test_context["session_factory"]() as session:
            service = AIBudgetService(session, _limits(user_daily_usd=limit))
            try:
                await service.reserve(
                    user_id=user.id,
                    idempotency_key=f"race-{index}-{uuid4().hex}",
                    feature="planner",
                    model="gpt-x",
                    estimated_cost_usd=Decimal("0.60"),
                    now=NOW,
                )
                await session.commit()
                return True
            except BudgetError:
                await session.rollback()
                return False
            except Exception:
                # The database refusing to let two writers interleave is a safe outcome:
                # nothing was spent. It counts as a loss, never as a grant.
                await session.rollback()
                return False

    results = await asyncio.gather(*(attempt(index) for index in range(4)))

    # At most one may win. Zero is also safe — the database refused to interleave the
    # writers — but two would mean the same money was promised twice.
    assert sum(1 for item in results if item) <= 1, results
    async with test_context["session_factory"]() as session:
        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert counter is not None
        total = Decimal(str(counter.spent_usd)) + Decimal(str(counter.reserved_usd))
        assert total <= limit


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


async def test_reconciling_replaces_the_estimate_with_what_was_really_spent(
    test_context,
) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"r{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.90"),
            now=NOW,
        )
        await service.reconcile(
            reservation.reservation_id,
            actual_cost_usd=Decimal("0.10"),
            input_tokens=100,
            output_tokens=20,
            provider_request_id="req-1",
            now=NOW,
        )
        await session.commit()

        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert counter is not None
        assert Decimal(str(counter.reserved_usd)) == Decimal("0")
        assert Decimal(str(counter.spent_usd)) == Decimal("0.10000000")
        assert int(counter.reserved_count) == 0

        # The unused 0.80 is available again.
        await service.reserve(
            user_id=user.id,
            idempotency_key=f"r2{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.85"),
            now=NOW,
        )


async def test_reconciling_twice_does_not_charge_twice(test_context) -> None:
    """Crash recovery re-runs the settle step. It must be a no-op the second time."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"d{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.50"),
            now=NOW,
        )
        for _ in range(3):
            await service.reconcile(
                reservation.reservation_id, actual_cost_usd=Decimal("0.20"), now=NOW
            )
        await session.commit()

        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert Decimal(str(counter.spent_usd)) == Decimal("0.20000000")


async def test_a_replayed_request_takes_no_second_reservation(test_context) -> None:
    user = await _user(test_context)
    key = f"replay-{uuid4().hex}"
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        first = await service.reserve(
            user_id=user.id,
            idempotency_key=key,
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.40"),
            now=NOW,
        )
        second = await service.reserve(
            user_id=user.id,
            idempotency_key=key,
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.40"),
            now=NOW,
        )
        await session.commit()

        assert second.replayed is True
        assert second.reservation_id == first.reservation_id
        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert Decimal(str(counter.reserved_usd)) == Decimal("0.40000000")
        assert int(counter.reserved_count) == 1


async def test_a_failed_provider_call_records_only_what_it_really_used(
    test_context,
) -> None:
    """Tokens burned before a failure still cost money. Pretending otherwise loses it."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"f{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.60"),
            now=NOW,
        )
        await service.reconcile(
            reservation.reservation_id,
            actual_cost_usd=Decimal("0.05"),
            outcome="provider_failed",
            now=NOW,
        )
        await session.commit()

        row = await session.get(AIBudgetReservation, reservation.reservation_id)
        assert row.outcome == "provider_failed"
        assert Decimal(str(row.actual_cost_usd)) == Decimal("0.05000000")


async def test_a_cancelled_call_returns_the_whole_reservation(test_context) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"c{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.90"),
            now=NOW,
        )
        await service.release(reservation.reservation_id, now=NOW)
        await session.commit()

        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert Decimal(str(counter.reserved_usd)) == Decimal("0")
        assert Decimal(str(counter.spent_usd)) == Decimal("0")


async def test_an_abandoned_reservation_is_swept_back(test_context) -> None:
    """A crashed worker must not hold capacity until somebody restarts everything."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        await service.reserve(
            user_id=user.id,
            idempotency_key=f"s{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.90"),
            now=NOW,
            ttl=timedelta(minutes=1),
        )
        await session.commit()

        swept = await service.sweep_expired(now=NOW + timedelta(minutes=5))
        await session.commit()

        assert swept == 1
        counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert Decimal(str(counter.reserved_usd)) == Decimal("0")


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


async def test_a_model_with_no_configured_price_is_refused(test_context) -> None:
    """Guessing a price is how a bill arrives that nobody predicted."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits())
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"u{uuid4().hex}",
                feature="planner",
                model="brand-new-model",
                estimated_cost_usd=None,
                now=NOW,
            )
        assert raised.value.code == "MODEL_PRICE_UNKNOWN"


async def test_a_single_turn_larger_than_the_turn_ceiling_is_refused(test_context) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits(per_turn_max_usd=Decimal("0.25")))
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"t{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.50"),
                now=NOW,
            )
        assert raised.value.code == "TURN_BUDGET_EXCEEDED"


async def test_the_global_ceiling_stops_a_call_the_user_could_afford(test_context) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(
            session,
            _limits(user_daily_usd=Decimal("100.00"), global_daily_usd=Decimal("0.50")),
        )
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"g{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.90"),
                now=NOW,
            )
        assert raised.value.code == "GLOBAL_DAILY_BUDGET_EXCEEDED"
        # And it is not described to the customer as their own fault: a platform ceiling
        # is the platform being busy, not this person having spent their allowance.
        lowered = raised.value.message.casefold()
        assert "your allowance" not in lowered
        assert "you have used" not in lowered
        # It also says what still works, so the answer is not a dead end.
        assert "build setups" in lowered


async def test_a_per_model_ceiling_is_enforced(test_context) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(
            session,
            _limits(
                user_daily_usd=Decimal("100.00"),
                model_daily_usd={"expensive": Decimal("0.10")},
            ),
        )
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"m{uuid4().hex}",
                feature="planner",
                model="expensive",
                estimated_cost_usd=Decimal("0.50"),
                now=NOW,
            )
        assert raised.value.code == "MODEL_DAILY_BUDGET_EXCEEDED"

        # A different model is unaffected by that ceiling.
        await service.reserve(
            user_id=user.id,
            idempotency_key=f"m2{uuid4().hex}",
            feature="planner",
            model="cheap",
            estimated_cost_usd=Decimal("0.50"),
            now=NOW,
        )


async def test_too_many_outstanding_reservations_are_refused(test_context) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(
            session,
            _limits(user_daily_usd=Decimal("100.00"), max_concurrent_reservations=2),
        )
        for _ in range(2):
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"n{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.10"),
                now=NOW,
            )
        with pytest.raises(BudgetError) as raised:
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"n{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.10"),
                now=NOW,
            )
        assert raised.value.code == "TOO_MANY_IN_FLIGHT"


async def test_no_ceiling_is_incremented_when_a_later_one_refuses(test_context) -> None:
    """Incrementing as we go leaves earlier windows holding budget for a refused call."""

    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(
            session,
            _limits(user_daily_usd=Decimal("100.00"), global_daily_usd=Decimal("0.10")),
        )
        with pytest.raises(BudgetError):
            await service.reserve(
                user_id=user.id,
                idempotency_key=f"p{uuid4().hex}",
                feature="planner",
                model="gpt-x",
                estimated_cost_usd=Decimal("0.90"),
                now=NOW,
            )
        await session.commit()

        user_counter = await _counter(session, "user_daily", str(user.id), day_window(NOW))
        assert user_counter is None or Decimal(str(user_counter.reserved_usd)) == Decimal("0")


# ---------------------------------------------------------------------------
# What a person may see
# ---------------------------------------------------------------------------


async def test_the_summary_shows_the_persons_own_allowance_and_no_secrets(
    test_context,
) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits(user_daily_usd=Decimal("2.00")))
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"v{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.50"),
            now=NOW,
        )
        await service.reconcile(
            reservation.reservation_id, actual_cost_usd=Decimal("0.25"), now=NOW
        )
        await session.commit()

        payload = (await service.summary_for(user.id, now=NOW)).to_dict()

        assert payload["allowance_usd"] == "2.0000"
        assert payload["used_usd"] == "0.2500"
        assert payload["reserved_usd"] == "0.0000"
        assert payload["remaining_usd"] == "1.7500"
        assert payload["ai_available"] is True
        assert payload["resets_at"]
        flat = str(payload).casefold()
        for forbidden in ("api_key", "secret", "bearer", "authorization", "openai_api"):
            assert forbidden not in flat, forbidden


async def test_an_exhausted_allowance_reports_unavailable_with_a_reason(
    test_context,
) -> None:
    user = await _user(test_context)
    async with test_context["session_factory"]() as session:
        service = AIBudgetService(session, _limits(user_daily_usd=Decimal("0.50")))
        reservation = await service.reserve(
            user_id=user.id,
            idempotency_key=f"w{uuid4().hex}",
            feature="planner",
            model="gpt-x",
            estimated_cost_usd=Decimal("0.50"),
            now=NOW,
        )
        await service.reconcile(
            reservation.reservation_id, actual_cost_usd=Decimal("0.50"), now=NOW
        )
        await session.commit()

        summary = await service.summary_for(user.id, now=NOW)
        assert summary.ai_available is False
        assert summary.unavailable_reason
        assert summary.remaining_usd == Decimal("0")
