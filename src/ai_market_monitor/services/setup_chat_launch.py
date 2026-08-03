from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Any, Literal, cast, get_args
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.universe_membership import (
    MembershipKind,
    is_dynamic_membership,
)
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    AIUsageEvent,
    ApprovedWatchlist,
    SetupChatDraftSnapshot,
    SetupChatOperationalIssue,
    SetupChatTurn,
    ShariaMethodology,
    ShariaMethodologyFamily,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ShariaAssetStatus,
    ShariaMethodologyStatus,
    ShariaUniverseMode,
)
from ai_market_monitor.engine.capability_shortlist import (
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.planner_references import (
    MethodologyReference,
    PlannerReferenceContext,
    WatchlistReference,
)
from ai_market_monitor.engine.setup_turn_execution import (
    ProviderGate,
    RuntimePreflight,
    ScreeningGate,
    SetupTurnRequest,
    apply_setup_turn,
)
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_migration import migrate_legacy_draft
from ai_market_monitor.engine.strategy_draft_v2 import validate_draft_semantics
from ai_market_monitor.engine.turn_timing import TurnTelemetry
from ai_market_monitor.engine.validated_intent_snapshot import (
    ValidatedIntentSnapshot,
    append_snapshot,
    normalized_intent_hash,
    repeat_state,
    snapshot_history,
)
from ai_market_monitor.schemas.preflight_cache import PreflightCacheEntry
from ai_market_monitor.schemas.screening_execution import (
    PreflightContract,
    PreflightManifest,
    ReviewedScreeningEvidence,
    ScreeningExecutionResult,
    symbol_set_hash,
)
from ai_market_monitor.schemas.setup_agent import (
    DIALOGUE_WINDOW_MAX,
    SegmentKind,
    SetupAgentTurnPlan,
    SetupConversationContext,
    SetupTurnExecutionResult,
    TurnSegment,
)
from ai_market_monitor.schemas.setup_authorization import AuthorizedPatchOperation
from ai_market_monitor.schemas.strategy import StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    ApprovalBindingV2,
    DraftFieldPatch,
    DraftMode,
    ProviderRequirementV2,
    ProviderRuntimeStatusV2,
    ShariaPolicyV2,
    StrategyDraftV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.market_preview import (
    assess_candle_data_quality,
    timeframe_duration,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
    deterministic_summary,
)
from ai_market_monitor.services.sharia_screening import ShariaScreeningService
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.system_brain import CapabilityCoverageService
from ai_market_monitor.services.watchlist_snapshot import (
    WatchlistIdentityError,
    scope_from_definition,
    scope_from_draft,
    watchlist_content_hash,
    watchlist_identity_changed,
)

#: Which transport stage an agent failure maps to for the HTTP error envelope. The
#: four stages stay distinct: a planning failure, a refused plan, a compile refusal
#: and a failed reply need different handling, and collapsing them once lost the
#: draft or reported real market logic as conversation.
LaunchStage = Literal[
    "intent",
    "extract",
    "patch",
    "interpret",
    "compile",
    "serialize",
    "provider",
]

_LAUNCH_STAGE_BY_AGENT_STAGE: dict[str, LaunchStage] = {
    "planning": "extract",
    "tool_validation": "patch",
    "compile": "compile",
    "response_composition": "serialize",
}

#: Bumped whenever the meaning of a stored data check changes — a new field in the
#: manifest, a different rule for choosing the contract, a change to what counts as
#: usable candles. Part of the cache identity, so old entries are never reinterpreted
#: under new rules; they simply stop matching and the check is redone.
_PREFLIGHT_CONTRACT_VERSION = 2


@dataclass(frozen=True, slots=True)
class _DraftRenderState:
    """What persisting the draft found, for callers that then write a message."""

    blocking: bool
    violations: tuple[str, ...]
    unresolved: tuple[UnresolvedFieldV2, ...]
    definition: StrategyDefinition | None


class SetupLaunchError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: LaunchStage,
        retryable: bool = False,
        status_code: int = 409,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class _CostReservation:
    """One in-flight maximum-turn reservation, released after the turn settles."""

    redis_key: str
    amount_usd: float
    ttl_seconds: int


class SetupChatLaunchService:
    """The only writable Setup Chat strategy path outside test compatibility mode."""

    def __init__(
        self,
        settings: Settings,
        owner: Any,
        *,
        agent: SetupChatAgent | None = None,
    ) -> None:
        self.settings = settings
        self.owner = owner
        self.agent = agent or SetupChatAgent(settings)
        #: What the last preflight actually checked, so the promise can be shown and
        #: hashed into approval rather than assumed.
        self._last_preflight_manifest: PreflightManifest | None = None
        self._preflight_redis: Redis | None = (
            None
            if settings.app_env == "test"
            else Redis.from_url(settings.redis_url, decode_responses=True)
        )

    async def handle(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        option_key: str | None,
        option_value: str | None,
        option_label: str | None,
        client_message_id: str | None,
    ) -> AISetupChatSession:
        started = monotonic()
        turn_record: SetupChatTurn | None = None
        if client_message_id:
            replay = await self._replayed_turn(session, chat, client_message_id)
            if replay is not None:
                # The completed turn already owns exact measured telemetry.  A replay
                # must not replace it with an empty zero-call record and make the paid
                # original look unmeasured.  Record replay latency separately while
                # preserving the authoritative turn_runtime.measured payload.
                context = dict(chat.context_json or {})
                context["last_idempotent_replay"] = {
                    "client_message_id": client_message_id,
                    "duration_ms": round((monotonic() - started) * 1000, 1),
                    "cache_hit": True,
                }
                chat.context_json = context
                await session.flush()
                await session.commit()
                return replay

        # Dynamic launch controls gate new work, never the exact read-only replay of a
        # completed idempotent turn above. An emergency switch or beta-list change must
        # not make a previously committed response disappear or regenerate wording.
        if self.settings.setup_chat_emergency_disabled:
            raise SetupLaunchError(
                "SETUP_CHAT_EMERGENCY_DISABLED",
                "Setup Chat is temporarily paused. Your existing draft is unchanged.",
                stage="intent",
                retryable=True,
                status_code=503,
            )
        beta_users = {
            item.casefold() for item in self.settings.setup_chat_private_beta_user_ids
        }
        if beta_users and str(chat.user_id).casefold() not in beta_users:
            raise SetupLaunchError(
                "SETUP_CHAT_PRIVATE_BETA_NOT_ENABLED",
                "Setup Chat is not enabled for this account.",
                stage="intent",
                status_code=403,
            )

        # The raw text, exactly as typed. Collapsing whitespace here destroyed the line
        # breaks and list structure that tell three numbered rules apart from one
        # sentence, and made the stored provenance disagree with what the user wrote.
        raw = message or option_label or option_value or ""
        cleaned = " ".join(raw.split())
        if option_key:
            if client_message_id:
                turn_record = await self._get_or_create_turn(
                    session,
                    chat,
                    client_message_id,
                )
            selected_chat = await self._run_server_option_turn(
                session,
                chat,
                option_key=option_key,
                option_value=option_value or cleaned,
                option_label=option_label,
                client_message_id=client_message_id,
                started=started,
                turn_record=turn_record,
            )
            return selected_chat

        reservation = await self._reserve_user_cost_budget(session, chat.user_id)
        try:
            if client_message_id:
                turn_record = await self._get_or_create_turn(
                    session,
                    chat,
                    client_message_id,
                )
            user_message = (
                await session.get(AISetupChatMessage, turn_record.source_message_id)
                if turn_record is not None and turn_record.source_message_id is not None
                else None
            )
            if user_message is None:
                user_message = await self.owner._append_message(
                    session,
                    chat,
                    role="user",
                    message_type="text",
                    # Stored as typed, so provenance quotes what the user can see they wrote.
                    content=raw,
                    payload={
                        "semantic_layer": "ai_first",
                        "launch_pipeline": "setup_agent_v3",
                        "option_key": option_key,
                        "option_value": option_value,
                    },
                    client_message_id=client_message_id,
                )
            if turn_record is not None:
                turn_record.source_message_id = user_message.id
                turn_record.status = TurnStatus.PLANNING.value
                await session.flush()
                await session.commit()

            return await self._run_agent_turn(
                session,
                chat,
                message=raw,
                source_turn_id=str(user_message.id),
                started=started,
                client_message_id=client_message_id,
                turn_record=turn_record,
            )
        finally:
            await self._release_user_cost_reservation(reservation)

    async def _reserve_user_cost_budget(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> _CostReservation | None:
        """Reserve only the concurrent, in-flight maximum-turn exposure.

        Usage events are the immutable cost authority. Redis atomically reserves the
        whole per-turn ceiling before provider work, so concurrent turns cannot share
        one remaining allowance. The reservation is released in ``handle()`` after the
        turn succeeds or fails; actual spend remains in ``AIUsageEvent``. The previous
        implementation accumulated every maximum reservation until midnight, charging
        ordinary $0.002 turns as $0.10 and blocking long evaluator sessions after about
        twenty turns.

        If Redis is unavailable, the immutable DB total and API rate window provide a
        conservative degraded boundary without turning Redis health into an AI error.
        """

        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        spent = await session.scalar(
            select(func.coalesce(func.sum(AIUsageEvent.estimated_cost_usd), 0)).where(
                AIUsageEvent.user_id == user_id,
                AIUsageEvent.operation == "setup_agent_turn",
                AIUsageEvent.created_at >= day_start,
            )
        )
        recorded_spend = float(spent or 0)
        reserve = self.settings.setup_agent_max_estimated_cost_usd_per_turn
        limit = self.settings.setup_agent_max_estimated_cost_usd_per_user_day
        projected = recorded_spend + reserve
        reservation: _CostReservation | None = None
        if self._preflight_redis is not None:
            now = datetime.now(UTC)
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            ttl = max(60, int((tomorrow - now).total_seconds()))
            # v2 stores only outstanding reservations. The prior key accumulated each
            # maximum reservation for the entire day and is deliberately not reused.
            key = f"setup-chat:daily-cost:v2:{now.date().isoformat()}:{user_id}"
            script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local next_value = current + tonumber(ARGV[2])
if tonumber(ARGV[1]) + next_value > tonumber(ARGV[3]) then return '-1' end
redis.call('SET', KEYS[1], tostring(next_value), 'EX', ARGV[4])
return tostring(next_value)
"""
            try:
                redis_eval = cast(Any, self._preflight_redis.eval)
                reserved = await redis_eval(
                    script,
                    1,
                    key,
                    str(recorded_spend),
                    str(reserve),
                    str(limit),
                    str(ttl),
                )
                outstanding = float(reserved)
                projected = -1 if outstanding < 0 else recorded_spend + outstanding
                if outstanding >= 0:
                    reservation = _CostReservation(
                        redis_key=key,
                        amount_usd=reserve,
                        ttl_seconds=ttl,
                    )
            except (RedisError, TypeError, ValueError):
                # Cost protection degrades to immutable DB usage plus the API's
                # per-user rate window. Provider interpretation remains available;
                # Redis availability is never recast as a semantic model failure.
                projected = recorded_spend + reserve
        if projected < 0 or projected > limit:
            raise SetupLaunchError(
                "SETUP_CHAT_DAILY_COST_BUDGET_REACHED",
                "Setup Chat's daily AI allowance is reached. Your draft is unchanged.",
                stage="intent",
                retryable=True,
                status_code=429,
            )
        return reservation

    async def _release_user_cost_reservation(
        self,
        reservation: _CostReservation | None,
    ) -> None:
        """Release an in-flight reservation; immutable usage retains actual spend."""

        if reservation is None or self._preflight_redis is None:
            return
        script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local next_value = current - tonumber(ARGV[1])
if next_value <= 0.0000001 then
  redis.call('DEL', KEYS[1])
  return '0'
end
redis.call('SET', KEYS[1], tostring(next_value), 'EX', ARGV[2])
return tostring(next_value)
"""
        try:
            redis_eval = cast(Any, self._preflight_redis.eval)
            await redis_eval(
                script,
                1,
                reservation.redis_key,
                str(reservation.amount_usd),
                str(reservation.ttl_seconds),
            )
        except (RedisError, TypeError, ValueError):
            # The reservation expires at the UTC-day boundary. A release outage may
            # conservatively reduce availability, but can never authorize overspend or
            # replace the original turn result.
            return

    async def _queue_operational_issue(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn: SetupChatTurn | None,
        *,
        proof: dict[str, Any],
        repeated_failure: bool,
    ) -> None:
        """Upsert one safe, deduplicated issue into the admin-owned queue."""

        fingerprint = str(proof.get("support_reference") or "")
        if not fingerprint:
            fingerprint = hashlib.sha256(
                json.dumps(proof, sort_keys=True, default=str).encode()
            ).hexdigest()[:32]
        now = datetime.now(UTC)
        issue = await session.scalar(
            select(SetupChatOperationalIssue)
            .where(SetupChatOperationalIssue.fingerprint == fingerprint)
            .with_for_update()
        )
        if issue is None:
            issue = SetupChatOperationalIssue(
                user_id=chat.user_id,
                chat_session_id=chat.id,
                setup_chat_turn_id=turn.id if turn is not None else None,
                fingerprint=fingerprint,
                issue_kind=("repeated_failure" if repeated_failure else "compiler_invariant"),
                failure_class=str(proof.get("failure_class") or "UNKNOWN"),
                status="open",
                occurrence_count=1,
                semantic_paths=[str(item) for item in proof.get("semantic_paths") or []],
                safe_source_excerpt=str(proof.get("source_excerpt") or "")[:1000],
                support_reference=str(proof.get("support_reference") or "")[:64],
                failure_proof=proof,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(issue)
        else:
            issue.occurrence_count += 1
            issue.last_seen_at = now
            issue.setup_chat_turn_id = turn.id if turn is not None else issue.setup_chat_turn_id
            issue.failure_proof = proof
            issue.status = "open"
            issue.resolved_at = None
            issue.resolution_note = None
        await session.flush()

    async def _run_server_option_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        option_key: str,
        option_value: str,
        option_label: str | None,
        client_message_id: str | None,
        started: float,
        turn_record: SetupChatTurn | None,
    ) -> AISetupChatSession:
        """Map one allowlisted server UI control into the canonical turn tool."""

        allowed = {
            "setup_mode",
            "monitor_name",
            "screened_universe_mode",
            "screened_watchlist",
            "screened_explicit_assets",
            "sharia_methodology",
        }
        if option_key not in allowed:
            option_telemetry = TurnTelemetry.start(self.settings.setup_turn_deadline_seconds)
            with option_telemetry.stage("request_acceptance"):
                pass
            await self._fail_db_turn(
                session,
                chat,
                turn_record,
                code="UNKNOWN_SETUP_OPTION",
                stage="intent",
                retryable=False,
                started=started,
                telemetry=option_telemetry,
            )
            raise SetupLaunchError(
                "UNKNOWN_SETUP_OPTION",
                "That setup option is no longer available. Refresh and choose again.",
                stage="intent",
                status_code=422,
            )
        rendered = option_label or option_value
        user_message = (
            await session.get(AISetupChatMessage, turn_record.source_message_id)
            if turn_record is not None and turn_record.source_message_id is not None
            else None
        )
        if user_message is None:
            user_message = await self.owner._append_message(
                session,
                chat,
                role="user",
                message_type="option",
                content=rendered,
                payload={
                    "option_key": option_key,
                    "option_value": option_value,
                    "authorizing_source": "server_owned_allowlist",
                    "launch_pipeline": "setup_agent_v3",
                },
                client_message_id=client_message_id,
            )
        if turn_record is not None:
            turn_record.source_message_id = user_message.id
            turn_record.status = TurnStatus.EXECUTING.value
            await session.flush()
            await session.commit()

        before = load_strategy_draft_v2(chat)
        operations = await self._server_option_operations(
            session,
            chat,
            draft=before,
            option_key=option_key,
            option_value=option_value,
            source_turn_id=str(user_message.id),
        )
        segment = TurnSegment(
            segment_id="server_option",
            exact_source_text=rendered,
            start_offset=0,
            end_offset=len(rendered),
            kind=SegmentKind.CLARIFICATION_ANSWER,
            reply_required=False,
            action_required=True,
            confidence=1.0,
        )
        plan = SetupAgentTurnPlan(
            source_turn_id=str(user_message.id),
            segments=[segment],
            operations=operations,
            overall_confidence=1.0,
        )
        callback = (
            self._turn_stage_callback(
                session,
                chat,
                turn_record,
                message=rendered,
                source_turn_id=str(user_message.id),
                expected_executable_hash=before.executable_hash,
                expected_workflow_state_hash=before.workflow_state_hash,
            )
            if turn_record is not None
            else None
        )
        if callback is not None:
            await callback(
                TurnStatus.EXECUTING.value,
                {
                    "planner_model": "server_owned_option",
                    "plan": plan.model_dump(mode="json"),
                },
            )
        outcome = await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=rendered,
                draft=before,
                source_turn_id=str(user_message.id),
                allowed_capability_keys=frozenset(),
                history=await self._snapshot_history(session, chat),
                conversation=_load_conversation_context(dict(chat.context_json or {})),
                screening=self._screening_gate(session, chat),
                providers=self._provider_gate(),
                runtime_preflight=self._runtime_preflight(),
                preflight_manifest=self._read_preflight_manifest,
                server_owned_option=True,
            )
        )
        if callback is not None:
            await callback(
                TurnStatus.COMPOSING.value,
                {
                    "planner_model": "server_owned_option",
                    "plan": plan.model_dump(mode="json"),
                    "execution_result": outcome.result.model_dump(mode="json"),
                    "draft_after": outcome.draft.model_dump(mode="json"),
                    "conversation_after": outcome.conversation.model_dump(mode="json"),
                    "definition": (
                        outcome.definition.model_dump(mode="json")
                        if outcome.definition is not None
                        else None
                    ),
                    "history_snapshot": outcome.history_snapshot,
                    "material_change": outcome.material_change,
                },
            )
        else:
            context = dict(chat.context_json or {})
            context["strategy_draft_v2"] = outcome.draft.model_dump(mode="json")
            context["strategy_state_authority"] = "v2"
            chat.context_json = context
            await self._persist_draft_state(
                session,
                chat,
                outcome.draft,
                definition=outcome.definition,
                execution=outcome.result,
            )

        content, message_type, payload = await self._server_option_reply(
            session,
            chat,
            option_key=option_key,
            draft=outcome.draft,
            execution=outcome.result,
        )
        assistant = await self.owner._assistant(
            session,
            chat,
            content,
            message_type=message_type,
            payload={
                **payload,
                "draft_v2": outcome.draft.model_dump(mode="json"),
                "execution_result": outcome.result.model_dump(mode="json"),
                "model_call_count": 0,
            },
        )
        await self._complete_db_turn(
            session,
            chat,
            turn_record,
            reply={
                "message": content,
                "execution_result": outcome.result.model_dump(mode="json"),
            },
            assistant_message_id=assistant.id,
        )
        _set_runtime(chat, started, model_calls=0, cache_hits=0)
        await session.flush()
        await session.commit()
        return chat

    async def _server_option_operations(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        draft: StrategyDraftV2,
        option_key: str,
        option_value: str,
        source_turn_id: str,
    ) -> list[AuthorizedPatchOperation]:
        payloads: list[dict[str, Any]] = []
        value = option_value.strip()
        if option_key == "setup_mode":
            normalized = value.casefold()
            if normalized not in {"scanner", "monitor"}:
                raise SetupLaunchError(
                    "INVALID_SETUP_MODE",
                    "Choose Scanner or Monitor.",
                    stage="intent",
                    status_code=422,
                )
            mode = DraftMode(normalized)
            payloads.append(
                {
                    "kind": "set_fields",
                    "fields": DraftFieldPatch(
                        mode=mode,
                        name=(
                            "Untitled Scanner" if mode == DraftMode.SCANNER else "Untitled Monitor"
                        ),
                    ),
                }
            )
            if self.settings.sharia_screening_enforced:
                methodology = await ShariaScreeningService(
                    session, self.settings
                ).default_methodology()
                if methodology is not None:
                    payloads.append(
                        {
                            "kind": "set_sharia_policy",
                            "sharia_policy": draft.sharia_policy.model_copy(
                                update={
                                    "methodology_id": methodology.id,
                                    "methodology_version": methodology.version,
                                    "allowed_statuses": sorted(
                                        (
                                            ShariaAssetStatus.ELIGIBLE,
                                            ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS,
                                        ),
                                        key=lambda item: item.value,
                                    ),
                                    "compliance_change_behavior": (
                                        ComplianceChangeBehavior.PAUSE_ASSET
                                    ),
                                }
                            ),
                        }
                    )
                if not any(
                    item.unresolved_id == "sharia.universe_mode" for item in draft.unresolved_fields
                ):
                    payloads.append(
                        {
                            "kind": "add_unresolved",
                            "unresolved": UnresolvedFieldV2(
                                unresolved_id="sharia.universe_mode",
                                source_turn_id=source_turn_id,
                                source_fragment=value,
                                target_type="universe",
                                target_field="sharia_policy.universe_mode",
                                expected_answer_schema={
                                    "type": "string",
                                    "enum": [
                                        ShariaUniverseMode.ELIGIBLE_MARKET.value,
                                        ShariaUniverseMode.APPROVED_WATCHLIST.value,
                                        ShariaUniverseMode.EXPLICIT_ASSETS.value,
                                    ],
                                },
                                allowed_options=[
                                    ShariaUniverseMode.ELIGIBLE_MARKET.value,
                                    ShariaUniverseMode.APPROVED_WATCHLIST.value,
                                    ShariaUniverseMode.EXPLICIT_ASSETS.value,
                                ],
                                question="Which screened assets should HilalMarkets watch?",
                                reason=(
                                    "A screened universe must be selected explicitly "
                                    "before this setup can be approved."
                                ),
                                created_workflow_revision=draft.workflow_revision,
                            ),
                        }
                    )
        elif option_key == "monitor_name":
            if not value:
                raise SetupLaunchError(
                    "INVALID_MONITOR_NAME",
                    "Enter a name for this Watchlist.",
                    stage="intent",
                    status_code=422,
                )
            payloads.append({"kind": "set_fields", "fields": DraftFieldPatch(name=value)})
        elif option_key == "screened_universe_mode":
            aliases = {
                "all_eligible_spot_assets": "eligible_market",
                "my_favorites": "approved_watchlist",
                "my_favourites": "approved_watchlist",
                "favorites": "approved_watchlist",
                "favourites": "approved_watchlist",
                "specific_eligible_assets": "explicit_assets",
            }
            normalized = aliases.get(
                value.casefold().replace(" ", "_"),
                value.casefold().replace(" ", "_"),
            )
            try:
                universe_mode = ShariaUniverseMode(normalized)
            except ValueError as exc:
                raise SetupLaunchError(
                    "INVALID_SCREENED_UNIVERSE_MODE",
                    "Choose one of the displayed screened-market scopes.",
                    stage="intent",
                    status_code=422,
                ) from exc
            updates: dict[str, Any] = {
                "universe_mode": universe_mode,
                "approved_watchlist_id": None,
                "approved_watchlist_version": None,
                "explicit_symbols": [],
            }
            if universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST:
                watchlists = list(
                    await session.scalars(
                        select(ApprovedWatchlist)
                        .where(ApprovedWatchlist.user_id == chat.user_id)
                        .order_by(
                            ApprovedWatchlist.is_default.desc(),
                            ApprovedWatchlist.name.asc(),
                        )
                    )
                )
                if len(watchlists) == 1:
                    updates.update(
                        {
                            "approved_watchlist_id": watchlists[0].id,
                            # Identity of the markets in the list, not of the row that
                            # names it. See `services/watchlist_snapshot.py`.
                            "approved_watchlist_version": await watchlist_content_hash(
                                session,
                                watchlists[0],
                                scope=scope_from_draft(draft),
                                require_resolved=self.settings.is_deployed,
                            ),
                        }
                    )
            payloads.append(
                {
                    "kind": "set_sharia_policy",
                    "sharia_policy": draft.sharia_policy.model_copy(update=updates),
                }
            )
            for target_key in (
                "sharia.universe_mode",
                "sharia.approved_watchlist",
                "sharia.explicit_symbols",
            ):
                if any(item.unresolved_id == target_key for item in draft.unresolved_fields):
                    payloads.append({"kind": "resolve_unresolved_key", "target_key": target_key})
            if (
                universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST
                and updates["approved_watchlist_id"] is None
            ):
                payloads.append(
                    {
                        "kind": "add_unresolved",
                        "unresolved": UnresolvedFieldV2(
                            unresolved_id="sharia.approved_watchlist",
                            source_turn_id=source_turn_id,
                            source_fragment=value,
                            target_type="universe",
                            target_field="sharia_policy.approved_watchlist_id",
                            expected_answer_schema={"type": "string", "format": "uuid"},
                            question="Which Favorites list should HilalMarkets use?",
                            reason=(
                                "The approved watchlist and its immutable version are "
                                "part of the executable Sharia policy."
                            ),
                            created_workflow_revision=draft.workflow_revision,
                        ),
                    }
                )
            elif universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS:
                payloads.append(
                    {
                        "kind": "add_unresolved",
                        "unresolved": UnresolvedFieldV2(
                            unresolved_id="sharia.explicit_symbols",
                            source_turn_id=source_turn_id,
                            source_fragment=value,
                            target_type="universe",
                            target_field="sharia_policy.explicit_symbols",
                            expected_answer_schema={
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                            },
                            question=("Which eligible spot assets should HilalMarkets watch?"),
                            reason=(
                                "Every explicitly bounded asset must be screened and "
                                "runtime-verified before approval."
                            ),
                            created_workflow_revision=draft.workflow_revision,
                        ),
                    }
                )
        elif option_key == "screened_watchlist":
            try:
                watchlist_id = UUID(value)
            except ValueError as exc:
                raise SetupLaunchError(
                    "WATCHLIST_NOT_FOUND",
                    "That Favorites list is unavailable.",
                    stage="intent",
                    status_code=404,
                ) from exc
            watchlist = await session.get(ApprovedWatchlist, watchlist_id)
            if watchlist is None or watchlist.user_id != chat.user_id:
                raise SetupLaunchError(
                    "WATCHLIST_NOT_FOUND",
                    "That Favorites list is unavailable.",
                    stage="intent",
                    status_code=404,
                )
            payloads.append(
                {
                    "kind": "set_sharia_policy",
                    "sharia_policy": draft.sharia_policy.model_copy(
                        update={
                            "universe_mode": ShariaUniverseMode.APPROVED_WATCHLIST,
                            "approved_watchlist_id": watchlist.id,
                            # Identity of the markets in the list, not of the row that
                            # names it. See `services/watchlist_snapshot.py`.
                            "approved_watchlist_version": await watchlist_content_hash(
                                session,
                                watchlist,
                                scope=scope_from_draft(draft),
                                require_resolved=self.settings.is_deployed,
                            ),
                            "explicit_symbols": [],
                        }
                    ),
                }
            )
            if any(
                item.unresolved_id == "sharia.approved_watchlist"
                for item in draft.unresolved_fields
            ):
                payloads.append(
                    {
                        "kind": "resolve_unresolved_key",
                        "target_key": "sharia.approved_watchlist",
                    }
                )
        elif option_key == "screened_explicit_assets":
            symbols = _option_symbols(value, draft.market_scope.quote_asset)
            if not symbols:
                raise SetupLaunchError(
                    "SCREENED_ASSETS_REQUIRED",
                    "Type at least one displayed spot asset symbol.",
                    stage="intent",
                    status_code=422,
                )
            payloads.append(
                {
                    "kind": "set_sharia_policy",
                    "sharia_policy": draft.sharia_policy.model_copy(
                        update={
                            "universe_mode": ShariaUniverseMode.EXPLICIT_ASSETS,
                            "approved_watchlist_id": None,
                            "approved_watchlist_version": None,
                            "explicit_symbols": symbols,
                        }
                    ),
                }
            )
            if any(
                item.unresolved_id == "sharia.explicit_symbols" for item in draft.unresolved_fields
            ):
                payloads.append(
                    {
                        "kind": "resolve_unresolved_key",
                        "target_key": "sharia.explicit_symbols",
                    }
                )
        elif option_key == "sharia_methodology":
            try:
                methodology_id = UUID(value)
            except ValueError as exc:
                raise SetupLaunchError(
                    "METHODOLOGY_NOT_AVAILABLE",
                    "That methodology is unavailable.",
                    stage="intent",
                    status_code=404,
                ) from exc
            methodology = await session.get(ShariaMethodology, methodology_id)
            if methodology is None or methodology.status != ShariaMethodologyStatus.ACTIVE:
                raise SetupLaunchError(
                    "METHODOLOGY_NOT_AVAILABLE",
                    "That methodology is not currently active.",
                    stage="intent",
                    status_code=409,
                )
            payloads.append(
                {
                    "kind": "set_sharia_policy",
                    "sharia_policy": draft.sharia_policy.model_copy(
                        update={
                            "methodology_id": methodology.id,
                            "methodology_version": methodology.version,
                        }
                    ),
                }
            )

        operations: list[AuthorizedPatchOperation] = []
        for index, payload in enumerate(payloads, start=1):
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "source_turn_id": source_turn_id,
                        "option_key": option_key,
                        "index": index,
                        "payload": {
                            key: (
                                item.model_dump(mode="json")
                                if hasattr(item, "model_dump")
                                else item
                            )
                            for key, item in payload.items()
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()[:20]
            operations.append(
                AuthorizedPatchOperation.model_validate(
                    {
                        "operation_id": f"ui_{option_key}_{digest}"[:80],
                        "authorizing_segment_id": "server_option",
                        **payload,
                    }
                )
            )
        return operations

    async def _server_option_reply(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        option_key: str,
        draft: StrategyDraftV2,
        execution: SetupTurnExecutionResult,
    ) -> tuple[str, str, dict[str, Any]]:
        if option_key == "setup_mode" and self.settings.sharia_screening_enforced:
            if draft.sharia_policy.methodology_id is None:
                return (
                    "Screened monitoring is unavailable because no approved methodology is active.",
                    "screening_unavailable",
                    {"can_approve": False, "can_scan": False},
                )
            options = [
                {
                    "key": "screened_universe_mode",
                    "label": "All eligible spot assets",
                    "value": ShariaUniverseMode.ELIGIBLE_MARKET.value,
                },
                {
                    "key": "screened_universe_mode",
                    "label": "My Favorites",
                    "value": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                },
                {
                    "key": "screened_universe_mode",
                    "label": "Specific eligible assets",
                    "value": ShariaUniverseMode.EXPLICIT_ASSETS.value,
                },
            ]
            return (
                "Which screened assets should HilalMarkets watch?",
                "screened_universe_required",
                {"clarifications": [{"key": "screened_universe_mode", "options": options}]},
            )
        if (
            option_key == "screened_universe_mode"
            and draft.sharia_policy.universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST
            and draft.sharia_policy.approved_watchlist_id is None
        ):
            rows = list(
                await session.scalars(
                    select(ApprovedWatchlist)
                    .where(ApprovedWatchlist.user_id == chat.user_id)
                    .order_by(
                        ApprovedWatchlist.is_default.desc(),
                        ApprovedWatchlist.name.asc(),
                    )
                )
            )
            if not rows:
                return (
                    "You do not have a Favorites list yet. Choose another screened scope.",
                    "screened_watchlist_missing",
                    {"can_approve": False, "can_scan": False},
                )
            return (
                "Which Favorites list should HilalMarkets use?",
                "screened_watchlist_required",
                {
                    "clarifications": [
                        {
                            "key": "screened_watchlist",
                            "options": [
                                {
                                    "key": "screened_watchlist",
                                    "label": row.name,
                                    "value": str(row.id),
                                }
                                for row in rows[:8]
                            ],
                        }
                    ]
                },
            )
        if (
            option_key == "screened_universe_mode"
            and draft.sharia_policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS
            and not draft.sharia_policy.explicit_symbols
        ):
            return (
                "Which eligible spot assets should HilalMarkets watch?",
                "screened_assets_required",
                {"awaiting_answer": True, "can_approve": False},
            )
        return (
            deterministic_summary(execution),
            _agent_message_type(execution),
            {"can_approve": execution.approval_eligible},
        )

    async def revalidate_for_approval(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        draft: StrategyDraftV2,
        *,
        expected_executable_version: int,
        expected_executable_hash: str,
        expected_schema_hash: str,
    ) -> tuple[StrategyDefinition, ReviewedScreeningEvidence | None]:
        """Re-verify, in order, everything the user was shown before they approved.

        The seven steps below run every time, and each one refuses rather than repairs:

        1. the draft still has the exact version and executable identity that was reviewed
        2. it still compiles, and the compiled preview still hashes to what was shown
        3. the screening methodology is still active and still at the reviewed version
        4. the Favorites list, if one is used, still has exactly the reviewed membership
           — by content, not by a timestamp
        5. the universe re-resolves, and the markets it permits are **the same set** the
           user reviewed
        6. every data capability the rules need is still available
        7. the market-data check is fresh and keeps the same promise it made at review

        Step 5 is the one that was missing. The universe was re-resolved and the answer
        thrown away, so a setup reviewed over eight markets could be approved over a
        different set with nothing shown and nothing recorded.

        Returns the screened definition and the evidence to bind into the approval.
        """

        # 1. Identity of the reviewed draft.
        if (
            draft.executable_version != expected_executable_version
            or draft.executable_hash != expected_executable_hash
        ):
            raise SetupLaunchError(
                "SETUP_CHANGED",
                "The draft changed. Review the latest version before approval.",
                stage="compile",
                status_code=409,
            )
        # Approval can only bind a draft that is already independently restorable.
        # This also closes the one-time legacy-policy migration case, where the
        # canonical JSON payload is upgraded before any ordinary mutation exists to
        # create a snapshot.
        await self._store_snapshot(
            session,
            chat,
            draft.model_dump(mode="json"),
            source_turn_id=None,
        )
        try:
            definition = compile_strategy_draft_v2(draft)
        except StrategyV2CompileError as exc:
            raise SetupLaunchError(
                "SETUP_NOT_READY",
                "The exact draft no longer compiles.",
                stage="compile",
                status_code=409,
            ) from exc
        policy = draft.sharia_policy
        screening: ScreeningExecutionResult | None = None
        preflight_definition = definition
        if self.settings.sharia_screening_enforced:
            # 3. The methodology is still active and still the reviewed version.
            if policy.methodology_id is None or not policy.methodology_version:
                raise SetupLaunchError(
                    "SHARIA_POLICY_STALE",
                    "Choose an active methodology before approval.",
                    stage="compile",
                    status_code=409,
                )
            methodology = await session.get(ShariaMethodology, policy.methodology_id)
            if (
                methodology is None
                or methodology.status != ShariaMethodologyStatus.ACTIVE
                or methodology.version != policy.methodology_version
            ):
                raise SetupLaunchError(
                    "SHARIA_POLICY_STALE",
                    "The selected methodology changed. Review the updated policy.",
                    stage="compile",
                    status_code=409,
                )
            # 4. The Favorites list still holds exactly the reviewed markets.
            #
            # The old test compared `watchlist.updated_at.isoformat()`. Membership lives
            # in a separate table, so adding or removing a market did not always move
            # that timestamp, and an unrelated rename always did. A content hash of the
            # actual membership answers the real question.
            if (
                policy.universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST
                and policy.approved_watchlist_id is not None
            ):
                watchlist = await session.get(
                    ApprovedWatchlist,
                    policy.approved_watchlist_id,
                )
                if watchlist is None or watchlist.user_id != chat.user_id:
                    raise SetupLaunchError(
                        "APPROVED_WATCHLIST_STALE",
                        "The selected Favorites list changed. Review it again.",
                        stage="compile",
                        status_code=409,
                    )
                if await watchlist_identity_changed(
                    session,
                    watchlist.id,
                    policy.approved_watchlist_version,
                    scope=scope_from_definition(definition),
                    require_resolved=self.settings.is_deployed,
                ):
                    raise SetupLaunchError(
                        "APPROVED_WATCHLIST_STALE",
                        "The markets in that Favorites list changed. Review it again.",
                        stage="compile",
                        status_code=409,
                    )
            # 5. The universe re-resolves, and it is the same set that was reviewed.
            try:
                screening = await self._apply_screening_policy(
                    session,
                    chat,
                    definition,
                    persist_snapshot=True,
                )
            except ShariaUniverseError as exc:
                raise SetupLaunchError(
                    "SHARIA_SCREENING_STALE",
                    "The screened universe could not be refreshed for approval.",
                    stage="compile",
                    status_code=409,
                ) from exc
            except (KeyError, ValueError) as exc:
                raise SetupLaunchError(
                    "SHARIA_SCREENING_EMPTY",
                    str(exc) or "No selected asset is currently eligible under this policy.",
                    stage="compile",
                    status_code=409,
                ) from exc
            # The approved object stays the **authored** policy, so a dynamic universe
            # keeps re-resolving after approval. What the user reviewed is bound
            # separately, through `ReviewedScreeningEvidence` below.
            definition = screening.authored_definition
            preflight_definition = screening.preflight_definition

        # 6. Every data capability the rules need is still available.
        provider_requirements = await self._provider_gate()(draft.static_provider_requirements)
        if any(item.status != "available" for item in provider_requirements):
            raise SetupLaunchError(
                "PROVIDER_UNAVAILABLE",
                "A required data capability is unavailable.",
                stage="provider",
                status_code=409,
            )
        # 7. The market-data check is fresh, and it keeps its reviewed promise.
        statuses = await self._runtime_preflight()(preflight_definition)
        manifest = self._read_preflight_manifest()
        now = datetime.now(UTC)
        ttl_seconds = self.settings.setup_provider_preflight_ttl_seconds
        if any(
            item.status != "available"
            or item.checked_at is None
            or (now - item.checked_at).total_seconds() > ttl_seconds
            for item in statuses
        ):
            raise SetupLaunchError(
                "PROVIDER_PREFLIGHT_STALE",
                "Every selected market and timeframe must be verified again.",
                stage="provider",
                status_code=409,
            )
        if manifest is None:
            # Statuses without the manifest they came from is the one combination a cache
            # hit used to be able to produce. There is then no record of *what* was
            # checked, so there is nothing to bind the approval to.
            raise SetupLaunchError(
                "PROVIDER_PREFLIGHT_STALE",
                "The market-data check has to run again before this setup is approved.",
                stage="provider",
                status_code=409,
            )
        if not manifest.covers(list(preflight_definition.universe.include_symbols)):
            # A `verified_all` manifest that lists fewer pairs than it promises is a false
            # promise, not a strict one kept loosely.
            raise SetupLaunchError(
                "PROVIDER_PREFLIGHT_INCOMPLETE",
                "Not every market and timeframe in this setup was checked. "
                "Run the check again before approving.",
                stage="provider",
                status_code=409,
            )
        # 2 (second half). The compiled preview still hashes to what the user read.
        if definition.canonical_hash() != expected_schema_hash:
            raise SetupLaunchError(
                "SETUP_CHANGED",
                "The compiled preview changed. Review it again before approval.",
                stage="compile",
                status_code=409,
            )
        current = ReviewedScreeningEvidence.from_execution(
            screening=screening,
            manifest=manifest,
            reviewed_at=now,
        )
        reviewed = _load_reviewed_screening_evidence(chat)
        if reviewed is not None:
            changed = reviewed.differences_from(current)
            if changed:
                raise SetupLaunchError(
                    "SCREENING_EVIDENCE_STALE",
                    reviewed.describe_change(current)
                    + " Look at the updated setup before you approve it.",
                    stage="compile",
                    status_code=409,
                )
        elif screening is not None:
            # Screening ran, but nothing recorded what the user reviewed. Approving would
            # be approving an unread universe, so refuse and make them look at it.
            raise SetupLaunchError(
                "SCREENING_EVIDENCE_MISSING",
                "This setup has to be shown to you again before it can be approved.",
                stage="compile",
                status_code=409,
            )
        if screening is not None:
            # Absent evidence used to compare equal to absent evidence, so a review with
            # nothing recorded and an approval with nothing recorded agreed and the
            # approval went through. Presence is now checked on its own.
            missing = current.missing_evidence()
            if missing:
                raise SetupLaunchError(
                    "SCREENING_EVIDENCE_INCOMPLETE",
                    current.describe_missing(),
                    stage="compile",
                    status_code=409,
                )
        # The whole manifest, not only its hash: the worker reads the checked pairs from
        # here to decide which markets it must still check on every cycle.
        store_preflight_manifest(chat, manifest)
        return definition, current

    def _turn_stage_callback(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn: SetupChatTurn,
        *,
        message: str,
        source_turn_id: str,
        expected_executable_hash: str,
        expected_workflow_state_hash: str,
    ) -> Any:
        async def persist(stage: str, payload: dict[str, Any]) -> None:
            execution = payload.get("execution_result")
            if stage == TurnStatus.EXECUTING.value and not isinstance(execution, dict):
                # The model call has finished, so it is safe to hold this row lock only
                # across deterministic execution and its gates. This prevents two
                # different client-message IDs from both applying to the same stale
                # before-turn draft and losing one user's update.
                await session.refresh(chat, with_for_update=True)
                authoritative = load_strategy_draft_v2(chat)
                if (
                    authoritative.executable_hash != expected_executable_hash
                    or authoritative.workflow_state_hash != expected_workflow_state_hash
                ):
                    raise SetupLaunchError(
                        "SETUP_TURN_CONFLICT",
                        (
                            "The draft changed while this message was being understood. "
                            "Retry it against the latest draft."
                        ),
                        stage="patch",
                        retryable=True,
                        status_code=409,
                    )
            turn.status = stage
            turn.planner_model = str(payload.get("planner_model") or "") or None
            plan = payload.get("plan")
            if isinstance(plan, dict):
                turn.plan_json = plan
            if isinstance(execution, dict):
                result = SetupTurnExecutionResult.model_validate(execution)
                draft = StrategyDraftV2.model_validate(payload.get("draft_after"))
                conversation = SetupConversationContext.model_validate(
                    payload.get("conversation_after")
                )
                definition_payload = payload.get("definition")
                definition = (
                    StrategyDefinition.model_validate(definition_payload)
                    if isinstance(definition_payload, dict)
                    else None
                )
                history_snapshot = payload.get("history_snapshot")
                if bool(payload.get("material_change")) and isinstance(history_snapshot, dict):
                    await self._store_snapshot(
                        session,
                        chat,
                        history_snapshot,
                        source_turn_id=source_turn_id,
                    )
                context = dict(chat.context_json or {})
                if bool(payload.get("material_change")) and chat.status == "approved":
                    _archive_approval(chat, context, message)
                context.pop("strategy_draft_v2_history", None)
                context["strategy_draft_v2"] = draft.model_dump(mode="json")
                context["strategy_state_authority"] = "v2"
                context["launch_pipeline_version"] = "3.1"
                context["setup_conversation_context"] = conversation.model_dump(mode="json")
                context["last_semantic_diff"] = list(result.semantic_diff)
                context["last_execution_result"] = result.model_dump(mode="json")
                context["last_patch_source_turn_id"] = source_turn_id
                context["last_turn_failed"] = False
                context.pop("last_turn_failure", None)
                chat.context_json = context
                if not chat.original_idea and result.strategy_mutated:
                    chat.original_idea = message
                    chat.title = _title(message)
                await self._persist_draft_state(
                    session,
                    chat,
                    draft,
                    definition=definition,
                    execution=result,
                )
                if bool(payload.get("material_change")):
                    # The final public executable version is restorable immediately,
                    # not only after a later edit stores it as that turn's "before".
                    await self._store_snapshot(
                        session,
                        chat,
                        draft.model_dump(mode="json"),
                        source_turn_id=source_turn_id,
                    )
                turn.execution_result_json = {
                    "execution_result": execution,
                    "draft_after": draft.model_dump(mode="json"),
                    "conversation_after": conversation.model_dump(mode="json"),
                    "definition": definition_payload,
                    "material_change": bool(payload.get("material_change")),
                    # The exact words a retry will be answered with, written in the same
                    # transaction as the state they describe.
                    #
                    # Recovery used to *generate* this sentence when the retry arrived.
                    # Two retries arriving together each generated their own, and the
                    # answer a user got depended on when they pressed the button rather
                    # than on what the server did. Storing it here makes recovery a read.
                    RECOVERY_REPLY_KEY: deterministic_summary(result),
                }
                turn.mutation_committed = (
                    draft.executable_version != turn.executable_version_before
                    or draft.workflow_revision != turn.workflow_revision_before
                )
                turn.executable_version_after = draft.executable_version
                turn.workflow_revision_after = draft.workflow_revision
            await session.flush()
            if stage != TurnStatus.EXECUTING.value or isinstance(execution, dict):
                await session.commit()

        return persist

    async def _complete_db_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn: SetupChatTurn | None,
        *,
        reply: dict[str, Any] | None = None,
        assistant_message_id: UUID | None = None,
    ) -> None:
        if turn is None:
            return
        draft = load_strategy_draft_v2(chat)
        assistant = (
            await session.get(AISetupChatMessage, assistant_message_id)
            if assistant_message_id is not None
            else await session.scalar(
                select(AISetupChatMessage)
                .where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.role == "assistant",
                )
                .order_by(AISetupChatMessage.sequence.desc())
                .limit(1)
            )
        )
        if assistant is not None and (
            assistant.session_id != chat.id or assistant.role != "assistant"
        ):
            raise SetupLaunchError(
                "TURN_REPLY_MISMATCH",
                "The stored assistant reply did not belong to this turn.",
                stage="serialize",
                status_code=409,
            )
        turn.assistant_message_id = assistant.id if assistant is not None else None
        turn.reply_json = reply or (
            {
                "message": assistant.content,
                "payload": assistant.payload,
            }
            if assistant is not None
            else {}
        )
        # The execution checkpoint owns this flag. A no-change execution has evidence
        # but no committed mutation, and completing it must not rewrite that fact.
        turn.mutation_committed = turn.mutation_committed or (
            draft.executable_version != turn.executable_version_before
            or draft.workflow_revision != turn.workflow_revision_before
        )
        turn.executable_version_after = draft.executable_version
        turn.workflow_revision_after = draft.workflow_revision
        turn.status = TurnStatus.COMPLETED.value
        turn.failure_code = None
        turn.failure_stage = None
        turn.failure_retryable = None
        turn.completed_at = datetime.now(UTC)
        await session.flush()

    async def _get_or_create_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        client_message_id: str,
    ) -> SetupChatTurn:
        existing = await session.scalar(
            select(SetupChatTurn)
            .where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == client_message_id,
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        draft = load_strategy_draft_v2(chat)
        created = SetupChatTurn(
            chat_session_id=chat.id,
            client_message_id=client_message_id,
            status=TurnStatus.RECEIVED.value,
            executable_version_before=draft.executable_version,
            workflow_revision_before=draft.workflow_revision,
        )
        try:
            async with session.begin_nested():
                session.add(created)
                await session.flush()
        except IntegrityError:
            concurrent = await session.scalar(
                select(SetupChatTurn)
                .where(
                    SetupChatTurn.chat_session_id == chat.id,
                    SetupChatTurn.client_message_id == client_message_id,
                )
                .with_for_update()
            )
            if concurrent is None:
                raise
            return concurrent
        return created

    async def _store_snapshot(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        payload: dict[str, Any],
        *,
        source_turn_id: str | None,
    ) -> SetupChatDraftSnapshot:
        draft = StrategyDraftV2.model_validate(payload)
        existing = await session.scalar(
            select(SetupChatDraftSnapshot).where(
                SetupChatDraftSnapshot.chat_session_id == chat.id,
                SetupChatDraftSnapshot.user_id == chat.user_id,
                SetupChatDraftSnapshot.executable_version == draft.executable_version,
                SetupChatDraftSnapshot.executable_hash == draft.executable_hash,
            )
        )
        if existing is not None:
            return existing
        snapshot = SetupChatDraftSnapshot(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            source_turn_id=UUID(source_turn_id) if source_turn_id else None,
            executable_version=draft.executable_version,
            executable_hash=draft.executable_hash,
            draft_json=draft.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
        session.add(snapshot)
        await session.flush()
        return snapshot

    async def _snapshot_history(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> list[dict[str, Any]]:
        """Load only immutable snapshots owned by this user and chat.

        Legacy session JSON is imported once, validated fail-closed, then removed as a
        writable authority. Unprovable payloads are ignored and can never be restored.
        """

        context = dict(chat.context_json or {})
        legacy = context.pop("strategy_draft_v2_history", None)
        if isinstance(legacy, list):
            for item in legacy:
                if not isinstance(item, dict):
                    continue
                try:
                    await self._store_snapshot(
                        session,
                        chat,
                        item,
                        source_turn_id=None,
                    )
                except ValidationError:
                    continue
            context["strategy_snapshot_history_migrated_at"] = datetime.now(UTC).isoformat()
            chat.context_json = context
            await session.flush()
        current = load_strategy_draft_v2(chat)
        if current.condition_ast is not None:
            await self._store_snapshot(
                session,
                chat,
                current.model_dump(mode="json"),
                source_turn_id=None,
            )
        rows = list(
            await session.scalars(
                select(SetupChatDraftSnapshot)
                .where(
                    SetupChatDraftSnapshot.chat_session_id == chat.id,
                    SetupChatDraftSnapshot.user_id == chat.user_id,
                )
                .order_by(SetupChatDraftSnapshot.executable_version.asc())
            )
        )
        return [
            {
                "snapshot_id": str(item.id),
                "executable_version": item.executable_version,
                "draft": item.draft_json,
            }
            for item in rows
        ]

    async def _fail_db_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn: SetupChatTurn | None,
        *,
        code: str,
        stage: str,
        retryable: bool,
        details: tuple[str, ...] = (),
        started: float,
        telemetry: TurnTelemetry,
    ) -> None:
        with telemetry.stage("persistence"):
            if turn is not None:
                turn.status = (
                    TurnStatus.RETRYABLE_FAILURE.value
                    if retryable
                    else TurnStatus.PERMANENT_FAILURE.value
                )
                turn.failure_code = code
                turn.failure_stage = stage
                turn.failure_retryable = retryable
                turn.failure_details_json = [str(item)[:300] for item in details[:12]]
            await session.flush()
        # The measurement belongs to this exact idempotency record. Keep the session's
        # latest-turn compatibility view in sync, but never rely on that overwriteable
        # view as the durable history.
        _set_runtime(
            chat,
            started,
            model_calls=telemetry.model_calls,
            cache_hits=telemetry.cache_hits,
            telemetry=telemetry,
        )
        if turn is not None:
            turn.telemetry_json = telemetry.to_payload()
        await session.flush()
        await session.commit()

    async def _replayed_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        client_message_id: str,
    ) -> AISetupChatSession | None:
        """Answer a repeated key from the stored record instead of re-running the turn.

        The old check returned the chat as-is whenever the key had been seen, which meant
        a retry after a mid-turn crash returned a session with **no assistant answer** and
        no error — the user saw their message vanish. Now the stored status decides:

        * ``COMPLETED`` — the same final answer, no model call, no second patch
        * ``RETRYABLE_FAILURE`` — reprocess, because nothing was applied
        * ``PLANNING`` / ``EXECUTING`` — an in-progress conflict, never a silent no-op
        """
        record = await session.scalar(
            select(SetupChatTurn)
            .where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == client_message_id,
            )
            .with_for_update()
        )
        if record is None:
            existing = await session.scalar(
                select(AISetupChatMessage.id).where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.client_message_id == client_message_id,
                )
            )
            if existing is not None:
                raise SetupLaunchError(
                    "LEGACY_TURN_STATE_UNKNOWN",
                    (
                        "This older message has no durable turn result. Start a new "
                        "message rather than replaying an unknown state."
                    ),
                    stage="interpret",
                    status_code=409,
                )
            return None
        status = str(record.status or "")
        if status == TurnStatus.COMPLETED.value:
            chat.__dict__["_setup_replayed_turn"] = {
                "client_message_id": record.client_message_id,
                "source_message_id": (
                    str(record.source_message_id) if record.source_message_id else None
                ),
                "assistant_message_id": (
                    str(record.assistant_message_id) if record.assistant_message_id else None
                ),
                "reply": dict(record.reply_json or {}),
                "execution": dict(record.execution_result_json or {}),
            }
            return chat
        if status in {
            TurnStatus.COMPOSING.value,
            TurnStatus.RETRYABLE_FAILURE.value,
        } and isinstance(record.execution_result_json, dict):
            if record.assistant_message_id is not None and isinstance(record.reply_json, dict):
                record.status = TurnStatus.COMPLETED.value
                record.completed_at = record.completed_at or datetime.now(UTC)
                await session.flush()
                await session.commit()
                chat.__dict__["_setup_replayed_turn"] = {
                    "client_message_id": record.client_message_id,
                    "source_message_id": (
                        str(record.source_message_id) if record.source_message_id else None
                    ),
                    "assistant_message_id": str(record.assistant_message_id),
                    "reply": dict(record.reply_json),
                    "execution": dict(record.execution_result_json),
                }
                return chat
            stored = record.execution_result_json.get("execution_result")
            if isinstance(stored, dict):
                result = SetupTurnExecutionResult.model_validate(stored)
                # Read the reply the committing transaction already wrote. Regenerating
                # it here would let two retries of the same message produce two different
                # answers for one unchanged result.
                #
                # A turn committed before this field existed has none, so it is derived
                # once and written back, and every retry after that reads it.
                message = _recovery_reply(record, result)
                assistant = await self.owner._assistant(
                    session,
                    chat,
                    message,
                    message_type=_agent_message_type(result),
                    payload={
                        "execution_result": stored,
                        "draft_v2": record.execution_result_json.get("draft_after"),
                        "recovered_from_committed_turn": True,
                    },
                )
                await self._complete_db_turn(
                    session,
                    chat,
                    record,
                    reply={"message": message, "execution_result": stored},
                    assistant_message_id=assistant.id,
                )
                await session.commit()
                chat.__dict__["_setup_replayed_turn"] = {
                    "client_message_id": record.client_message_id,
                    "source_message_id": (
                        str(record.source_message_id) if record.source_message_id else None
                    ),
                    "assistant_message_id": (
                        str(record.assistant_message_id) if record.assistant_message_id else None
                    ),
                    "reply": dict(record.reply_json or {}),
                    "execution": dict(record.execution_result_json),
                }
                return chat
        if status in {
            TurnStatus.PLANNING.value,
            TurnStatus.EXECUTING.value,
            TurnStatus.COMPOSING.value,
        }:
            raise SetupLaunchError(
                "TURN_IN_PROGRESS",
                "That message is still being processed. Try again in a moment.",
                stage="interpret",
                retryable=True,
                status_code=409,
            )
        if status == TurnStatus.PERMANENT_FAILURE.value:
            raise SetupLaunchError(
                str(record.failure_code or "TURN_FAILED"),
                "That message could not be processed.",
                stage="interpret",
                status_code=422,
            )
        if status == TurnStatus.RETRYABLE_FAILURE.value:
            record.retry_count += 1
            record.status = TurnStatus.RECEIVED.value
            record.failure_code = None
            record.failure_stage = None
            record.failure_retryable = None
            await session.flush()
        # RECEIVED or retryable-without-mutation: reprocessing is safe.
        return None

    async def _governed_planner_references(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        draft: StrategyDraftV2,
    ) -> PlannerReferenceContext:
        """Expose public choices while retaining governed identities server-side."""

        methodologies = await ShariaScreeningService(
            session, self.settings
        ).selectable_market_methodologies()
        family_ids = {item.family_id for item in methodologies if item.family_id}
        families = {
            item.id: item
            for item in (
                list(
                    await session.scalars(
                        select(ShariaMethodologyFamily).where(
                            ShariaMethodologyFamily.id.in_(family_ids),
                            ShariaMethodologyFamily.is_active.is_(True),
                        )
                    )
                )
                if family_ids
                else []
            )
        }
        methodology_refs = tuple(
            MethodologyReference(
                reference=f"methodology_{index + 1}",
                public_identifier=item.code,
                public_name=item.name,
                family=(families[item.family_id].name if item.family_id in families else None),
                aliases=tuple(
                    value
                    for value in (
                        item.governing_body,
                        families[item.family_id].code if item.family_id in families else None,
                    )
                    if value
                ),
                methodology_id=str(item.id),
                methodology_version=item.version,
            )
            for index, item in enumerate(methodologies[:12])
        )

        watchlist_refs: list[WatchlistReference] = []
        watchlists = list(
            await session.scalars(
                select(ApprovedWatchlist)
                .where(ApprovedWatchlist.user_id == chat.user_id)
                .order_by(
                    ApprovedWatchlist.is_default.desc(),
                    ApprovedWatchlist.name.asc(),
                )
                .limit(12)
            )
        )
        for item in watchlists:
            try:
                content_identity = await watchlist_content_hash(
                    session,
                    item,
                    scope=scope_from_draft(draft),
                    require_resolved=True,
                )
            except WatchlistIdentityError:
                # An unresolved membership is not an executable planner choice.
                continue
            watchlist_refs.append(
                WatchlistReference(
                    reference=f"watchlist_{len(watchlist_refs) + 1}",
                    public_name=item.name,
                    aliases=("favorites", "favourites") if item.is_default else (),
                    watchlist_id=str(item.id),
                    watchlist_version=content_identity,
                )
            )
        return PlannerReferenceContext(
            methodologies=methodology_refs,
            watchlists=tuple(watchlist_refs),
        )

    async def _run_agent_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        source_turn_id: str,
        started: float,
        client_message_id: str | None = None,
        turn_record: SetupChatTurn | None = None,
    ) -> AISetupChatSession:
        """One free-text turn: plan, execute once, then answer from what happened."""

        # One clock for the turn, created here because this is where the authenticated
        # request begins. Every stage reads its remaining time from this object, so a
        # slow provider cannot borrow the time persistence still needs.
        telemetry = TurnTelemetry.start(self.settings.setup_turn_deadline_seconds)
        with telemetry.stage("request_acceptance"):
            # Authentication, ownership and request-shape checks have already run at
            # the service boundary; this marks their hand-off to the bounded turn.
            pass
        with telemetry.stage("context_selection"):
            draft = load_strategy_draft_v2(chat)
            context = dict(chat.context_json or {})
            conversation = _load_conversation_context(context)
            history = await self._snapshot_history(session, chat)
            planner_references = await self._governed_planner_references(session, chat, draft)
            context = dict(chat.context_json or {})
        stage_callback = None
        if turn_record is not None:
            canonical_stage_callback = self._turn_stage_callback(
                session,
                chat,
                turn_record,
                message=message,
                source_turn_id=source_turn_id,
                expected_executable_hash=draft.executable_hash,
                expected_workflow_state_hash=draft.workflow_state_hash,
            )

            async def stage_callback(stage: str, payload: dict[str, Any]) -> None:
                # The execution checkpoint is a real database write. Attribute its
                # elapsed time to persistence even though it occurs inside the broader
                # canonical-execution boundary.
                with telemetry.stage("persistence"):
                    await canonical_stage_callback(stage, payload)

        turn = SetupAgentTurnInput(
            telemetry=telemetry,
            message=message,
            source_turn_id=source_turn_id,
            draft=draft,
            session_id=str(chat.id),
            dialogue=tuple(
                await self._recent_dialogue(session, chat, exclude_message_id=source_turn_id)
            ),
            conversation=conversation,
            history=tuple(history),
            setup_mode=draft.mode,
            previous_turn_failed=bool(context.get("last_turn_failed")),
            # What has already failed in this chat, so a paid correction is not spent on
            # a class that has already survived one. Two attempts is evidence; a third is
            # a loop the user pays for in waiting.
            repeated_failure_codes=tuple(
                str(item) for item in (context.get("setup_failure_history") or [])[-6:]
            ),
            # Everything earlier turns already proved about this exact request. Without
            # it, a trader who restates the same instruction pays for a full planner
            # call every time and reaches the same refusal — eight times, in evaluator
            # run 20260803T000036Z.
            repeats=repeat_state(
                snapshot_history(context),
                canonical_draft_hash=draft.executable_hash or "",
                normalized_user_intent_hash=normalized_intent_hash(" ".join(message.split())),
            ),
            planner_references=planner_references,
            # Screening and provider availability are gates, so they run inside
            # execution and their outcome reaches the composer. Running them after the
            # reply let a message announce a draft the platform then blocked.
            screening=self._screening_gate(session, chat),
            providers=self._provider_gate(),
            runtime_preflight=self._runtime_preflight(),
            preflight_manifest=self._read_preflight_manifest,
            stage_callback=stage_callback,
        )
        try:
            outcome = await self.agent.run_turn(turn)
        except SetupAgentError as exc:
            # Planning can be paid and complete even when the server subsequently
            # rejects an ungrounded operation. Usage belongs to the attempted turn,
            # not only to successful mutations, and must be recorded before the
            # classified failure is returned.
            await CapabilityCoverageService(self.settings).record_usage(
                session,
                chat=chat,
                operation="setup_agent_turn",
                usage=exc.usage or None,
            )
            # The draft is untouched and stays exactly as it was. The turn is reported
            # as the failure it is, never as small talk, and the same idempotency key
            # can be retried.
            context["last_turn_failed"] = True
            context["last_turn_failure"] = {
                "code": exc.code,
                "stage": exc.stage,
                "retryable": exc.retryable,
                "details": list(exc.details[:6]),
            }
            context["setup_failure_history"] = [
                *(context.get("setup_failure_history") or []),
                exc.code,
            ][-6:]
            if exc.failure_record is not None:
                context["last_turn_failure"]["failure_class"] = (
                    exc.failure_record.failure_class.value
                )
                context["last_turn_failure"]["failure_owner"] = exc.failure_record.owner.value
                context["last_turn_failure"]["support_reference"] = (
                    exc.failure_record.support_reference
                )
                context["last_turn_failure"]["repair_decision"] = (
                    exc.failure_record.repair_decision
                )
                context["last_turn_failure"]["proof"] = exc.failure_record.to_dict()
                # A real compiler fault or a canonical refusal is a product defect, not
                # something the trader can fix by rewriting. It goes to an operator
                # queue; the customer gets a reference, never "please rephrase".
                if exc.operator_alertable:
                    context["setup_operator_review_queue"] = [
                        *(context.get("setup_operator_review_queue") or []),
                        exc.failure_record.to_dict(),
                    ][-20:]
                repeated_failure = int(
                    telemetry.notes.get("same_failure_repeat_count") or 0
                ) >= 1
                if exc.operator_alertable or repeated_failure:
                    try:
                        async with session.begin_nested():
                            await self._queue_operational_issue(
                                session,
                                chat,
                                turn_record,
                                proof=exc.failure_record.to_dict(),
                                repeated_failure=repeated_failure,
                            )
                    except SQLAlchemyError:
                        # The original Setup Chat failure remains authoritative. A queue
                        # write outage must never replace it or mutate the draft.
                        telemetry.notes["operator_queue_persist_failed"] = True
            # A failed turn still established most of what the trader wrote. Keeping it
            # is what lets the next turn answer without asking for everything again.
            _record_intent_snapshot(context, telemetry)
            _record_funnel(
                context,
                outcome="refused",
                telemetry=telemetry,
                failure_code=exc.code,
                model_calls=telemetry.model_calls,
            )
            chat.context_json = context
            # Persist the failure record and the measured session telemetry in the
            # same commit.  Committing the turn row first made the in-memory chat look
            # measured while a fresh database session saw no failure telemetry.
            await self._fail_db_turn(
                session,
                chat,
                turn_record,
                code=exc.code,
                stage=exc.stage,
                retryable=exc.retryable,
                details=exc.details,
                started=started,
                telemetry=telemetry,
            )
            raise SetupLaunchError(
                exc.code,
                str(exc),
                stage=_LAUNCH_STAGE_BY_AGENT_STAGE.get(exc.stage, "interpret"),
                retryable=exc.retryable,
                status_code=503 if exc.retryable else 422,
            ) from exc

        await CapabilityCoverageService(self.settings).record_usage(
            session,
            chat=chat,
            operation="setup_agent_turn",
            usage=outcome.usage or None,
        )

        if outcome.execution is None:
            # Conversation, product questions and explanations are read-only turns.
            # They must not compile, screen, refresh providers, rewrite derived UI or
            # disturb an approval.
            context["setup_conversation_context"] = outcome.conversation.model_dump(mode="json")
            context["last_turn_trace"] = outcome.trace.to_dict()
            context["last_turn_failed"] = False
            context.pop("last_turn_failure", None)
            context.pop("setup_failure_history", None)
            _record_funnel(
                context,
                outcome="answered",
                telemetry=telemetry,
                failure_code=None,
                model_calls=outcome.trace.model_calls,
            )
            chat.context_json = context
            with telemetry.stage("persistence"):
                assistant = await self.owner._assistant(
                    session,
                    chat,
                    outcome.message,
                    message_type="conversation",
                    payload={
                        "execution_result": None,
                        "segments": list(outcome.trace.segments),
                        "turn_trace": outcome.trace.to_dict(),
                        "model_call_count": outcome.trace.model_calls,
                    },
                )
                await self._complete_db_turn(
                    session,
                    chat,
                    turn_record,
                    reply={"message": outcome.message, "execution_result": None},
                    assistant_message_id=assistant.id,
                )
            _set_runtime(
                chat,
                started,
                model_calls=outcome.trace.model_calls,
                cache_hits=0,
                telemetry=telemetry,
            )
            if turn_record is not None:
                turn_record.telemetry_json = telemetry.to_payload()
            await session.flush()
            await session.commit()
            return chat

        # A durable turn checkpoint already stored the canonical mutation and archived
        # its immutable pre-change snapshot before the composer ran. The no-key fallback
        # below exists only for internal callers that cannot provide idempotency.
        checkpointed = bool(
            turn_record is not None and turn_record.execution_result_json is not None
        )
        if checkpointed:
            context = dict(chat.context_json or {})
        if not checkpointed and outcome.material_change and outcome.history_snapshot:
            await self._store_snapshot(
                session,
                chat,
                outcome.history_snapshot,
                source_turn_id=source_turn_id,
            )
            if chat.status == "approved":
                _archive_approval(chat, context, message)
        context["strategy_draft_v2"] = outcome.draft.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        context["launch_pipeline_version"] = "3.1"
        context["setup_conversation_context"] = outcome.conversation.model_dump(mode="json")
        context["last_turn_trace"] = outcome.trace.to_dict()
        context["last_turn_failed"] = False
        context.pop("last_turn_failure", None)
        context.pop("setup_failure_history", None)
        _record_intent_snapshot(context, telemetry)
        _record_funnel(
            context,
            outcome=(
                "changed"
                if outcome.execution is not None and outcome.execution.strategy_mutated
                else "no_change"
            ),
            telemetry=telemetry,
            failure_code=None,
            model_calls=outcome.trace.model_calls,
            asked_question=outcome.clarification is not None,
            approval_eligible=bool(
                outcome.execution is not None and outcome.execution.approval_eligible
            ),
        )
        if outcome.execution is not None:
            context["last_semantic_diff"] = list(outcome.execution.semantic_diff)
            context["last_execution_result"] = outcome.execution.model_dump(mode="json")
        context["last_patch_source_turn_id"] = source_turn_id
        # Marked complete in the same context write that stores the new draft, so a
        # crash cannot leave a turn applied but recorded as unfinished — or the reverse.
        chat.context_json = context
        if (
            not chat.original_idea
            and outcome.execution is not None
            and outcome.execution.strategy_mutated
        ):
            chat.original_idea = message
            chat.title = _title(message)

        # The execution result already decided every gate, including the final status.
        # Persisting it rather than re-deriving one is what stops the session and the
        # reply the user just read from disagreeing.
        with telemetry.stage("persistence"):
            state = await self._persist_draft_state(
                session,
                chat,
                outcome.draft,
                definition=outcome.definition,
                execution=outcome.execution,
            )
            assistant = await self.owner._assistant(
                session,
                chat,
                outcome.message,
                message_type=_agent_message_type(outcome.execution),
                payload={
                    "draft_v2": outcome.draft.model_dump(mode="json"),
                    "execution_result": (
                        outcome.execution.model_dump(mode="json")
                        if outcome.execution is not None
                        else None
                    ),
                    "segments": list(outcome.trace.segments),
                    "semantic_violations": list(state.violations),
                    "clarifications": (
                        [outcome.clarification.model_dump(mode="json")]
                        if outcome.clarification is not None
                        else []
                    ),
                    "can_approve": chat.status == "ready_for_approval",
                    "turn_trace": outcome.trace.to_dict(),
                    "model_call_count": outcome.trace.model_calls,
                },
            )
            await self._complete_db_turn(
                session,
                chat,
                turn_record,
                reply={
                    "message": outcome.message,
                    "execution_result": (
                        outcome.execution.model_dump(mode="json")
                        if outcome.execution is not None
                        else None
                    ),
                },
                assistant_message_id=assistant.id,
            )
        _set_runtime(
            chat,
            started,
            model_calls=outcome.trace.model_calls,
            cache_hits=0,
            telemetry=telemetry,
        )
        if turn_record is not None:
            turn_record.telemetry_json = telemetry.to_payload()
        await session.flush()
        await session.commit()
        return chat

    def _screening_gate(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> ScreeningGate | None:
        """The Sharia policy and screened-universe gate, as an execution-phase callable."""

        if not self.settings.sharia_screening_enforced:
            return None

        async def gate(
            definition: StrategyDefinition,
        ) -> tuple[ScreeningExecutionResult | None, str | None]:
            try:
                return await self._apply_screening_policy(session, chat, definition), None
            except (KeyError, ValueError, ShariaUniverseError) as exc:
                return None, str(exc) or "Choose and validate Halal Assets first."

        return gate

    def _read_preflight_manifest(self) -> PreflightManifest | None:
        """What the last preflight in this turn actually checked.

        ``None`` when no preflight ran, so an absent check is reported as absent instead
        of being read as a passed one.
        """
        return self._last_preflight_manifest

    def _provider_gate(self) -> ProviderGate:
        """Mark each provider requirement available or not, from configuration.

        A requirement the platform has no adapter for stays unavailable, which blocks
        approval transparently instead of compiling a rule that cannot be evaluated.
        """

        async def gate(
            requirements: list[ProviderRequirementV2],
        ) -> list[ProviderRuntimeStatusV2]:
            return [
                ProviderRuntimeStatusV2(
                    provider=item.provider,
                    capability=item.capability,
                    status=(
                        "available" if self._provider_available(item.provider) else "unavailable"
                    ),
                )
                for item in requirements
            ]

        return gate

    def _provider_available(self, provider: str) -> bool:
        """Only candle data is wired. Anything else blocks approval, visibly.

        Claiming an unwired feed is available would compile a rule that can never be
        evaluated, and the alert would simply never fire with no explanation.
        """
        name = provider.strip().casefold()
        return name in configured_runtime_provider_requirements(self.settings.market_data_provider)

    def _runtime_preflight(self) -> RuntimePreflight:
        """Verify the configured adapter for this exact exchange/symbol/timeframe set.

        Compilation and provider availability are separate facts. A transient provider
        failure leaves the draft compile-ready but runtime-unverified; a confirmed
        unsupported market blocks it.

        The universe reaching this function is always the **screened** one — the symbols
        the Sharia resolver permitted — because the caller passes
        :attr:`ScreeningExecutionResult.preflight_definition`, a throwaway copy carrying
        the resolved markets. The authored definition is never modified. It runs under one
        of two promises, chosen by size and recorded in the manifest so the reply, the
        stored preview and the approval record all state the same thing:

        ``verified_all``
            Every permitted symbol × every required timeframe was checked. Used whenever
            the universe is at or below ``setup_preflight_symbol_cap``, which covers
            explicit asset lists and ordinary Favorites lists.

        ``policy_verified_runtime_fail_closed``
            The universe is larger than the cap, or its membership can change without a
            user edit ("everything eligible", a shared Favorites list). The rules'
            timeframes and capabilities are verified against a bounded sample; each
            symbol is then checked when monitoring starts, and one without usable data is
            skipped, never guessed at.
        """

        async def preflight(
            definition: StrategyDefinition,
        ) -> list[ProviderRuntimeStatusV2]:
            # Clear first, always. A manifest describes exactly one check, and this
            # service instance is reused across calls within a request. Reading the cache
            # while a previous call's manifest was still in memory was how "these markets
            # are available" could be shown next to a promise made about other markets.
            self._last_preflight_manifest = None
            identity = self._runtime_preflight_identity(definition)
            key = f"hm:setup-agent:provider-preflight:{identity}"
            cached = await self._read_preflight_cache(key, identity)
            if cached is not None:
                # Restore the whole entry — statuses *and* the manifest they were
                # produced with — or use neither.
                self._last_preflight_manifest = cached.manifest
                return list(cached.statuses)

            checked_at = datetime.now(UTC)
            provider_name = type(self.owner.market_provider).__name__
            try:
                listed = await asyncio.wait_for(
                    self.owner.market_provider.list_symbols(
                        definition.universe.exchange,
                        definition.universe.quote_currencies,
                    ),
                    timeout=5,
                )
                normalized_listed = {_normalized_market_symbol(item) for item in listed}
                requested = [
                    _normalized_market_symbol(item) for item in definition.universe.include_symbols
                ]
                missing = [item for item in requested if item not in normalized_listed]
                statuses = [
                    ProviderRuntimeStatusV2(
                        provider=provider_name,
                        capability=f"market:{item}"[:120],
                        status="unavailable",
                        checked_at=checked_at,
                        safe_error="A selected market is unavailable.",
                    )
                    for item in missing
                ]
                # Which markets this preflight actually promises to have checked.
                #
                # The fallback used to be `sorted(listed)[:1]` — one arbitrary symbol,
                # after which the whole universe was reported runtime-ready. A Favorites
                # list of forty markets was "verified" by checking whichever symbol
                # sorted first, which might not even be in the list.
                #
                # The contract is now explicit and bounded, and it is recorded so the UI,
                # the execution result and the approval record all state the same promise.
                cap = self.settings.setup_preflight_symbol_cap
                # A universe whose membership can change on its own can never be promised
                # as `verified_all`, however small it is today. "Every resolved symbol was
                # checked" would be a claim about a set that will be different tomorrow,
                # and the runtime would then skip the per-symbol check it needs.
                dynamic = is_dynamic_membership(
                    definition.universe.sharia_policy.universe_mode
                    if definition.universe.sharia_policy
                    else None
                )
                if requested:
                    resolved = [item for item in requested if item in normalized_listed]
                    contract: PreflightContract = (
                        "verified_all"
                        if len(resolved) <= cap and not dynamic
                        else "policy_verified_runtime_fail_closed"
                    )
                    targets = resolved[:cap]
                    unverified = resolved[cap:]
                else:
                    # No explicit universe reached this preflight, so the draft runs over
                    # whatever the exchange lists. Nothing can be promised per symbol; a
                    # bounded sample proves the timeframes and capabilities work.
                    contract = "policy_verified_runtime_fail_closed"
                    sample = sorted(normalized_listed)
                    targets = sample[:cap]
                    unverified = sample[cap:]
                if not targets and not statuses:
                    statuses = [
                        ProviderRuntimeStatusV2(
                            provider=provider_name,
                            capability="exchange_symbol_timeframe_preflight",
                            status="unavailable",
                            checked_at=checked_at,
                            safe_error="No market is available for this exchange scope.",
                        )
                    ]
                    self._last_preflight_manifest = PreflightManifest(
                        contract="policy_verified_runtime_fail_closed",
                        unverified_symbols=unverified,
                        required_timeframes=list(
                            dict.fromkeys(
                                [
                                    definition.base_timeframe,
                                    *definition.supporting_timeframes,
                                ]
                            )
                        ),
                        symbol_cap=cap,
                        checked_at=checked_at,
                    )
                else:
                    semaphore = asyncio.Semaphore(self.settings.setup_preflight_max_concurrency)
                    timeframes = list(
                        dict.fromkeys(
                            [
                                definition.base_timeframe,
                                *definition.supporting_timeframes,
                            ]
                        )
                    )

                    async def verify_pair(
                        symbol: str,
                        timeframe: str,
                    ) -> ProviderRuntimeStatusV2:
                        capability = f"market:{symbol}:{timeframe}"[:120]
                        try:
                            async with semaphore:
                                candles = await asyncio.wait_for(
                                    self.owner.market_provider.fetch_ohlcv(
                                        definition.universe.exchange,
                                        symbol,
                                        timeframe,
                                        definition.universe.min_historical_candles,
                                    ),
                                    timeout=5,
                                )
                        except (TimeoutError, ConnectionError, OSError):
                            return ProviderRuntimeStatusV2(
                                provider=provider_name,
                                capability=capability,
                                status="unknown",
                                checked_at=checked_at,
                                safe_error=("Market-data runtime could not be verified."),
                            )
                        except Exception:
                            return ProviderRuntimeStatusV2(
                                provider=provider_name,
                                capability=capability,
                                status="unknown",
                                checked_at=checked_at,
                                safe_error=("Market-data runtime verification failed safely."),
                            )
                        pair_definition = definition.model_copy(
                            update={
                                "base_timeframe": timeframe,
                                "supporting_timeframes": [],
                            }
                        )
                        quality_checked_at = checked_at
                        if (
                            self.settings.app_env == "test"
                            and self.settings.allow_mock_providers
                            and provider_name == "FixtureMarketDataProvider"
                            and candles
                        ):
                            # The deterministic fixture has a frozen clock by design.
                            # Validate its ordering/history/completeness at that frozen
                            # instant; production and every non-fixture adapter always
                            # use the real current time for freshness.
                            quality_checked_at = candles[-1].timestamp + timeframe_duration(
                                timeframe
                            )
                        quality = assess_candle_data_quality(
                            pair_definition,
                            {timeframe: candles},
                            quality_checked_at,
                        )
                        return ProviderRuntimeStatusV2(
                            provider=provider_name,
                            capability=capability,
                            status="available" if quality.usable else "unavailable",
                            checked_at=checked_at,
                            safe_error=(
                                None
                                if quality.usable
                                else quality.safe_message
                                or ("Market data is stale or incomplete for a required timeframe.")
                            ),
                        )

                    statuses.extend(
                        list(
                            await asyncio.gather(
                                *(
                                    verify_pair(symbol, timeframe)
                                    for symbol in targets
                                    for timeframe in timeframes
                                )
                            )
                        )
                    )
                    # The manifest states exactly which pairs were checked and under
                    # which promise, so nobody downstream has to infer it.
                    manifest = PreflightManifest(
                        contract=contract,
                        verified_pairs=[
                            f"{symbol}@{timeframe}"
                            for symbol in targets
                            for timeframe in timeframes
                        ],
                        unverified_symbols=unverified,
                        required_timeframes=timeframes,
                        symbol_cap=cap,
                        checked_at=checked_at,
                    )
                    # The manifest travels on its own, never as a status row. A row in
                    # this list is an availability verdict that `_provider_status` reads
                    # and that approval re-checks for freshness; a record that always
                    # says "available" would be a row nothing can ever block on, and it
                    # would make the count of checked pairs wrong.
                    self._last_preflight_manifest = manifest
            except (TimeoutError, ConnectionError, OSError):
                statuses = [
                    ProviderRuntimeStatusV2(
                        provider=provider_name,
                        capability="exchange_symbol_timeframe_preflight",
                        status="unknown",
                        checked_at=checked_at,
                        safe_error="Market-data runtime could not be verified.",
                    )
                ]
            except Exception:
                statuses = [
                    ProviderRuntimeStatusV2(
                        provider=provider_name,
                        capability="exchange_symbol_timeframe_preflight",
                        status="unknown",
                        checked_at=checked_at,
                        safe_error="Market-data runtime verification failed safely.",
                    )
                ]
            await self._write_preflight_cache(
                key,
                identity,
                statuses,
                self._last_preflight_manifest,
                checked_at,
            )
            return statuses

        return preflight

    def _runtime_preflight_identity(self, definition: StrategyDefinition) -> str:
        """Everything that changes what a data check *means*, as one hash.

        Used both as the cache key and as the identity stored inside the entry, so a hit
        is only accepted when the stored identity matches the one recomputed now. Two
        separate values could disagree — a key collision, a shared Redis database, a
        changed key format — and the entry would then be read against a universe it was
        never produced for.

        Every field below changes the answer. Dropping any one of them would let a check
        of one thing be presented as a check of another.
        """
        universe = definition.universe
        payload = {
            "contract_version": _PREFLIGHT_CONTRACT_VERSION,
            "provider": type(self.owner.market_provider).__name__,
            "exchange": universe.exchange,
            "market_type": universe.market_type.value,
            "quotes": sorted(item.upper() for item in universe.quote_currencies),
            "resolved_symbol_set_hash": symbol_set_hash(list(universe.include_symbols)),
            "base_timeframe": definition.base_timeframe,
            "supporting_timeframes": sorted(definition.supporting_timeframes),
            "minimum_history": universe.min_historical_candles,
            "trigger_mode": definition.trigger_mode.value,
            "symbol_cap": self.settings.setup_preflight_symbol_cap,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    async def _read_preflight_cache(
        self,
        key: str,
        identity: str,
    ) -> PreflightCacheEntry | None:
        """One validated entry, or nothing. Never half of one.

        Every rejection below is silent and simply redoes the check. Raising here would
        turn a cache problem into a failed turn, and a diagnostic must never become the
        failure.
        """
        if self._preflight_redis is None:
            return None
        try:
            payload = await self._preflight_redis.get(key)
        except RedisError:
            return None
        if not payload:
            return None
        try:
            entry = PreflightCacheEntry.model_validate_json(payload)
        except (TypeError, ValueError, ValidationError):
            return None
        now = datetime.now(UTC)
        if not entry.matches(identity):
            return None
        if not entry.is_fresh(now):
            return None
        if not entry.statuses_are_fresh(
            now,
            ttl_seconds=self.settings.setup_provider_preflight_ttl_seconds,
        ):
            return None
        if not entry.manifest_is_intact():
            # Statuses without the manifest they were produced with are exactly the
            # combination that let a cache hit produce "available" plus no evidence.
            return None
        return entry

    async def _write_preflight_cache(
        self,
        key: str,
        identity: str,
        statuses: list[ProviderRuntimeStatusV2],
        manifest: PreflightManifest | None,
        checked_at: datetime,
    ) -> None:
        if self._preflight_redis is None:
            return
        if manifest is None:
            # Nothing to restore atomically, so nothing is cached. Caching statuses alone
            # would recreate the defect this replaces.
            return
        ttl = (
            self.settings.setup_provider_preflight_ttl_seconds
            if all(item.status == "available" for item in statuses)
            else min(30, self.settings.setup_provider_preflight_ttl_seconds)
        )
        entry = PreflightCacheEntry(
            definition_identity=identity,
            statuses=statuses,
            manifest=manifest,
            cached_at=checked_at,
            expires_at=checked_at + timedelta(seconds=ttl),
        )
        try:
            await self._preflight_redis.set(key, entry.model_dump_json(), ex=ttl)
        except RedisError:
            return

    async def _recent_dialogue(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        exclude_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """The last few turns, oldest first. Bounded, never the whole log.

        The current turn is excluded: it is already supplied as `current_user_turn`, and
        sending it twice invited the model to treat its own echo as prior context.
        """

        rows = await session.scalars(
            select(AISetupChatMessage)
            .where(AISetupChatMessage.session_id == chat.id)
            .order_by(AISetupChatMessage.sequence.desc())
            .limit(DIALOGUE_WINDOW_MAX + 1)
        )
        recent = [item for item in list(rows)[::-1] if str(item.id) != exclude_message_id]
        return [
            {"role": item.role, "content": (item.content or "")[:1500]}
            for item in recent
            if item.role in {"user", "assistant"} and (item.content or "").strip()
        ]

    async def _persist_draft_state(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        draft: StrategyDraftV2,
        *,
        definition: StrategyDefinition | None = None,
        execution: SetupTurnExecutionResult | None = None,
    ) -> _DraftRenderState:
        """Write every derived field the dashboard reads, and emit no message.

        When ``execution`` is supplied, every gate has already run inside the turn and
        its verdict is used as-is. Re-running the compiler and screening here was how the
        service could discover a *later* blocker that contradicted the reply the user had
        just read.
        """
        violations = (
            list(execution.semantic_violations) if execution else (validate_draft_semantics(draft))
        )
        compile_error: str | None = None
        screening_error: str | None = None
        reviewed_evidence: ReviewedScreeningEvidence | None = None
        if execution is not None:
            blocking = not execution.approval_eligible
            compile_error = (
                f"{execution.compile_status}: {execution.safe_errors[0]}"
                if execution.compile_status in {"blocked", "failed"} and execution.safe_errors
                else None
            )
            screening_error = (
                execution.safe_errors[0]
                if execution.screening_status == "blocked" and execution.safe_errors
                else None
            )
            # The turn already ran both gates and recorded what they found. Reading those
            # records — rather than screening again here — is what keeps the reply, the
            # stored preview and the approval binding describing one universe.
            reviewed_evidence = _reviewed_evidence_from_execution(execution)
        else:
            blocking = draft.blocking or bool(violations)
            if not blocking and definition is None:
                try:
                    definition = compile_strategy_draft_v2(draft)
                except StrategyV2CompileError as exc:
                    compile_error = f"{exc.code}: {exc}"
                    blocking = True
            if definition is not None and self.settings.sharia_screening_enforced:
                try:
                    screened = await self._apply_screening_policy(session, chat, definition)
                except (KeyError, ValueError, ShariaUniverseError) as exc:
                    screening_error = str(exc) or "Choose and validate Halal Assets."
                    definition = None
                    blocking = True
                else:
                    # What gets persisted is the **authored** policy. The screened markets
                    # travel in the evidence beside it, so a dynamic universe still
                    # re-resolves after approval instead of being frozen to this moment.
                    definition = screened.authored_definition
                    reviewed_evidence = ReviewedScreeningEvidence.from_execution(
                        screening=screened,
                        manifest=self._read_preflight_manifest(),
                        reviewed_at=datetime.now(UTC),
                    )

        chat.draft_schema_json = (
            definition.model_dump(mode="json") if definition is not None else None
        )
        # Record exactly which screening facts the user is about to see. Approval compares
        # against this, so "the universe may have changed since you looked" stops being an
        # assumption and becomes a check. Cleared whenever there is no preview to review.
        _store_reviewed_screening_evidence(chat, reviewed_evidence if definition else None)
        chat.unsupported_conditions = [
            {
                "code": item.key,
                "message": item.missing_contract,
                "source_fragment": item.source_fragment,
                "severity": "critical" if item.blocking else "warning",
            }
            for item in draft.unsupported_requirements
        ]
        chat.ambiguities = [
            {
                "code": item.key,
                "message": item.question,
                "source_fragment": item.source_fragment,
                "severity": "critical" if item.blocking else "warning",
            }
            for item in draft.unresolved_fields
        ]
        chat.lint_warnings = [
            {
                "code": error.split(":", 1)[0],
                "severity": "critical",
                "message": error,
            }
            for error in [
                *violations,
                *([compile_error] if compile_error else []),
                *([screening_error] if screening_error else []),
            ]
        ]
        chat.rule_confidence = _rule_confidence(draft)
        chat.assumptions = []
        chat.translation_sheet = _translation_sheet(draft)
        # Status is read from the validated approval binding first. Deriving it from the
        # compiler alone reset an already-approved session to `ready_for_approval` on a
        # turn that changed nothing, silently losing an approval the user had given.
        if execution is not None and execution.final_chat_status:
            chat.status = execution.final_chat_status
        elif _draft_is_approved(draft):
            chat.status = "approved"
        else:
            chat.status = (
                "needs_clarification"
                if blocking
                else "ready_for_approval"
            )
        # Every remaining field is exposed. Only the number of *questions* is capped:
        # hiding the rest left the user unable to see what the draft still needed.
        unresolved = [item for item in draft.unresolved_fields if item.blocking]
        return _DraftRenderState(
            blocking=blocking,
            violations=tuple(violations),
            unresolved=tuple(unresolved),
            definition=definition,
        )

    async def _apply_screening_policy(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        definition: StrategyDefinition,
        *,
        persist_snapshot: bool = False,
    ) -> ScreeningExecutionResult:
        """Resolve the universe, and keep the resolution apart from the policy.

        This used to resolve the universe, check that *something* survived, and then
        return the original unscreened definition. The permitted symbols were discarded,
        so runtime preflight, the preview and approval all worked on a different universe
        from the one screening had approved.

        The first fix wrote the permitted symbols into ``universe.include_symbols``. That
        made preview and preflight agree, and broke something worse: ``include_symbols``
        is what :meth:`ShariaUniverseResolver._technical_symbols` reads as the *authored*
        universe, so an ``eligible_market`` monitor became permanently pinned to the
        assets that were eligible on the day it was approved — the exact opposite of what
        that mode promises.

        So the authored definition is returned untouched, and the permitted symbols travel
        beside it in :class:`ScreeningExecutionResult`. Preview and the data check read the
        resolution; the persisted, approved, re-resolved object stays the policy.

        ``persist_snapshot`` is on only at approval, where the resolution becomes a stored
        compliance record. During a chat turn the universe is resolved for review, and
        writing a snapshot per keystroke would fill the audit trail with drafts.
        """
        policy = definition.universe.sharia_policy
        if policy is None or policy.methodology_id is None:
            raise ValueError("Choose a screening methodology before compiling.")
        resolution = await ShariaUniverseResolver(
            session,
            self.owner.market_provider,
            self.settings,
        ).resolve(
            definition,
            user_id=chat.user_id,
            persist_snapshot=persist_snapshot,
        )
        if not resolution.included_symbols:
            raise ValueError("No asset currently meets both the screening policy and market scope.")
        watchlist_hash: str | None = None
        if policy.approved_watchlist_id is not None:
            watchlist = await session.get(ApprovedWatchlist, policy.approved_watchlist_id)
            if watchlist is not None:
                try:
                    watchlist_hash = await watchlist_content_hash(
                        session,
                        watchlist,
                        scope=scope_from_definition(definition),
                        require_resolved=self.settings.is_deployed,
                    )
                except WatchlistIdentityError as exc:
                    # A Favorites list holding a market the platform cannot identify is
                    # not something to approve around. The message names the markets.
                    raise ValueError(str(exc)) from exc
        included_symbols = list(resolution.included_symbols)
        excluded_symbols = [item.symbol for item in resolution.excluded]
        return ScreeningExecutionResult(
            authored_definition=definition,
            resolution_snapshot_id=resolution.snapshot_id,
            resolution_snapshot_hash=resolution.snapshot_hash,
            policy_hash=resolution.policy_hash,
            resolved_at=resolution.resolved_at,
            # Everything the resolver looked at is what it kept plus what it turned away.
            considered_symbols=sorted({*included_symbols, *excluded_symbols}),
            included_symbols=included_symbols,
            excluded_symbols=excluded_symbols,
            methodology_id=resolution.methodology_id,
            methodology_version=resolution.methodology_version,
            watchlist_snapshot_hash=watchlist_hash,
        )


def load_strategy_draft_v2(chat: AISetupChatSession) -> StrategyDraftV2:
    context = dict(chat.context_json or {})
    payload = context.get("strategy_draft_v2")
    if isinstance(payload, dict):
        migrated_payload = dict(payload)
        if "sharia_policy" not in migrated_payload:
            policy = _legacy_sharia_policy(context)
            migrated_payload["sharia_policy"] = policy.model_dump(mode="json")
            migrated_payload["executable_version"] = (
                int(migrated_payload.get("executable_version") or 1) + 1
            )
            migrated_payload["approval"] = ApprovalBindingV2().model_dump(mode="json")
            migrated_payload["executable_hash"] = ""
            migrated_payload["workflow_state_hash"] = ""
            migrated_payload["unresolved_fields"] = [
                *list(migrated_payload.get("unresolved_fields") or []),
                *[
                    item.model_dump(mode="json")
                    for item in _migration_policy_unresolved(
                        policy,
                        workflow_revision=int(migrated_payload.get("workflow_revision") or 1),
                        legacy_context=context,
                    )
                ],
            ]
        draft = StrategyDraftV2.model_validate(migrated_payload)
    else:
        draft = migrate_legacy_draft(
            chat.draft_schema_json,
            setup_mode=str(context.get("setup_mode") or "monitor"),
            unsupported=chat.unsupported_conditions or [],
        )
        if any(context.get(key) is not None for key in _LEGACY_SHARIA_POLICY_KEYS):
            policy = _legacy_sharia_policy(context)
            draft = StrategyDraftV2.model_validate(
                draft.model_copy(
                    update={
                        "sharia_policy": policy,
                        "unresolved_fields": [
                            *draft.unresolved_fields,
                            *_migration_policy_unresolved(
                                policy,
                                workflow_revision=draft.workflow_revision,
                                legacy_context=context,
                            ),
                        ],
                        "executable_version": draft.executable_version + 1,
                        "approval": ApprovalBindingV2(),
                        "executable_hash": "",
                        "workflow_state_hash": "",
                    }
                ).model_dump(mode="json")
            )
    changed = context.get("strategy_draft_v2") != draft.model_dump(mode="json")
    context["strategy_draft_v2"] = draft.model_dump(mode="json")
    for key in _LEGACY_SHARIA_POLICY_KEYS:
        changed = context.pop(key, None) is not None or changed
    if changed:
        context["sharia_policy_authority"] = "strategy_draft_v2"
        chat.context_json = context
    return draft


_LEGACY_SHARIA_POLICY_KEYS = {
    "screened_universe_mode",
    "sharia_methodology_id",
    "sharia_methodology_code",
    "sharia_methodology_name",
    "sharia_methodology_version",
    "allowed_sharia_statuses",
    "qualification_policy",
    "disputed_asset_policy",
    "compliance_change_behavior",
    "approved_watchlist_id",
    "approved_watchlist_name",
    "approved_watchlist_version",
    "screened_explicit_symbols",
}


def _legacy_sharia_policy(context: dict[str, Any]) -> ShariaPolicyV2:
    """Move the legacy session policy into the one canonical executable owner."""

    mode_value = str(
        context.get("screened_universe_mode") or ShariaUniverseMode.ELIGIBLE_MARKET.value
    )
    try:
        universe_mode = ShariaUniverseMode(mode_value)
    except ValueError:
        universe_mode = ShariaUniverseMode.ELIGIBLE_MARKET
    allowed: list[ShariaAssetStatus] = []
    for value in context.get("allowed_sharia_statuses") or [
        ShariaAssetStatus.ELIGIBLE.value,
        ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
    ]:
        try:
            allowed.append(ShariaAssetStatus(value))
        except ValueError:
            continue
    if not allowed:
        allowed = [ShariaAssetStatus.ELIGIBLE]
    try:
        compliance_behavior = ComplianceChangeBehavior(
            context.get("compliance_change_behavior") or ComplianceChangeBehavior.PAUSE_ASSET.value
        )
    except ValueError:
        compliance_behavior = ComplianceChangeBehavior.PAUSE_ASSET
    return ShariaPolicyV2(
        universe_mode=universe_mode,
        methodology_id=context.get("sharia_methodology_id"),
        methodology_version=context.get("sharia_methodology_version"),
        allowed_statuses=allowed,
        qualification_policy=context.get("qualification_policy") or "include_with_warning",
        disputed_asset_policy=context.get("disputed_asset_policy") or "exclude",
        compliance_change_behavior=compliance_behavior,
        approved_watchlist_id=context.get("approved_watchlist_id"),
        approved_watchlist_version=context.get("approved_watchlist_version"),
        explicit_symbols=list(context.get("screened_explicit_symbols") or []),
    )


def _migration_policy_unresolved(
    policy: ShariaPolicyV2,
    *,
    workflow_revision: int,
    legacy_context: dict[str, Any] | None = None,
) -> list[UnresolvedFieldV2]:
    """Represent incomplete legacy policy as typed, visible, fail-closed work."""

    unresolved: list[UnresolvedFieldV2] = []
    if policy.universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST and (
        policy.approved_watchlist_id is None or not policy.approved_watchlist_version
    ):
        unresolved.append(
            UnresolvedFieldV2(
                unresolved_id="sharia.approved_watchlist",
                source_turn_id=None,
                source_fragment="Migrated legacy Setup Chat Sharia policy.",
                target_type="universe",
                target_field="sharia_policy.approved_watchlist_id",
                expected_answer_schema={"type": "string", "format": "uuid"},
                question="Which current Favorites list should HilalMarkets use?",
                reason=(
                    "The legacy setup did not preserve a complete immutable watchlist identity."
                ),
                created_workflow_revision=workflow_revision,
            )
        )
    if policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS and not policy.explicit_symbols:
        unresolved.append(
            UnresolvedFieldV2(
                unresolved_id="sharia.explicit_symbols",
                source_turn_id=None,
                source_fragment="Migrated legacy Setup Chat Sharia policy.",
                target_type="universe",
                target_field="sharia_policy.explicit_symbols",
                expected_answer_schema={
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                question="Which eligible spot assets should HilalMarkets watch?",
                reason="The legacy setup did not preserve an explicit screened asset list.",
                created_workflow_revision=workflow_revision,
            )
        )
    if legacy_context is not None:
        raw_mode = legacy_context.get("screened_universe_mode")
        if raw_mode is not None and str(raw_mode) not in {
            item.value for item in ShariaUniverseMode
        }:
            unresolved.append(
                UnresolvedFieldV2(
                    unresolved_id="sharia.universe_mode",
                    source_turn_id=None,
                    source_fragment="Migrated legacy Setup Chat Sharia policy.",
                    target_type="universe",
                    target_field="sharia_policy.universe_mode",
                    expected_answer_schema={
                        "type": "string",
                        "enum": [item.value for item in ShariaUniverseMode],
                    },
                    allowed_options=[item.value for item in ShariaUniverseMode],
                    question="Which screened universe should HilalMarkets use?",
                    reason=(
                        "The legacy setup stored an unrecognized universe mode, so "
                        "the policy cannot be approved until it is selected again."
                    ),
                    created_workflow_revision=workflow_revision,
                )
            )
        raw_statuses = legacy_context.get("allowed_sharia_statuses")
        known_statuses = {item.value for item in ShariaAssetStatus}
        if isinstance(raw_statuses, list) and any(
            str(item) not in known_statuses for item in raw_statuses
        ):
            unresolved.append(
                UnresolvedFieldV2(
                    unresolved_id="sharia.allowed_statuses",
                    source_turn_id=None,
                    source_fragment="Migrated legacy Setup Chat Sharia policy.",
                    target_type="universe",
                    target_field="sharia_policy.allowed_statuses",
                    expected_answer_schema={
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": sorted(known_statuses),
                        },
                        "minItems": 1,
                    },
                    question="Which governed Sharia statuses may this setup include?",
                    reason=("The legacy setup contained an unrecognized allowed status."),
                    created_workflow_revision=workflow_revision,
                )
            )
        raw_behavior = legacy_context.get("compliance_change_behavior")
        if raw_behavior is not None and str(raw_behavior) not in {
            item.value for item in ComplianceChangeBehavior
        }:
            unresolved.append(
                UnresolvedFieldV2(
                    unresolved_id="sharia.compliance_change_behavior",
                    source_turn_id=None,
                    source_fragment="Migrated legacy Setup Chat Sharia policy.",
                    target_type="universe",
                    target_field="sharia_policy.compliance_change_behavior",
                    expected_answer_schema={
                        "type": "string",
                        "enum": [item.value for item in ComplianceChangeBehavior],
                    },
                    allowed_options=[item.value for item in ComplianceChangeBehavior],
                    question=(
                        "What should happen if an included asset's compliance status changes?"
                    ),
                    reason=("The legacy setup stored an unrecognized compliance-change behavior."),
                    created_workflow_revision=workflow_revision,
                )
            )
    return unresolved


def _normalized_market_symbol(value: str) -> str:
    return value.upper().replace("-", "/").split(":", 1)[0].strip()


def _option_symbols(value: str, quote_asset: str) -> list[str]:
    tokens = re.findall(r"\b[A-Za-z0-9]{2,12}(?:[/_-][A-Za-z0-9]{2,12})?\b", value)
    ignored = {"AND", "OR", "THE", "ASSET", "ASSETS", "COIN", "COINS"}
    normalized: list[str] = []
    for token in tokens:
        compact = token.upper().replace("-", "/").replace("_", "/")
        if compact in ignored:
            continue
        symbol = compact if "/" in compact else f"{compact}/{quote_asset.upper()}"
        normalized.append(symbol)
    return list(dict.fromkeys(normalized))


def _archive_approval(
    chat: AISetupChatSession,
    context: dict[str, Any],
    changed_by: str,
) -> None:
    prior = list(context.get("previous_approvals") or [])
    prior.append(
        {
            "strategy_id": str(chat.approved_strategy_id) if chat.approved_strategy_id else None,
            "strategy_version_id": (
                str(chat.approved_strategy_version_id)
                if chat.approved_strategy_version_id
                else None
            ),
            "approved_at": chat.approved_at.isoformat() if chat.approved_at else None,
            "invalidated_by": changed_by[:500],
            "draft_v2": context.get("strategy_draft_v2"),
        }
    )
    context["previous_approvals"] = prior[-50:]
    chat.approved_at = None
    chat.approved_strategy_id = None
    chat.approved_strategy_version_id = None


class TurnStatus(StrEnum):
    """Where one keyed turn got to. Durable, so a retry knows what already happened."""

    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    COMPOSING = "COMPOSING"
    COMPLETED = "COMPLETED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


def _draft_is_approved(draft: StrategyDraftV2) -> bool:
    """True when the approval binding names this exact version and hash."""

    return (
        draft.approval.approved
        and draft.approval.executable_version == draft.executable_version
        and draft.approval.executable_hash == draft.executable_hash
    )


#: Where the reviewed screening facts live on the chat session.
REVIEWED_SCREENING_EVIDENCE_KEY = "reviewed_screening_evidence"

#: Where the market-data check that went with them lives. Stored whole, not just hashed,
#: because the worker needs the list of checked pairs to know which markets it still has
#: to check itself every cycle.
REVIEWED_PREFLIGHT_MANIFEST_KEY = "last_preflight_manifest"


def store_preflight_manifest(
    chat: AISetupChatSession,
    manifest: PreflightManifest | None,
) -> None:
    context = dict(chat.context_json or {})
    if manifest is None:
        context.pop(REVIEWED_PREFLIGHT_MANIFEST_KEY, None)
    else:
        payload = manifest.model_dump(mode="json")
        payload["manifest_hash"] = manifest.manifest_hash
        context[REVIEWED_PREFLIGHT_MANIFEST_KEY] = payload
    chat.context_json = context


#: Where the stored recovery reply lives inside a turn's committed execution payload.
RECOVERY_REPLY_KEY = "recovery_reply"


def _recovery_reply(record: SetupChatTurn, result: SetupTurnExecutionResult) -> str:
    """The exact answer a retry gets: read, not regenerated.

    Written when the turn committed. A turn that committed before this field existed has
    none, so it is derived once here and written back to the same record, which makes
    every later retry of that message a read as well.
    """

    payload = dict(record.execution_result_json or {})
    stored = payload.get(RECOVERY_REPLY_KEY)
    if isinstance(stored, str) and stored.strip():
        return stored
    message = deterministic_summary(result)
    payload[RECOVERY_REPLY_KEY] = message
    record.execution_result_json = payload
    return message


def _reviewed_evidence_from_execution(
    execution: SetupTurnExecutionResult,
) -> ReviewedScreeningEvidence | None:
    """Rebuild what the turn's own gates recorded, without screening again.

    The turn already resolved the universe and ran the market-data check, and stored both
    results. Re-running them here would mean the user reads one answer and the database
    keeps another.
    """

    screening = execution.screening_evidence or {}
    manifest = execution.preflight_manifest or {}
    if not screening and not manifest:
        return None
    return ReviewedScreeningEvidence(
        screening_snapshot_id=_optional_text(screening.get("resolution_snapshot_id")),
        screening_snapshot_hash=_optional_text(screening.get("resolution_snapshot_hash")),
        screening_policy_hash=_optional_text(screening.get("policy_hash")),
        methodology_id=_optional_text(screening.get("methodology_id")),
        methodology_version=_optional_text(screening.get("methodology_version")),
        resolved_symbol_set_hash=_optional_text(screening.get("resolved_symbol_set_hash")),
        secured_preview_hash=_optional_text(screening.get("secured_preview_hash")),
        watchlist_snapshot_hash=_optional_text(screening.get("watchlist_snapshot_hash")),
        provider_preflight_manifest_hash=_optional_text(manifest.get("manifest_hash")),
        preflight_contract=_optional_contract(manifest.get("contract")),
        membership_kind=_optional_membership_kind(screening.get("membership_kind")),
        included_symbol_count=_optional_count(screening.get("included_count")),
        dynamic_membership=bool(screening.get("dynamic_membership")),
        reviewed_at=datetime.now(UTC),
    )


def _optional_membership_kind(value: object) -> MembershipKind | None:
    """Read a stored membership kind back, or nothing. Never a guessed one."""

    if isinstance(value, str) and value in get_args(MembershipKind):
        return cast(MembershipKind, value)
    return None


def _optional_text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value.strip() else None


def _optional_count(value: object) -> int:
    """A count read back from a stored payload. Anything unusable reads as zero.

    This is display detail on an evidence record. Raising here would turn a cosmetic
    field into a failed approval, which is the "a diagnostic must never become the
    failure" rule.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _optional_contract(value: object) -> PreflightContract | None:
    allowed = get_args(PreflightContract)
    return cast(PreflightContract, value) if value in allowed else None


def _store_reviewed_screening_evidence(
    chat: AISetupChatSession,
    evidence: ReviewedScreeningEvidence | None,
) -> None:
    """Keep, or clear, the screening facts the user last reviewed."""

    context = dict(chat.context_json or {})
    if evidence is None:
        context.pop(REVIEWED_SCREENING_EVIDENCE_KEY, None)
    else:
        context[REVIEWED_SCREENING_EVIDENCE_KEY] = evidence.model_dump(mode="json")
    chat.context_json = context


def _load_reviewed_screening_evidence(
    chat: AISetupChatSession,
) -> ReviewedScreeningEvidence | None:
    """What the user last reviewed, or ``None`` when nothing was recorded.

    A payload that no longer parses reads as *nothing reviewed*, which refuses approval.
    Treating an unreadable record as "unchanged" would approve on evidence nobody can see.
    """

    payload = (chat.context_json or {}).get(REVIEWED_SCREENING_EVIDENCE_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return ReviewedScreeningEvidence.model_validate(payload)
    except ValidationError:
        return None


def _load_conversation_context(context: dict[str, Any]) -> SetupConversationContext:
    payload = context.get("setup_conversation_context")
    if isinstance(payload, dict):
        try:
            return SetupConversationContext.model_validate(payload)
        except ValidationError:
            # Language context is a convenience, never executable state. A stale shape
            # must not fail a turn; it starts empty instead.
            return SetupConversationContext()
    return SetupConversationContext()


def _agent_message_type(execution: SetupTurnExecutionResult | None) -> str:
    if execution is None:
        return "assistant_reply"
    return {
        "applied": "draft_updated",
        "no_change": "no_change",
        "blocked": "draft_blocked",
        "rejected": "instruction_rejected",
        "conversation_only": "assistant_reply",
    }.get(execution.status, "assistant_reply")


def _no_change_summary(draft: StrategyDraftV2) -> str:
    """Factual words for an answer that resolved nothing, built from real state."""

    pending = next((item for item in draft.unresolved_fields if item.blocking), None)
    if pending is not None:
        return f"That did not change the draft. Still needed: {pending.question}"
    return (
        f"That did not change the executable setup. It stays at version {draft.executable_version}."
    )


def _translation_sheet(draft: StrategyDraftV2) -> dict[str, Any]:
    conditions = []
    if draft.condition_ast is not None:
        for node in draft.condition_ast.walk():
            if node.node_type.value == "condition":
                conditions.append(
                    {
                        "key": node.node_id,
                        "name": node.formula.value.replace("_", " ").title()
                        if node.formula
                        else "Condition",
                        "role": "primary_trigger",
                        "required": node.required,
                        "timeframe": node.trigger_timeframe or "Not provided",
                        "operator": node.operator.value if node.operator else "Not provided",
                        "threshold": node.threshold,
                        "movement_direction": node.movement_direction.value,
                        "strategy_bias": node.strategy_bias.value,
                        "source_fragment": node.source_fragment,
                    }
                )
    return {
        "schema_version": "2.2",
        "executable_version": draft.executable_version,
        "workflow_revision": draft.workflow_revision,
        "monitor_name": draft.name,
        "exchange": draft.market_scope.exchange,
        "market_type": draft.market_scope.market_type,
        "quote_currencies": [draft.market_scope.quote_asset],
        "symbols_watchlist": draft.universe.included_symbols,
        "excluded_symbols": draft.universe.excluded_symbols,
        "conditions": conditions,
        "timeframes": list(
            dict.fromkeys(
                item["timeframe"] for item in conditions if item["timeframe"] != "Not provided"
            )
        ),
        "fields": [
            {"label": "Mode", "value": draft.mode.value.title()},
            {"label": "Name", "value": draft.name},
            {"label": "Exchange", "value": draft.market_scope.exchange.title()},
            {"label": "Quote asset", "value": draft.market_scope.quote_asset},
            {
                "label": "Included assets",
                "value": ", ".join(draft.universe.included_symbols) or "Selected Halal Assets",
            },
            {
                "label": "Excluded assets",
                "value": ", ".join(draft.universe.excluded_symbols) or "None",
            },
        ],
        "unresolved_fields": [item.model_dump(mode="json") for item in draft.unresolved_fields],
        "unsupported_requirements": [
            item.model_dump(mode="json") for item in draft.unsupported_requirements
        ],
        "assumptions": [],
        "execution": (
            "Inactive deterministic spot-market preview. Approval and activation "
            "remain separate authenticated actions."
        ),
    }


def _rule_confidence(draft: StrategyDraftV2) -> list[dict[str, Any]]:
    if draft.condition_ast is None:
        return []
    return [
        {
            "rule_key": item.node_id,
            "confidence": "high",
            "score": 1.0,
            "requires_confirmation": False,
        }
        for item in draft.condition_ast.walk()
        if item.node_type.value == "condition"
    ]


def _title(text: str) -> str:
    return (text[:72].rstrip(" ,.;:") or "New monitor")[:160]


#: What one turn did, from the user's point of view rather than the code's. These are the
#: only outcomes a turn can have, so a funnel row can never be uncategorised.
FUNNEL_OUTCOMES: tuple[str, ...] = ("changed", "no_change", "answered", "refused")

#: How many turns of history the funnel keeps per chat. Enough to see a user give up;
#: short enough that the session row stays small.
_FUNNEL_WINDOW = 40


def _record_intent_snapshot(context: dict[str, Any], telemetry: TurnTelemetry) -> None:
    """Persist what this turn established, whether or not the turn succeeded.

    The agent builds the snapshot as soon as it has a reading, and adds the failure to
    it if one happens. Storing it on both paths is the point: a refused turn is exactly
    the turn whose evidence must survive, or the trader is asked to type it all again.
    """

    payload = telemetry.notes.get("validated_intent_snapshot")
    if not isinstance(payload, dict):
        return
    append_snapshot(context, ValidatedIntentSnapshot.from_dict(payload))


def _record_funnel(
    context: dict[str, Any],
    *,
    outcome: str,
    telemetry: TurnTelemetry,
    failure_code: str | None,
    model_calls: int,
    asked_question: bool = False,
    approval_eligible: bool = False,
) -> None:
    """One row per turn: what the user got, how long they waited, what it cost.

    Abandonment is not a thing you can see in an error log — a user who gives up leaves
    no error. It is visible as a shape: turns that took too long, turns that asked a
    question and were never answered, turns refused twice for the same reason. This
    records the facts those questions need, per turn, on the session itself.

    Every field is measured or counted. Nothing here is inferred from a plan.
    """

    if outcome not in FUNNEL_OUTCOMES:
        raise ValueError(f"unknown turn outcome: {outcome}")
    rows = list(context.get("setup_turn_funnel") or [])
    previous = rows[-1] if rows else {}
    rows.append(
        {
            "turn_index": len(rows) + 1,
            "outcome": outcome,
            "failure_code": failure_code,
            "total_ms": round(telemetry.total_ms, 1),
            "server_ms": round(telemetry.server_ms, 1),
            "waiting_on_provider_ms": round(telemetry.external_wait_ms, 1),
            "model_calls": model_calls,
            "repaired": telemetry.stage_counts.get("repair_provider_wait", 0) > 0,
            "asked_question": asked_question,
            "approval_eligible": approval_eligible,
            "same_intent_retry_count": int(
                telemetry.notes.get("same_intent_retry_count") or 0
            ),
            "same_failure_repeat_count": int(
                telemetry.notes.get("same_failure_repeat_count") or 0
            ),
            # True when the previous turn asked something and this one is the answer.
            # A question that is never answered is the clearest signal of a user leaving.
            "answered_previous_question": bool(previous.get("asked_question")),
        }
    )
    context["setup_turn_funnel"] = rows[-_FUNNEL_WINDOW:]
    context["setup_funnel_summary"] = _funnel_summary(context["setup_turn_funnel"])


def _funnel_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The gates the brief measures, computed from the rows above and nothing else."""

    total = len(rows)
    if not total:
        return {}
    completed = [item for item in rows if item["outcome"] != "refused"]
    durations = sorted(float(item["total_ms"]) for item in rows)
    unanswered = sum(
        1
        for index, item in enumerate(rows)
        if item["asked_question"]
        and (index + 1 >= total or not rows[index + 1]["answered_previous_question"])
    )
    return {
        "turns": total,
        "completed_turns": len(completed),
        "refused_turns": total - len(completed),
        "single_model_call_share": round(
            sum(1 for item in rows if item["model_calls"] <= 1) / total, 4
        ),
        "repair_attempt_share": round(sum(1 for item in rows if item["repaired"]) / total, 4),
        "repeated_full_message_rate": round(
            sum(1 for item in rows if item.get("same_intent_retry_count", 0) > 0) / total,
            4,
        ),
        "user_resend_rate": round(
            sum(1 for item in rows if item.get("same_intent_retry_count", 0) > 0) / total,
            4,
        ),
        "failure_loop_turns": sum(
            1 for item in rows if item.get("same_failure_repeat_count", 0) >= 2
        ),
        "abandonment_after_failure": bool(
            rows[-1]["outcome"] == "refused"
            and (
                rows[-1].get("same_failure_repeat_count", 0) >= 2
                or rows[-1].get("asked_question", False)
            )
        ),
        "clean_first_turn": bool(rows[0]["outcome"] != "refused" and not rows[0]["repaired"]),
        "questions_asked": sum(1 for item in rows if item["asked_question"]),
        "questions_left_unanswered": unanswered,
        "reached_approval_eligible": any(item["approval_eligible"] for item in rows),
        "total_ms_p50": durations[len(durations) // 2],
        "total_ms_p95": durations[min(len(durations) - 1, int(len(durations) * 0.95))],
    }


def _set_runtime(
    chat: AISetupChatSession,
    started: float,
    *,
    model_calls: int,
    cache_hits: int,
    telemetry: TurnTelemetry | None = None,
) -> None:
    """Record what this turn actually spent, stage by stage.

    The measured breakdown is the point. A single total tells you a turn was slow; it
    never tells you which stage to fix, which is how a slow path acquires a larger
    timeout instead of less work. When no telemetry was collected the total is still
    written, so the shape of the record never changes.
    """

    context = dict(chat.context_json or {})
    measured = telemetry.to_payload() if telemetry is not None else {}
    stages = [
        {"stage": name, "duration_ms": value}
        for name, value in (measured.get("stage_ms") or {}).items()
    ]
    stages.append(
        {
            "stage": "launch_pipeline_total",
            "duration_ms": round((monotonic() - started) * 1000),
        }
    )
    context["turn_runtime"] = {
        "attach": True,
        "cache_hits": measured.get("cache_hits", cache_hits),
        "model_call_count": measured.get("model_calls", model_calls),
        "stages": stages,
        **({"measured": measured} if measured else {}),
    }
    chat.context_json = context
