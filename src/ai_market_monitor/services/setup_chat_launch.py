from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, Final, Literal, cast, get_args
from uuid import UUID, uuid4

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
    SetupChatPendingChange,
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
from ai_market_monitor.engine.active_clarification import stale_step
from ai_market_monitor.engine.active_question import (
    AnswerDomain,
    AnswerOutcome,
    display_options,
    labels_for,
    resolve_active_answer,
)
from ai_market_monitor.engine.builder_contract import disabled_capabilities_from, find_mechanic
from ai_market_monitor.engine.builder_operations import (
    BuilderActionError,
    add_condition_plan,
    arrange_plan,
    group_conditions_plan,
    move_condition_plan,
    remove_condition_plan,
    set_group_operator_plan,
    ungroup_conditions_plan,
    update_condition_plan,
)
from ai_market_monitor.engine.builder_starters import find_starter
from ai_market_monitor.engine.capability_shortlist import (
    configured_runtime_provider_requirements,
)
from ai_market_monitor.engine.change_review import build_draft_diff, build_pending_change
from ai_market_monitor.engine.clarification_continuation import governed_option_selection
from ai_market_monitor.engine.conversation_intent import selected_mode_word
from ai_market_monitor.engine.conversation_language import (
    governed_scan_error,
    language_of,
    localized,
    resolve_conversation_language,
    scan_error_is_resolvable,
    scanner_labels,
    scope_labels,
)
from ai_market_monitor.engine.destructive_change import (
    classify_destructive_change,
    may_be_destructive,
)
from ai_market_monitor.engine.draft_diff import diff_drafts
from ai_market_monitor.engine.plan_freshness import (
    FreshnessVerdict,
    PlanningAuthority,
    plan_freshness,
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
from ai_market_monitor.engine.setup_turn_lifecycle import (
    RecoveryAction,
    TurnStatus,
    holds_session,
    lease_seconds,
    recovery_policy,
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
from ai_market_monitor.schemas.request_identity import request_fingerprint
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
from ai_market_monitor.schemas.setup_authorization import (
    AuthorizedPatchOperation,
    ClarificationContract,
)
from ai_market_monitor.schemas.setup_change_review import (
    PENDING_CHANGE_TTL_MINUTES,
    PendingDestructiveChange,
    SetupDraftDiff,
)
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
from ai_market_monitor.services.ai_spend import AISpendGuard, AISpendRefused, TurnSpend
from ai_market_monitor.services.feature_control import Feature
from ai_market_monitor.services.market_preview import (
    assess_candle_data_quality,
    timeframe_duration,
)
from ai_market_monitor.services.on_demand_scans import OnDemandScanError
from ai_market_monitor.services.setup_chat_agent import (
    PROVIDER_FAILURE_CODES,
    SCAN_SCOPE_QUESTION,
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
    deterministic_summary,
    scan_window_contract,
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


def _launch_stage_for(code: str, agent_stage: str) -> LaunchStage:
    """Which stage the customer is told about.

    A provider outage belongs to the provider, whatever step happened to be
    running when it struck. The table above maps by step alone, so a model
    timeout during planning reached the customer as ``extract`` — the product
    saying it could not read the rules they had just written correctly. The
    request was fine; the model never answered.

    Only outages move. A model that answered with something unusable is still a
    failure of the step that asked for it, and keeps its own stage.
    """

    if code in PROVIDER_FAILURE_CODES:
        return "provider"
    return _LAUNCH_STAGE_BY_AGENT_STAGE.get(agent_stage, "interpret")

#: Bumped whenever the meaning of a stored data check changes — a new field in the
#: manifest, a different rule for choosing the contract, a change to what counts as
#: usable candles. Part of the cache identity, so old entries are never reinterpreted
#: under new rules; they simply stop matching and the check is redone.
_PREFLIGHT_CONTRACT_VERSION = 2

#: Which canonical requirement each server-rendered control authorises. A control is
#: the only place where "the person chose this" is known for certain, so it says so
#: explicitly rather than leaving reconciliation to infer choice from a changed value.
#: Re-selecting the value that is already stored is a choice too — "all eligible spot
#: assets" is also the platform default, and inferring from change alone left it
#: permanently unanswerable.
#: The one control every clarification option uses, whatever step it belongs to.
#:
#: Before this existed, only six governed controls had allowlisted keys. Every other
#: option button — a candle period, a reference point, a capability parameter — had no
#: key, so the client posted its visible *label* as an ordinary chat message and hoped
#: the label still parsed back to the value. A translated or reworded label silently
#: stopped answering its own question. One generic control removes that whole class:
#: the canonical value travels, the label is presentation, and the server validates the
#: value against the question that is actually open.
CLARIFICATION_ANSWER_OPTION: Final[str] = "clarification_answer"

#: Who authorises an Undo, a Restore, a Reset or a confirmed change. There is no user
#: sentence behind these — the user pressed a control the server drew — so the segment
#: names the server rather than quoting words nobody typed.
_DRAFT_ACTION_SEGMENT_ID: Final[str] = "server_draft_action"

#: Where the last turn's canonical before/after difference is kept for the client. One
#: key, written only from a real diff of two stored drafts.
LAST_DIFF_KEY: Final[str] = "last_draft_diff"


def _capability_keys_of(mechanic_key: str | None) -> frozenset[str]:
    """The registry key behind one guided control, if it has one.

    This is the shortlist for a Builder turn. It is not a waiver: the key came out of
    the platform's own catalogue when the control was drawn, so naming it here is
    evidence that the platform really offered it.
    """

    mechanic = find_mechanic(mechanic_key or "")
    if mechanic is None or not mechanic.capability_key:
        return frozenset()
    return frozenset({mechanic.capability_key})

#: The database constraint that stops two mutating turns owning one chat session. Named
#: here so a lost race can be recognised and answered as "wait", rather than escaping as
#: a server error the user can do nothing about.
_CLAIM_CONSTRAINT: Final[str] = "uq_setup_chat_turn_active_claim"

SERVER_OPTION_CONFIRMED_PATHS: Final[dict[str, frozenset[str]]] = {
    "screened_universe_mode": frozenset({"sharia_policy.universe_mode"}),
    "screened_watchlist": frozenset(
        {"sharia_policy.universe_mode", "sharia_policy.approved_watchlist_id"}
    ),
    "screened_explicit_assets": frozenset(
        {"sharia_policy.universe_mode", "sharia_policy.explicit_symbols"}
    ),
    "sharia_methodology": frozenset({"sharia_policy.methodology_id"}),
}


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
        #: Set only by ``TURN_IN_PROGRESS``, so the client can show which message is
        #: still running instead of guessing that its own send was lost.
        self.active_client_message_id: str | None = None
        self.active_stage: str | None = None


class _PendingChangeRequired(Exception):
    """Control flow: this turn produced a change too big to apply without asking.

    Raised from the execution checkpoint, which is the last moment before anything is
    written. It carries the stored proposal so the caller answers with the confirmation
    card rather than re-deciding anything.
    """

    def __init__(self, proposal: SetupChatPendingChange) -> None:
        super().__init__("pending destructive change")
        self.proposal = proposal


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
        client_message_id: str,
        answered_question_id: str | None = None,
        answered_step_revision: int | None = None,
    ) -> AISetupChatSession:
        started = monotonic()
        turn_record: SetupChatTurn | None = None
        # What this request actually asks for. Computed once, before anything routes on
        # it, so the replay check and the conflict check read the same identity.
        fingerprint = request_fingerprint(
            message=message,
            option_key=option_key,
            option_value=option_value,
            question_id=answered_question_id,
            step_revision=answered_step_revision,
        )
        if client_message_id:
            replay = await self._replayed_turn(
                session, chat, client_message_id, fingerprint=fingerprint
            )
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
        conversation = _load_conversation_context(dict(chat.context_json or {}))
        active = conversation.active_question
        # While a question is open, every message must say which question it was written
        # under — typed text included. The check runs here, before any routing decision,
        # because a stale or unidentified message must not reach the governed option
        # route *or* the agent: either one would apply yesterday's choice to today's
        # field, and the trader would never see it happen.
        if active is not None and answered_question_id is None:
            if not self.settings.setup_chat_allow_missing_answer_identity:
                return await self._refuse_unidentified_answer(session, chat, conversation)
            context = dict(chat.context_json or {})
            context["legacy_answers_without_identity"] = (
                int(context.get("legacy_answers_without_identity") or 0) + 1
            )
            chat.context_json = context
        if _answer_is_stale(
            chat,
            question_id=answered_question_id,
            step_revision=answered_step_revision,
        ):
            return await self._refuse_stale_answer(session, chat, client_message_id)
        # One generic control for every clarification option there is. Every step used to
        # need its own allowlisted key, so a timeframe or reference-point button had no
        # key at all and posted its visible *label* as ordinary chat text — which meant a
        # renamed or translated label silently stopped answering its own question. The
        # canonical value is carried here and validated against the question that is
        # A change the user has not answered yet blocks every other mutating message.
        # Letting a new instruction through would build on a draft the user believes is
        # about to be replaced, and the proposal would then be stale through no fault of
        # theirs. Nothing canonical moves here and no model is called.
        pending = await self._pending_change(session, chat)
        if pending is not None:
            return await self._refuse_while_pending(session, chat, pending)
        # actually open before anything else looks at it.
        if option_key == CLARIFICATION_ANSWER_OPTION:
            resolved = self._clarification_option(conversation, option_value or cleaned)
            if resolved is None:
                raise SetupLaunchError(
                    "CLARIFICATION_ANSWER_NOT_EXECUTABLE",
                    "That choice is not one this question can take. Please choose again.",
                    stage="intent",
                    status_code=422,
                )
            option_key, option_value, typed = resolved
            if option_key is None:
                # Not a governed control, so it takes the ordinary answer path with the
                # canonical value standing in for what the trader would have typed. Typed
                # and clicked therefore produce the same reading and the same operation.
                message, raw, cleaned, option_value = typed, typed, typed, None
        # Typing "Scanner" is the same choice as pressing the Scanner button, so it takes
        # the same governed route. It used to be answered conversationally instead, which
        # left `draft.mode` on Monitor — and the governed scan reads `draft.mode`, so the
        # very next market question refused itself for being in the wrong mode.
        if not option_key:
            typed_mode = selected_mode_word(cleaned)
            if typed_mode is not None:
                option_key, option_value = "setup_mode", typed_mode
        if not option_key:
            # Typing "all" while the scope question is open is the same choice as
            # pressing "All eligible spot assets", so it takes the same governed route.
            # One path, one recorded provenance, whichever way the trader answered.
            typed_scope = _typed_scope_answer(chat, cleaned)
            if typed_scope is not None:
                option_key, option_value = "screened_universe_mode", typed_scope
        if option_key:
            if client_message_id:
                turn_record = await self._get_or_create_turn(
                    session,
                    chat,
                    client_message_id,
                    fingerprint=fingerprint,
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
                    fingerprint=fingerprint,
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

    async def _pending_change(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> SetupChatPendingChange | None:
        """The proposal this session is waiting on, if it is still valid.

        An expired proposal is marked stale here rather than being quietly ignored. The
        user needs to be told their old change was not applied; silence would leave them
        believing it was.
        """

        row = await session.scalar(
            select(SetupChatPendingChange)
            .where(
                SetupChatPendingChange.chat_session_id == chat.id,
                SetupChatPendingChange.status == "pending",
            )
            .order_by(SetupChatPendingChange.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if datetime.now(UTC) >= expires:
            row.status = "stale"
            row.resolved_at = datetime.now(UTC)
            await session.flush()
            return None
        return row

    def _pending_is_stale(
        self,
        row: SetupChatPendingChange,
        draft: StrategyDraftV2,
    ) -> bool:
        """True when the draft moved after this proposal was offered.

        Both hashes are checked. An answered clarification moves only the workflow
        hash, and applying a stored operation set across that is exactly the "edit a
        condition that was deleted" case the proposal exists to prevent.
        """

        return (
            row.executable_hash != draft.executable_hash
            or row.workflow_state_hash != draft.workflow_state_hash
        )

    async def _refuse_while_pending(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        row: SetupChatPendingChange,
    ) -> AISetupChatSession:
        """Say plainly that a change is waiting, and do nothing else.

        No draft change, no workflow advance, no model call. The proposal travels back
        with the refusal so the client can render the same confirmation card again.
        """

        conversation = _load_conversation_context(dict(chat.context_json or {}))
        language = language_of(conversation.active_language)
        lines = [str(item) for item in (row.summary_json or [])]
        content = localized("change.pending_blocks_turn", language)
        rendered = content if not lines else content + "\n\n" + "\n".join(f"- {i}" for i in lines)
        await self.owner._assistant(
            session,
            chat,
            rendered,
            message_type="pending_change",
            payload={
                "error_code": "PENDING_CHANGE_AWAITING_CONFIRMATION",
                "proposal_id": row.proposal_id,
                "model_call_count": 0,
            },
        )
        await session.flush()
        await session.commit()
        return chat

    async def handle_draft_action(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        action: str,
        client_message_id: str,
        snapshot_id: str | None = None,
        expected_executable_version: int | None = None,
        proposal_id: str | None = None,
        confirmed: bool = False,
    ) -> AISetupChatSession:
        """Undo, Restore, Reset, or answer a pending change — all without a model.

        None of these ask the model to rebuild an old state. Undo and Restore replay an
        immutable snapshot the user already owns. Reset writes a known empty draft.
        Confirm replays operations that were authorized when the change was proposed.
        A model that reconstructed any of them would be inventing the past.

        Every one of them is keyed, so a double-clicked button acts once.
        """

        started = monotonic()
        fingerprint = request_fingerprint(
            message="",
            option_key=f"draft_action:{action}",
            option_value=snapshot_id or proposal_id or str(expected_executable_version or ""),
        )
        replay = await self._replayed_turn(
            session, chat, client_message_id, fingerprint=fingerprint
        )
        if replay is not None:
            return replay
        turn_record = await self._get_or_create_turn(
            session, chat, client_message_id, fingerprint=fingerprint
        )
        self._touch_stage(turn_record, TurnStatus.EXECUTING.value)
        turn_record.planner_model = "server_owned_draft_action"
        await session.flush()
        await session.commit()

        try:
            if action == "cancel_pending_change":
                return await self._cancel_pending_change(
                    session, chat, proposal_id or "", turn_record, started=started
                )
            if action == "confirm_pending_change":
                return await self._confirm_pending_change(
                    session, chat, proposal_id or "", turn_record, started=started
                )
            if action == "reset_current_draft":
                return await self._reset_draft(
                    session, chat, turn_record, confirmed=confirmed, started=started
                )
            return await self._restore_version(
                session,
                chat,
                turn_record,
                action=action,
                snapshot_id=snapshot_id,
                expected_executable_version=expected_executable_version,
                started=started,
            )
        except SetupLaunchError:
            # The claim must come back even when the action is refused, or the user is
            # locked out of their own chat by a message that never ran.
            self._release_claim(turn_record)
            turn_record.status = TurnStatus.RETRYABLE_FAILURE.value
            await session.flush()
            await session.commit()
            raise

    async def handle_builder_action(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        action: str,
        client_message_id: str,
        value: str | None = None,
        mechanic_key: str | None = None,
        values: dict[str, Any] | None = None,
        node_id: str | None = None,
        required: bool = True,
        order: list[str] | None = None,
        join: str | None = None,
        node_ids: list[str] | None = None,
        operator: str | None = None,
        group_id: str | None = None,
        position: int | None = None,
    ) -> AISetupChatSession:
        """One guided change, applied through the same authority a chat turn uses.

        Nothing here is a second way to write a draft. The operations are built by
        ``engine/builder_operations.py`` from fields the server drew, then handed to
        ``_apply_server_owned_operations`` — the identical path Undo, Restore and every
        option button already take. Screening, providers, Boolean topology, approval
        binding and version history all run exactly as they do for the assistant.

        Zero model calls. That is the point: a person must be able to finish a setup
        with the assistant switched off entirely.
        """

        if not self.settings.setup_builder_enabled:
            raise SetupLaunchError(
                "BUILDER_DISABLED",
                "The guided builder is switched off at the moment.",
                stage="intent",
                status_code=503,
            )
        started = monotonic()
        fingerprint = request_fingerprint(
            message="",
            option_key=f"builder_action:{action}",
            option_value=json.dumps(
                {
                    "value": value,
                    "mechanic_key": mechanic_key,
                    "values": values or {},
                    "node_id": node_id,
                    "required": required,
                    "order": order or [],
                    "join": join,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        replay = await self._replayed_turn(
            session, chat, client_message_id, fingerprint=fingerprint
        )
        if replay is not None:
            return replay
        turn_record = await self._get_or_create_turn(
            session, chat, client_message_id, fingerprint=fingerprint
        )
        self._touch_stage(turn_record, TurnStatus.EXECUTING.value)
        turn_record.planner_model = "server_owned_builder"
        await session.flush()
        await session.commit()

        try:
            draft = load_strategy_draft_v2(chat)
            source_turn_id = str(turn_record.id)
            operations, rendered, capability_keys = await self._builder_operations(
                session,
                chat,
                draft=draft,
                action=action,
                value=value,
                mechanic_key=mechanic_key,
                values=values or {},
                node_id=node_id,
                required=required,
                order=order or [],
                join=join,
                node_ids=node_ids or [],
                operator=operator,
                group_id=group_id,
                position=position,
                source_turn_id=source_turn_id,
            )
            if not operations:
                return await self._deterministic_action_reply(
                    session,
                    chat,
                    turn_record,
                    key="builder.nothing_changed",
                    started=started,
                    diff=None,
                )
            return await self._apply_server_owned_operations(
                session,
                chat,
                turn_record,
                operations=operations,
                history=await self._snapshot_history(session, chat),
                rendered=rendered,
                reply_key=None,
                started=started,
                allowed_capability_keys=capability_keys,
            )
        except BuilderActionError as exc:
            self._release_claim(turn_record)
            turn_record.status = TurnStatus.RETRYABLE_FAILURE.value
            await session.flush()
            await session.commit()
            raise SetupLaunchError(
                exc.code,
                str(exc),
                stage="patch",
                status_code=422,
            ) from exc
        except SetupLaunchError:
            # The claim must come back even when the change is refused, or the person is
            # locked out of their own setup by a click that never ran.
            self._release_claim(turn_record)
            turn_record.status = TurnStatus.RETRYABLE_FAILURE.value
            await session.flush()
            await session.commit()
            raise

    #: Guided steps that reuse the option-button path exactly. Both surfaces would
    #: otherwise grow their own copy of "what does choosing Scanner do", and the copies
    #: would stop agreeing the first time the screening rules changed.
    _BUILDER_OPTION_KEYS: Final[dict[str, str]] = {
        "select_mode": "setup_mode",
        "rename_plan": "monitor_name",
        "select_universe": "screened_universe_mode",
        "select_watchlist": "screened_watchlist",
        "set_explicit_assets": "screened_explicit_assets",
        "select_methodology": "sharia_methodology",
    }

    async def _builder_operations(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        draft: StrategyDraftV2,
        action: str,
        value: str | None,
        mechanic_key: str | None,
        values: dict[str, Any],
        node_id: str | None,
        required: bool,
        order: list[str],
        join: str | None,
        node_ids: list[str],
        operator: str | None,
        group_id: str | None,
        position: int | None,
        source_turn_id: str,
    ) -> tuple[list[AuthorizedPatchOperation], str, frozenset[str]]:
        """Build the canonical operations for one guided action."""

        option_key = self._BUILDER_OPTION_KEYS.get(action)
        if option_key is not None:
            operations = await self._server_option_operations(
                session,
                chat,
                draft=draft,
                option_key=option_key,
                option_value=value or "",
                source_turn_id=source_turn_id,
            )
            # `_server_option_operations` authorises against its own segment id, and the
            # draft-action path draws a different one. Re-point rather than duplicate the
            # builder: one producer, one meaning, two callers.
            repointed = [
                item.model_copy(update={"authorizing_segment_id": _DRAFT_ACTION_SEGMENT_ID})
                for item in operations
            ]
            return repointed, f"{action.replace('_', ' ')}: {value}", frozenset()

        if action == "add_condition":
            plan = add_condition_plan(
                mechanic_key=mechanic_key or "",
                values=values,
                source_turn_id=source_turn_id,
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
                required=required,
                configured_providers=self._configured_providers(),
                disabled_capabilities=self._disabled_capabilities(),
            )
            return list(plan.operations), plan.rendered, _capability_keys_of(mechanic_key)

        if action == "update_condition":
            self._require_existing_condition(draft, node_id or "")
            plan = update_condition_plan(
                node_id=node_id or "",
                mechanic_key=mechanic_key or "",
                values=values,
                source_turn_id=source_turn_id,
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
                required=required,
                configured_providers=self._configured_providers(),
                disabled_capabilities=self._disabled_capabilities(),
            )
            return list(plan.operations), plan.rendered, _capability_keys_of(mechanic_key)

        if action == "remove_condition":
            self._require_existing_condition(draft, node_id or "")
            plan = remove_condition_plan(
                node_id=node_id or "",
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        if action == "arrange_conditions":
            plan = arrange_plan(
                root=draft.condition_ast,
                order=order,
                join=join or "and",
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        # Boolean structure. Each of these targets exact stored nodes by id and rebuilds
        # the tree around them, so nested logic survives the edit. ``arrange_conditions``
        # above can only express one flat root join; on its own it flattened every group
        # a person had built.
        if action == "group_conditions":
            plan = group_conditions_plan(
                root=draft.condition_ast,
                node_ids=node_ids,
                operator=operator or "and",
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        if action == "ungroup_conditions":
            plan = ungroup_conditions_plan(
                root=draft.condition_ast,
                group_id=group_id or "",
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        if action == "set_group_operator":
            plan = set_group_operator_plan(
                root=draft.condition_ast,
                group_id=group_id or "",
                operator=operator or "and",
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        if action == "move_condition":
            self._require_existing_condition(draft, node_id or "")
            plan = move_condition_plan(
                root=draft.condition_ast,
                node_id=node_id or "",
                group_id=group_id or "",
                position=position,
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
            )
            return list(plan.operations), plan.rendered, frozenset()

        if action == "apply_starter":
            return await self._starter_operations(
                session,
                chat,
                draft=draft,
                starter_key=value or "",
                source_turn_id=source_turn_id,
            )

        raise SetupLaunchError(
            "BUILDER_ACTION_UNKNOWN",
            "That is not something the guided builder can do.",
            stage="intent",
            status_code=422,
        )

    @staticmethod
    def _require_existing_condition(draft: StrategyDraftV2, node_id: str) -> None:
        """Refuse an edit aimed at a rule that is not there.

        The rule may have been removed in another tab, or the page may be showing an
        older version. Either way the safe answer is to say so — applying the edit to
        whatever rule is there now would change something nobody pointed at.
        """

        existing = {
            node.node_id
            for node in (draft.condition_ast.walk() if draft.condition_ast else [])
        }
        if node_id not in existing:
            raise SetupLaunchError(
                "CONDITION_NOT_FOUND",
                "That rule is no longer part of this Watchlist. Reload and try again.",
                stage="patch",
                status_code=409,
            )

    async def _starter_operations(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        draft: StrategyDraftV2,
        starter_key: str,
        source_turn_id: str,
    ) -> tuple[list[AuthorizedPatchOperation], str, frozenset[str]]:
        """A starting point, expanded into the clicks it stands for.

        A starter is not a separate kind of setup. It produces the same operations the
        person's own choices would have produced, so there is nothing extra to execute
        and nothing extra that can go wrong.
        """

        starter = find_starter(starter_key)
        if starter is None:
            raise SetupLaunchError(
                "STARTER_UNKNOWN",
                "That starting point is not available.",
                stage="intent",
                status_code=404,
            )
        operations: list[AuthorizedPatchOperation] = []
        capability_keys: set[str] = set()
        if starter.mode != draft.mode.value:
            operations.extend(
                item.model_copy(update={"authorizing_segment_id": _DRAFT_ACTION_SEGMENT_ID})
                for item in await self._server_option_operations(
                    session,
                    chat,
                    draft=draft,
                    option_key="setup_mode",
                    option_value=starter.mode,
                    source_turn_id=source_turn_id,
                )
            )
        for index, rule in enumerate(starter.rules, start=1):
            plan = add_condition_plan(
                mechanic_key=rule.mechanic_key,
                values=dict(rule.values),
                source_turn_id=source_turn_id,
                segment_id=_DRAFT_ACTION_SEGMENT_ID,
                required=rule.required,
                configured_providers=self._configured_providers(),
                disabled_capabilities=self._disabled_capabilities(),
            )
            operations.extend(
                item.model_copy(update={"operation_id": f"{item.operation_id}_{index}"[:80]})
                for item in plan.operations
            )
            capability_keys |= _capability_keys_of(rule.mechanic_key)
        return operations, f"start from “{starter.label}”", frozenset(capability_keys)

    async def _restore_version(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn_record: SetupChatTurn,
        *,
        action: str,
        snapshot_id: str | None,
        expected_executable_version: int | None,
        started: float,
    ) -> AISetupChatSession:
        """Put back a version the user owns, as a new current version.

        Undo picks the target itself: the newest saved version below the current one.
        Restore is given the target. After that the two are the same operation, so
        there is one code path and one set of gates rather than two that can drift.
        """

        draft = load_strategy_draft_v2(chat)
        history = await self._snapshot_history(session, chat)
        owned = [
            item
            for item in history
            if isinstance(item.get("draft"), dict)
            and int(item.get("executable_version") or 0) > 0
        ]
        if action == "undo_last_material_change":
            # The newest version strictly below the current one. A conversation-only
            # turn never made a version, so it can never be an undo target — which is
            # exactly the requirement that "Undo ignores conversation-only turns".
            candidates = [
                item
                for item in owned
                if int(item["executable_version"]) < draft.executable_version
            ]
            if not candidates:
                return await self._deterministic_action_reply(
                    session,
                    chat,
                    turn_record,
                    key="change.nothing_to_undo",
                    started=started,
                    diff=None,
                )
            target = max(candidates, key=lambda item: int(item["executable_version"]))
        else:
            target = next(
                (item for item in owned if str(item.get("snapshot_id")) == str(snapshot_id)),
                {},
            )
            if not target:
                raise SetupLaunchError(
                    "SNAPSHOT_NOT_FOUND",
                    "That saved version is not available on this setup.",
                    stage="patch",
                    status_code=404,
                )
            if (
                expected_executable_version is not None
                and int(target["executable_version"]) != expected_executable_version
            ):
                raise SetupLaunchError(
                    "SNAPSHOT_VERSION_MISMATCH",
                    "That saved version has moved. Open the version list again.",
                    stage="patch",
                    status_code=409,
                )

        operation = AuthorizedPatchOperation(
            operation_id=f"draft_action_{action}",
            authorizing_segment_id=_DRAFT_ACTION_SEGMENT_ID,
            kind="restore_snapshot",
            target_snapshot_id=str(target["snapshot_id"]),
            target_executable_version=int(target["executable_version"]),
        )
        return await self._apply_server_owned_operations(
            session,
            chat,
            turn_record,
            operations=[operation],
            history=history,
            rendered=action.replace("_", " "),
            reply_key=(
                "change.undone" if action == "undo_last_material_change" else "change.restored"
            ),
            started=started,
        )

    async def _reset_draft(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn_record: SetupChatTurn,
        *,
        confirmed: bool,
        started: float,
    ) -> AISetupChatSession:
        """Return this draft to its starting state, keeping every saved version.

        Reset is deliberately *not* a planner operation. Nothing the user types can
        reach it, so no misreading of a sentence can clear their work.

        It also does exactly one thing. Archiving a Watch Plan, stopping live
        monitoring and deleting account data are separate actions with their own
        lifecycles, and collapsing them into this one is how a user loses more than
        they asked to.
        """

        draft = load_strategy_draft_v2(chat)
        already_empty = draft.condition_ast is None and not draft.unresolved_fields
        if not already_empty and not confirmed:
            raise SetupLaunchError(
                "RESET_CONFIRMATION_REQUIRED",
                "Clearing this draft removes the rules you have built. Confirm to continue.",
                stage="patch",
                status_code=428,
            )
        # Store what is there now, so reset itself can be undone.
        if draft.condition_ast is not None:
            await self._store_snapshot(
                session, chat, draft.model_dump(mode="json"), source_turn_id=None
            )
        fresh = StrategyDraftV2(
            draft_id=draft.draft_id,
            executable_version=draft.executable_version + 1,
            workflow_revision=draft.workflow_revision + 1,
        )
        before = draft
        context = dict(chat.context_json or {})
        context["strategy_draft_v2"] = fresh.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        # The conversation starts again with the draft. Leaving an open question behind
        # would ask about a rule that no longer exists.
        context["setup_conversation_context"] = SetupConversationContext().model_dump(mode="json")
        context.pop("last_execution_result", None)
        context.pop("last_semantic_diff", None)
        if chat.status == "approved":
            _archive_approval(chat, context, "reset draft")
        chat.context_json = context
        chat.status = "interviewing"
        await self._persist_draft_state(session, chat, fresh)
        return await self._deterministic_action_reply(
            session,
            chat,
            turn_record,
            key="change.reset",
            started=started,
            diff=build_draft_diff(before, fresh),
        )

    async def _cancel_pending_change(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        proposal_id: str,
        turn_record: SetupChatTurn,
        *,
        started: float,
    ) -> AISetupChatSession:
        """Throw the proposal away. The draft was never touched, so nothing reverts."""

        row = await self._owned_proposal(session, chat, proposal_id)
        row.status = "cancelled"
        row.resolved_at = datetime.now(UTC)
        await session.flush()
        return await self._deterministic_action_reply(
            session, chat, turn_record, key="change.cancelled", started=started, diff=None
        )

    async def _confirm_pending_change(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        proposal_id: str,
        turn_record: SetupChatTurn,
        *,
        started: float,
    ) -> AISetupChatSession:
        """Apply exactly the operations that were offered — no re-planning.

        Six things are checked before anything moves: the proposal exists, this user
        owns it, it has not expired, the draft is still the one it was built for, its
        stored operations still hash to what was shown, and it is still pending. A
        proposal that fails any of them is marked stale and refused, never applied to a
        draft it was not built against.
        """

        row = await self._owned_proposal(session, chat, proposal_id)
        draft = load_strategy_draft_v2(chat)
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        stale = datetime.now(UTC) >= expires or self._pending_is_stale(row, draft)
        if not stale:
            stored = PendingDestructiveChange.model_validate(
                {
                    "proposal_id": row.proposal_id,
                    "source_turn_id": str(row.source_turn_id or row.proposal_id),
                    "client_message_id": row.client_message_id or "",
                    "executable_hash": row.executable_hash,
                    "workflow_state_hash": row.workflow_state_hash,
                    "executable_version": row.executable_version,
                    "operations": row.operations_json,
                    "diff": row.diff_json,
                    "reasons": row.reasons_json,
                    "summary_lines": row.summary_json,
                    "invalidates_approval": row.invalidates_approval,
                    "governance_notes": row.governance_notes_json or [],
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "status": "pending",
                }
            )
            # The stored list must still be the list that was shown. A payload edited
            # between offering and confirming is a different change wearing the same id.
            stale = stored.operation_payload_hash != row.operation_payload_hash
        if stale:
            row.status = "stale"
            row.resolved_at = datetime.now(UTC)
            await session.flush()
            return await self._deterministic_action_reply(
                session,
                chat,
                turn_record,
                key="change.proposal_stale",
                started=started,
                diff=None,
            )

        operations = [
            AuthorizedPatchOperation.model_validate(item) for item in row.operations_json
        ]
        row.status = "confirmed"
        await session.flush()
        result = await self._apply_server_owned_operations(
            session,
            chat,
            turn_record,
            operations=operations,
            history=await self._snapshot_history(session, chat),
            rendered="confirm change",
            reply_key=None,
            started=started,
        )
        row.status = "applied"
        row.resolved_at = datetime.now(UTC)
        await session.flush()
        await session.commit()
        return result

    async def _owned_proposal(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        proposal_id: str,
    ) -> SetupChatPendingChange:
        """Load a proposal that belongs to this user and this chat, or refuse.

        Ownership is checked on both, not only on the id. A proposal id alone must
        never be enough to change somebody else's setup.
        """

        row = await session.scalar(
            select(SetupChatPendingChange)
            .where(
                SetupChatPendingChange.proposal_id == proposal_id,
                SetupChatPendingChange.chat_session_id == chat.id,
                SetupChatPendingChange.user_id == chat.user_id,
            )
            .with_for_update()
        )
        if row is None:
            raise SetupLaunchError(
                "PENDING_CHANGE_NOT_FOUND",
                "That change is no longer waiting for an answer.",
                stage="patch",
                status_code=404,
            )
        if row.status != "pending":
            raise SetupLaunchError(
                "PENDING_CHANGE_ALREADY_SETTLED",
                "That change was already answered.",
                stage="patch",
                status_code=409,
            )
        return row

    async def _apply_server_owned_operations(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn_record: SetupChatTurn,
        *,
        operations: list[AuthorizedPatchOperation],
        history: list[dict[str, Any]],
        rendered: str,
        reply_key: str | None,
        started: float,
        allowed_capability_keys: frozenset[str] = frozenset(),
    ) -> AISetupChatSession:
        """Run an operation list the server built, through the normal gates.

        ``server_owned_option`` skips *language* grounding, because there is no user
        sentence to ground against — the user pressed a button the server drew. Every
        other gate still runs: screening, providers, Boolean topology, approval
        binding. Skipping those would make this a way around them.

        ``allowed_capability_keys`` is the shortlist for this turn. For a server-drawn
        control it holds exactly the key behind the control that was pressed, taken from
        the platform's own registry — so the capability shortlist gate is satisfied by
        evidence rather than waived.
        """

        before = load_strategy_draft_v2(chat)
        segment = TurnSegment(
            segment_id=_DRAFT_ACTION_SEGMENT_ID,
            exact_source_text=rendered,
            start_offset=0,
            end_offset=len(rendered),
            kind=SegmentKind.CLARIFICATION_ANSWER,
            reply_required=False,
            action_required=True,
            confidence=1.0,
        )
        plan = SetupAgentTurnPlan(
            source_turn_id=str(turn_record.id),
            segments=[segment],
            operations=operations,
            overall_confidence=1.0,
        )
        outcome = await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=rendered,
                draft=before,
                source_turn_id=str(turn_record.id),
                allowed_capability_keys=allowed_capability_keys,
                history=history,
                conversation=_load_conversation_context(dict(chat.context_json or {})),
                screening=self._screening_gate(session, chat),
                providers=self._provider_gate(),
                runtime_preflight=self._runtime_preflight(),
                preflight_manifest=self._read_preflight_manifest,
                server_owned_option=True,
            )
        )
        context = dict(chat.context_json or {})
        if outcome.material_change and chat.status == "approved":
            _archive_approval(chat, context, rendered)
        context["strategy_draft_v2"] = outcome.draft.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        context["setup_conversation_context"] = outcome.conversation.model_dump(mode="json")
        context["last_execution_result"] = outcome.result.model_dump(mode="json")
        chat.context_json = context
        await self._persist_draft_state(
            session,
            chat,
            outcome.draft,
            definition=outcome.definition,
            execution=outcome.result,
        )
        await self._store_snapshot(
            session, chat, outcome.draft.model_dump(mode="json"), source_turn_id=None
        )
        return await self._deterministic_action_reply(
            session,
            chat,
            turn_record,
            key=reply_key,
            started=started,
            diff=build_draft_diff(before, outcome.draft),
            execution=outcome.result,
        )

    async def _deterministic_action_reply(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn_record: SetupChatTurn,
        *,
        key: str | None,
        started: float,
        diff: SetupDraftDiff | None,
        execution: SetupTurnExecutionResult | None = None,
    ) -> AISetupChatSession:
        """Answer a draft action from stored facts. Zero model calls, always."""

        conversation = _load_conversation_context(dict(chat.context_json or {}))
        language = conversation.active_language
        if key is not None:
            content = localized(key, language_of(language))
        elif execution is not None:
            content = deterministic_summary(execution, language=language)
        else:
            content = localized("change.cancelled", language_of(language))
        payload: dict[str, Any] = {
            "draft_v2": load_strategy_draft_v2(chat).model_dump(mode="json"),
            "model_call_count": 0,
            "server_owned_action": True,
        }
        if diff is not None:
            payload["draft_diff"] = diff.model_dump(mode="json")
        if execution is not None:
            payload["execution_result"] = execution.model_dump(mode="json")
        context = dict(chat.context_json or {})
        if diff is not None:
            context[LAST_DIFF_KEY] = diff.model_dump(mode="json")
            chat.context_json = context
        assistant = await self.owner._assistant(
            session,
            chat,
            content,
            message_type="draft_action",
            payload=payload,
        )
        await self._complete_db_turn(
            session,
            chat,
            turn_record,
            reply={"message": content, "execution_result": payload.get("execution_result")},
            assistant_message_id=assistant.id,
        )
        _set_runtime(chat, started, model_calls=0, cache_hits=0)
        await session.flush()
        await session.commit()
        return chat

    def _clarification_option(
        self,
        conversation: SetupConversationContext,
        value: str,
    ) -> tuple[str | None, str | None, str] | None:
        """Validate one clicked clarification answer against the question really open.

        Returns the allowlisted control to route to and the value it expects, or
        ``(None, None, canonical)`` when the answer takes the ordinary path. ``None``
        means the value is not one this question can execute — refused, never guessed at.

        The label the trader saw is presentation only and is never trusted here. The
        canonical value is authoritative *after* it has been checked against the open
        question, which is what makes a clicked answer exactly as safe as a typed one.
        """

        contract = conversation.active_question
        if contract is None:
            return None
        canonical = str(value or "").strip()
        if not canonical:
            return None
        permitted = set(contract.canonical_values) | set(contract.allowed_options)
        continuation = contract.continuation
        if continuation is not None and continuation.allowed_canonical_values:
            permitted |= set(continuation.allowed_canonical_values)
        if permitted and canonical not in permitted:
            return None
        selection = governed_option_selection(continuation, canonical)
        if selection is not None:
            return selection[0], selection[1], canonical
        return None, None, canonical

    async def _refuse_unidentified_answer(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        conversation: SetupConversationContext,
    ) -> AISetupChatSession:
        """Refuse a message that does not say which question it was written under.

        Nothing canonical moves and no model call is made. The current question and its
        identity go back with the refusal, so an out-of-date client can recover by
        re-reading the question rather than by guessing.
        """

        contract = conversation.active_question
        language = language_of(conversation.active_language)
        content = localized("ask.stale_answer", language)
        rendered = f"{content}\n\n{contract.question}" if contract is not None else content
        context = dict(chat.context_json or {})
        context["last_identity_required_refusal"] = True
        chat.context_json = context
        await self.owner._assistant(
            session,
            chat,
            rendered,
            message_type="clarification" if contract is not None else "text",
            payload={
                "clarification": (
                    contract.client_payload() if contract is not None else None
                ),
                "error_code": "ACTIVE_QUESTION_IDENTITY_REQUIRED",
                "active_question_id": contract.question_id if contract is not None else None,
                "active_step_revision": (
                    contract.step_revision if contract is not None else None
                ),
                "model_call_count": 0,
            },
        )
        await session.flush()
        await session.commit()
        return chat

    async def _refuse_stale_answer(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        client_message_id: str | None,
    ) -> AISetupChatSession:
        """Say the answer was for an earlier question, and show the current one.

        Nothing canonical moves: no draft change, no workflow advance, no model call.
        The trader sees where the setup actually is rather than a value silently landing
        on a field they were never asked about.
        """

        del client_message_id
        conversation = _load_conversation_context(dict(chat.context_json or {}))
        language = language_of(conversation.active_language)
        content = localized("ask.stale_answer", language)
        contract = conversation.active_question
        rendered = f"{content}\n\n{contract.question}" if contract is not None else content
        context = dict(chat.context_json or {})
        context["last_stale_answer_refused"] = True
        chat.context_json = context
        await self.owner._assistant(
            session,
            chat,
            rendered,
            message_type="clarification" if contract is not None else "text",
            payload={
                "clarification": (
                    contract.client_payload() if contract is not None else None
                ),
                "stale_answer_refused": True,
            },
        )
        await session.flush()
        await session.commit()
        return chat

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
                server_option_confirmed_paths=SERVER_OPTION_CONFIRMED_PATHS.get(
                    option_key, frozenset()
                ),
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

        # Continue a fully specified current-market query immediately after the
        # server-owned mode/scope choice closes its last governed dependency.
        context = dict(chat.context_json or {})
        conversation = _load_conversation_context(context)
        pending_scan = dict(conversation.pending_read_only_scan or {})
        scan_scope_option = option_key in {
            "setup_mode",
            "screened_universe_mode",
            "screened_watchlist",
            "screened_explicit_assets",
            "sharia_methodology",
        }
        if (
            pending_scan
            and scan_scope_option
            and pending_scan.get("measurement_window") == "24h"
            and _scan_draft_scope_ready(outcome.draft)
            and outcome.draft.mode == DraftMode.SCANNER
        ):
            return await self._finish_governed_percentage_scan_turn(
                session,
                chat,
                draft=outcome.draft,
                conversation=conversation,
                request=pending_scan,
                trace_payload={
                    "source_turn_id": str(user_message.id),
                    "planner_model": "server_owned_option",
                    "model_call_count": 0,
                    "patch_validation": "pending_read_only_scan_continued",
                    "active_language": conversation.active_language,
                },
                model_calls=0,
                context=context,
                started=started,
                telemetry=TurnTelemetry.start(self.settings.setup_turn_deadline_seconds),
                turn_record=turn_record,
                execution_result=outcome.result.model_dump(mode="json"),
            )

        content, message_type, payload = await self._server_option_reply(
            session,
            chat,
            option_key=option_key,
            draft=outcome.draft,
            execution=outcome.result,
        )
        active_question_payload = payload.pop("_active_question", None)
        conversation = _load_conversation_context(dict(chat.context_json or {}))
        if isinstance(active_question_payload, dict):
            active_question = ClarificationContract.model_validate(active_question_payload)
            conversation = conversation.with_question(active_question)
        fingerprint = _chat_response_fingerprint(content)
        conversation = conversation.model_copy(
            update={
                "last_assistant_summary": content[:1000],
                "last_response_fingerprint": fingerprint,
            }
        )
        context = dict(chat.context_json or {})
        context["setup_conversation_context"] = conversation.model_dump(mode="json")
        chat.context_json = context
        assistant = await self.owner._assistant(
            session,
            chat,
            content,
            message_type=message_type,
            payload={
                **payload,
                "draft_v2": outcome.draft.model_dump(mode="json"),
                "execution_result": outcome.result.model_dump(mode="json"),
                "active_language": conversation.active_language,
                "response_fingerprint": fingerprint,
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
                                question="Which screened assets should Hilal Markets watch?",
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
                            question="Which Favorites list should Hilal Markets use?",
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
                            question=("Which eligible spot assets should Hilal Markets watch?"),
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
        conversation = _load_conversation_context(dict(chat.context_json or {}))
        language = conversation.active_language
        if option_key == "setup_mode" and self.settings.sharia_screening_enforced:
            if draft.sharia_policy.methodology_id is None:
                return (
                    localized("scope.methodology_unavailable", language_of(language)),
                    "screening_unavailable",
                    {"can_approve": False, "can_scan": False},
                )
            labels = scope_labels(language_of(language))
            options = [
                {
                    "key": "screened_universe_mode",
                    "label": labels["eligible_market"],
                    "value": ShariaUniverseMode.ELIGIBLE_MARKET.value,
                },
                {
                    "key": "screened_universe_mode",
                    "label": labels["approved_watchlist"],
                    "value": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                },
                {
                    "key": "screened_universe_mode",
                    "label": labels["explicit_assets"],
                    "value": ShariaUniverseMode.EXPLICIT_ASSETS.value,
                },
            ]
            return (
                localized("ask.universe", language_of(language)),
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
                    localized("scope.watchlist_missing", language_of(language)),
                    "screened_watchlist_missing",
                    {"can_approve": False, "can_scan": False},
                )
            return (
                localized("ask.watchlist", language_of(language)),
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
                localized("ask.explicit_assets", language_of(language)),
                "screened_assets_required",
                {"awaiting_answer": True, "can_approve": False},
            )

        pending_scan = dict(conversation.pending_read_only_scan or {})
        scope_option = option_key in {
            "setup_mode",
            "screened_universe_mode",
            "screened_watchlist",
            "screened_explicit_assets",
            "sharia_methodology",
        }
        if (
            pending_scan
            and scope_option
            and _scan_draft_scope_ready(draft)
            and not pending_scan.get("measurement_window")
        ):
            question = localized("ask.scan_window_24h", language_of(language))
            # One builder for this question. This was the third copy of it, and the
            # copies disagreed: this one named no field and no canonical values, so only
            # the literal string `24h` could answer it and "24 hours" fell through as a
            # brand new request with no market in it.
            clarification = scan_window_contract(question, f"{chat.id}:pending-scan-window")
            return (
                localized("scope.selected", language_of(language)),
                "scanner_window_required",
                {
                    "clarifications": [clarification.client_payload()],
                    "_active_question": clarification.model_dump(mode="json"),
                    "can_scan": False,
                },
            )
        return (
            deterministic_summary(execution, language=language),
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

    async def _answer_with_proposal(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn_record: SetupChatTurn | None,
        *,
        proposal: SetupChatPendingChange,
        started: float,
        telemetry: TurnTelemetry | None = None,
    ) -> AISetupChatSession:
        """Show exactly what the change would do, and change nothing.

        The sentences come from the canonical diff. The assistant is not asked to
        describe its own work here, because a planner that misread the instruction would
        also misdescribe the result.
        """

        conversation = _load_conversation_context(dict(chat.context_json or {}))
        language = language_of(conversation.active_language)
        lines = [str(item) for item in (proposal.summary_json or [])]
        notes = [str(item) for item in (proposal.governance_notes_json or [])]
        body = "\n".join(f"- {item}" for item in lines + notes)
        content = localized("change.confirm_required", language)
        rendered = f"{content}\n\n{body}" if body else content
        assistant = await self.owner._assistant(
            session,
            chat,
            rendered,
            message_type="pending_change",
            payload={
                "pending_change": {
                    "proposal_id": proposal.proposal_id,
                    "reasons": list(proposal.reasons_json or []),
                    "summary_lines": lines,
                    "governance_notes": notes,
                    "invalidates_approval": bool(proposal.invalidates_approval),
                    "diff": proposal.diff_json,
                    "expires_at": proposal.expires_at.isoformat(),
                },
                "draft_v2": load_strategy_draft_v2(chat).model_dump(mode="json"),
                # Nothing was applied. Saying so in the payload stops a client from
                # rendering this as a completed change.
                "strategy_mutated": False,
                "model_call_count": telemetry.model_calls if telemetry is not None else 0,
            },
        )
        await self._complete_db_turn(
            session,
            chat,
            turn_record,
            reply={"message": rendered, "pending_change_id": proposal.proposal_id},
            assistant_message_id=assistant.id,
        )
        if turn_record is not None:
            # The turn finished honestly: it produced a proposal, not a mutation.
            turn_record.mutation_committed = False
        _set_runtime(
            chat,
            started,
            model_calls=telemetry.model_calls if telemetry is not None else 0,
            cache_hits=telemetry.cache_hits if telemetry is not None else 0,
            telemetry=telemetry,
        )
        await session.flush()
        await session.commit()
        return chat

    async def _propose_if_destructive(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        turn: SetupChatTurn,
        *,
        draft: StrategyDraftV2,
        plan_payload: object,
        message: str,
        source_turn_id: str,
    ) -> SetupChatPendingChange | None:
        """Turn a big change into a proposal instead of applying it.

        Two steps, in this order, because the second is real work:

        1. A cheap look at the typed operation list. Almost every turn adds one rule or
           answers one question, and those can stop here.
        2. Only if that says "maybe": run the operations against a **copy** of the draft
           to see exactly what they would do, then classify from that real difference.

        The copy is what makes the shown diff trustworthy. It is the outcome confirming
        will produce, not a description of one. Nothing is written to the session here —
        the projection runs on a detached draft and its result is thrown away except for
        the diff.

        Returns the stored proposal, or ``None`` when the change may simply be applied.
        """

        if not isinstance(plan_payload, dict):
            return None
        try:
            plan = SetupAgentTurnPlan.model_validate(plan_payload)
        except ValidationError:
            return None
        kinds = tuple(item.kind for item in plan.operations)
        if not kinds or not may_be_destructive(plan.operations):
            return None

        projection = await apply_setup_turn(
            SetupTurnRequest(
                plan=plan,
                message=message,
                draft=draft,
                source_turn_id=source_turn_id,
                allowed_capability_keys=frozenset(),
                history=await self._snapshot_history(session, chat),
                conversation=_load_conversation_context(dict(chat.context_json or {})),
                screening=self._screening_gate(session, chat),
                providers=self._provider_gate(),
                runtime_preflight=self._runtime_preflight(),
                preflight_manifest=self._read_preflight_manifest,
                # The plan was already grounded once, when it was built. Re-grounding a
                # projection would refuse it for wording reasons that have nothing to do
                # with whether the change is destructive.
                server_owned_option=True,
            )
        )
        verdict = classify_destructive_change(
            before=draft,
            after=projection.draft,
            changes=diff_drafts(draft, projection.draft),
            operation_kinds=kinds,
            referenced_condition_ids=tuple(
                item.target_condition_id
                for item in plan.operations
                if item.target_condition_id and item.kind != "remove_condition"
            ),
        )
        if not verdict.requires_confirmation:
            return None

        pending = build_pending_change(
            proposal_id=uuid4().hex,
            source_turn_id=str(turn.id),
            client_message_id=str(turn.client_message_id or ""),
            draft=draft,
            projected=projection.draft,
            operations=list(plan.operations),
            verdict=verdict,
            governance_notes=self._governance_notes(draft, projection.draft),
            ttl_minutes=PENDING_CHANGE_TTL_MINUTES,
        )
        row = SetupChatPendingChange(
            chat_session_id=chat.id,
            user_id=chat.user_id,
            proposal_id=pending.proposal_id,
            source_turn_id=turn.id,
            client_message_id=turn.client_message_id,
            status="pending",
            executable_hash=pending.executable_hash,
            workflow_state_hash=pending.workflow_state_hash,
            executable_version=pending.executable_version,
            operations_json=[item.model_dump(mode="json") for item in pending.operations],
            operation_payload_hash=pending.operation_payload_hash,
            diff_json=pending.diff.model_dump(mode="json"),
            reasons_json=list(pending.reasons),
            summary_json=list(pending.summary_lines),
            invalidates_approval=pending.invalidates_approval,
            governance_notes_json=list(pending.governance_notes),
            expires_at=pending.expires_at,
            created_at=pending.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    def _governance_notes(
        before: StrategyDraftV2,
        after: StrategyDraftV2,
    ) -> tuple[str, ...]:
        """Plain warnings about Sharia and market-data effects, when there are any.

        These are statements about what would change, never a Sharia ruling. The
        platform's own review process assigns status; this only says that the setting
        deciding which review applies is about to move.
        """

        notes: list[str] = []
        if before.sharia_policy.methodology_id != after.sharia_policy.methodology_id:
            notes.append(
                "The Sharia screening method would change, so which coins pass may change."
            )
        if before.sharia_policy.universe_mode != after.sharia_policy.universe_mode:
            notes.append("The list of coins your setup watches would change.")
        before_providers = {
            (item.provider, item.capability) for item in before.provider_requirements
        }
        after_providers = {
            (item.provider, item.capability) for item in after.provider_requirements
        }
        if after_providers - before_providers:
            notes.append("This would need market data your setup does not use yet.")
        if before.approval.approved and not after.approval.approved:
            notes.append("Your approved setup would need approving again.")
        return tuple(notes)

    @staticmethod
    def _capability_registry_version() -> str:
        """Which reading of the capability catalogue this plan was built against.

        A capability whose meaning changed between planning and execution must not be
        executed on the old reading. Failing to read the version is not a reason to skip
        the check, so an unreadable registry returns a value that never compares equal.
        """

        try:
            from ai_market_monitor.engine.capability_index import get_capability_index

            return str(get_capability_index().snapshot.registry_version)
        except Exception:
            return "registry-unavailable"

    def _freshness(
        self,
        chat: AISetupChatSession,
        authoritative: StrategyDraftV2,
        *,
        planning_authority: PlanningAuthority | None,
        expected_executable_hash: str,
        expected_workflow_state_hash: str,
        plan: object,
    ) -> FreshnessVerdict:
        """Compare every authority the plan depended on against the draft right now.

        Falls back to the two hashes when there is no recorded authority — a turn
        started by an older code path still gets the protection it always had, rather
        than silently getting none.
        """

        if planning_authority is None:
            same = (
                authoritative.executable_hash == expected_executable_hash
                and authoritative.workflow_state_hash == expected_workflow_state_hash
            )
            return FreshnessVerdict(decision="apply" if same else "refuse")
        kinds: list[str] = []
        targets: list[str] = []
        if isinstance(plan, dict):
            for item in plan.get("operations") or []:
                if not isinstance(item, dict):
                    continue
                kinds.append(str(item.get("kind") or ""))
                target = item.get("target_condition_id")
                if target:
                    targets.append(str(target))
        current = PlanningAuthority.read(
            authoritative,
            _load_conversation_context(dict(chat.context_json or {})),
            capability_registry_version=planning_authority.capability_registry_version,
        )
        return plan_freshness(
            planning_authority,
            current,
            operation_kinds=tuple(kinds),
            target_condition_ids=tuple(targets),
        )

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
        planning_authority: PlanningAuthority | None = None,
        confirm_destructive: bool = False,
    ) -> Any:
        async def persist(stage: str, payload: dict[str, Any]) -> None:
            execution = payload.get("execution_result")
            if stage == TurnStatus.EXECUTING.value and not isinstance(execution, dict):
                # The model call has finished, so it is safe to take the row lock now and
                # hold it only across deterministic execution and its gates. Holding it
                # across the provider call would have made every slow answer block the
                # user's whole session.
                await session.refresh(chat, with_for_update=True)
                authoritative = load_strategy_draft_v2(chat)
                verdict = self._freshness(
                    chat,
                    authoritative,
                    planning_authority=planning_authority,
                    expected_executable_hash=expected_executable_hash,
                    expected_workflow_state_hash=expected_workflow_state_hash,
                    plan=payload.get("plan"),
                )
                if verdict.is_refusal:
                    turn.failure_details_json = [
                        f"stale:{item}" for item in verdict.moved[:8]
                    ] + [verdict.reason[:200]]
                    raise SetupLaunchError(
                        "SETUP_TURN_CONFLICT",
                        (
                            "This Watch Plan changed in another tab. Your request was not "
                            "applied. Review the latest version and try again."
                        ),
                        stage="patch",
                        retryable=True,
                        status_code=409,
                    )
                if verdict.decision == "rebase":
                    # Something moved, but nothing this plan touches. It runs against the
                    # newer draft with no extra model call — the operation names its own
                    # target, so it was never about the part that changed.
                    stamps = dict(turn.stage_timestamps_json or {})
                    stamps["rebased"] = datetime.now(UTC).isoformat()
                    turn.stage_timestamps_json = stamps
                    turn.recovery_disposition = "deterministic_rebase"
                # A change big enough to lose the user's work is written down and
                # offered, not applied. This runs after the freshness check, so a
                # proposal is always built against a draft that is still current.
                #
                # Only free-text turns are gated. A server-drawn control *is* the
                # confirmation: the user pressed a button this application rendered,
                # showing exactly the choice it would make. Asking them to confirm their
                # own answer to our own question teaches them to click through.
                proposal = await self._propose_if_destructive(
                    session,
                    chat,
                    turn,
                    draft=authoritative,
                    plan_payload=payload.get("plan"),
                    # The exact words the plan was built from. The projection re-runs
                    # that plan, and every segment's offsets point into this string, so
                    # anything else fails span verification against text nobody wrote.
                    message=message,
                    source_turn_id=source_turn_id,
                ) if confirm_destructive else None
                if proposal is not None:
                    raise _PendingChangeRequired(proposal)
            # When this checkpoint carries an execution result, the mutation commits in
            # this very transaction. The honest state at that moment is EXECUTED — the
            # draft has moved and the reply has not been written yet — and recovery from
            # EXECUTED knows never to run the mutation again.
            self._touch_stage(
                turn,
                TurnStatus.EXECUTED.value if isinstance(execution, dict) else stage,
            )
            turn.planner_model = str(payload.get("planner_model") or "") or None
            plan = payload.get("plan")
            if isinstance(plan, dict):
                turn.plan_json = plan
                usage = payload.get("planner_usage")
                if isinstance(usage, dict) and turn.planner_usage_json is None:
                    # Only the first recording. A retry must never overwrite what the
                    # paid original actually cost.
                    turn.planner_usage_json = usage
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
                    RECOVERY_REPLY_KEY: deterministic_summary(
                        result, language=conversation.active_language
                    ),
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
        stamps = dict(turn.stage_timestamps_json or {})
        stamps[TurnStatus.COMPLETED.value] = turn.completed_at.isoformat()
        turn.stage_timestamps_json = stamps
        # Finished turns give the session back. Without this the next message would be
        # refused as a duplicate for as long as the row survived.
        self._release_claim(turn)
        await session.flush()

    async def _active_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        exclude_client_message_id: str | None = None,
    ) -> SetupChatTurn | None:
        """The mutating turn that currently owns this session, if there is one.

        Read from ``session_claim``, which the database keeps unique. A stale claim
        whose lease has expired does not count: the recovery worker will settle it, and
        until then a user must not be locked out of their own chat forever.
        """

        held = await session.scalar(
            select(SetupChatTurn)
            .where(SetupChatTurn.session_claim == chat.id)
            .with_for_update()
        )
        if held is None:
            return None
        if exclude_client_message_id and held.client_message_id == exclude_client_message_id:
            return None
        if not holds_session(held.status):
            # Settled but never released — release it now so the session is usable.
            held.session_claim = None
            await session.flush()
            return None
        if self._lease_expired(held):
            return None
        return held

    @staticmethod
    def _lease_expired(turn: SetupChatTurn) -> bool:
        """True when this turn has been silent past what its stage is allowed."""

        deadline = turn.lease_expires_at
        if deadline is None:
            reference = turn.updated_at or turn.created_at
            if reference is None:
                return False
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=UTC)
            deadline = reference + timedelta(seconds=lease_seconds(turn.status))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return datetime.now(UTC) >= deadline

    @staticmethod
    def _touch_stage(turn: SetupChatTurn, stage: str) -> None:
        """Record that a stage was entered, and give it a fresh lease.

        The lease is what a recovery owner reads to tell a slow turn from a dead one,
        so it has to be renewed at every stage rather than set once at the start.
        """

        now = datetime.now(UTC)
        stamps = dict(turn.stage_timestamps_json or {})
        stamps[stage] = now.isoformat()
        turn.stage_timestamps_json = stamps
        turn.status = stage
        turn.lease_expires_at = now + timedelta(seconds=max(lease_seconds(stage), 30))

    @staticmethod
    def _release_claim(turn: SetupChatTurn | None) -> None:
        """Give the session back. Every terminal path must call this.

        Leaving a claim behind would lock the user out of their own chat until the
        recovery worker noticed, which is a much worse failure than the double-turn it
        was protecting against.
        """

        if turn is not None:
            turn.session_claim = None
            turn.lease_expires_at = None
            turn.lease_owner = None

    async def _get_or_create_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        client_message_id: str,
        *,
        fingerprint: str | None = None,
        is_mutating: bool = True,
    ) -> SetupChatTurn:
        """Find this exact attempt, or claim the session for a new one.

        Two different refusals live here and they mean different things:

        * ``IDEMPOTENCY_KEY_CONFLICT`` — this id was used for a *different* request.
          Answering from the stored record would show a reply to a message the user
          never sent, so it is refused instead.
        * ``TURN_IN_PROGRESS`` — a different message already owns the session. Its
          state comes back so the client can wait rather than send again.
        """

        existing = await session.scalar(
            select(SetupChatTurn)
            .where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == client_message_id,
            )
            .with_for_update()
        )
        if existing is not None:
            self._require_matching_fingerprint(existing, fingerprint)
            if existing.request_fingerprint is None and fingerprint:
                # A turn stored before fingerprints existed. Record it now so the next
                # retry of this same id is checked properly.
                existing.request_fingerprint = fingerprint
                await session.flush()
            return existing

        if is_mutating:
            blocking = await self._active_turn(session, chat)
            if blocking is not None:
                raise self._turn_in_progress(blocking)

        draft = load_strategy_draft_v2(chat)
        now = datetime.now(UTC)
        created = SetupChatTurn(
            chat_session_id=chat.id,
            client_message_id=client_message_id,
            request_fingerprint=fingerprint,
            session_claim=chat.id if is_mutating else None,
            is_mutating=is_mutating,
            status=TurnStatus.RECEIVED.value,
            executable_version_before=draft.executable_version,
            workflow_revision_before=draft.workflow_revision,
            executable_hash_before=draft.executable_hash,
            workflow_state_hash_before=draft.workflow_state_hash,
            stage_timestamps_json={TurnStatus.RECEIVED.value: now.isoformat()},
            lease_expires_at=now + timedelta(seconds=lease_seconds(TurnStatus.RECEIVED)),
        )
        try:
            async with session.begin_nested():
                session.add(created)
                await session.flush()
        except IntegrityError as conflict:
            # Two different constraints can fire here and they need different answers:
            #
            # * the client-message-id constraint — this exact attempt already has a
            #   turn, so hand that one back
            # * the session-claim constraint — a *different* message got the session
            #   first, so this one waits
            #
            # The claim losing a race is normal under load. It must read as
            # TURN_IN_PROGRESS, never as a server error: the user did nothing wrong and
            # their message is not lost.
            if created in session:
                # The savepoint rollback usually detaches it already; this covers the
                # case where it did not, so a failed insert cannot be flushed again.
                session.expunge(created)
            concurrent = await session.scalar(
                select(SetupChatTurn).where(
                    SetupChatTurn.chat_session_id == chat.id,
                    SetupChatTurn.client_message_id == client_message_id,
                )
            )
            if concurrent is not None:
                self._require_matching_fingerprint(concurrent, fingerprint)
                return concurrent
            holder = await self._active_turn(session, chat)
            if holder is not None:
                raise self._turn_in_progress(holder) from None
            if _CLAIM_CONSTRAINT in str(conflict.orig or conflict):
                # The winner's row is not visible from this transaction yet, which is
                # exactly what losing the race looks like from here.
                raise SetupLaunchError(
                    "TURN_IN_PROGRESS",
                    (
                        "Another message for this setup started first. "
                        "It will finish in a moment."
                    ),
                    stage="intent",
                    retryable=True,
                    status_code=409,
                ) from None
            raise
        return created

    @staticmethod
    def _require_matching_fingerprint(turn: SetupChatTurn, fingerprint: str | None) -> None:
        """Refuse a reused key that carries different content.

        Returning the stored answer here would be worse than an error: the user would
        read a confident reply to a question they did not ask, and nothing would say so.
        """

        stored = turn.request_fingerprint
        if not stored or not fingerprint or stored == fingerprint:
            return
        raise SetupLaunchError(
            "IDEMPOTENCY_KEY_CONFLICT",
            (
                "This message id was already used for a different message. "
                "Send the new message with its own id."
            ),
            stage="intent",
            status_code=409,
        )

    @staticmethod
    def _turn_in_progress(turn: SetupChatTurn) -> SetupLaunchError:
        """One message at a time, said in a way the client can act on."""

        error = SetupLaunchError(
            "TURN_IN_PROGRESS",
            "Your previous message is still being worked on. It will finish in a moment.",
            stage="intent",
            retryable=True,
            status_code=409,
        )
        error.active_client_message_id = turn.client_message_id
        error.active_stage = str(turn.status or "")
        return error

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

    async def _open_ai_spend(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        source_turn_id: str,
    ) -> TurnSpend:
        """Ask permission to spend on this turn, before anything paid happens.

        The reservation is the *per-turn ceiling* the product already enforces, not a
        guess at what this particular message will cost. Reserved money is real money: a
        burst of simultaneous turns must each see the ones before them, and only a
        pessimistic hold makes that true. Reconciling afterwards puts the difference back.
        """

        guard = AISpendGuard(session, self.settings)
        try:
            return await guard.open_turn(
                user_id=chat.user_id,
                feature=Feature.PLANNER,
                # The turn id, so a retried request finds its own reservation instead of
                # taking a second one and charging the same work twice.
                idempotency_key=f"setup_turn:{source_turn_id}",
                model=self.settings.openai_model,
                service_tier=self.settings.setup_agent_complex_service_tier,
                estimated_cost_usd=Decimal(
                    str(self.settings.setup_agent_max_estimated_cost_usd_per_turn)
                ),
            )
        except AISpendRefused as refused:
            raise SetupLaunchError(
                refused.code,
                refused.message,
                stage="interpret",
                # Retryable when the reason will pass on its own — a busy platform, a
                # daily window. Never retryable when a person has to change something.
                retryable=refused.scope in {"global_daily", "global_monthly", "concurrency"},
                status_code=429 if refused.scope == "concurrency" else 503,
            ) from refused

    async def _settle_ai_spend(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        spend: TurnSpend,
        *,
        usage: dict[str, Any] | None,
        outcome: str,
    ) -> None:
        """Record what the turn really cost, in the budget and in the ledger, once.

        Both records are written from the same numbers and the ledger row points back at
        the reservation, so the two can be checked against each other. Before this they
        were two independent accounts of the same money with nothing joining them.
        """

        coverage = CapabilityCoverageService(self.settings)
        event = await coverage.record_usage(
            session,
            chat=chat,
            operation="setup_agent_turn",
            # Every decision that was in force travels with the turn. "It was on" does not
            # let anybody reproduce an incident; knowing the person was allowlisted, or in
            # a cohort, or inside the percentage, does.
            usage=({**usage, "_rollout": spend.features} if usage else None),
            reservation_id=spend.reservation_id,
            outcome=outcome,
            rollout_version=spend.rollout_version,
        )
        actual = (
            Decimal(str(event.estimated_cost_usd))
            if event is not None
            # No usage at all still settles the reservation, at the amount that was held.
            # Releasing it as free would let a paid attempt that reported nothing look
            # like a turn that never happened.
            else (
                spend.reservation.estimated_cost_usd
                if spend.reservation is not None and outcome not in {"cancelled", "refused"}
                else Decimal("0")
            )
        )
        await AISpendGuard(session, self.settings).settle_turn(
            spend,
            actual_cost_usd=actual,
            input_tokens=int(event.input_tokens) if event is not None else 0,
            output_tokens=int(event.output_tokens) if event is not None else 0,
            provider_request_id=event.provider_request_id if event is not None else None,
            outcome=outcome,
        )

    async def _release_ai_spend(self, session: AsyncSession, spend: TurnSpend) -> None:
        """Hand back a promise for a turn that never produced anything."""

        await AISpendGuard(session, self.settings).release_turn(spend, outcome="cancelled")

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
                # A failed turn owes nothing more, so it must not keep holding the
                # session. The user has to be able to try again straight away.
                self._release_claim(turn)
            await session.flush()
        # The measurement belongs to this exact idempotency record. Keep the session's
        # latest-turn compatibility view in sync, but never rely on that overwriteable
        # view as the durable history.
        measured = _set_runtime(
            chat,
            started,
            model_calls=telemetry.model_calls,
            cache_hits=telemetry.cache_hits,
            telemetry=telemetry,
        )
        if turn is not None:
            turn.telemetry_json = measured
        await session.flush()
        await session.commit()

    async def _replayed_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        client_message_id: str,
        fingerprint: str | None = None,
    ) -> AISetupChatSession | None:
        """Answer a repeated key from the stored record instead of re-running the turn.

        The old check returned the chat as-is whenever the key had been seen, which meant
        a retry after a mid-turn crash returned a session with **no assistant answer** and
        no error — the user saw their message vanish. Now the stored status decides:

        * ``COMPLETED`` — the same final answer, no model call, no second patch
        * ``RETRYABLE_FAILURE`` — reprocess, because nothing was applied
        * ``PLANNING`` / ``EXECUTING`` — an in-progress conflict, never a silent no-op

        The fingerprint is checked first. A key reused for different content is refused
        outright: returning the old answer would show a confident reply to a message the
        user never sent.
        """
        record = await session.scalar(
            select(SetupChatTurn)
            .where(
                SetupChatTurn.chat_session_id == chat.id,
                SetupChatTurn.client_message_id == client_message_id,
            )
            .with_for_update()
        )
        if record is not None:
            self._require_matching_fingerprint(record, fingerprint)
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
            TurnStatus.EXECUTED.value,
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
            TurnStatus.PLANNED.value,
            TurnStatus.EXECUTING.value,
            TurnStatus.EXECUTED.value,
            TurnStatus.COMPOSING.value,
            TurnStatus.RECOVERING.value,
        }:
            # Still running, or stalled and not yet recovered. Either way this exact
            # message must not be started a second time, because the first attempt may
            # be about to commit. The lease is what tells them apart, and the recovery
            # worker owns that decision — not this request.
            if not self._lease_expired(record):
                raise self._turn_in_progress(record)
            # The lease is gone, so nothing is going to finish this turn on its own.
            # Anything after execution may already be committed, so it is recovered from
            # what is stored rather than re-run.
            if recovery_policy(status).mutation_may_be_committed:
                raise SetupLaunchError(
                    "TURN_RECOVERY_PENDING",
                    (
                        "Your previous message is being checked after an interruption. "
                        "Nothing was lost. Try again in a moment."
                    ),
                    stage="interpret",
                    retryable=True,
                    status_code=409,
                )
            # Nothing was committed, so this key may safely start again.
            record.status = TurnStatus.RECEIVED.value
            record.retry_count += 1
            record.recovery_disposition = RecoveryAction.ABANDON_AMBIGUOUS.value
            self._touch_stage(record, TurnStatus.RECEIVED.value)
            record.session_claim = chat.id
            await session.flush()
            return None
        if status == TurnStatus.PERMANENT_FAILURE.value:
            raise SetupLaunchError(
                str(record.failure_code or "TURN_FAILED"),
                "That message could not be processed.",
                stage="interpret",
                status_code=422,
            )
        if status in {
            TurnStatus.CANCELLED.value,
            TurnStatus.ABANDONED.value,
            TurnStatus.SUPERSEDED.value,
        }:
            # Settled without a stored reply. Reprocessing is safe: by definition
            # nothing was committed for these states.
            record.status = TurnStatus.RECEIVED.value
            record.retry_count += 1
            self._touch_stage(record, TurnStatus.RECEIVED.value)
            record.session_claim = chat.id
            await session.flush()
            return None
        if status == TurnStatus.RETRYABLE_FAILURE.value:
            record.retry_count += 1
            record.status = TurnStatus.RECEIVED.value
            record.failure_code = None
            record.failure_stage = None
            record.failure_retryable = None
            # Retrying reclaims the session: this turn is in flight again.
            record.session_claim = chat.id
            self._touch_stage(record, TurnStatus.RECEIVED.value)
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

    async def _finish_governed_percentage_scan_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        draft: StrategyDraftV2,
        conversation: SetupConversationContext,
        request: dict[str, object],
        trace_payload: dict[str, Any],
        model_calls: int,
        context: dict[str, Any],
        started: float,
        telemetry: TurnTelemetry,
        turn_record: SetupChatTurn | None,
        execution_result: dict[str, Any] | None = None,
    ) -> AISetupChatSession:
        """Execute a durable scan after either an agent or server-owned option turn."""

        request = dict(request)
        movement_direction = request.get("movement_direction")
        threshold_percent = request.get("threshold_percent")
        if movement_direction is None or threshold_percent is None:
            # `_route_read_only_scan` never hands a scan over to either caller of this
            # method without both fields set (it returns early while either is None).
            # Refusing here, rather than defaulting to "up"/0%, is the second gate: if
            # that invariant is ever broken by a later change, this must not silently
            # run a live scan against a threshold nobody stated.
            raise SetupLaunchError(
                "READ_ONLY_SCAN_REQUEST_INCOMPLETE",
                "This scan request is missing its stated move size or direction.",
                stage="patch",
            )
        direction = str(movement_direction)
        threshold = float(str(threshold_percent))
        window = str(request.get("measurement_window") or "")
        language = conversation.active_language
        try:
            result = await self.owner.screened_percentage_snapshot(
                session,
                chat,
                draft=draft,
                direction=cast(Literal["up", "down"], direction),
                threshold=threshold,
                timeframe=window,
                idempotency_key=self._read_only_scan_idempotency_key(
                    chat, turn_record, str(request.get("source_text") or "")
                ),
            )
            content = _governed_scan_message(result, language)
            message_type = "scanner_result"
            keep_request: dict[str, object] = {}
        except OnDemandScanError as exc:
            result = {
                "status": "failed",
                "error_code": exc.code,
                "safe_message": str(exc),
                "results": [],
                "market_statuses": [],
                "read_only": True,
                "strategy_mutated": False,
                "query": {
                    "movement_direction": direction,
                    "threshold_percent": threshold,
                    "measurement_window": window,
                },
            }
            content = governed_scan_error(exc.code, str(exc), language_of(language))
            message_type = "scanner_error"
            # Kept only when the refusal is one the trader can still clear — no screened
            # scope yet, or a provider that is down. Throwing the values away there made
            # them describe the same scan again after fixing the very thing we asked
            # them to fix. A spent quota or an unsupported window ends the attempt.
            keep_request = dict(request) if scan_error_is_resolvable(exc.code) else {}

        fingerprint = _chat_response_fingerprint(content)
        conversation = conversation.model_copy(
            update={
                # The goal is closed either way: the open question was answered, and a
                # scan that keeps re-running itself on every later message is a loop.
                "active_goal": None,
                "pending_read_only_scan": keep_request,
                "last_assistant_summary": content[:1000],
                "last_response_fingerprint": fingerprint,
            }
        ).cleared_question()
        context["setup_conversation_context"] = conversation.model_dump(mode="json")
        context["last_turn_trace"] = trace_payload
        context["last_turn_failed"] = False
        context.pop("last_turn_failure", None)
        context.pop("setup_failure_history", None)
        context["last_read_only_route"] = "governed_percentage_scan"
        context["last_response_fingerprint"] = fingerprint
        context["scanner_result"] = result
        _record_funnel(
            context,
            outcome=("scan_completed" if result.get("status") != "failed" else "scan_refused"),
            telemetry=telemetry,
            failure_code=(
                str(result.get("error_code") or "") or None
                if result.get("status") == "failed"
                else None
            ),
            model_calls=model_calls,
        )
        chat.context_json = context
        payload = {
            "scanner_result": result,
            "read_only": True,
            "strategy_mutated": False,
            "response_fingerprint": fingerprint,
            "active_language": language,
            "scanner_ui": scanner_labels(language_of(language)),
            "turn_trace": trace_payload,
            "model_call_count": model_calls,
        }
        with telemetry.stage("persistence"):
            assistant = await self.owner._assistant(
                session,
                chat,
                content,
                message_type=message_type,
                payload=payload,
            )
            await self._complete_db_turn(
                session,
                chat,
                turn_record,
                reply={
                    "message": content,
                    "execution_result": execution_result,
                    "scanner_result": result,
                },
                assistant_message_id=assistant.id,
            )
        measured = _set_runtime(
            chat,
            started,
            model_calls=model_calls,
            cache_hits=0,
            telemetry=telemetry,
        )
        if turn_record is not None:
            turn_record.telemetry_json = measured
        await session.flush()
        await session.commit()
        return chat

    @staticmethod
    def _read_only_scan_idempotency_key(
        chat: AISetupChatSession,
        turn_record: SetupChatTurn | None,
        message: str,
    ) -> str:
        """One quota identity per accepted chat turn, stable across an HTTP replay."""

        if turn_record is not None:
            return f"setup-chat-percentage-turn:{turn_record.id}"
        nonce = uuid4().hex
        message_hash = _chat_response_fingerprint(message)
        return f"setup-chat-percentage:{chat.id}:{message_hash}:{nonce}"

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
            # One recording of every authority this plan is about to depend on, taken
            # before the paid call and compared after it. Two hashes alone missed a
            # changed methodology, a granted approval and a reordered rule list.
            authority = PlanningAuthority.read(
                draft,
                conversation,
                capability_registry_version=self._capability_registry_version(),
            )
            turn_record.executable_hash_before = draft.executable_hash
            turn_record.workflow_state_hash_before = draft.workflow_state_hash
            turn_record.schema_version = str(draft.schema_version)
            canonical_stage_callback = self._turn_stage_callback(
                session,
                chat,
                turn_record,
                message=message,
                source_turn_id=source_turn_id,
                expected_executable_hash=draft.executable_hash,
                expected_workflow_state_hash=draft.workflow_state_hash,
                planning_authority=authority,
                # Free text is the one place a change can be much bigger than the user
                # realised, because it is the one place they did not pick from a list.
                confirm_destructive=True,
            )

            async def stage_callback(stage: str, payload: dict[str, Any]) -> None:
                # The execution checkpoint is a real database write. Attribute its
                # elapsed time to persistence even though it occurs inside the broader
                # canonical-execution boundary.
                with telemetry.stage("persistence"):
                    await canonical_stage_callback(stage, payload)

        # One decision, made by the language owner. The private copy here read a
        # different set of "no language signal" answers than the agent's, so a chat
        # could change language on a turn the agent had judged silent.
        active_language = resolve_conversation_language(
            message, session_language=conversation.active_language
        ).language.value
        conversation = conversation.model_copy(update={"active_language": active_language})
        context["active_language"] = active_language
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
            active_language=active_language,
            # The conversation's own memory. The mode comes from `draft.mode` above and
            # the half-collected scan from `conversation.pending_read_only_scan`; only
            # these three have nowhere else to live.
            previous_intent=str(context.get("conversation_previous_intent") or "") or None,
            session_language=str(context.get("conversation_language") or "") or None,
            previous_response_fingerprints=tuple(
                str(item) for item in (context.get("conversation_response_fingerprints") or [])[-8:]
            ),
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
        # Nothing paid happens until the assistant is both switched on for this person and
        # has budget. Both refusals are a *degraded* product: the draft is untouched, the
        # Builder still authors, and the person is told in plain words what is available.
        try:
            spend = await self._open_ai_spend(session, chat, source_turn_id=source_turn_id)
        except SetupLaunchError as refused:
            # The turn row is already open at this point. Leaving it open would make the
            # chat look busy for ever, and the next Builder click would be told "your
            # previous message is still being worked on" — turning a budget limit into a
            # person locked out of the one thing that still works.
            await self._fail_db_turn(
                session,
                chat,
                turn_record,
                code=refused.code,
                stage=refused.stage,
                retryable=refused.retryable,
                details=(),
                started=started,
                telemetry=telemetry,
            )
            raise
        settled = False
        try:
            outcome = await self.agent.run_turn(turn)
        except _PendingChangeRequired as pending:
            # The change was understood and priced, and then not applied. The draft is
            # untouched, the proposal is stored, and the user decides. No second model
            # call happens on confirm — the operations are already written down.
            #
            # The planner call still happened and was still paid for, so the reservation
            # settles at what was held rather than being released as free.
            settled = True
            await self._settle_ai_spend(
                session, chat, spend, usage=None, outcome="completed"
            )
            return await self._answer_with_proposal(
                session,
                chat,
                turn_record,
                proposal=pending.proposal,
                started=started,
                telemetry=telemetry,
            )
        except SetupAgentError as exc:
            # Planning can be paid and complete even when the server subsequently
            # rejects an ungrounded operation. Usage belongs to the attempted turn,
            # not only to successful mutations, and must be recorded before the
            # classified failure is returned.
            settled = True
            await self._settle_ai_spend(
                session,
                chat,
                spend,
                usage=exc.usage or None,
                outcome="provider_failed",
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
            _record_conversation_memory(context, telemetry)
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
                stage=_launch_stage_for(exc.code, exc.stage),
                retryable=exc.retryable,
                status_code=503 if exc.retryable else 422,
            ) from exc
        except BaseException:
            # Anything that escapes here — a cancelled request, a bug, a shutdown — must
            # not leave budget held for a turn nobody will ever settle. Without this the
            # allowance shrinks a little with every crash until somebody restarts
            # everything, and the sweep only notices fifteen minutes later.
            if not settled:
                settled = True
                await self._release_ai_spend(session, spend)
            raise

        settled = True
        await self._settle_ai_spend(
            session,
            chat,
            spend,
            usage=outcome.usage or None,
            outcome="completed",
        )

        if outcome.read_only_scan_request is not None:
            return await self._finish_governed_percentage_scan_turn(
                session,
                chat,
                draft=outcome.draft,
                conversation=outcome.conversation,
                request=dict(outcome.read_only_scan_request),
                trace_payload=outcome.trace.to_dict(),
                model_calls=outcome.trace.model_calls,
                context=context,
                started=started,
                telemetry=telemetry,
                turn_record=turn_record,
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
            clarification_payloads = (
                [outcome.clarification.client_payload()]
                if outcome.clarification is not None
                else _pending_scope_clarifications(
                    outcome.draft, outcome.conversation.active_language
                )
                if outcome.conversation.pending_read_only_scan
                else []
            )
            with telemetry.stage("persistence"):
                assistant = await self.owner._assistant(
                    session,
                    chat,
                    outcome.message,
                    message_type=("clarification" if clarification_payloads else "conversation"),
                    payload={
                        "execution_result": None,
                        "segments": list(outcome.trace.segments),
                        "clarifications": clarification_payloads,
                        "response_fingerprint": outcome.trace.response_fingerprint,
                        "active_language": outcome.conversation.active_language,
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
            measured = _set_runtime(
                chat,
                started,
                model_calls=outcome.trace.model_calls,
                cache_hits=0,
                telemetry=telemetry,
            )
            if turn_record is not None:
                turn_record.telemetry_json = measured
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
        _record_conversation_memory(context, telemetry)
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
                        [outcome.clarification.client_payload()]
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
        measured = _set_runtime(
            chat,
            started,
            model_calls=outcome.trace.model_calls,
            cache_hits=0,
            telemetry=telemetry,
        )
        if turn_record is not None:
            turn_record.telemetry_json = measured
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

    def _configured_providers(self) -> frozenset[str]:
        """The data feeds the configured adapter implements.

        One answer, used by the Builder catalogue, by the mutation path and by the
        approval gate. When these disagreed the Builder could offer a mechanic that the
        action then refused, which reads to the person as the product breaking.
        """

        return configured_runtime_provider_requirements(self.settings.market_data_provider)

    def _disabled_capabilities(self) -> frozenset[str]:
        """Capabilities paused by configuration, read from the one owner.

        Used by the mutation path as well as the catalogue, so a capability that the
        Builder shows as paused is also refused when an action reaches the server. If only
        the catalogue knew, a stale browser tab could still write the paused rule.
        """

        return disabled_capabilities_from(self.settings.builder_capabilities_disabled)

    def _provider_available(self, provider: str) -> bool:
        """Only candle data is wired. Anything else blocks approval, visibly.

        Claiming an unwired feed is available would compile a rule that can never be
        evaluated, and the alert would simply never fire with no explanation.
        """
        return provider.strip().casefold() in self._configured_providers()

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
                question="Which current Favorites list should Hilal Markets use?",
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
                question="Which eligible spot assets should Hilal Markets watch?",
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
                    question="Which screened universe should Hilal Markets use?",
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
    conversation = payload.get("conversation_after")
    language = (
        str(conversation.get("active_language") or "en")
        if isinstance(conversation, dict)
        else "en"
    )
    message = deterministic_summary(result, language=language)
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
def _chat_response_fingerprint(message: str) -> str:
    normalized = " ".join((message or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _pending_scope_clarifications(
    draft: StrategyDraftV2,
    language: str,
) -> list[dict[str, Any]]:
    """Re-present the server-owned scope choices after an intervening scan question."""

    if not _scan_draft_scope_ready(draft):
        labels = scope_labels(language_of(language))
        return [
            {
                "key": "screened_universe_mode",
                "options": [
                    {
                        "key": "screened_universe_mode",
                        "label": labels["eligible_market"],
                        "value": ShariaUniverseMode.ELIGIBLE_MARKET.value,
                    },
                    {
                        "key": "screened_universe_mode",
                        "label": labels["approved_watchlist"],
                        "value": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                    },
                    {
                        "key": "screened_universe_mode",
                        "label": labels["explicit_assets"],
                        "value": ShariaUniverseMode.EXPLICIT_ASSETS.value,
                    },
                ],
            }
        ]
    return []


def _answer_is_stale(
    chat: AISetupChatSession,
    *,
    question_id: str | None,
    step_revision: int | None,
) -> bool:
    """Whether a client-supplied answer identity names a question that has moved on.

    Only checked when the client sends one. A typed answer carries no identity and must
    keep working; a rendered button knows exactly which question it was drawn under, and
    holding it to that is what stops a click on a stale screen filling the current field
    with the previous step's choice.
    """

    if question_id is None and step_revision is None:
        return False
    conversation = _load_conversation_context(dict(chat.context_json or {}))
    contract = conversation.active_question
    if contract is None:
        # There is no question at all now. An answer written for one is out of date by
        # definition, and applying it would be applying it to nothing.
        return True
    return stale_step(
        contract, question_id=question_id, step_revision=step_revision
    )


def _typed_scope_answer(chat: AISetupChatSession, message: str) -> str | None:
    """A written answer to the open screened-scope question, as a canonical value.

    Returns ``None`` unless that exact question is the one on screen — so an ordinary
    message containing the word "all" is never mistaken for a governed scope choice.
    Only a fully resolved reading is accepted; a near miss or an ambiguity is left to
    the conversational path, which keeps the question and explains.
    """

    conversation = _load_conversation_context(dict(chat.context_json or {}))
    question = conversation.active_question
    if question is None or question.question_id != SCAN_SCOPE_QUESTION:
        return None
    language = language_of(conversation.active_language)
    values = list(question.canonical_values) or list(
        display_options(AnswerDomain.UNIVERSE_MODE)
    )
    resolution = resolve_active_answer(
        message,
        domain=AnswerDomain.UNIVERSE_MODE,
        allowed_options=values,
        offered_values=values,
        display_labels=labels_for(AnswerDomain.UNIVERSE_MODE, values, language),
    )
    if resolution.outcome is not AnswerOutcome.RESOLVED:
        return None
    return str(resolution.canonical_value)


def _scan_draft_scope_ready(draft: StrategyDraftV2) -> bool:
    policy = draft.sharia_policy
    if policy.methodology_id is None or not policy.methodology_version:
        return False
    if any(
        item.blocking and item.target_type in {"universe", "sharia_policy"}
        for item in draft.unresolved_fields
    ):
        return False
    if (
        policy.universe_mode == ShariaUniverseMode.APPROVED_WATCHLIST
        and (policy.approved_watchlist_id is None or not policy.approved_watchlist_version)
    ):
        return False
    return not (
        policy.universe_mode == ShariaUniverseMode.EXPLICIT_ASSETS
        and not policy.explicit_symbols
    )


def _governed_scan_message(result: dict[str, Any], language: str) -> str:
    tongue = language_of(language)
    matches = [
        item
        for item in result.get("results") or []
        if isinstance(item, dict) and item.get("category") == "confirmed"
    ]
    checked = int(result.get("symbols_scanned") or 0)
    evaluated = str(result.get("evaluated_at") or "")
    rendered_rows: list[str] = []
    for item in matches[:10]:
        raw_receipt = item.get("proof_receipt")
        receipt: dict[str, Any] = raw_receipt if isinstance(raw_receipt, dict) else {}
        change = float(str(receipt.get("percentage_change") or 0))
        rendered_rows.append(f"{item.get('symbol')} {change:+.2f}%")
    rendered = ", ".join(rendered_rows)
    extra = max(0, len(matches) - len(rendered_rows))
    extra_text = f" (+{extra})" if extra else ""
    if not matches:
        return localized("scan_result.none", tongue, checked=checked, time=evaluated)
    return localized(
        "scan_result.some",
        tongue,
        rows=rendered,
        extra=extra_text,
        checked=checked,
        time=evaluated,
    )


def _load_conversation_context(context: dict[str, Any]) -> SetupConversationContext:
    """Read a stored conversation, rebuilding as much of it as is safe.

    A stale shape must not fail a turn — conversation metadata is not executable. But
    the previous behaviour, throwing the whole record away on any validation error, was
    worse than the problem it avoided: the open question went with it while the blocker
    that caused the question stayed in the draft. The trader was then blocked for a
    reason nothing on screen mentioned, with nothing to answer.

    So the record is rebuilt in three widening steps, each keeping strictly less:

    1. as stored;
    2. with keys this version does not know dropped — which is what a rollback to an
       older build sees, and what an unrecognised future field looks like;
    3. field by field, keeping every field that still validates on its own.

    Step three deliberately tries ``active_question`` before ``pending_workflow``: if
    the pair can no longer be reconciled, the *question* is what must survive, because a
    visible question is what makes a live blocker answerable. The workflow behind it is
    rebuilt from the draft's own metadata the next time the trader answers.
    """

    language = str(context.get("active_language") or "en")
    payload = context.get("setup_conversation_context")
    if not isinstance(payload, dict):
        return SetupConversationContext(active_language=language)
    try:
        return SetupConversationContext.model_validate(payload)
    except ValidationError:
        pass
    known = set(SetupConversationContext.model_fields)
    trimmed = {key: value for key, value in payload.items() if key in known}
    try:
        return SetupConversationContext.model_validate(trimmed)
    except ValidationError:
        pass
    # Field by field. The order is the recovery policy: language first so every later
    # message is in the right one, then the open question, then everything that depends
    # on it.
    priority = (
        "active_language",
        "active_question",
        "active_question_id",
        "question_text",
        "question_target",
        "valid_answer_shape",
        "pending_workflow",
        "paused_question",
        "paused_workflow",
    )
    ordered = [key for key in priority if key in trimmed]
    ordered += [key for key in trimmed if key not in priority]
    rebuilt: dict[str, Any] = {"active_language": language}
    for key in ordered:
        candidate = {**rebuilt, key: trimmed[key]}
        try:
            SetupConversationContext.model_validate(candidate)
        except ValidationError:
            continue
        rebuilt = candidate
    return SetupConversationContext.model_validate(rebuilt)


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
#: Every outcome a turn can end in. A read-only Scanner run is its own pair, because a
#: scan that found nothing is not a refusal and a scan that ran is not a draft change.
#: They were missing, so a completed scan raised `unknown turn outcome: scan_completed`
#: *after* the scan had already run — the funnel recorder turning a finished turn into
#: a failed one.
FUNNEL_OUTCOMES: tuple[str, ...] = (
    "changed",
    "no_change",
    "answered",
    "refused",
    "scan_completed",
    "scan_refused",
)

#: How many turns of history the funnel keeps per chat. Enough to see a user give up;
#: short enough that the session row stays small.
_FUNNEL_WINDOW = 40


def _record_conversation_memory(context: dict[str, Any], telemetry: TurnTelemetry) -> None:
    """Carry this turn's routing decision, language and wording into the next turn.

    Only what has nowhere else to live. The selected mode is ``draft.mode`` and the
    half-collected scan is ``conversation.pending_read_only_scan``; both used to be
    copied here as well, and the copies were what the next turn read — so a scan could
    be waiting in one place while the router looked in the other and found nothing.
    """

    notes = telemetry.notes
    intent = notes.get("conversation_intent")
    if intent and intent != "CONFUSION_SIGNAL":
        # A confusion signal is *about* the previous intent, so it must not replace it.
        context["conversation_previous_intent"] = str(intent)
    language = notes.get("conversation_language")
    if language:
        context["conversation_language"] = str(language)
    fingerprint = notes.get("response_fingerprint")
    if fingerprint:
        context["conversation_response_fingerprints"] = [
            *(context.get("conversation_response_fingerprints") or []),
            str(fingerprint),
        ][-8:]
    # Copies written by an earlier build. Removed on the way past so a stale one can
    # never be read as current state.
    context.pop("conversation_active_mode", None)
    context.pop("conversation_pending_goal", None)


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
        # Recording a turn must never be the thing that fails it. An outcome nobody
        # listed is a gap in this table, not a reason to throw away a scan the user
        # already paid for and the platform already ran.
        outcome = "no_change"
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
) -> dict[str, Any]:
    """Record what this turn actually spent, stage by stage.

    The measured breakdown is the point. A single total tells you a turn was slow; it
    never tells you which stage to fix, which is how a slow path acquires a larger
    timeout instead of less work. When no telemetry was collected the total is still
    written, so the shape of the record never changes.

    **Returns the measurement it wrote**, and callers store *that* on the turn row.
    ``to_payload()`` reads a running clock, so calling it a second time for the turn row
    produced a second, later measurement: one turn with two different durations recorded
    against it, and whichever row you happened to read decided what you believed. The
    difference was small — 16 ms in the case that found it — which is exactly why it
    could sit there. There is one measurement per turn now.
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
    return measured
