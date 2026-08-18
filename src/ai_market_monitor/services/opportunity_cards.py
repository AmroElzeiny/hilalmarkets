from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.dashboard_paths import LIFECYCLES_PATH
from ai_market_monitor.core.site_content import SHARIA_STATUS_PRESENTATION
from ai_market_monitor.db.models import (
    CandidateReadinessSnapshot,
    ExchangeMarket,
    NearMissSnapshot,
    PublishedAssetAssessment,
    SetupConditionResult,
    SetupInstance,
    StrategyCondition,
)
from ai_market_monitor.db.models.enums import ConditionOutcome, ShariaAssetStatus
from ai_market_monitor.schemas.sharia import AssetAssessmentSummary
from ai_market_monitor.services.product_language import number_in_words
from ai_market_monitor.services.sharia_screening import canonical_symbol


class OpportunityCardReadService:
    """Build customer opportunity cards only from retained deterministic evidence."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def for_setup(
        self,
        *,
        setup: SetupInstance,
        assessment: AssetAssessmentSummary,
        strategy_name: str,
    ) -> dict[str, Any]:
        evidence = await self._condition_evidence(setup)
        trend = await self._readiness_direction(setup)
        market = await self._exact_active_market(setup, assessment)
        status = SHARIA_STATUS_PRESENTATION[assessment.status.value]
        passed = [item for item in evidence if item[0].outcome == ConditionOutcome.PASSED]
        missing = [
            item
            for item in evidence
            if item[0].outcome
            in {
                ConditionOutcome.FAILED,
                ConditionOutcome.PENDING,
                ConditionOutcome.UNAVAILABLE,
                ConditionOutcome.ERROR,
            }
            and item[1].is_required
        ]
        blocker = await self._primary_missing_condition(setup, missing)
        freshness_values = [
            item[0].data_freshness_ms
            for item in evidence
            if item[0].data_freshness_ms is not None
        ]
        freshness_ms = max(freshness_values, default=None)
        freshness_missing = len(evidence) - len(freshness_values)
        return {
            "asset": assessment.canonical_asset,
            "symbol": setup.symbol,
            "name": assessment.asset_name or assessment.canonical_asset,
            "exchange": setup.exchange,
            "timeframe": setup.timeframe,
            "status_label": status["label"],
            "status_badge": status["badge"],
            "methodology_name": assessment.methodology_name,
            "methodology_version": assessment.methodology_version,
            "methodology_id": assessment.methodology_id,
            "passport_version_id": setup.sharia_passport_version_id,
            "event_time": setup.last_evaluated_at,
            "reviewed_at": assessment.reviewed_at,
            "opportunity_type": strategy_name,
            "readiness": max(0, min(100, round(float(setup.completion_score)))),
            "direction": trend,
            "present_conditions": tuple(item[1].label for item in passed[:2]),
            "missing_requirement": self._missing_requirement(blocker) if blocker else None,
            "condition_evidence_available": bool(evidence),
            "data_freshness": self._freshness_label(
                freshness_ms=freshness_ms,
                missing_count=freshness_missing,
            ),
            "last_evaluated_at": setup.last_evaluated_at,
            "market_availability": (
                f"Available on {setup.exchange.title()} spot"
                if market is not None
                else "Exact active spot mapping unavailable"
            ),
            "mapping_state": "verified" if market is not None else "unavailable",
            "summary": assessment.summary,
            "qualifications": tuple(assessment.qualifications[:3]),
            "journey_href": LIFECYCLES_PATH,
            "can_create_watch_plan": (
                market is not None
                and assessment.status
                in {
                    ShariaAssetStatus.ELIGIBLE,
                    ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
                }
            ),
        }

    async def _condition_evidence(
        self,
        setup: SetupInstance,
    ) -> list[tuple[SetupConditionResult, StrategyCondition]]:
        scan_result_id = setup.latest_scan_result_id
        if scan_result_id is None:
            scan_result_id = await self.session.scalar(
                select(SetupConditionResult.scan_result_id)
                .where(SetupConditionResult.setup_instance_id == setup.id)
                .order_by(SetupConditionResult.evaluated_at.desc())
                .limit(1)
            )
        if scan_result_id is None:
            return []
        result = await self.session.execute(
            select(SetupConditionResult, StrategyCondition)
            .join(
                StrategyCondition,
                StrategyCondition.id == SetupConditionResult.strategy_condition_id,
            )
            .where(
                SetupConditionResult.setup_instance_id == setup.id,
                SetupConditionResult.scan_result_id == scan_result_id,
            )
            .order_by(StrategyCondition.sequence.asc())
        )
        return list(result.tuples().all())

    async def _readiness_direction(self, setup: SetupInstance) -> str:
        snapshot = await self.session.scalar(
            select(NearMissSnapshot)
            .where(NearMissSnapshot.setup_instance_id == setup.id)
            .order_by(NearMissSnapshot.captured_at.desc())
            .limit(1)
        )
        if snapshot is None or snapshot.previous_score is None:
            return "No prior score"
        movement = float(setup.completion_score) - float(snapshot.previous_score)
        if movement > 0.5:
            return "Getting closer"
        if movement < -0.5:
            return "Moving away"
        return "Stable"

    async def _primary_missing_condition(
        self,
        setup: SetupInstance,
        missing: list[tuple[SetupConditionResult, StrategyCondition]],
    ) -> tuple[SetupConditionResult, StrategyCondition] | None:
        if not missing:
            return None
        readiness = await self.session.scalar(
            select(CandidateReadinessSnapshot)
            .where(CandidateReadinessSnapshot.setup_instance_id == setup.id)
            .order_by(CandidateReadinessSnapshot.updated_at.desc())
            .limit(1)
        )
        if readiness is not None and readiness.blocker_key:
            recorded = next(
                (
                    item
                    for item in missing
                    if item[1].condition_key == readiness.blocker_key
                ),
                None,
            )
            if recorded is not None:
                return recorded
        severity = {
            ConditionOutcome.ERROR: 0,
            ConditionOutcome.UNAVAILABLE: 1,
            ConditionOutcome.FAILED: 2,
            ConditionOutcome.PENDING: 3,
        }
        return min(
            missing,
            key=lambda item: (severity.get(item[0].outcome, 4), item[1].sequence),
        )

    async def _exact_active_market(
        self,
        setup: SetupInstance,
        assessment: AssetAssessmentSummary,
    ) -> ExchangeMarket | None:
        publication = await self.session.scalar(
            select(PublishedAssetAssessment).where(
                PublishedAssetAssessment.asset_assessment_id == assessment.id,
                PublishedAssetAssessment.publication_state == "published",
                PublishedAssetAssessment.is_active.is_(True),
            )
        )
        if publication is None:
            return None
        return await self.session.scalar(
            select(ExchangeMarket).where(
                ExchangeMarket.canonical_asset_id == publication.canonical_asset_id,
                func.lower(ExchangeMarket.exchange) == setup.exchange.casefold(),
                ExchangeMarket.market_symbol == canonical_symbol(setup.symbol),
                ExchangeMarket.market_type == "spot",
                ExchangeMarket.is_active.is_(True),
            )
        )

    @classmethod
    def _missing_requirement(
        cls,
        item: tuple[SetupConditionResult, StrategyCondition],
    ) -> str:
        result, definition = item
        actual = cls._display_value(result.actual_value)
        required = cls._display_value(result.required_value)
        state = {
            ConditionOutcome.UNAVAILABLE: "Data unavailable for",
            ConditionOutcome.ERROR: "Evaluation error for",
            ConditionOutcome.PENDING: "Still waiting for",
        }.get(result.outcome, "Still missing")
        values = ""
        if actual is not None or required is not None:
            values = f" - Current {actual or 'unavailable'} - Required {required or 'not stored'}"
        return f"{state}: {definition.label}{values}"

    @staticmethod
    def _display_value(payload: dict[str, Any]) -> str | None:
        value = payload.get("value")
        if value is None:
            return None
        if isinstance(value, bool):
            return "Yes" if value else "No"
        # How a market number is written is decided in one place, so a threshold cannot
        # be shown to four decimals on one surface and to two on another.
        number = number_in_words(value)
        if number is not None:
            return number
        return str(value)[:80]

    @staticmethod
    def _freshness_label(*, freshness_ms: int | None, missing_count: int) -> str:
        if freshness_ms is None:
            return "Condition freshness unavailable"
        label = f"{freshness_ms / 1000:.1f}s at evaluation"
        if missing_count:
            suffix = "condition" if missing_count == 1 else "conditions"
            return f"Partial freshness: {label}; {missing_count} {suffix} unavailable"
        return label
