"""The one door an AI turn goes through before it is allowed to spend money.

Three separate questions used to be asked in three separate places, in whatever order the
code happened to run: is the assistant switched on for this person, is there budget left,
and what did the call actually cost. This module asks them in one order, once, and records
the answer:

1. **Is the feature offered?** :mod:`feature_control` decides, and only for the *AI*.
   Nothing here can switch off the Builder — a person who cannot author anything has no
   product left, so authoring is not something a flag is allowed to take away.
2. **May this turn spend?** :mod:`ai_budget` reserves against locked counter rows. An
   unknown price is a refusal, not a zero.
3. **What did it really cost?** The reservation is settled with the provider's own usage,
   and the ledger row is written pointing back at the reservation, so the two records of
   the same money can be checked against each other.

**Every refusal here is a degraded product, never a broken one.** The caller catches
:class:`AISpendRefused`, tells the person in plain words, and the deterministic Builder,
the compiler, screening and every existing gate carry on untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.ai_budget import (
    AIBudgetService,
    BudgetError,
    BudgetLimits,
    Reservation,
    limits_from_settings,
)
from ai_market_monitor.services.feature_control import (
    Feature,
    FeatureControlService,
    rollout_config_from_settings,
)
from ai_market_monitor.services.reliability_metrics import METRICS

logger = structlog.get_logger(__name__)


class AISpendRefused(RuntimeError):
    """The assistant may not run this turn. The rest of the product still can.

    Carries a plain-language message written for a customer, never an operator: a global
    ceiling is never described as the person's own fault, and a personal one is never
    described as a platform outage.
    """

    def __init__(self, code: str, message: str, *, scope: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.scope = scope


@dataclass(frozen=True, slots=True)
class TurnSpend:
    """A granted permission to run one AI turn, and how it was decided."""

    reservation: Reservation | None
    model: str
    service_tier: str | None
    rollout_version: str
    #: Every feature decision that was in force, for the turn record.
    features: dict[str, Any]

    @property
    def reservation_id(self) -> UUID | None:
        return None if self.reservation is None else self.reservation.reservation_id


def estimate_turn_cost_usd(
    settings: Settings,
    *,
    model: str,
    service_tier: str | None,
    max_input_tokens: int,
    max_output_tokens: int,
) -> Decimal | None:
    """The pessimistic price of one turn, or ``None`` when the model has no price.

    ``None`` is deliberately not zero. A model whose price is not configured cannot be
    held against a ceiling, and letting it through is exactly how a bill arrives that
    nobody predicted.
    """

    pricing = (
        settings.openai_fast_model_pricing_usd_per_million.get(model)
        if service_tier in {"fast", "priority"}
        else settings.openai_model_pricing_usd_per_million.get(model)
    )
    if not pricing:
        return None
    total = (
        max_input_tokens * float(pricing.get("input", 0))
        + max_output_tokens * float(pricing.get("output", 0))
    ) / 1_000_000
    return Decimal(str(round(total, 8)))


class AISpendGuard:
    """Ask the three questions, in order, once."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        limits: BudgetLimits | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.limits = limits or limits_from_settings(settings)
        self.features = FeatureControlService(rollout_config_from_settings(settings))
        self.budget = AIBudgetService(session, self.limits)

    # -- feature -----------------------------------------------------------

    def decide(
        self,
        feature: Feature,
        *,
        user_id: UUID | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> bool:
        decision = self.features.decide(feature, user_id=user_id, cohorts=cohorts)
        METRICS.record_feature_decision(
            feature=str(feature),
            enabled=decision.enabled,
            reason=decision.reason,
            version=self.features.config.version,
        )
        return decision.enabled

    def snapshot(
        self,
        *,
        user_id: UUID | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        return self.features.snapshot(user_id=user_id, cohorts=cohorts)

    # -- reserve -----------------------------------------------------------

    async def open_turn(
        self,
        *,
        user_id: UUID | None,
        feature: Feature,
        idempotency_key: str,
        model: str,
        service_tier: str | None,
        max_input_tokens: int = 0,
        max_output_tokens: int = 0,
        estimated_cost_usd: Decimal | None = None,
        cohorts: frozenset[str] | None = None,
    ) -> TurnSpend:
        """Permission to run one AI turn, or :class:`AISpendRefused`.

        The feature question comes first because it is free. Reserving budget for a turn
        that a flag was going to refuse anyway would hold a customer's allowance open for
        nothing, and the refusal it produced would name the wrong reason.
        """

        version = self.features.config.version
        snapshot = self.snapshot(user_id=user_id, cohorts=cohorts)
        if not self.decide(feature, user_id=user_id, cohorts=cohorts):
            raise AISpendRefused(
                "AI_FEATURE_DISABLED",
                "The assistant is switched off right now. You can still build setups yourself.",
                scope="feature",
            )

        # A caller with its own per-turn ceiling passes it, because that is the number the
        # turn is really bounded to. The token estimate is the fallback for callers that
        # have no such ceiling. Either way an unpriced model produces ``None``, and
        # ``None`` is refused rather than treated as free.
        priced = (
            estimate_turn_cost_usd(
                self.settings,
                model=model,
                service_tier=service_tier,
                max_input_tokens=max(1, max_input_tokens),
                max_output_tokens=max(1, max_output_tokens),
            )
            is not None
        )
        estimate = (
            (estimated_cost_usd if priced else None)
            if estimated_cost_usd is not None
            else estimate_turn_cost_usd(
                self.settings,
                model=model,
                service_tier=service_tier,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
            )
        )
        try:
            reservation = await self.budget.reserve(
                user_id=user_id,
                idempotency_key=idempotency_key,
                feature=str(feature),
                model=model,
                estimated_cost_usd=estimate,
                service_tier=service_tier,
            )
        except BudgetError as error:
            METRICS.record_budget_refusal(scope=error.scope or "unknown", code=error.code)
            raise AISpendRefused(error.code, error.message, scope=error.scope) from error

        METRICS.record_reservation(event="replayed" if reservation.replayed else "reserved")
        return TurnSpend(
            reservation=reservation,
            model=model,
            service_tier=service_tier,
            rollout_version=version,
            features=snapshot,
        )

    # -- settle ------------------------------------------------------------

    async def settle_turn(
        self,
        spend: TurnSpend,
        *,
        actual_cost_usd: Decimal,
        input_tokens: int = 0,
        output_tokens: int = 0,
        provider_request_id: str | None = None,
        outcome: str = "completed",
    ) -> None:
        """Turn the promise into a fact.

        Called for a failed turn too, with whatever usage the provider did report. A call
        that failed after the tokens were generated still cost money, and a ledger that
        only records successes disagrees with the invoice every month.
        """

        if spend.reservation is None:
            return
        try:
            await self.budget.reconcile(
                spend.reservation.reservation_id,
                actual_cost_usd=actual_cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider_request_id=provider_request_id,
                outcome=outcome,
            )
        except BudgetError:
            # The turn already happened and its answer is already the customer's. A
            # bookkeeping failure must not become their error; the sweep returns the
            # reservation later.
            logger.warning("ai_budget_settle_failed", outcome=outcome)
            return
        METRICS.record_reservation(event="settled")
        METRICS.record_usage(
            feature=str(spend.reservation.idempotency_key.split(":", 1)[0]),
            model=spend.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(actual_cost_usd),
        )

    async def release_turn(self, spend: TurnSpend, *, outcome: str = "cancelled") -> None:
        """Give back a promise nothing was spent against."""

        if spend.reservation is None:
            return
        try:
            await self.budget.release(spend.reservation.reservation_id, outcome=outcome)
        except BudgetError:
            logger.warning("ai_budget_release_failed", outcome=outcome)
            return
        METRICS.record_reservation(event="released")
