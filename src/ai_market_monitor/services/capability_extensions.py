from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    CapabilityExtension,
    CapabilityExtensionAttempt,
    CapabilityExtensionScan,
    Strategy,
    StrategyVersion,
    TelegramConnection,
)
from ai_market_monitor.db.models.enums import ConnectionStatus, LogicalOperator, TriggerMode
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.dynamic_mechanics import (
    DynamicMechanicValidationError,
    compile_dynamic_rule,
    evaluate_expression,
    expression_hash,
    required_history_candles,
    validate_expression,
    validate_expression_parameters,
)
from ai_market_monitor.schemas.capability_extensions import (
    MechanicCertificationResult,
    MechanicDraft,
    MechanicRepair,
    MechanicReview,
)
from ai_market_monitor.schemas.strategy import (
    AlertPolicy,
    ConditionGroup,
    ConditionRule,
    StrategyDefinition,
    StrategyDirection,
    UniverseDefinition,
)
from ai_market_monitor.services.capability_extension_ai import (
    CapabilityExtensionAI,
    CapabilityExtensionAIError,
)
from ai_market_monitor.services.interfaces import MarketDataProvider
from ai_market_monitor.services.strategy import StrategyService
from ai_market_monitor.telegram.adapter import TelegramDeliveryError, TelegramHttpAdapter
from ai_market_monitor.telegram.types import TelegramButton, TelegramOutboundMessage


class ExtensionAI(Protocol):
    last_usage: dict[str, Any]

    async def draft(self, **kwargs: Any) -> MechanicDraft: ...

    async def review(self, **kwargs: Any) -> MechanicReview: ...

    async def repair(self, **kwargs: Any) -> MechanicRepair: ...


class CapabilityExtensionService:
    def __init__(
        self,
        settings: Settings,
        *,
        ai: ExtensionAI | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai or CapabilityExtensionAI(settings)

    async def request(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        chat_session_id: UUID | None,
        source_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> CapabilityExtension:
        normalized = " ".join(source_prompt.casefold().split())
        request_fingerprint = hashlib.sha256(f"{user_id}:{normalized}".encode()).hexdigest()
        existing = await session.scalar(
            select(CapabilityExtension).where(
                CapabilityExtension.user_id == user_id,
                CapabilityExtension.request_fingerprint == request_fingerprint,
            )
        )
        if existing is not None:
            if chat_session_id is not None:
                existing.chat_session_id = chat_session_id
                if existing.certified_at is not None and existing.artifact_hash:
                    await self._install_chat_draft(
                        session,
                        existing,
                        MechanicDraft.model_validate(existing.manifest),
                    )
                else:
                    await self._status(
                        session,
                        existing,
                        "This mechanic is already in the certification queue. I will keep this "
                        "chat updated.",
                        stage=existing.stage,
                    )
            return existing
        key_slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:60]
        capability_key = f"custom_{key_slug or request_fingerprint[:16]}"
        collision = await session.scalar(
            select(CapabilityExtension.id).where(
                CapabilityExtension.capability_key == capability_key,
                CapabilityExtension.capability_version == "0.1.0",
            )
        )
        if collision is not None:
            capability_key = f"custom_{request_fingerprint[:24]}"
        extension = CapabilityExtension(
            user_id=user_id,
            chat_session_id=chat_session_id,
            request_fingerprint=request_fingerprint,
            capability_key=capability_key,
            capability_version="0.1.0",
            registry_hash=get_capability_index().registry_hash,
            source_prompt=source_prompt[:5000],
            conversation_history=conversation_history[-30:],
            status="queued",
            stage="requested",
            build_log=[_log("requested", "User approved creation of a missing mechanic.")],
        )
        session.add(extension)
        await session.flush()
        await self._status(
            session,
            extension,
            "Your mechanic isn't in TraceEdge yet, so I'm creating a safe, testable version.",
            stage="drafting",
        )
        await self._status(
            session,
            extension,
            "I'll test it against Bybit spot-market data. This can take a couple of minutes.",
            stage="queued",
            telegram=True,
        )
        return extension

    async def process(
        self, session: AsyncSession, extension: CapabilityExtension, provider: MarketDataProvider
    ) -> None:
        if extension.status not in {"queued", "repair_queued"}:
            return
        try:
            if extension.status == "repair_queued":
                await self._process_live_repair(session, extension, provider)
            else:
                await self._process_initial(session, extension, provider)
        except (CapabilityExtensionAIError, DynamicMechanicValidationError, ValueError) as exc:
            extension.status = "failed"
            extension.stage = "failed"
            extension.last_error = str(exc)[:2000]
            _append_log(extension, "failed", str(exc))
            await self._status(
                session,
                extension,
                "I couldn't certify this mechanic safely. The exact failure is available "
                "for review.",
                stage="failed",
                telegram=True,
            )

    async def _process_initial(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        provider: MarketDataProvider,
    ) -> None:
        extension.status = "building"
        extension.stage = "drafting"
        await self._status(
            session,
            extension,
            "I'm translating your idea into measurable mechanics now.",
            stage="drafting",
        )
        timeframe = _timeframe(extension.source_prompt)
        draft = await self._draft_attempt(session, extension, timeframe=timeframe)
        for pass_number in range(3):
            self._store_draft(extension, draft)
            extension.stage = "market_testing"
            await self._status(
                session,
                extension,
                "I'm testing the result against the market and checking for false or "
                "constant matches.",
                stage="market_testing",
            )
            try:
                market_report = await self._market_test(
                    session,
                    extension,
                    draft,
                    provider,
                    phase="preflight",
                    cycle_number=pass_number + 1,
                )
            except DynamicMechanicValidationError as exc:
                market_report = {
                    "classification": "invalid",
                    "deterministic": False,
                    "symbols_planned": 0,
                    "symbols_scanned": 0,
                    "current_candidates": [],
                    "current_candidate_count": 0,
                    "historical_observations": 0,
                    "historical_passes": 0,
                    "candidate_rate": 0,
                    "train_rate": 0,
                    "holdout_rate": 0,
                    "errors": [{"error": str(exc)}],
                }
                _append_log(extension, "validation_failed", str(exc))
            if pass_number == 0 and market_report["classification"] in {
                "too_strict",
                "too_permissive",
            }:
                review_model = self.settings.capability_extension_draft_model
                review_effort = "high"
                service_tier = self.settings.capability_extension_repair_service_tier
            elif pass_number > 0:
                review_model = self.settings.capability_extension_review_model
                review_effort = "medium"
                service_tier = self.settings.capability_extension_repair_service_tier
            else:
                review_model = self.settings.capability_extension_review_model
                review_effort = "low"
                service_tier = "default"
            review = await self._review_attempt(
                session,
                extension,
                draft,
                market_report,
                model=review_model,
                reasoning_effort=review_effort,
                service_tier=service_tier,
                operation=f"preflight_review_{pass_number + 1}",
            )
            extension.ai_review = review.model_dump(mode="json")
            extension.failure_classification = review.failure_source
            certification = self._certify(draft, market_report, review)
            extension.validation_report = certification.model_dump(mode="json")
            extension.validation_score = certification.score
            if certification.passed:
                await self._certify_for_user(session, extension, draft)
                return
            if review.failure_source != "implementation" or review.verdict != "repair":
                extension.status = "needs_user_clarification"
                extension.stage = "user_review"
                await self._status(
                    session,
                    extension,
                    (
                        "The mechanic is valid code, but your requested conditions may be too "
                        "strict or need one more definition. I won't silently relax them."
                    ),
                    stage="user_review",
                    telegram=True,
                )
                return
            if pass_number == 2:
                break
            await self._status(
                session,
                extension,
                "I found an implementation issue. I'm repairing it without changing your logic.",
                stage="repairing",
                telegram=True,
            )
            repair = await self._repair_attempt(session, extension, draft, review)
            if repair.user_logic_changed or not repair.changed_implementation_only:
                extension.status = "needs_user_clarification"
                extension.stage = "user_review"
                extension.validation_report = {
                    **extension.validation_report,
                    "deferred_changes": repair.deferred_changes,
                }
                return
            draft = repair.revised_draft
            extension.repair_generation += 1
        extension.status = "needs_user_clarification"
        extension.stage = "user_review"
        await self._status(
            session,
            extension,
            "The implementation checks passed, but this mechanic still finds no balanced "
            "evidence. Your conditions may be intentionally rare.",
            stage="user_review",
            telegram=True,
        )

    async def _process_live_repair(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        provider: MarketDataProvider,
    ) -> None:
        draft = MechanicDraft.model_validate(extension.manifest)
        reason = str(extension.validation_report.get("repair_reason") or "no_candidates")
        effort = "high" if reason == "no_notifications" else "low"
        market_report = dict(extension.validation_report.get("live_window") or {})
        await self._status(
            session,
            extension,
            "I'm reviewing five live scan cycles to separate a code problem from strict "
            "user logic.",
            stage="repair_review",
            telegram=True,
        )
        review = await self._review_attempt(
            session,
            extension,
            draft,
            market_report,
            model=self.settings.capability_extension_review_model,
            reasoning_effort=effort,
            service_tier=self.settings.capability_extension_repair_service_tier,
            operation=f"live_{reason}_review",
        )
        if review.failure_source != "implementation" or review.verdict != "repair":
            extension.status = "certified_user"
            extension.stage = "monitoring"
            extension.failure_classification = review.failure_source
            message = {
                "delivery": (
                    "The mechanic found market candidates, but no notification destination was "
                    "queued. Check the monitor schedule, cooldown, and connected channels."
                ),
                "market_data": (
                    "No mechanic error was found. Market-data availability interrupted the "
                    "review, so the current certified version remains active."
                ),
                "user_logic": (
                    "No implementation error was found. Your strategy may be intentionally "
                    "strict or the market has not met it yet."
                ),
            }.get(
                review.failure_source,
                "No implementation error was found. The current certified version remains active.",
            )
            await self._status(
                session,
                extension,
                message,
                stage="monitoring",
                telegram=True,
            )
            return
        repair = await self._repair_attempt(session, extension, draft, review)
        if repair.user_logic_changed or not repair.changed_implementation_only:
            extension.status = "certified_user"
            extension.stage = "monitoring"
            extension.validation_report = {
                **extension.validation_report,
                "deferred_changes": repair.deferred_changes,
            }
            return
        candidate = repair.revised_draft
        extension.repair_generation += 1
        candidate_report = await self._market_test(
            session,
            extension,
            candidate,
            provider,
            phase="live_repair_verification",
            cycle_number=extension.repair_generation,
        )
        verification = await self._review_attempt(
            session,
            extension,
            candidate,
            candidate_report,
            model=self.settings.capability_extension_review_model,
            reasoning_effort=effort,
            service_tier=self.settings.capability_extension_repair_service_tier,
            operation=f"live_{reason}_verification",
        )
        certification = self._certify(candidate, candidate_report, verification)
        if not certification.passed:
            extension.status = "certified_user"
            extension.stage = "monitoring"
            extension.validation_report = {
                **extension.validation_report,
                "rejected_revision": {
                    "certification": certification.model_dump(mode="json"),
                    "review": verification.model_dump(mode="json"),
                },
            }
            await self._status(
                session,
                extension,
                "The proposed implementation repair did not pass the second market test, so "
                "your approved monitor was left unchanged.",
                stage="monitoring",
                telegram=True,
            )
            return
        candidate_hash = expression_hash(
            candidate.expression, candidate.model_dump(mode="json", exclude={"expression"})
        )
        extension.validation_report = {
            **extension.validation_report,
            "pending_revision": {
                "draft": candidate.model_dump(mode="json"),
                "artifact_hash": candidate_hash,
                "review": review.model_dump(mode="json"),
                "verification": verification.model_dump(mode="json"),
                "certification": certification.model_dump(mode="json"),
                "requires_user_approval": True,
            },
        }
        extension.status = "repair_ready"
        extension.stage = "awaiting_user_approval"
        await self._status(
            session,
            extension,
            "I found and repaired an implementation issue. The new version is waiting for "
            "your approval; your current monitor remains unchanged.",
            stage="awaiting_user_approval",
            telegram=True,
        )

    async def record_live_scan(
        self,
        session: AsyncSession,
        *,
        strategy_version_id: UUID,
        scan_job_id: UUID | None,
        symbols_scanned: int,
        candidates_found: int,
        notifications_created: int,
    ) -> CapabilityExtension | None:
        extension = await session.scalar(
            select(CapabilityExtension).where(
                CapabilityExtension.strategy_version_id == strategy_version_id,
                CapabilityExtension.status.in_({"certified_user", "approved_global"}),
            )
        )
        if extension is None:
            return None
        extension.scan_count += 1
        extension.symbols_scanned_total += symbols_scanned
        extension.candidates_total += candidates_found
        extension.notifications_total += notifications_created
        extension.empty_scan_streak = 0 if candidates_found else extension.empty_scan_streak + 1
        extension.no_notification_streak = (
            0 if notifications_created else extension.no_notification_streak + 1
        )
        reason = None
        if extension.empty_scan_streak >= self.settings.capability_extension_empty_scan_threshold:
            reason = "no_candidates"
        elif (
            extension.no_notification_streak
            >= self.settings.capability_extension_no_notification_threshold
        ):
            reason = "no_notifications"
        classification = "balanced" if candidates_found else "no_current_candidates"
        session.add(
            CapabilityExtensionScan(
                extension_id=extension.id,
                scan_job_id=scan_job_id,
                phase="live",
                cycle_number=extension.scan_count,
                exchange="configured",
                timeframe=str(extension.manifest.get("timeframe") or "15m"),
                symbols_planned=symbols_scanned,
                symbols_scanned=symbols_scanned,
                candidates_found=candidates_found,
                notifications_created=notifications_created,
                candidate_rate=(candidates_found / symbols_scanned if symbols_scanned else 0),
                classification=classification,
                metrics={},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        if reason:
            extension.status = "repair_queued"
            extension.stage = "live_review_queued"
            extension.validation_report = {
                **(extension.validation_report or {}),
                "repair_reason": reason,
                "live_window": {
                    "scan_count": extension.scan_count,
                    "empty_scan_streak": extension.empty_scan_streak,
                    "no_notification_streak": extension.no_notification_streak,
                    "symbols_scanned_total": extension.symbols_scanned_total,
                    "candidates_total": extension.candidates_total,
                    "notifications_total": extension.notifications_total,
                },
            }
        await session.flush()
        return extension

    async def link_strategy_version(
        self,
        session: AsyncSession,
        *,
        artifact_hashes: set[str],
        strategy_version_id: UUID,
    ) -> int:
        if not artifact_hashes:
            return 0
        rows = list(
            (
                await session.scalars(
                    select(CapabilityExtension).where(
                        CapabilityExtension.artifact_hash.in_(artifact_hashes)
                    )
                )
            ).all()
        )
        for row in rows:
            row.strategy_version_id = strategy_version_id
            row.approved_at = datetime.now(UTC)
        await session.flush()
        return len(rows)

    async def materialize_pending_revision(
        self,
        session: AsyncSession,
        *,
        extension: CapabilityExtension,
        user_id: UUID,
    ) -> tuple[Strategy, StrategyVersion]:
        if extension.user_id != user_id:
            raise ValueError("Capability extension was not found")
        if extension.pending_strategy_version_id is not None:
            version = await session.get(StrategyVersion, extension.pending_strategy_version_id)
            strategy = await session.get(Strategy, version.strategy_id) if version else None
            if version is not None and strategy is not None:
                return strategy, version
        pending = dict((extension.validation_report or {}).get("pending_revision") or {})
        if extension.status != "repair_ready" or not pending.get("requires_user_approval"):
            raise ValueError("No certified repair is waiting for approval")
        certification = dict(pending.get("certification") or {})
        if not certification.get("passed"):
            raise ValueError("The pending repair has not passed certification")
        if extension.strategy_version_id is None:
            raise ValueError("The repaired mechanic is not linked to a monitor")
        current_version = await session.get(StrategyVersion, extension.strategy_version_id)
        strategy = (
            await session.get(Strategy, current_version.strategy_id)
            if current_version is not None
            else None
        )
        if current_version is None or strategy is None or strategy.user_id != user_id:
            raise ValueError("The linked monitor no longer exists")
        candidate = MechanicDraft.model_validate(pending.get("draft") or {})
        candidate_hash = str(pending.get("artifact_hash") or "")
        next_version = _next_capability_version(extension.capability_version)
        replacement = compile_dynamic_rule(
            capability_key=extension.capability_key,
            capability_version=next_version,
            artifact_hash=candidate_hash,
            label=candidate.label,
            timeframe=candidate.timeframe,
            expression=candidate.expression,
            resolved_parameters=candidate.resolved_parameters,
            proof_template=candidate.proof_template,
            source_fragment=extension.source_prompt,
        )
        current_definition = StrategyDefinition.model_validate(current_version.schema_json)
        conditions, replaced = _replace_dynamic_rule(
            current_definition.conditions,
            artifact_hash=str(extension.artifact_hash or ""),
            replacement=replacement,
        )
        if not replaced:
            raise ValueError("The active monitor no longer contains this mechanic")
        revised_definition = current_definition.model_copy(update={"conditions": conditions})
        version = await StrategyService(
            session,
            self.settings.disclaimer_version,
        ).create_system_revision(
            strategy,
            revised_definition,
            user_id=user_id,
            source_text=extension.source_prompt,
            reason=(
                "A deterministic implementation repair passed independent AI review and a "
                "second Bybit market test. User approval is still required."
            ),
        )
        extension.pending_strategy_version_id = version.id
        extension.validation_report = {
            **extension.validation_report,
            "pending_revision": {
                **pending,
                "capability_version": next_version,
                "strategy_version_id": str(version.id),
            },
        }
        await self._status(
            session,
            extension,
            "A reviewable monitor revision is ready. Your current monitor stays active until "
            "you approve and activate the new version.",
            stage="awaiting_user_approval",
            telegram=True,
        )
        return strategy, version

    async def _draft_attempt(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        *,
        timeframe: str,
    ) -> MechanicDraft:
        attempt = await self._attempt(
            session,
            extension,
            operation="initial_draft",
            model=self.settings.capability_extension_draft_model,
            effort=self.settings.capability_extension_draft_reasoning_effort,
            service_tier="default",
            input_payload={"prompt": extension.source_prompt, "timeframe": timeframe},
        )
        try:
            draft = await self.ai.draft(
                prompt=extension.source_prompt,
                history=extension.conversation_history,
                timeframe=timeframe,
                model=attempt.model,
                reasoning_effort=attempt.reasoning_effort,
                service_tier=attempt.service_tier,
            )
        except Exception as exc:
            self._fail_attempt(attempt, exc)
            raise
        self._complete_attempt(attempt, draft.model_dump(mode="json"))
        return draft

    async def _review_attempt(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        draft: MechanicDraft,
        market_report: dict[str, Any],
        *,
        model: str,
        reasoning_effort: str,
        service_tier: str,
        operation: str,
    ) -> MechanicReview:
        attempt = await self._attempt(
            session,
            extension,
            operation=operation,
            model=model,
            effort=reasoning_effort,
            service_tier=service_tier,
            input_payload={
                "code": draft.model_dump(mode="json"),
                "build_log": extension.build_log,
                "market_report": market_report,
                "conversation_history": extension.conversation_history,
            },
        )
        try:
            review = await self.ai.review(
                prompt=extension.source_prompt,
                history=extension.conversation_history,
                draft=draft,
                build_log=extension.build_log,
                market_report=market_report,
                model=model,
                reasoning_effort=reasoning_effort,
                service_tier=service_tier,
            )
        except Exception as exc:
            self._fail_attempt(attempt, exc)
            raise
        self._complete_attempt(attempt, review.model_dump(mode="json"))
        return review

    async def _repair_attempt(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        draft: MechanicDraft,
        review: MechanicReview,
    ) -> MechanicRepair:
        attempt = await self._attempt(
            session,
            extension,
            operation="implementation_repair",
            model=self.settings.capability_extension_implementation_model,
            effort="low",
            service_tier=self.settings.capability_extension_repair_service_tier,
            input_payload={
                "code": draft.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "build_log": extension.build_log,
            },
        )
        try:
            repair = await self.ai.repair(
                prompt=extension.source_prompt,
                history=extension.conversation_history,
                draft=draft,
                review=review,
                build_log=extension.build_log,
                reasoning_effort="low",
            )
        except Exception as exc:
            self._fail_attempt(attempt, exc)
            raise
        self._complete_attempt(attempt, repair.model_dump(mode="json"))
        return repair

    async def _market_test(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        draft: MechanicDraft,
        provider: MarketDataProvider,
        *,
        phase: str,
        cycle_number: int,
    ) -> dict[str, Any]:
        validation = validate_expression(
            draft.expression,
            max_nodes=self.settings.capability_extension_max_expression_nodes,
            max_depth=self.settings.capability_extension_max_expression_depth,
        )
        validate_expression_parameters(draft.expression, draft.resolved_parameters)
        exchange = self.settings.capability_extension_preflight_exchange
        requested_history = required_history_candles(
            draft.expression,
            draft.timeframe,
            minimum=max(
                self.settings.capability_extension_candle_limit,
                validation.warmup_candles,
            ),
        )
        history_limit = min(
            requested_history,
            self.settings.capability_extension_max_history_candles,
        )
        symbol_budget_limit = max(
            10,
            self.settings.capability_extension_market_test_candle_budget // history_limit,
        )
        symbols = await provider.list_symbols(exchange, ["USDT"])
        symbols = symbols[
            : min(
                self.settings.capability_extension_preflight_max_symbols,
                symbol_budget_limit,
            )
        ]
        semaphore = asyncio.Semaphore(self.settings.capability_extension_preflight_concurrency)

        async def inspect(symbol: str) -> dict[str, Any]:
            async with semaphore:
                try:
                    candles = await provider.fetch_ohlcv(
                        exchange,
                        symbol,
                        draft.timeframe,
                        history_limit,
                    )
                    if len(candles) < validation.warmup_candles:
                        return {"symbol": symbol, "error": "insufficient_history"}
                    current_first = evaluate_expression(
                        draft.expression, candles, draft.resolved_parameters
                    )
                    current_second = evaluate_expression(
                        draft.expression, candles, draft.resolved_parameters
                    )
                    if current_first != current_second:
                        return {"symbol": symbol, "error": "nondeterministic"}
                    usable = len(candles) - validation.warmup_candles + 1
                    step = max(1, usable // 20)
                    endpoints = list(range(validation.warmup_candles, len(candles) + 1, step))[-20:]
                    history = []
                    for endpoint in endpoints:
                        try:
                            history.append(
                                evaluate_expression(
                                    draft.expression,
                                    candles[:endpoint],
                                    draft.resolved_parameters,
                                )
                            )
                        except DynamicMechanicValidationError:
                            continue
                    if not history:
                        return {"symbol": symbol, "error": "insufficient_historical_periods"}
                    return {
                        "symbol": symbol,
                        "current": current_first,
                        "passes": sum(history),
                        "observations": len(history),
                    }
                except Exception as exc:
                    return {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"[:300]}

        rows = await asyncio.gather(*(inspect(symbol) for symbol in symbols))
        good = [row for row in rows if "error" not in row]
        errors = [row for row in rows if "error" in row]
        observations = sum(int(row["observations"]) for row in good)
        passes = sum(int(row["passes"]) for row in good)
        current_candidates = [str(row["symbol"]) for row in good if row["current"]]
        candidate_rate = passes / observations if observations else 0.0
        split = max(1, int(len(good) * 0.7))
        train = good[:split]
        holdout = good[split:]
        train_observations = sum(int(row["observations"]) for row in train)
        holdout_observations = sum(int(row["observations"]) for row in holdout)
        train_rate = (
            sum(int(row["passes"]) for row in train) / train_observations
            if train_observations
            else 0.0
        )
        holdout_rate = (
            sum(int(row["passes"]) for row in holdout) / holdout_observations
            if holdout_observations
            else train_rate
        )
        if not good or len(errors) > len(rows) * 0.5:
            classification = "insufficient_data"
        elif passes == 0:
            classification = "too_strict"
        elif candidate_rate > self.settings.capability_extension_max_candidate_rate:
            classification = "too_permissive"
        elif candidate_rate < self.settings.capability_extension_min_candidate_rate:
            classification = "too_strict"
        else:
            classification = "balanced"
        report = {
            "classification": classification,
            "exchange": exchange,
            "timeframe": draft.timeframe,
            "history_candles_requested": requested_history,
            "history_candles_fetched": history_limit,
            "history_truncated": requested_history > history_limit,
            "symbols_planned": len(symbols),
            "symbols_scanned": len(good),
            "current_candidates": current_candidates[:100],
            "current_candidate_count": len(current_candidates),
            "historical_observations": observations,
            "historical_passes": passes,
            "candidate_rate": candidate_rate,
            "train_rate": train_rate,
            "holdout_rate": holdout_rate,
            "errors": errors[:30],
            "deterministic": not any(row.get("error") == "nondeterministic" for row in errors),
            "expression": {
                "nodes": validation.node_count,
                "depth": validation.max_depth,
                "warmup_candles": validation.warmup_candles,
            },
        }
        session.add(
            CapabilityExtensionScan(
                extension_id=extension.id,
                phase=phase,
                cycle_number=cycle_number,
                exchange=exchange,
                timeframe=draft.timeframe,
                symbols_planned=len(symbols),
                symbols_scanned=len(good),
                candidates_found=len(current_candidates),
                notifications_created=0,
                candidate_rate=candidate_rate,
                classification=classification,
                metrics=report,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        _append_log(
            extension,
            "market_test",
            f"{classification}: {passes}/{observations} historical observations; "
            f"{len(current_candidates)} current candidates across {len(good)} symbols.",
        )
        await session.flush()
        return report

    def _certify(
        self,
        draft: MechanicDraft,
        market_report: dict[str, Any],
        review: MechanicReview,
    ) -> MechanicCertificationResult:
        blockers: list[str] = []
        checks: dict[str, Any] = {}
        try:
            validation = validate_expression(
                draft.expression,
                max_nodes=self.settings.capability_extension_max_expression_nodes,
                max_depth=self.settings.capability_extension_max_expression_depth,
            )
            checks["schema"] = {"passed": True, "score": 20, **asdict(validation)}
        except DynamicMechanicValidationError as exc:
            checks["schema"] = {"passed": False, "score": 0, "error": str(exc)}
            blockers.append(str(exc))
        deterministic = bool(market_report.get("deterministic"))
        checks["determinism"] = {"passed": deterministic, "score": 20 if deterministic else 0}
        if not deterministic:
            blockers.append("Repeated evaluation produced different results")
        errors = list(market_report.get("errors") or [])
        symbols = int(market_report.get("symbols_planned") or 0)
        execution_ratio = max(0.0, 1 - len(errors) / max(1, symbols))
        checks["market_execution"] = {
            "passed": execution_ratio >= 0.8,
            "score": round(15 * execution_ratio, 2),
        }
        raw_classification = str(market_report.get("classification") or "invalid")
        allowed_classifications = {
            "balanced",
            "too_strict",
            "too_permissive",
            "insufficient_data",
            "invalid",
        }
        classification = cast(
            Literal[
                "balanced",
                "too_strict",
                "too_permissive",
                "insufficient_data",
                "invalid",
            ],
            raw_classification if raw_classification in allowed_classifications else "invalid",
        )
        balanced = classification == "balanced"
        checks["candidate_balance"] = {"passed": balanced, "score": 20 if balanced else 0}
        if not balanced:
            blockers.append(f"Market-test classification is {classification}")
        train_rate = float(market_report.get("train_rate") or 0)
        holdout_rate = float(market_report.get("holdout_rate") or 0)
        stable = balanced and abs(train_rate - holdout_rate) <= max(0.1, train_rate * 4)
        checks["holdout"] = {"passed": stable, "score": 10 if stable else 3 if balanced else 0}
        proof_ok = bool(draft.proof_template.strip())
        checks["proof"] = {"passed": proof_ok, "score": 5 if proof_ok else 0}
        ai_pass = (
            review.verdict == "pass"
            and review.failure_source == "none"
            and review.preserves_user_logic
            and review.confidence >= 0.75
        )
        checks["independent_ai_review"] = {
            "passed": ai_pass,
            "score": 10 if ai_pass else 0,
            "verdict": review.verdict,
            "failure_source": review.failure_source,
        }
        if not ai_pass:
            blockers.append(f"Independent review verdict is {review.verdict}")
        score = round(sum(float(item["score"]) for item in checks.values()), 2)
        passed = score >= self.settings.capability_extension_certification_score and not blockers
        return MechanicCertificationResult(
            score=score,
            passed=passed,
            blockers=list(dict.fromkeys(blockers)),
            checks=checks,
            classification=classification,
        )

    async def _certify_for_user(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        draft: MechanicDraft,
    ) -> None:
        extension.status = "certified_user"
        extension.stage = "awaiting_user_approval"
        extension.certified_at = datetime.now(UTC)
        self._store_draft(extension, draft)
        await self._install_chat_draft(session, extension, draft)
        _append_log(extension, "certified", f"Certification score {extension.validation_score}.")
        await self._status(
            session,
            extension,
            "Your mechanic passed deterministic checks and market testing. Review the exact "
            "rule before approving the monitor.",
            stage="awaiting_user_approval",
            telegram=True,
        )

    async def _install_chat_draft(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        draft: MechanicDraft,
    ) -> None:
        if extension.chat_session_id is None or extension.artifact_hash is None:
            return
        chat = await session.get(AISetupChatSession, extension.chat_session_id)
        if chat is None:
            return
        rule = compile_dynamic_rule(
            capability_key=extension.capability_key,
            capability_version=extension.capability_version,
            artifact_hash=extension.artifact_hash,
            label=draft.label,
            timeframe=draft.timeframe,
            expression=draft.expression,
            resolved_parameters=draft.resolved_parameters,
            proof_template=draft.proof_template,
            source_fragment=extension.source_prompt,
        )
        existing_definition = None
        if chat.draft_schema_json:
            try:
                existing_definition = StrategyDefinition.model_validate(chat.draft_schema_json)
            except ValueError:
                existing_definition = None
        known_rules = self._known_rules(chat, excluding=extension.source_prompt)
        if existing_definition is not None:
            definition = existing_definition.model_copy(
                update={
                    "conditions": existing_definition.conditions.model_copy(
                        update={
                            "children": [
                                *existing_definition.conditions.children,
                                rule,
                            ]
                        }
                    )
                }
            )
        else:
            source = chat.original_idea or extension.source_prompt
            has_and = bool(re.search(r"\band\b", source, flags=re.IGNORECASE))
            has_or = bool(re.search(r"\bor\b", source, flags=re.IGNORECASE))
            operator = LogicalOperator.OR if has_or and not has_and else LogicalOperator.AND
            definition = StrategyDefinition(
                name=draft.label,
                description=draft.deterministic_definition,
                direction=StrategyDirection.BOTH,
                base_timeframe=draft.timeframe,
                trigger_mode=TriggerMode.CANDLE_CLOSE,
                universe=UniverseDefinition(
                    exchange=self.settings.capability_extension_preflight_exchange,
                    quote_currencies=["USDT"],
                ),
                conditions=ConditionGroup(
                    key="all_required_conditions",
                    operator=operator,
                    children=[*known_rules, rule],
                ),
                alerts=AlertPolicy(channels=["telegram"], maximum_alerts_per_hour=50),
            )
        chat.draft_schema_json = definition.model_dump(mode="json")
        mixed_logic = has_and and has_or if existing_definition is None else False
        chat.status = "needs_clarification" if mixed_logic else "ready_for_approval"
        chat.assumptions = draft.assumptions
        chat.ambiguities = (
            [
                {
                    "term": "mixed AND/OR logic",
                    "question": (
                        "Your setup mixes AND and OR. Confirm the grouping before approval."
                    ),
                    "blocking": True,
                }
            ]
            if mixed_logic
            else []
        )
        chat.unsupported_conditions = []
        chat.lint_warnings = []
        all_rules = _condition_rules(definition.conditions)
        chat.rule_confidence = [
            {
                "rule_key": item.key,
                "confidence": "high",
                "score": item.confidence or 0.9,
                "requires_confirmation": False,
            }
            for item in all_rules
        ]
        chat.translation_sheet = {
            "original_idea": chat.original_idea or extension.source_prompt,
            "monitor_name": draft.label,
            "summary_paragraph": draft.deterministic_definition,
            "exchange": self.settings.capability_extension_preflight_exchange,
            "market_type": "spot",
            "direction": "both",
            "quote_currencies": ["USDT"],
            "symbols_watchlist": [],
            "timeframes": [draft.timeframe],
            "conditions": [
                {
                    "key": item.key,
                    "name": item.label,
                    "role": "primary_trigger" if item.key == rule.key else "required_filter",
                    "required": item.required,
                    "timeframe": item.timeframe,
                    "operator": (
                        "certified dynamic mechanic"
                        if item.key == rule.key
                        else item.comparator.value
                    ),
                }
                for item in all_rules
            ],
            "alert_timing": {"trigger_mode": "candle_close"},
            "delivery_channels": ["telegram"],
            "assumptions": draft.assumptions,
            "execution": "Deterministic crypto spot monitoring only. No automatic trading.",
            "certification": extension.validation_report,
            "capability_key": extension.capability_key,
            "capability_version": extension.capability_version,
            "artifact_hash": extension.artifact_hash,
            "approval_required": True,
        }
        context = dict(chat.context_json or {})
        context["capability_extension_id"] = str(extension.id)
        context["capability_extension_status"] = extension.status
        chat.context_json = context

    @staticmethod
    def _known_rules(
        chat: AISetupChatSession,
        *,
        excluding: str,
    ) -> list[ConditionRule]:
        resolver = get_capability_index().resolver
        rules = []
        normalized_exclusion = " ".join(excluding.casefold().split())
        for index, binding in enumerate((chat.context_json or {}).get("capability_bindings") or []):
            source_fragment = str(binding.get("source_fragment") or "")
            if " ".join(source_fragment.casefold().split()) == normalized_exclusion:
                continue
            try:
                rules.append(
                    resolver.validate_selection(
                        capability_key=str(binding["capability_key"]),
                        parameters=dict(binding.get("parameters") or {}),
                        timeframe=str(binding.get("timeframe") or "15m"),
                        required=bool(binding.get("required", True)),
                        source_fragment=source_fragment,
                        condition_key=f"known_{index}_{binding['capability_key']}"[:100],
                        confidence=float(binding.get("confidence") or 0.9),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rules

    def _store_draft(self, extension: CapabilityExtension, draft: MechanicDraft) -> None:
        manifest = draft.model_dump(mode="json")
        expression = manifest.pop("expression")
        extension.manifest = draft.model_dump(mode="json")
        extension.expression = expression
        extension.artifact_hash = expression_hash(expression, manifest)
        extension.generated_code = json.dumps(
            {
                "capability_key": extension.capability_key,
                "capability_version": extension.capability_version,
                "artifact_hash": extension.artifact_hash,
                "manifest": manifest,
                "expression": expression,
            },
            indent=2,
            sort_keys=True,
        )

    async def _attempt(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        *,
        operation: str,
        model: str,
        effort: str,
        service_tier: str,
        input_payload: dict[str, Any],
    ) -> CapabilityExtensionAttempt:
        count = int(
            await session.scalar(
                select(func.count(CapabilityExtensionAttempt.id)).where(
                    CapabilityExtensionAttempt.extension_id == extension.id
                )
            )
            or 0
        )
        attempt = CapabilityExtensionAttempt(
            extension_id=extension.id,
            attempt_number=count + 1,
            operation=operation,
            model=model,
            reasoning_effort=effort,
            service_tier=service_tier,
            status="started",
            input_payload=input_payload,
            created_at=datetime.now(UTC),
        )
        session.add(attempt)
        await session.flush()
        return attempt

    def _complete_attempt(
        self, attempt: CapabilityExtensionAttempt, output_payload: dict[str, Any]
    ) -> None:
        attempt.status = "succeeded"
        attempt.output_payload = output_payload
        attempt.usage = dict(getattr(self.ai, "last_usage", {}) or {})
        attempt.completed_at = datetime.now(UTC)

    @staticmethod
    def _fail_attempt(attempt: CapabilityExtensionAttempt, error: Exception) -> None:
        attempt.status = "failed"
        attempt.error_detail = str(error)[:2000]
        attempt.completed_at = datetime.now(UTC)

    async def _status(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        message: str,
        *,
        stage: str,
        telegram: bool = False,
    ) -> None:
        extension.stage = stage
        _append_log(extension, stage, message)
        if extension.chat_session_id is not None:
            sequence = int(
                await session.scalar(
                    select(func.max(AISetupChatMessage.sequence)).where(
                        AISetupChatMessage.session_id == extension.chat_session_id
                    )
                )
                or 0
            )
            session.add(
                AISetupChatMessage(
                    session_id=extension.chat_session_id,
                    sequence=sequence + 1,
                    role="assistant",
                    message_type="mechanic_build_status",
                    content=message,
                    payload={
                        "extension_id": str(extension.id),
                        "stage": stage,
                        "status": extension.status,
                    },
                    created_at=datetime.now(UTC),
                )
            )
        # A website-originated build already streams status into its chat session. Sending the
        # same internal test updates to the user's alert channel crosses surfaces and is noisy.
        # Telegram-only requests have no website chat_session_id and keep Telegram status.
        if telegram and extension.chat_session_id is None:
            await self._telegram_status(session, extension, message)
        await session.flush()

    async def _telegram_status(
        self,
        session: AsyncSession,
        extension: CapabilityExtension,
        message: str,
    ) -> None:
        if (
            not self.settings.telegram_enabled
            or self.settings.telegram_adapter != "http"
            or self.settings.telegram_bot_token is None
        ):
            return
        connection = await session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.user_id == extension.user_id,
                TelegramConnection.status == ConnectionStatus.ACTIVE,
                TelegramConnection.alerts_enabled.is_(True),
            )
        )
        if connection is None or not connection.chat_id:
            return
        try:
            await TelegramHttpAdapter(self.settings).deliver(
                TelegramOutboundMessage(
                    chat_id=connection.chat_id,
                    text=f"TraceEdge mechanic update\n\n{message}",
                    buttons=(
                        [
                            TelegramButton(
                                "Review repair",
                                "external:capability_repair",
                                url=(
                                    f"{str(self.settings.public_base_url).rstrip('/')}"
                                    "/dashboard/strategies/new#monitors"
                                ),
                            )
                        ]
                        if extension.status == "repair_ready"
                        else []
                    ),
                )
            )
        except TelegramDeliveryError:
            return


def _append_log(extension: CapabilityExtension, stage: str, message: str) -> None:
    extension.build_log = [*(extension.build_log or []), _log(stage, message)][-500:]


def _log(stage: str, message: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "stage": stage,
        "message": message[:1000],
    }


def _timeframe(prompt: str) -> str:
    match = re.search(
        r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)\b",
        prompt.casefold(),
    )
    return match.group(1) if match else "15m"


def _condition_rules(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    rules: list[ConditionRule] = []
    for child in node.children:
        rules.extend(_condition_rules(child))
    return rules


def _replace_dynamic_rule(
    node: ConditionRule | ConditionGroup,
    *,
    artifact_hash: str,
    replacement: ConditionRule,
) -> tuple[ConditionRule | ConditionGroup, bool]:
    if isinstance(node, ConditionRule):
        if node.capability_artifact_hash != artifact_hash:
            return node, False
        return replacement.model_copy(update={"key": node.key, "required": node.required}), True
    replaced = False
    children = []
    for child in node.children:
        updated, child_replaced = _replace_dynamic_rule(
            child,
            artifact_hash=artifact_hash,
            replacement=replacement,
        )
        replaced = replaced or child_replaced
        children.append(updated)
    return node.model_copy(update={"children": children}), replaced


def _next_capability_version(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return "0.1.1"
    major, minor, patch = (int(part) for part in parts)
    return f"{major}.{minor}.{patch + 1}"
