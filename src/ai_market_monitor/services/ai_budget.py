"""One authority on whether an AI call may spend money, and how much it actually spent.

Every AI call in the product goes through ``reserve → execute → reconcile``. There is no
second way to check a budget, because two ways to check a budget is two answers to "is
there room", and the wrong one always wins under load.

**Why a counter row and not a SUM.** The obvious implementation is
``SELECT SUM(cost) WHERE user = ? AND day = ?`` followed by "if that is under the limit,
make the call". That is a read and then an independent write. Two workers both read
"£9 of £10 spent", both decide there is £1 of room, and both spend it. The database never
notices, because neither statement was wrong on its own.

Instead each budget window is **one row**, locked with ``SELECT ... FOR UPDATE`` inside
the same transaction that increments it. The second worker blocks on the lock and then
reads the number the first one wrote. On SQLite, which has no row locks, the same safety
comes from its single-writer transaction model — the second write waits for the first to
commit.

**Reserved money is real money.** A budget check counts ``spent + reserved``. Counting
only what has settled makes every call currently in flight invisible to the next one, so
a burst of simultaneous turns all pass a check that only one of them should have.

**Redis is never the authority.** It may cache and it may coordinate, but the number that
decides is the one in the locked row. A Redis outage therefore cannot create overspend;
at worst it removes a fast path. Where the database itself is unreachable the answer is
to refuse the call, because an unknown budget is not the same as an available one.

**Unknown pricing fails closed.** A model with no price cannot be estimated, so it cannot
be reserved against, so it is refused. Guessing a price is how a bill arrives that nobody
predicted.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.db.models import AIBudgetCounter, AIBudgetReservation

logger = structlog.get_logger(__name__)

#: How long a promise may sit unsettled before it is swept back. A worker that crashed
#: between reserving and reconciling must not hold capacity until the next restart.
DEFAULT_RESERVATION_TTL = timedelta(minutes=15)

#: Outcomes that mean no provider call was ever made, so the plan's message allowance was
#: not touched. Anything else counts as a message the customer used.
_UNUSED_OUTCOMES: frozenset[str] = frozenset({"cancelled", "expired", "refused", "released"})


class BudgetError(RuntimeError):
    """A refusal to spend, carrying which limit stopped it."""

    def __init__(self, code: str, message: str, *, scope: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.scope = scope


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Every ceiling, in one place. ``None`` means "this one is not enforced"."""

    per_turn_max_usd: Decimal = Decimal("1.00")
    user_daily_usd: Decimal | None = Decimal("5.00")
    user_monthly_usd: Decimal | None = Decimal("50.00")
    global_daily_usd: Decimal | None = Decimal("200.00")
    global_monthly_usd: Decimal | None = Decimal("2000.00")
    #: Per model, per day. Stops one expensive route quietly eating the whole budget.
    model_daily_usd: dict[str, Decimal] = field(default_factory=dict)
    #: Per feature, per person, per day. A named assistant that promises a person "this
    #: much a day" needs a window of its own; held against the shared ``user_daily``
    #: figure instead, whichever feature they used first would spend the other's
    #: allowance, and the promise would be true only for whoever went first.
    feature_user_daily_usd: dict[str, Decimal] = field(default_factory=dict)
    #: How many reservations one person may hold at once. Without it, a client that
    #: reserves and never settles can hold the whole allowance open.
    max_concurrent_reservations: int = 3
    #: What this person's plan buys them per day, in money. Overrides the shared
    #: ``user_daily_usd`` when the plan is more generous *and* when it is less: a plan is
    #: the promise the customer paid for, so it is the number that decides for them.
    plan_daily_usd: Decimal | None = None
    plan_monthly_usd: Decimal | None = None
    #: How many assistant messages the plan includes per day. Counted separately from
    #: money because that is what the customer was actually sold — "40 questions a day" is
    #: a promise a person can check, "$0.85 a day" is not. A cheap model must not silently
    #: multiply the number of messages the plan advertises.
    plan_daily_messages: int | None = None

    def for_plan(
        self,
        *,
        daily_usd: Decimal | None = None,
        monthly_usd: Decimal | None = None,
        daily_messages: int | None = None,
    ) -> BudgetLimits:
        """These limits, narrowed to one customer's plan.

        The plan value replaces the platform default rather than sitting beside it, so
        there is still exactly one number per window. Two ceilings for the same window
        would mean two answers to "how much is left", and the enforcement path and the
        dashboard would eventually pick different ones.
        """

        return BudgetLimits(
            per_turn_max_usd=self.per_turn_max_usd,
            user_daily_usd=daily_usd if daily_usd is not None else self.user_daily_usd,
            user_monthly_usd=(
                monthly_usd if monthly_usd is not None else self.user_monthly_usd
            ),
            global_daily_usd=self.global_daily_usd,
            global_monthly_usd=self.global_monthly_usd,
            model_daily_usd=dict(self.model_daily_usd),
            feature_user_daily_usd=dict(self.feature_user_daily_usd),
            max_concurrent_reservations=self.max_concurrent_reservations,
            plan_daily_usd=daily_usd,
            plan_monthly_usd=monthly_usd,
            plan_daily_messages=daily_messages,
        )

    def for_feature(self, feature: str, daily_usd: Decimal) -> BudgetLimits:
        """These limits, with one feature given its own daily allowance per person."""

        merged = dict(self.feature_user_daily_usd)
        merged[str(feature)] = daily_usd
        return replace(self, feature_user_daily_usd=merged)


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    """One counter row this reservation is held against.

    ``unit`` says what the row counts. Most windows count money; the plan's message
    allowance counts turns, one per reservation regardless of what the turn costs. Both
    go through the same locked row on purpose — a second counting mechanism for messages
    would be a second thing to get wrong under concurrency, and message quota is exactly
    where two simultaneous requests both spending the last one is easiest to hit.
    """

    scope: str
    scope_key: str
    window_key: str
    limit_usd: Decimal | None
    unit: str = "usd"

    def amount_for(self, estimated_cost_usd: Decimal) -> Decimal:
        return Decimal("1") if self.unit == "message" else estimated_cost_usd

    def as_dict(self) -> dict[str, str]:
        return {
            "scope": self.scope,
            "scope_key": self.scope_key,
            "window_key": self.window_key,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class Reservation:
    """A granted promise. Hold it until the call is done, then reconcile it."""

    reservation_id: UUID
    idempotency_key: str
    estimated_cost_usd: Decimal
    windows: tuple[BudgetWindow, ...]
    #: True when this key had already been reserved. A replay costs nothing extra and
    #: must not be counted twice.
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    """What one person may see about their own allowance."""

    allowance_usd: Decimal | None
    used_usd: Decimal
    reserved_usd: Decimal
    remaining_usd: Decimal | None
    window_key: str
    resets_at: str
    ai_available: bool
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowance_usd": _money(self.allowance_usd),
            "used_usd": _money(self.used_usd),
            "reserved_usd": _money(self.reserved_usd),
            "remaining_usd": _money(self.remaining_usd),
            "window": self.window_key,
            "resets_at": self.resets_at,
            "ai_available": self.ai_available,
            "unavailable_reason": self.unavailable_reason,
        }


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.4f}"


def limits_from_settings(settings: Any) -> BudgetLimits:
    """Read the ceilings from configuration.

    One reader, so the enforcement path and the dashboard summary can never disagree
    about what the allowance is — a summary that showed a different number from the one
    being enforced would make every refusal look like a bug.
    """

    def optional(value: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    return BudgetLimits(
        per_turn_max_usd=Decimal(str(settings.ai_budget_per_turn_max_usd)),
        user_daily_usd=optional(settings.ai_budget_user_daily_usd),
        user_monthly_usd=optional(settings.ai_budget_user_monthly_usd),
        global_daily_usd=optional(settings.ai_budget_global_daily_usd),
        global_monthly_usd=optional(settings.ai_budget_global_monthly_usd),
        max_concurrent_reservations=int(settings.ai_budget_max_concurrent_reservations),
    )


def day_window(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d")


def month_window(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m")


class AIBudgetService:
    """The one place an AI call is allowed to ask "may I spend this?"."""

    def __init__(self, session: AsyncSession, limits: BudgetLimits | None = None) -> None:
        self.session = session
        self.limits = limits or BudgetLimits()

    # -- windows ---------------------------------------------------------

    @staticmethod
    def feature_window_key(feature: str, user_id: UUID | str) -> str:
        """The scope key one feature's per-person daily window is stored under.

        One writer for this string. The reserve path and the summary path must name the
        identical row, and two places building it by hand is how a limit gets enforced
        against one counter and displayed from another.
        """

        return f"{feature}:{user_id}"

    def windows_for(
        self,
        *,
        user_id: UUID | None,
        model: str,
        now: datetime,
        feature: str | None = None,
    ) -> tuple[BudgetWindow, ...]:
        """Every ceiling this call is held against, in a fixed order.

        The order matters: the rows are locked in it, always. Locking the same rows in a
        different order in two code paths is how two transactions deadlock holding each
        other's first row.
        """

        day = day_window(now)
        month = month_window(now)
        windows: list[BudgetWindow] = [
            BudgetWindow("global_daily", "global", day, self.limits.global_daily_usd),
            BudgetWindow("global_monthly", "global", month, self.limits.global_monthly_usd),
        ]
        model_cap = self.limits.model_daily_usd.get(model)
        if model_cap is not None:
            windows.append(BudgetWindow("model_daily", model, day, model_cap))
        if user_id is not None:
            windows.append(
                BudgetWindow("user_daily", str(user_id), day, self.limits.user_daily_usd)
            )
            windows.append(
                BudgetWindow(
                    "user_monthly", str(user_id), month, self.limits.user_monthly_usd
                )
            )
            if self.limits.plan_daily_messages is not None:
                windows.append(
                    BudgetWindow(
                        "user_messages_daily",
                        str(user_id),
                        day,
                        Decimal(int(self.limits.plan_daily_messages)),
                        unit="message",
                    )
                )
            feature_cap = (
                self.limits.feature_user_daily_usd.get(str(feature)) if feature else None
            )
            if feature_cap is not None:
                windows.append(
                    BudgetWindow(
                        "user_feature_daily",
                        self.feature_window_key(str(feature), user_id),
                        day,
                        feature_cap,
                    )
                )
        return tuple(sorted(windows, key=lambda item: (item.scope, item.scope_key)))

    # -- reserve ---------------------------------------------------------

    async def reserve(
        self,
        *,
        user_id: UUID | None,
        idempotency_key: str,
        feature: str,
        model: str,
        estimated_cost_usd: Decimal | None,
        provider: str = "openai",
        service_tier: str | None = None,
        now: datetime | None = None,
        ttl: timedelta = DEFAULT_RESERVATION_TTL,
    ) -> Reservation:
        """Take budget for one turn, or refuse with the limit that stopped it.

        ``estimated_cost_usd`` of ``None`` means the model's price is unknown. That is a
        refusal, not a zero: a call whose cost cannot be predicted cannot be held against
        a ceiling, and letting it through is how an unbudgeted bill happens.
        """

        moment = now or datetime.now(UTC)

        if estimated_cost_usd is None:
            raise BudgetError(
                "MODEL_PRICE_UNKNOWN",
                f"No price is configured for {model}, so this cannot be charged safely.",
            )
        if estimated_cost_usd < 0:
            raise BudgetError("ESTIMATE_INVALID", "A negative cost cannot be reserved.")
        if estimated_cost_usd > self.limits.per_turn_max_usd:
            raise BudgetError(
                "TURN_BUDGET_EXCEEDED",
                "This request is larger than one turn is allowed to spend.",
                scope="per_turn",
            )

        existing = await self.session.scalar(
            select(AIBudgetReservation).where(
                AIBudgetReservation.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            # A replay. It already holds (or already spent) its budget; taking a second
            # reservation would charge the same work twice.
            return Reservation(
                reservation_id=existing.id,
                idempotency_key=idempotency_key,
                estimated_cost_usd=Decimal(str(existing.estimated_cost_usd)),
                windows=self._windows_from(existing.windows),
                replayed=True,
            )

        if user_id is not None and self.limits.max_concurrent_reservations > 0:
            outstanding = await self._outstanding_for(user_id, moment)
            if outstanding >= self.limits.max_concurrent_reservations:
                raise BudgetError(
                    "TOO_MANY_IN_FLIGHT",
                    "You already have several requests running. Wait for one to finish.",
                    scope="concurrency",
                )

        windows = self.windows_for(
            user_id=user_id, model=model, now=moment, feature=feature
        )
        counters = []
        for window in windows:
            counter = await self._locked_counter(window, moment)
            counters.append((window, counter))

        # Every ceiling is checked before any is incremented. Incrementing as we go would
        # leave the earlier windows holding budget for a call the later ones refused.
        for window, counter in counters:
            if window.limit_usd is None:
                continue
            committed = Decimal(str(counter.spent_usd)) + Decimal(str(counter.reserved_usd))
            if committed + window.amount_for(estimated_cost_usd) > window.limit_usd:
                raise BudgetError(
                    _refusal_code(window.scope),
                    _refusal_message(window.scope),
                    scope=window.scope,
                )

        for window, counter in counters:
            counter.reserved_usd = Decimal(str(counter.reserved_usd)) + window.amount_for(
                estimated_cost_usd
            )
            counter.reserved_count = int(counter.reserved_count) + 1
            counter.updated_at = moment

        reservation = AIBudgetReservation(
            user_id=user_id,
            idempotency_key=idempotency_key,
            feature=feature,
            provider=provider,
            model=model,
            service_tier=service_tier,
            estimated_cost_usd=estimated_cost_usd,
            state="reserved",
            windows={"windows": [item.as_dict() for item in windows]},
            created_at=moment,
            expires_at=moment + ttl,
        )
        try:
            # Same reasoning as the counter insert: a lost race on the idempotency key is
            # an expected outcome, and undoing it must not undo the counters this
            # reservation has already incremented.
            async with self.session.begin_nested():
                self.session.add(reservation)
                await self.session.flush()
        except IntegrityError:
            # Two workers raced on the same key. The other one won; this is a replay, so
            # the increments made above have to come back off.
            for window, counter in counters:
                counter.reserved_usd = max(
                    Decimal("0"),
                    Decimal(str(counter.reserved_usd)) - window.amount_for(estimated_cost_usd),
                )
                counter.reserved_count = max(0, int(counter.reserved_count) - 1)
            again = await self.session.scalar(
                select(AIBudgetReservation).where(
                    AIBudgetReservation.idempotency_key == idempotency_key
                )
            )
            if again is None:
                raise
            return Reservation(
                reservation_id=again.id,
                idempotency_key=idempotency_key,
                estimated_cost_usd=Decimal(str(again.estimated_cost_usd)),
                windows=self._windows_from(again.windows),
                replayed=True,
            )

        logger.info(
            "ai_budget_reserved",
            feature=feature,
            model=model,
            estimated_cost_usd=str(estimated_cost_usd),
            windows=[item.scope for item in windows],
        )
        return Reservation(
            reservation_id=reservation.id,
            idempotency_key=idempotency_key,
            estimated_cost_usd=estimated_cost_usd,
            windows=windows,
        )

    # -- reconcile / release ---------------------------------------------

    async def reconcile(
        self,
        reservation_id: UUID,
        *,
        actual_cost_usd: Decimal,
        input_tokens: int = 0,
        output_tokens: int = 0,
        provider_request_id: str | None = None,
        outcome: str = "completed",
        now: datetime | None = None,
    ) -> None:
        """Turn a promise into a fact: release the estimate, record what was really spent.

        Called for a failure too, with whatever usage the provider did report. A call that
        failed after the tokens were consumed still cost money, and pretending otherwise
        makes the ledger disagree with the invoice.
        """

        moment = now or datetime.now(UTC)
        reservation = await self.session.get(AIBudgetReservation, reservation_id)
        if reservation is None:
            raise BudgetError("RESERVATION_UNKNOWN", "That reservation no longer exists.")
        if reservation.state != "reserved":
            # Already settled. Reconciling twice would charge the same call twice, which
            # is exactly what crash recovery would otherwise do.
            return

        estimate = Decimal(str(reservation.estimated_cost_usd))
        spend = max(Decimal("0"), actual_cost_usd)
        # A turn that never reached the provider used none of the plan's messages, however
        # much had been promised for it. A turn that reached the provider and failed used
        # one, however little it cost — the customer asked their question and got no
        # answer, and charging them a message for that would be wrong twice over, so the
        # honest line is "did a call actually go out".
        message_used = outcome not in _UNUSED_OUTCOMES
        for window in self._windows_from(reservation.windows):
            counter = await self._locked_counter(window, moment)
            counter.reserved_usd = max(
                Decimal("0"), Decimal(str(counter.reserved_usd)) - window.amount_for(estimate)
            )
            counter.reserved_count = max(0, int(counter.reserved_count) - 1)
            if window.unit == "message":
                counter.spent_usd = Decimal(str(counter.spent_usd)) + (
                    Decimal("1") if message_used else Decimal("0")
                )
            else:
                counter.spent_usd = Decimal(str(counter.spent_usd)) + spend
            counter.updated_at = moment

        reservation.state = "settled"
        reservation.actual_cost_usd = spend
        reservation.input_tokens = int(input_tokens)
        reservation.output_tokens = int(output_tokens)
        reservation.provider_request_id = provider_request_id
        reservation.outcome = outcome
        reservation.settled_at = moment
        await self.session.flush()

        logger.info(
            "ai_budget_reconciled",
            feature=reservation.feature,
            model=reservation.model,
            estimated_cost_usd=str(estimate),
            actual_cost_usd=str(spend),
            outcome=outcome,
        )

    async def release(
        self,
        reservation_id: UUID,
        *,
        outcome: str = "cancelled",
        now: datetime | None = None,
    ) -> None:
        """Give back an unused promise. Nothing was spent, so nothing is recorded as spent."""

        await self.reconcile(
            reservation_id,
            actual_cost_usd=Decimal("0"),
            outcome=outcome,
            now=now,
        )

    async def sweep_expired(self, *, now: datetime | None = None, limit: int = 200) -> int:
        """Return budget promised by workers that never came back.

        Without this a crashed worker's reservation holds capacity for ever, and the
        allowance shrinks a little with every crash until somebody restarts everything.
        """

        moment = now or datetime.now(UTC)
        stale = (
            (
                await self.session.execute(
                    select(AIBudgetReservation)
                    .where(
                        AIBudgetReservation.state == "reserved",
                        AIBudgetReservation.expires_at < moment,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for reservation in stale:
            await self.reconcile(
                reservation.id,
                actual_cost_usd=Decimal("0"),
                outcome="expired",
                now=moment,
            )
            reservation.state = "expired"
        if stale:
            logger.warning("ai_budget_swept_expired", count=len(stale))
        return len(stale)

    # -- reading ---------------------------------------------------------

    async def summary_for(
        self,
        user_id: UUID,
        *,
        now: datetime | None = None,
        ai_available: bool = True,
        unavailable_reason: str | None = None,
        feature: str | None = None,
    ) -> BudgetSummary:
        """One person's own allowance. Never another person's, and never a provider secret.

        ``feature`` reports the window that feature is really held against, so a named
        assistant with its own allowance shows the number that would actually refuse it.
        Reading the shared ``user_daily`` row instead would show a figure nothing
        enforces, and every refusal would look like a bug.
        """

        moment = now or datetime.now(UTC)
        feature_cap = (
            self.limits.feature_user_daily_usd.get(str(feature)) if feature else None
        )
        window = (
            BudgetWindow(
                "user_feature_daily",
                self.feature_window_key(str(feature), user_id),
                day_window(moment),
                feature_cap,
            )
            if feature_cap is not None
            else BudgetWindow(
                "user_daily", str(user_id), day_window(moment), self.limits.user_daily_usd
            )
        )
        counter = await self.session.get(
            AIBudgetCounter, (window.scope, window.scope_key, window.window_key)
        )
        used = Decimal(str(counter.spent_usd)) if counter else Decimal("0")
        reserved = Decimal(str(counter.reserved_usd)) if counter else Decimal("0")
        allowance = window.limit_usd
        remaining = None if allowance is None else max(Decimal("0"), allowance - used - reserved)
        tomorrow = (moment.astimezone(UTC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        exhausted = remaining is not None and remaining <= 0
        return BudgetSummary(
            allowance_usd=allowance,
            used_usd=used,
            reserved_usd=reserved,
            remaining_usd=remaining,
            window_key=window.window_key,
            resets_at=tomorrow.isoformat(),
            ai_available=ai_available and not exhausted,
            unavailable_reason=(
                unavailable_reason
                or ("You have used today's assistant allowance." if exhausted else None)
            ),
        )

    # -- internals -------------------------------------------------------

    async def _locked_counter(
        self, window: BudgetWindow, moment: datetime
    ) -> AIBudgetCounter:
        """The counter row for this window, locked for update, created if absent.

        The lock is the whole point. Reading without it and writing afterwards is the
        read-then-write race this module exists to prevent.
        """

        statement = select(AIBudgetCounter).where(
            AIBudgetCounter.scope == window.scope,
            AIBudgetCounter.scope_key == window.scope_key,
            AIBudgetCounter.window_key == window.window_key,
        )
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect != "sqlite":
            # SQLite has no row locks; its single-writer transaction gives the same
            # guarantee. Asking for FOR UPDATE there raises instead of protecting.
            statement = statement.with_for_update()
        counter = await self.session.scalar(statement)
        if counter is not None:
            return counter

        # The insert runs inside a savepoint. Two workers reaching a brand-new window at
        # the same time both try to create it and one loses on the unique constraint —
        # that is expected, not an error. Rolling back the *whole* transaction to recover
        # would discard every counter already taken for this reservation and leave the
        # session inconsistent; the savepoint undoes only the failed insert.
        try:
            async with self.session.begin_nested():
                counter = AIBudgetCounter(
                    scope=window.scope,
                    scope_key=window.scope_key,
                    window_key=window.window_key,
                    spent_usd=Decimal("0"),
                    reserved_usd=Decimal("0"),
                    reserved_count=0,
                    updated_at=moment,
                )
                self.session.add(counter)
                await self.session.flush()
            return counter
        except IntegrityError:
            found = await self.session.scalar(statement)
            if found is None:
                raise
            return found

    async def _outstanding_for(self, user_id: UUID, moment: datetime) -> int:
        rows = (
            (
                await self.session.execute(
                    select(AIBudgetReservation.id).where(
                        AIBudgetReservation.user_id == user_id,
                        AIBudgetReservation.state == "reserved",
                        AIBudgetReservation.expires_at >= moment,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)

    @staticmethod
    def _windows_from(payload: dict[str, Any]) -> tuple[BudgetWindow, ...]:
        """Rebuild the exact windows a reservation was taken against.

        Read back rather than recomputed, so releasing touches the same rows that took
        it. Recomputing would silently move the release to a different day's row when a
        turn spans midnight.
        """

        raw = (payload or {}).get("windows") or []
        return tuple(
            BudgetWindow(
                scope=str(item["scope"]),
                scope_key=str(item["scope_key"]),
                window_key=str(item["window_key"]),
                limit_usd=None,
                unit=str(item.get("unit") or "usd"),
            )
            for item in raw
        )


def _refusal_code(scope: str) -> str:
    return {
        "user_daily": "USER_DAILY_BUDGET_EXCEEDED",
        "user_monthly": "USER_MONTHLY_BUDGET_EXCEEDED",
        "global_daily": "GLOBAL_DAILY_BUDGET_EXCEEDED",
        "global_monthly": "GLOBAL_MONTHLY_BUDGET_EXCEEDED",
        "model_daily": "MODEL_DAILY_BUDGET_EXCEEDED",
        "user_messages_daily": "PLAN_MESSAGE_QUOTA_EXCEEDED",
        "user_feature_daily": "USER_DAILY_BUDGET_EXCEEDED",
    }.get(scope, "BUDGET_EXCEEDED")


def _refusal_message(scope: str) -> str:
    """Plain words. The reader is a customer, not an operator.

    A global ceiling is never described as the person's own fault, and a personal one is
    never described as a platform outage.
    """

    return {
        "user_daily": "You have used today's assistant allowance. It resets tomorrow.",
        "user_feature_daily": (
            "You have used today's assistant allowance. It resets at midnight UTC."
        ),
        "user_monthly": "You have used this month's assistant allowance.",
        "global_daily": "The assistant is busy today. You can still build setups yourself.",
        "global_monthly": "The assistant is paused for this month. Setups still work.",
        "model_daily": "The assistant is busy right now. You can still build setups yourself.",
        "user_messages_daily": (
            "You have used all of today's assistant messages on your plan. "
            "You can still build setups yourself, and more messages arrive tomorrow."
        ),
    }.get(scope, "The assistant is not available right now.")
