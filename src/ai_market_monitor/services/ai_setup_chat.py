import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from statistics import fmean, pstdev
from time import monotonic
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import (
    AISetupChatMessage,
    AISetupChatSession,
    AIUsageEvent,
    ApprovedWatchlist,
    ApprovedWatchlistAsset,
    SetupChatTurn,
    Strategy,
)
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ShariaAssetStatus,
    ShariaUniverseMode,
    TriggerMode,
)
from ai_market_monitor.engine.capability_index import get_capability_index
from ai_market_monitor.engine.capability_resolver import (
    CapabilityResolutionReport,
)
from ai_market_monitor.engine.prompt_semantics import SUPPORTED_TIMEFRAMES
from ai_market_monitor.engine.setup_completeness import (
    RequirementField,
    assess_setup_requirements,
)
from ai_market_monitor.engine.strategy_state import (
    StrategyDraftState,
    canonical_compiler_text,
    is_reversion_request,
    patches_for_turn,
    revert_patches,
)
from ai_market_monitor.engine.turn_fragments import classify_turn as classify_strategy_turn
from ai_market_monitor.schemas.agent_control import (
    AgentClauseCoverage,
    AgentToolResult,
    SetupChatConversationState,
    SetupConversationRequirement,
)
from ai_market_monitor.schemas.ai_setup_chat import (
    MarketSnapshotAssetStatus,
    MarketSnapshotMover,
    MarketSnapshotResponse,
    SetupChatClarification,
    SetupChatErrorEnvelope,
    SetupChatInterviewResult,
    SetupChatOption,
    SetupChatTurnClassification,
    SetupChatTurnSegment,
    setup_chat_source_excerpt,
)
from ai_market_monitor.schemas.on_demand import OnDemandScanRequest
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.schemas.screening_execution import ReviewedScreeningEvidence
from ai_market_monitor.schemas.strategy import (
    Comparator,
    ConditionGroup,
    ConditionRule,
    InterpretationIssue,
    InterpretationPreview,
    ShariaPolicyDefinition,
    StrategyDefinition,
    StrategyDirection,
)
from ai_market_monitor.schemas.strategy_draft_v2 import DraftMode, StrategyDraftV2
from ai_market_monitor.services.agent_control import (
    AgentControlService,
    AgentResponsesClient,
    AgentTurnOutcome,
)
from ai_market_monitor.services.agent_tools import AgentToolService
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.ai_setup_evaluator_control import (
    consume_evaluator_llm_fault,
    evaluator_prompt_appendix,
)
from ai_market_monitor.services.capability_extensions import CapabilityExtensionService
from ai_market_monitor.services.hybrid_capability_resolution import (
    HybridCapabilityResolutionService,
)
from ai_market_monitor.services.interfaces import MarketDataProvider, StrategyInterpreter
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter
from ai_market_monitor.services.on_demand_scans import OnDemandScanError, OnDemandScanService
from ai_market_monitor.services.setup_chat_agent import SetupChatAgent
from ai_market_monitor.services.setup_chat_launch import (
    REVIEWED_PREFLIGHT_MANIFEST_KEY,
    SetupChatLaunchService,
    SetupLaunchError,
    load_strategy_draft_v2,
)
from ai_market_monitor.services.sharia_screening import (
    DEFAULT_ALLOWED_STATUSES,
    ShariaScreeningService,
    canonical_asset,
)
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.strategy import StrategyService
from ai_market_monitor.services.system_brain import CapabilityCoverageService
from ai_market_monitor.services.verified_strategy import VerifiedStrategyService


class SetupChatError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        stage: Literal[
            "intent",
            "extract",
            "patch",
            "interpret",
            "compile",
            "serialize",
            "provider",
        ] = "compile",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.stage = stage
        self.retryable = retryable


MAX_CLARIFICATION_QUESTIONS_PER_CONDITION = 2

#: Ceiling on how many questions may be open at once. Run 20260725T122105Z showed
#: sessions accumulating unbounded question sets and never reaching a draft; past
#: this many, the remainder are recorded as assumptions on the inactive draft
#: instead of being asked, and the user can still correct any of them by replying.
MAX_UNRESOLVED_CLARIFICATIONS = 3

#: User-facing text for a failed turn. It states what happened and what the user can
#: do, and deliberately carries no exception detail, stack frame, or identifier
#: beyond the request id needed to correlate with the internal log.
_TURN_FAILURE_MESSAGE = (
    "I hit an internal error while working on that turn and stopped rather than "
    "guess. Nothing was created, changed, or activated. Please send that last "
    "instruction again, or rephrase it more simply. Reference {request_id} if you "
    "contact support."
)


def _advance_strategy_state(
    stored: Any,
    new_fragments: list[str],
) -> StrategyDraftState:
    """Fold this turn's new fragments into the typed state, oldest first.

    A reversion fragment restores the previous value of the most recently changed
    field instead of adding a statement, so `go back to the 15m` walks the history
    back rather than re-parsing text that still contains the value it replaced.
    """
    state = StrategyDraftState.from_dict(stored if isinstance(stored, dict) else None)
    for fragment in new_fragments:
        text = " ".join(str(fragment).split())
        if not text:
            continue
        if is_reversion_request(text):
            reverted = revert_patches(state, source_text=text)
            if reverted:
                state = state.apply(reverted)
                continue
            # Nothing to revert; the wording may still name a value to use.
        state = state.apply(patches_for_turn(text, state))
    return state


def _resolved_strategy_state(chat: AISetupChatSession) -> dict[str, Any]:
    """The session's settled field values, JSON-safe, for the compiler."""
    stored = (chat.context_json or {}).get("strategy_state")
    state = StrategyDraftState.from_dict(stored if isinstance(stored, dict) else None)
    resolved: dict[str, Any] = {}
    for name, value in state.resolved().items():
        if isinstance(value, StrategyDirection | Comparator):
            resolved[name] = value.value
        elif isinstance(value, tuple):
            resolved[name] = list(value)
        else:
            resolved[name] = value
    return resolved


def _required_fields_known(chat: AISetupChatSession) -> frozenset[RequirementField]:
    """Requirements already settled outside the free text.

    A screened universe or approved watchlist is chosen through the application's own
    selection flow, so the compiler holds that value even though no symbol appears in
    the description. These count as satisfied because they are genuinely known, never
    because the wording implied them.
    """
    context = chat.context_json or {}
    known: set[RequirementField] = set()
    if context.get("screened_universe_mode") or context.get("approved_watchlist_id"):
        known.add("universe")
    return frozenset(known)


def _setup_is_complete(chat: AISetupChatSession, text: str) -> bool:
    """True when ``text`` specifies every field the compiler cannot default."""
    known = set(_required_fields_known(chat))
    resolved = _resolved_strategy_state(chat)
    if resolved.get("include_symbols"):
        known.add("universe")
    if resolved.get("base_timeframe"):
        known.add("timeframe")
    if (
        resolved.get("mechanic_fragments")
        or resolved.get("formula")
        or resolved.get("formula_fragments")
        or resolved.get("boolean_groups")
    ):
        known.add("trigger_condition")
    return assess_setup_requirements(text, known_fields=frozenset(known)).is_complete


def _can_interpret_deterministically(
    chat: AISetupChatSession,
    compiler_text: str,
) -> bool:
    """Whether the existing rule interpreter exactly owns the current contract.

    This is a positive gate. Explicit formulas and locally matched registered
    mechanics need no model judgment; unknown or ambiguous mechanics still follow
    capability resolution and the bounded AI path.
    """

    state = StrategyDraftState.from_dict(
        (chat.context_json or {}).get("strategy_state")
        if isinstance((chat.context_json or {}).get("strategy_state"), dict)
        else None
    )
    if not state.patches:
        return False
    resolved = state.resolved()
    turn = classify_strategy_turn(compiler_text)
    has_explicit_formula = bool(resolved.get("formula"))
    if not turn.trading_conditions and not has_explicit_formula:
        return False
    resolution = get_capability_index().resolver.resolve_prompt(compiler_text)
    if resolution.needs_clarification:
        return False
    if resolution.fragments and any(
        fragment.status != "matched" for fragment in resolution.fragments
    ):
        return False
    return bool(
        has_explicit_formula
        or resolved.get("boolean_groups")
        or resolution.fragments
    )


def _strategy_approval_state(chat: AISetupChatSession) -> str:
    context = chat.context_json or {}
    state = StrategyDraftState.from_dict(
        context.get("strategy_state") if isinstance(context.get("strategy_state"), dict) else None
    )
    return state.approval_state


def _first_approval_blocker(chat: AISetupChatSession) -> str:
    context = chat.context_json or {}
    pending = context.get("awaiting_clarification")
    if isinstance(pending, dict):
        question = str(pending.get("question") or "").strip()
        if question:
            return question
    for item in (
        *(chat.ambiguities or []),
        *(chat.unsupported_conditions or []),
        *(chat.lint_warnings or []),
    ):
        if not isinstance(item, dict):
            continue
        if item.get("blocking", True) is False or item.get("severity") not in {None, "critical"}:
            continue
        message = str(item.get("message") or item.get("label") or "").strip()
        if message:
            return message
    return "The draft is missing a measurable rule, timeframe, or market universe."


def _text_digest(value: str) -> str:
    """Stable digest of setup text, used to avoid recompiling an unchanged description."""
    return sha256(" ".join(value.split()).encode("utf-8")).hexdigest()


def _record_turn_runtime_stage(
    chat: AISetupChatSession,
    stage: str,
    started: float,
    *,
    cache_hit: bool,
) -> None:
    context = dict(chat.context_json or {})
    runtime = dict(context.get("turn_runtime") or {})
    stages = list(runtime.get("stages") or [])
    stages.append(
        {
            "stage": stage[:80],
            "duration_ms": max(0, round((monotonic() - started) * 1000)),
            "cache_hit": cache_hit,
        }
    )
    runtime["stages"] = stages[-20:]
    runtime["cache_hits"] = int(runtime.get("cache_hits") or 0) + int(cache_hit)
    context["turn_runtime"] = runtime
    chat.context_json = context


def _caused_by(
    exc: BaseException,
    types: tuple[type[BaseException], ...],
) -> BaseException | None:
    """The first exception in the chain that is one of ``types``.

    A transport failure is usually re-raised by the code that called the provider,
    so testing only the outermost exception misses it and the turn is reported as a
    strategy defect instead of an infrastructure one.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, types):
            return current
        current = current.__cause__ or current.__context__
    return None


def setup_chat_error_envelope(exc: BaseException) -> SetupChatErrorEnvelope:
    """Classify an unhandled turn failure into a sanitized, machine-readable envelope.

    The stage tells the operator which half of the pipeline broke without exposing
    any internal detail to the caller: a timeout against a model or market provider
    is a provider problem, a schema violation is a serialization problem, and a
    strategy build failure is a compile problem.
    """
    if isinstance(exc, SetupChatError):
        return SetupChatErrorEnvelope(
            error_code=exc.code,
            request_id=uuid4().hex,
            stage=exc.stage,
            retryable=exc.retryable,
            message=str(exc),
        )

    name = type(exc).__name__
    text = f"{name}: {exc}".casefold()

    if any(
        marker in text
        for marker in (
            "getaddrinfo failed",
            "name or service not known",
            "temporary failure in name resolution",
            "name resolution",
        )
    ):
        return SetupChatErrorEnvelope(
            error_code="TARGET_DNS_RESOLUTION_FAILURE",
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message="The configured provider hostname could not be resolved.",
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return SetupChatErrorEnvelope(
            error_code="TARGET_CONNECT_TIMEOUT",
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message="The connection to a required provider timed out.",
        )
    if isinstance(exc, httpx.ReadTimeout):
        return SetupChatErrorEnvelope(
            error_code="TARGET_READ_TIMEOUT",
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message="A required provider did not finish its response in time.",
        )
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in text:
        return SetupChatErrorEnvelope(
            error_code="TARGET_TOTAL_TIMEOUT",
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message="The bounded setup turn exceeded its total time limit.",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return SetupChatErrorEnvelope(
            error_code=(
                "TARGET_HTTP_409"
                if status_code == 409
                else "TARGET_HTTP_429"
                if status_code == 429
                else "TARGET_HTTP_5XX"
            ),
            request_id=uuid4().hex,
            stage="provider",
            retryable=status_code == 429 or status_code >= 500,
            message="A required provider returned an unavailable response.",
        )
    # Transport failures below reach here as themselves or wrapped by a caller, so
    # the chain is walked. Every one of them used to fall through to the final
    # branch and be reported as `STRATEGY_COMPILE_FAILED` — a refused connection
    # recorded as a chatbot answer. In run 20260728T132409Z that mislabelling
    # covered 12 of 34 cases: 4 refused connections and 8 dropped responses.
    transport = _caused_by(exc, (httpx.TransportError, ConnectionError))
    if transport is not None:
        if isinstance(transport, httpx.ConnectError | ConnectionRefusedError):
            code, note = (
                "TARGET_CONNECTION_REFUSED",
                "A required provider refused the connection.",
            )
        elif isinstance(transport, httpx.RemoteProtocolError):
            code, note = (
                "TARGET_PARTIAL_STREAM",
                "A required provider closed the response before it finished.",
            )
        else:
            code, note = (
                "TARGET_TRANSPORT_FAILURE",
                "The connection to a required provider failed.",
            )
        return SetupChatErrorEnvelope(
            error_code=code,
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message=note,
        )
    # `JSONDecodeError` is a `ValueError`, so it must be classified before the
    # compile branch below or a truncated provider body reads as a strategy defect.
    if _caused_by(exc, (json.JSONDecodeError,)) is not None:
        return SetupChatErrorEnvelope(
            error_code="TARGET_INVALID_JSON",
            request_id=uuid4().hex,
            stage="provider",
            retryable=True,
            message="A required provider returned a response that was not valid JSON.",
        )
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        location = (
            ".".join(str(part) for part in errors[0].get("loc") or ()) or None if errors else None
        )
        return SetupChatErrorEnvelope(
            error_code="TARGET_SCHEMA_VALIDATION",
            request_id=uuid4().hex,
            stage="serialize",
            retryable=False,
            message="The drafted response did not satisfy the strategy schema.",
            field=location[:200] if location else None,
        )
    if isinstance(exc, ValueError):
        return SetupChatErrorEnvelope(
            error_code="STRATEGY_COMPILE_FAILED",
            request_id=uuid4().hex,
            stage="compile",
            retryable=False,
            message="The setup could not be compiled into deterministic monitor rules.",
        )
    return SetupChatErrorEnvelope(
        error_code="STRATEGY_COMPILE_FAILED",
        request_id=uuid4().hex,
        stage="interpret",
        retryable=False,
        message="The setup could not be interpreted on this turn.",
    )


class SetupChatInterviewer(Protocol):
    async def classify_turn(
        self,
        *,
        history: list[dict[str, str]],
        current_message: str,
        accumulated_setup: str,
        active_clarification: dict[str, Any] | None = None,
        capability_context: dict[str, Any] | None = None,
    ) -> SetupChatTurnClassification: ...

    async def respond(
        self,
        *,
        history: list[dict[str, str]],
        current_message: str,
        accumulated_setup: str,
        capability_context: dict[str, Any] | None = None,
    ) -> SetupChatInterviewResult: ...


class OpenAISetupChatInterviewer:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.last_usage: dict[str, Any] = {}

    async def classify_turn(
        self,
        *,
        history: list[dict[str, str]],
        current_message: str,
        accumulated_setup: str,
        active_clarification: dict[str, Any] | None = None,
        capability_context: dict[str, Any] | None = None,
    ) -> SetupChatTurnClassification:
        """Route a turn before any of its text can mutate the strategy draft."""
        self.last_usage = {}
        if self.settings.openai_api_key is None:
            raise SetupChatError(
                "openai_not_configured",
                "AI Setup Chat is unavailable because OPENAI_API_KEY is not configured.",
                status_code=503,
            )
        route = select_setup_model(
            self.settings,
            current_message=current_message,
            accumulated_setup=accumulated_setup,
            history=history,
            active_clarification=active_clarification,
            capability_context=capability_context,
        )
        payload = {
            "model": route.model,
            "store": False,
            "max_output_tokens": 900,
            "reasoning": {"effort": route.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "traceedge_setup_turn_router",
                    "strict": True,
                    "schema": _turn_classification_schema(),
                }
            },
            "instructions": _turn_router_prompt() + evaluator_prompt_appendix(),
            "input": json.dumps(
                {
                    "conversation": history[-20:],
                    "current_message": current_message,
                    "accumulated_setup": accumulated_setup,
                    "active_clarification": active_clarification or {},
                    "capability_context": capability_context or {},
                },
                sort_keys=True,
            ),
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            response_payload = consume_evaluator_llm_fault()
            if response_payload is None:
                async with httpx.AsyncClient(
                    base_url=str(self.settings.openai_base_url).rstrip("/"),
                    timeout=self.settings.openai_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.post("/responses", headers=headers, json=payload)
                response.raise_for_status()
                response_payload = response.json()
            self.last_usage = {
                **dict(response_payload.get("usage") or {}),
                **route.usage_metadata(),
            }
            return SetupChatTurnClassification.model_validate_json(
                _extract_responses_output_text(response_payload)
            )
        except SetupChatError:
            raise
        except (
            httpx.HTTPError,
            ValidationError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise SetupChatError(
                "ai_turn_classification_failed",
                "I could not classify that message safely. Please retry.",
                status_code=502,
            ) from exc

    async def respond(
        self,
        *,
        history: list[dict[str, str]],
        current_message: str,
        accumulated_setup: str,
        capability_context: dict[str, Any] | None = None,
    ) -> SetupChatInterviewResult:
        if self.settings.openai_api_key is None:
            raise SetupChatError(
                "openai_not_configured",
                "AI Setup Chat is unavailable because OPENAI_API_KEY is not configured.",
                status_code=503,
            )
        route = select_setup_model(
            self.settings,
            current_message=current_message,
            accumulated_setup=accumulated_setup,
            history=history,
            capability_context=capability_context,
        )
        payload = {
            "model": route.model,
            "store": False,
            "max_output_tokens": 1600,
            "reasoning": {"effort": route.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "traceedge_setup_interview",
                    "strict": True,
                    "schema": _interview_schema(),
                }
            },
            "instructions": _system_prompt() + evaluator_prompt_appendix(),
            "input": json.dumps(
                {
                    "conversation": history[-20:],
                    "current_message": current_message,
                    "accumulated_setup": accumulated_setup,
                    "capability_context": capability_context or {},
                },
                sort_keys=True,
            ),
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            response_payload = consume_evaluator_llm_fault()
            if response_payload is None:
                async with httpx.AsyncClient(
                    base_url=str(self.settings.openai_base_url).rstrip("/"),
                    timeout=self.settings.openai_timeout_seconds,
                    transport=self.transport,
                ) as client:
                    response = await client.post("/responses", headers=headers, json=payload)
                response.raise_for_status()
                response_payload = response.json()
            self.last_usage = {
                **dict(response_payload.get("usage") or {}),
                **route.usage_metadata(),
            }
            return SetupChatInterviewResult.model_validate_json(
                _extract_responses_output_text(response_payload)
            )
        except SetupChatError:
            raise
        except (
            httpx.HTTPError,
            ValidationError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise SetupChatError(
                "ai_interview_failed",
                (
                    "I could not safely interpret that message. Please retry or describe "
                    "the setup in more measurable terms."
                ),
                status_code=502,
            ) from exc


class AISetupChatService:
    def __init__(
        self,
        settings: Settings,
        market_provider: MarketDataProvider,
        strategy_interpreter: StrategyInterpreter,
        *,
        interviewer: SetupChatInterviewer | None = None,
        agent_client: AgentResponsesClient | None = None,
        launch_agent: SetupChatAgent | None = None,
    ) -> None:
        self.settings = settings
        self.market_provider = market_provider
        self.strategy_interpreter = strategy_interpreter
        self.interviewer = interviewer or OpenAISetupChatInterviewer(settings)
        self.agent_client = agent_client
        #: Free text is answered by this agent. Injected in tests so a whole turn can
        #: be exercised without a paid call.
        self.launch_agent = launch_agent
        # One turn can need the same interpretation twice: once for the inactive draft
        # emitted while questions are open, once for the final compile. The service is
        # constructed per request, so memoising here bills the model call once.
        self._interpretations: dict[tuple[str, str, str, str], InterpretationPreview] = {}

    async def create_session(self, session: AsyncSession, user_id: UUID) -> AISetupChatSession:
        from ai_market_monitor.engine.strategy_draft_v2 import new_strategy_draft

        launch_context: dict[str, Any] = {}
        if not self.settings.setup_chat_legacy_test_compat_enabled:
            launch_context = {
                "strategy_draft_v2": new_strategy_draft().model_dump(mode="json"),
                "strategy_state_authority": "v2",
                "launch_pipeline_version": "3.1",
            }
        chat = AISetupChatSession(
            user_id=user_id,
            status="interviewing",
            title="New monitor",
            context_json={
                "setup_fragments": [],
                "resolved_ambiguities": {},
                "conversation_state": SetupChatConversationState().model_dump(mode="json"),
                **launch_context,
            },
        )
        session.add(chat)
        await session.flush()
        await self._append_message(
            session,
            chat,
            role="assistant",
            message_type="welcome",
            content=(
                "Hi, I'm your HilalMarkets setup assistant. Choose Scanner for a one-time market "
                "search or Monitor for persistent alerts. I'll make every rule clear before "
                "anything is created."
            ),
            payload={
                "start_modes": [
                    {
                        "key": "setup_mode",
                        "value": "scanner",
                        "label": "Scanner",
                        "description": (
                            "One-time market search. Add the conditions coins must match now; "
                            "nothing is saved as a monitor."
                        ),
                    },
                    {
                        "key": "setup_mode",
                        "value": "monitor",
                        "label": "Monitor",
                        "description": (
                            "Persistent monitoring with alerts, proof, notifications, and "
                            "lifecycle tracking after approval."
                        ),
                    },
                ]
            },
        )
        return chat

    async def latest_open_session(
        self, session: AsyncSession, user_id: UUID
    ) -> AISetupChatSession | None:
        return await session.scalar(
            select(AISetupChatSession)
            .where(
                AISetupChatSession.user_id == user_id,
                AISetupChatSession.status.in_(
                    [
                        "interviewing",
                        "needs_clarification",
                        "ready_for_approval",
                        "ready_to_scan",
                    ]
                ),
            )
            .order_by(AISetupChatSession.updated_at.desc())
            .limit(1)
        )

    async def owned_session(
        self, session: AsyncSession, user_id: UUID, chat_id: UUID
    ) -> AISetupChatSession:
        chat = await session.get(AISetupChatSession, chat_id)
        if chat is None or chat.user_id != user_id:
            raise SetupChatError("chat_not_found", "Setup chat was not found.", status_code=404)
        return chat

    async def approve_draft(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        expected_schema_hash: str,
        expected_executable_version: int | None = None,
        expected_executable_hash: str | None = None,
        confirmed_low_confidence_rule_keys: set[str] | None = None,
    ) -> None:
        """Approve the exact current draft through the existing domain gates."""

        if chat.status == "approved":
            context = dict(chat.context_json or {})
            v2_payload = context.get("strategy_draft_v2")
            if context.get("strategy_state_authority") == "v2" and isinstance(
                v2_payload, dict
            ):
                from ai_market_monitor.schemas.strategy_draft_v2 import StrategyDraftV2

                approved_v2 = StrategyDraftV2.model_validate(v2_payload)
                if (
                    approved_v2.approval.approved
                    and expected_executable_version == approved_v2.executable_version
                    and expected_executable_hash == approved_v2.executable_hash
                    and expected_schema_hash == approved_v2.approval.schema_hash
                    and chat.approved_strategy_id is not None
                    and chat.approved_strategy_version_id is not None
                ):
                    return
            raise SetupChatError(
                "setup_changed",
                "This approval request does not match the approved draft.",
                status_code=409,
            )
        if chat.status != "ready_for_approval" or not chat.draft_schema_json:
            raise SetupChatError(
                "setup_not_ready",
                "Resolve every blocking question before approving this setup.",
                status_code=409,
            )
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        canonical_hash = definition.canonical_hash()
        if canonical_hash != expected_schema_hash:
            raise SetupChatError(
                "setup_changed",
                "The translated rules changed. Review the latest translation before approval.",
                status_code=409,
            )
        low_confidence = {
            str(item["rule_key"])
            for item in (chat.rule_confidence or [])
            if item.get("requires_confirmation") and item.get("rule_key")
        }
        confirmed = confirmed_low_confidence_rule_keys or set()
        if not low_confidence.issubset(confirmed):
            raise SetupChatError(
                "low_confidence_confirmation_required",
                "Confirm every low-confidence rule before approval.",
                status_code=409,
            )
        if any(item.get("severity") == "critical" for item in (chat.lint_warnings or [])):
            raise SetupChatError(
                "strategy_lint_blocked",
                "Resolve critical strategy lint findings before approval.",
                status_code=409,
            )

        context = dict(chat.context_json or {})
        v2_payload = context.get("strategy_draft_v2")
        strategy_state: StrategyDraftState | None = None
        # None on the legacy V1 path, which has no screening evidence to bind. The
        # approval record then says so plainly instead of carrying an empty object that
        # reads like a check that passed.
        screening_evidence: ReviewedScreeningEvidence | None = None
        snapshot_hash: str
        if context.get("strategy_state_authority") == "v2" and isinstance(v2_payload, dict):
            from ai_market_monitor.engine.requirement_state import blocking_requirement_states
            from ai_market_monitor.engine.strategy_draft_v2 import (
                conversation_snapshot_hash,
                validate_draft_semantics,
            )
            from ai_market_monitor.schemas.strategy_draft_v2 import (
                ApprovalBindingV2,
                StrategyDraftV2,
            )

            draft_v2 = StrategyDraftV2.model_validate(v2_payload)
            if (
                draft_v2.authoring_blocking
                or blocking_requirement_states(draft_v2)
                or validate_draft_semantics(draft_v2)
            ):
                raise SetupChatError(
                    "setup_not_ready",
                    "Resolve every blocking V2 draft item before approval.",
                    status_code=409,
                )
            if (
                expected_executable_version != draft_v2.executable_version
                or expected_executable_hash != draft_v2.executable_hash
            ):
                raise SetupChatError(
                    "setup_changed",
                    "The V2 draft changed. Review the latest version before approval.",
                    status_code=409,
                )
            try:
                definition, screening_evidence = await SetupChatLaunchService(
                    self.settings,
                    self,
                    agent=self.launch_agent,
                ).revalidate_for_approval(
                    session,
                    chat,
                    draft_v2,
                    expected_executable_version=draft_v2.executable_version,
                    expected_executable_hash=draft_v2.executable_hash,
                    expected_schema_hash=expected_schema_hash,
                )
            except SetupLaunchError as exc:
                raise SetupChatError(
                    exc.code.casefold(),
                    str(exc),
                    status_code=exc.status_code,
                ) from exc
            canonical_hash = definition.canonical_hash()
            chat.draft_schema_json = definition.model_dump(mode="json")
            approval_messages = await self.messages(session, chat.id)
            snapshot_hash = conversation_snapshot_hash(
                (item.role, item.content) for item in approval_messages
            )
            draft_v2 = StrategyDraftV2.model_validate(
                draft_v2.model_copy(
                    update={
                        "approval": ApprovalBindingV2(
                            approved=True,
                            user_id=chat.user_id,
                            executable_version=draft_v2.executable_version,
                            executable_hash=draft_v2.executable_hash,
                            schema_hash=expected_schema_hash,
                            conversation_snapshot_hash=snapshot_hash,
                            # Which markets, under which policy, verified how. Bound so a
                            # later question about what was approved has one answer.
                            screening_evidence=screening_evidence,
                            approved_at=datetime.now(UTC),
                        ),
                        "executable_hash": draft_v2.executable_hash,
                        "workflow_state_hash": draft_v2.workflow_state_hash,
                    }
                ).model_dump(mode="json")
            )
            context["strategy_draft_v2"] = draft_v2.model_dump(mode="json")
            draft_version = draft_v2.executable_version
        else:
            state_payload = context.get("strategy_state")
            strategy_state = StrategyDraftState.from_dict(
                state_payload if isinstance(state_payload, dict) else None
            )
            legacy_snapshot_hash = strategy_state.conversation_snapshot_hash
            if legacy_snapshot_hash is None:
                raise SetupChatError(
                    "approval_snapshot_missing",
                    "The draft must be recompiled before approval.",
                    status_code=409,
                )
            snapshot_hash = legacy_snapshot_hash
            try:
                strategy_state = strategy_state.with_approval(
                    canonical_hash=canonical_hash,
                    draft_version=strategy_state.draft_version,
                    conversation_snapshot_hash=snapshot_hash,
                    user_id=str(chat.user_id),
                )
            except ValueError as exc:
                raise SetupChatError(
                    "setup_changed",
                    "The draft identity changed before approval. Review the latest version.",
                    status_code=409,
                ) from exc
            draft_version = strategy_state.draft_version

        preview = InterpretationPreview(
            strategy=definition,
            assumptions=chat.assumptions or [],
            ambiguities=[
                InterpretationIssue.model_validate(item) for item in (chat.ambiguities or [])
            ],
            unsupported_conditions=[
                InterpretationIssue.model_validate(item)
                for item in (chat.unsupported_conditions or [])
            ],
            interpreter=str(context.get("interpreter") or "ai-setup-chat"),
            raw_metadata={
                "source": "ai_setup_chat",
                "chat_session_id": str(chat.id),
                "draft_version": draft_version,
                "conversation_snapshot_hash": snapshot_hash,
            },
        )
        strategy_service = StrategyService(
            session,
            self.settings.disclaimer_version,
            self.settings,
        )
        strategy, version = await strategy_service.create_from_interpretation(
            chat.user_id,
            preview,
            source_text=chat.original_idea,
        )
        verification_service = VerifiedStrategyService(session, self.settings)
        await verification_service.prepare_version(
            user_id=chat.user_id,
            strategy=strategy,
            version=version,
        )
        await verification_service.sync_interpretation(
            user_id=chat.user_id,
            strategy=strategy,
            version=version,
        )
        await verification_service.approve_visible_draft(
            user_id=chat.user_id,
            version=version,
            expected_schema_hash=version.schema_hash,
        )
        await strategy_service.approve(
            version,
            user_id=chat.user_id,
            expected_schema_hash=version.schema_hash,
        )
        # What the user reviewed, stored on the version that runs.
        #
        # The market-data contract is the part the worker needs: it says whether every
        # market was checked before approval or only a sample, and therefore which
        # markets must be checked again on every cycle. Written even when screening did
        # not run, so an empty record means "nothing was promised" rather than "the
        # record is missing" — the runtime reads both the same fail-closed way.
        version.approval_evidence = {
            "screening_evidence": (
                screening_evidence.model_dump(mode="json")
                if screening_evidence is not None
                else {}
            ),
            "preflight_manifest": (
                (chat.context_json or {}).get(REVIEWED_PREFLIGHT_MANIFEST_KEY) or {}
            ),
            "membership_sentence": (
                screening_evidence.membership_sentence
                if screening_evidence is not None
                else ""
            ),
        }
        if strategy_state is not None:
            strategy_state = strategy_state.mark_compiled()
        artifact_hashes = {
            str(rule.capability_artifact_hash)
            for rule in _condition_rules(definition.conditions)
            if rule.capability_artifact_hash
        }
        if artifact_hashes:
            await CapabilityExtensionService(self.settings).link_strategy_version(
                session,
                artifact_hashes=artifact_hashes,
                strategy_version_id=version.id,
            )
        if strategy_state is not None:
            context["strategy_state"] = strategy_state.to_dict()
        context["approved_draft_version"] = draft_version
        context["approved_schema_hash"] = canonical_hash
        context["approved_conversation_snapshot_hash"] = snapshot_hash
        chat.context_json = context
        chat.status = "approved"
        chat.approved_strategy_id = strategy.id
        chat.approved_strategy_version_id = version.id
        chat.approved_at = datetime.now(UTC)
        await session.flush()

    async def messages(self, session: AsyncSession, chat_id: UUID) -> list[AISetupChatMessage]:
        return list(
            (
                await session.scalars(
                    select(AISetupChatMessage)
                    .where(AISetupChatMessage.session_id == chat_id)
                    .order_by(AISetupChatMessage.sequence.asc())
                )
            ).all()
        )

    async def attach_turn_usage(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        correlation_id: str,
        duration_ms: int | None = None,
    ) -> None:
        runtime = dict((chat.context_json or {}).get("turn_runtime") or {})
        if runtime.get("attach") is False:
            return
        events = list(
            (
                await session.scalars(
                    select(AIUsageEvent)
                    .where(AIUsageEvent.chat_session_id == chat.id)
                    .order_by(AIUsageEvent.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
        correlated = [
            event
            for event in events
            if (event.raw_usage or {}).get("_traceedge_correlation_id") == correlation_id
        ]
        assistant = await session.scalar(
            select(AISetupChatMessage)
            .where(
                AISetupChatMessage.session_id == chat.id,
                AISetupChatMessage.role == "assistant",
            )
            .order_by(AISetupChatMessage.sequence.desc())
            .limit(1)
        )
        if assistant is None:
            return
        models = sorted({event.model for event in correlated})
        operation_counts = Counter(event.operation for event in correlated)
        usage = {
            "input_tokens": sum(event.input_tokens for event in correlated),
            "input_tokens_details": {
                "cached_tokens": sum(event.cached_input_tokens for event in correlated)
            },
            "output_tokens": sum(event.output_tokens for event in correlated),
            "output_tokens_details": {
                "reasoning_tokens": sum(event.reasoning_tokens for event in correlated)
            },
            "estimated_cost_usd": float(
                sum(
                    (event.estimated_cost_usd for event in correlated),
                    start=0,
                )
            ),
            "models": models,
            "model_call_count": len(correlated),
            "operation_counts": dict(sorted(operation_counts.items())),
            "planner_repair_attempt_count": sum(
                int((event.raw_usage or {}).get("_setup_repair_attempts") or 0)
                for event in correlated
            ),
            "planner_repair_success_count": sum(
                int((event.raw_usage or {}).get("_setup_repair_successes") or 0)
                for event in correlated
            ),
            "retry_count": sum(
                int((event.raw_usage or {}).get("_traceedge_retry_count") or 0)
                for event in correlated
            ),
            "cache_hits": int(runtime.get("cache_hits") or 0),
            "stage_timings": list(runtime.get("stages") or [])[-20:],
            "turn_duration_ms": max(0, int(duration_ms or 0)),
        }
        payload = dict(assistant.payload or {})
        payload["_traceedge_model"] = (
            models[0] if len(models) == 1 else "mixed" if models else "deterministic"
        )
        payload["usage"] = usage
        assistant.payload = payload

    async def handle_message(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        option_key: str | None = None,
        option_value: str | None = None,
        option_label: str | None = None,
        client_message_id: str | None = None,
        answered_question_id: str | None = None,
        answered_step_revision: int | None = None,
    ) -> AISetupChatSession:
        if not self.settings.setup_chat_legacy_test_compat_enabled:
            try:
                return await SetupChatLaunchService(
                    self.settings,
                    self,
                    agent=self.launch_agent,
                ).handle(
                    session,
                    chat,
                    message=message,
                    option_key=option_key,
                    option_value=option_value,
                    option_label=option_label,
                    client_message_id=client_message_id,
                    answered_question_id=answered_question_id,
                    answered_step_revision=answered_step_revision,
                )
            except SetupLaunchError as exc:
                raise SetupChatError(
                    exc.code,
                    str(exc),
                    status_code=exc.status_code,
                    stage=exc.stage,
                    retryable=exc.retryable,
                ) from exc
        if (chat.context_json or {}).get("strategy_state_authority") == "v2":
            raise SetupChatError(
                "legacy_v2_read_only",
                "This V2 draft can only be changed through the current Setup Chat runtime.",
                status_code=409,
            )
        cleaned = " ".join(message.split())
        initial_strategy_turn = (
            classify_strategy_turn(cleaned) if cleaned and not option_key else None
        )
        if chat.status == "approved":
            # Replaying the same authenticated approval is a no-op. It must not
            # create another strategy version or turn an idempotent retry into 409.
            if initial_strategy_turn is not None and initial_strategy_turn.is_approval:
                context = dict(chat.context_json or {})
                context["turn_runtime"] = {"attach": False, "cache_hits": 0, "stages": []}
                chat.context_json = context
                return chat
            if initial_strategy_turn is None or not _turn_materially_updates_strategy(
                initial_strategy_turn
            ):
                raise SetupChatError(
                    "chat_already_approved",
                    (
                        "This setup is already approved. Submit a measurable rule change "
                        "to create a new draft version."
                    ),
                    status_code=409,
                )
            context = dict(chat.context_json or {})
            previous_approvals = list(context.get("previous_approvals") or [])
            previous_approvals.append(
                {
                    "draft_version": context.get("approved_draft_version"),
                    "schema_hash": context.get("approved_schema_hash"),
                    "conversation_snapshot_hash": context.get(
                        "approved_conversation_snapshot_hash"
                    ),
                    "strategy_id": (
                        str(chat.approved_strategy_id) if chat.approved_strategy_id else None
                    ),
                    "strategy_version_id": (
                        str(chat.approved_strategy_version_id)
                        if chat.approved_strategy_version_id
                        else None
                    ),
                    "approved_at": chat.approved_at.isoformat() if chat.approved_at else None,
                    "invalidated_by": cleaned[:500],
                }
            )
            context["previous_approvals"] = previous_approvals[-50:]
            context["approval_invalidated_by_material_edit"] = True
            for key in (
                "approved_draft_version",
                "approved_schema_hash",
                "approved_conversation_snapshot_hash",
                "last_approval_refresh_key",
            ):
                context.pop(key, None)
            chat.context_json = context
            chat.status = "interviewing"
            chat.approved_at = None
            chat.approved_strategy_id = None
            chat.approved_strategy_version_id = None
        if client_message_id:
            existing = await session.scalar(
                select(AISetupChatMessage.id).where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.client_message_id == client_message_id,
                )
            )
            if existing is not None:
                context = dict(chat.context_json or {})
                context["turn_runtime"] = {"attach": False, "cache_hits": 0, "stages": []}
                chat.context_json = context
                return chat
        context = dict(chat.context_json or {})
        context["turn_runtime"] = {"attach": True, "cache_hits": 0, "stages": []}
        chat.context_json = dict(context)
        fragments = list(context.get("setup_fragments") or [])
        fragment_count_before = len(fragments)
        resolved = dict(context.get("resolved_ambiguities") or {})
        answered_keys = set(context.get("answered_clarification_keys") or [])
        answered_fingerprints = set(context.get("answered_clarification_fingerprints") or [])
        pending_clarification = dict(context.get("awaiting_clarification", {}) or {})
        awaiting_key = str(
            pending_clarification.get("key") or context.get("awaiting_clarification_key", "") or ""
        )
        awaiting_fingerprint = str(
            pending_clarification.get("fingerprint") or f"key:{awaiting_key}"
        )
        pending_model = _pending_clarification_model(pending_clarification)
        selected_option = _selected_clarification_option(pending_model, option_value)
        prior_messages = await self.messages(session, chat.id)
        history = _conversation_history(prior_messages)
        strategy_turn = initial_strategy_turn
        if strategy_turn is not None and strategy_turn.is_approval:
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="approval",
                content=cleaned,
                payload={"approval_source": "authenticated_chat_phrase"},
                client_message_id=client_message_id,
            )
            current_state = StrategyDraftState.from_dict(
                context.get("strategy_state")
                if isinstance(context.get("strategy_state"), dict)
                else None
            )
            accumulated = "\n".join(str(item) for item in fragments)
            compiler_text = canonical_compiler_text(current_state, fallback=accumulated)
            if chat.status != "ready_for_approval" and _setup_is_complete(chat, compiler_text):
                refresh_key = _text_digest(compiler_text)
                if context.get("last_approval_refresh_key") != refresh_key:
                    await self._compile(session, chat, compiler_text)
                    context = dict(chat.context_json or {})
                    context["last_approval_refresh_key"] = refresh_key
                    chat.context_json = context
            if chat.status != "ready_for_approval" or not chat.draft_schema_json:
                blocker = _first_approval_blocker(chat)
                await self._assistant(
                    session,
                    chat,
                    (
                        "I cannot approve this draft yet. "
                        f"{blocker} Resolve that item, then approve the updated version."
                    ),
                    message_type="approval_blocked",
                    payload={
                        "approved": False,
                        "can_approve": False,
                        "approval_state": _strategy_approval_state(chat),
                    },
                )
                return chat
            definition = StrategyDefinition.model_validate(chat.draft_schema_json)
            confirmed = {
                str(item["rule_key"])
                for item in (chat.rule_confidence or [])
                if item.get("requires_confirmation") and item.get("rule_key")
            }
            await self.approve_draft(
                session,
                chat,
                expected_schema_hash=definition.canonical_hash(),
                confirmed_low_confidence_rule_keys=confirmed,
            )
            await self._assistant(
                session,
                chat,
                (
                    "Your exact reviewed Watchlist version is approved. "
                    "It has not been activated; activation remains a separate action."
                ),
                message_type="approval_confirmed",
                payload={
                    "approved": True,
                    "approved_schema_hash": definition.canonical_hash(),
                    "approved_strategy_id": str(chat.approved_strategy_id),
                    "approved_strategy_version_id": str(chat.approved_strategy_version_id),
                },
            )
            return chat
        if (
            strategy_turn is not None
            and chat.status == "ready_for_approval"
            and _requests_stale_approval_reuse(cleaned, strategy_turn)
        ):
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="approval_policy",
                content=cleaned,
                payload={"approval_source": "stale_approval_reuse_request"},
                client_message_id=client_message_id,
            )
            canonical_hash = (
                StrategyDefinition.model_validate(chat.draft_schema_json).canonical_hash()
                if chat.draft_schema_json
                else None
            )
            await self._assistant(
                session,
                chat,
                (
                    "The previous approval cannot carry over to this edited draft. "
                    f"Review the current version and hash {canonical_hash}, then submit "
                    "a new explicit approval for this exact version."
                ),
                message_type="approval_required",
                payload={
                    "approved": False,
                    "can_approve": bool(canonical_hash),
                    "schema_hash": canonical_hash,
                    "approval_state": _strategy_approval_state(chat),
                },
            )
            return chat
        agent_enabled_for_user = self.settings.ai_agent_control_enabled and (
            self.settings.ai_agent_shadow_mode
            or _agent_rollout_enabled(chat.user_id, self.settings.ai_agent_rollout_percent)
        )
        deterministic_state_update = (
            strategy_turn is not None and _is_deterministic_state_update(strategy_turn)
        )
        if (
            agent_enabled_for_user
            and cleaned
            and not option_key
            and pending_model is None
            and not deterministic_state_update
        ):
            # Canonical state is application-owned and must be current before the
            # model sees this turn. The agent may choose a tool, but it does not
            # decide whether user text mutates the strategy or reconstruct state
            # from conversation memory.
            strategy_turn = classify_strategy_turn(cleaned)
            mutates_strategy = (
                not _looks_like_product_question(cleaned)
                and not _is_explanation_request(cleaned)
                and any(
                    fragment.category
                    in {
                        "SYMBOL",
                        "INCLUDE",
                        "EXCLUDE",
                        "TIMEFRAME",
                        "DIRECTION",
                        "OPERATOR",
                        "THRESHOLD",
                        "FORMULA",
                        "REVERSION",
                        "TRADING_MECHANIC",
                    }
                    for fragment in strategy_turn.fragments
                )
            )
            if mutates_strategy:
                normalized = " ".join(cleaned.casefold().split())
                if normalized not in {
                    " ".join(str(fragment).casefold().split()) for fragment in fragments
                }:
                    fragments.append(cleaned)
                context["setup_fragments"] = fragments[-30:]
                context["strategy_state"] = _advance_strategy_state(
                    context.get("strategy_state"),
                    [cleaned],
                ).to_dict()
                context["last_strategy_turn"] = {
                    "text": cleaned[:1000],
                    "fragment_kinds": [fragment.kind for fragment in strategy_turn.fragments],
                }
                chat.context_json = dict(context)
                if not chat.original_idea:
                    chat.original_idea = cleaned
                    chat.title = _chat_title(cleaned)
            outcome = await self._run_bounded_agent(
                session,
                chat,
                message=cleaned,
                history=history,
            )
            if outcome.shadow_mode:
                context = dict(chat.context_json or {})
                context["agent_shadow_run_id"] = str(outcome.run_id)
                chat.context_json = context
            elif outcome.handled:
                await self._apply_agent_outcome(
                    session,
                    chat,
                    message=cleaned,
                    client_message_id=client_message_id,
                    outcome=outcome,
                )
                return chat
            # The legacy fallback now starts from the same deterministic state.
            # Count only fragments added after this point when applying fallback
            # patches, avoiding a second copy of the current turn.
            context = dict(chat.context_json or {})
            fragments = list(context.get("setup_fragments") or [])
            fragment_count_before = len(fragments)
        turn_classification: SetupChatTurnClassification | None = None
        routed_technical_fragments: list[str] = []
        if cleaned and not option_key:
            if awaiting_key == "monitor_name":
                turn_classification = _clarification_answer_turn(cleaned)
            elif deterministic_state_update:
                turn_classification = _fallback_turn_classification(
                    cleaned,
                    active_clarification=pending_model,
                )
            else:
                turn_classification = await self._route_turn(
                    history=history,
                    current_message=cleaned,
                    accumulated_setup="\n".join(fragments),
                    active_clarification=pending_model,
                )
                await CapabilityCoverageService(self.settings).record_usage(
                    session,
                    chat=chat,
                    operation="setup_turn_classification",
                    usage=getattr(self.interviewer, "last_usage", None),
                )
            turn_classification = _normalize_turn_classification(
                turn_classification,
                current_message=cleaned,
                active_clarification=pending_model,
            )
            routed_technical_fragments = _validated_technical_fragments(
                cleaned, turn_classification.technical_fragments
            )
            if _is_non_mutating_turn(
                turn_classification, routed_technical_fragments
            ) and not (
                pending_model is not None
                and turn_classification.intent == "conversation"
            ):
                await self._append_message(
                    session,
                    chat,
                    role="user",
                    message_type="text",
                    content=cleaned,
                    payload={"turn_classification": turn_classification.model_dump(mode="json")},
                    client_message_id=client_message_id,
                )
                context["last_turn_classification"] = turn_classification.model_dump(mode="json")
                chat.context_json = dict(context)
                if turn_classification.intent == "market_snapshot":
                    snapshot = await self.market_snapshot()
                    await self._assistant(
                        session,
                        chat,
                        snapshot.message,
                        message_type="market_snapshot",
                        payload=snapshot.model_dump(mode="json"),
                    )
                    return chat
                message_type = {
                    "conversation": "conversation",
                    "product_question": "product_answer",
                    "option_question": "clarification_help",
                    "unsafe": "scope_refusal",
                    "out_of_scope": "scope_refusal",
                }.get(turn_classification.intent, "conversation")
                if (
                    turn_classification.intent == "conversation"
                    and self._classify(cleaned) == "greeting"
                ):
                    message_type = "greeting"
                response_payload: dict[str, Any] = {
                    "turn_classification": turn_classification.model_dump(mode="json")
                }
                assistant_message = turn_classification.assistant_message.strip()
                if pending_model is not None:
                    pending_model = _with_other_option(pending_model)
                    response_payload.update(
                        {
                            "clarifications": [pending_model.model_dump(mode="json")],
                            "awaiting_answer": True,
                            "beginner_note": pending_model.reason,
                        }
                    )
                    if turn_classification.intent == "option_question":
                        fallback, explanations = _clarification_help(pending_model)
                        assistant_message = assistant_message or fallback
                        response_payload["explanations"] = explanations
                if not assistant_message:
                    assistant_message = _turn_response_fallback(
                        turn_classification,
                        current_message=cleaned,
                        capability_context=_routing_capability_context(cleaned),
                    )
                await self._assistant(
                    session,
                    chat,
                    assistant_message,
                    message_type=message_type,
                    payload=response_payload,
                )
                return chat
        if pending_model is not None and _is_clarification_help_turn(
            cleaned=cleaned,
            option_value=option_value,
            option_label=option_label,
            selected_option=selected_option,
        ):
            cleaned = (
                cleaned
                or option_label
                or (selected_option.label if selected_option else None)
                or "Explain the choices"
            )
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="option" if option_key else "text",
                content=cleaned,
                payload={
                    "option_key": option_key,
                    "option_value": option_value,
                    "option_label": option_label,
                    "option_action": "explain",
                },
                client_message_id=client_message_id,
            )
            context["clarification_help_requests"] = (
                int(context.get("clarification_help_requests") or 0) + 1
            )
            chat.context_json = dict(context)
            chat.status = "needs_clarification"
            explanation, explanation_items = _clarification_help(pending_model)
            await self._assistant(
                session,
                chat,
                explanation,
                message_type="clarification_help",
                payload={
                    "clarifications": [pending_model.model_dump(mode="json")],
                    "explanations": explanation_items,
                    "awaiting_answer": True,
                    "beginner_note": pending_model.reason,
                },
            )
            return chat
        if option_key and option_value == "__build_mechanic__":
            cleaned = cleaned or option_label or "Create this mechanic"
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="option",
                content=cleaned,
                payload={
                    "option_key": option_key,
                    "option_value": option_value,
                    "option_label": option_label,
                },
                client_message_id=client_message_id,
            )
            all_messages = await self.messages(session, chat.id)
            history = _conversation_history(all_messages)
            source_fragment = _extension_source_fragment(context, option_key)
            from ai_market_monitor.services.capability_extensions import (
                CapabilityExtensionService,
            )

            extension = await CapabilityExtensionService(self.settings).request(
                session,
                user_id=chat.user_id,
                chat_session_id=chat.id,
                source_prompt=source_fragment or chat.original_idea or cleaned,
                conversation_history=history,
            )
            context.pop("awaiting_clarification", None)
            context.pop("awaiting_clarification_key", None)
            context["capability_extension_id"] = str(extension.id)
            context["capability_extension_status"] = extension.status
            chat.context_json = context
            chat.status = "building_mechanic"
            return chat
        if option_key and option_value == "__other__":
            cleaned = cleaned or option_label or "Other (type in chat)"
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="option",
                content=cleaned,
                payload={
                    "option_key": option_key,
                    "option_value": option_value,
                    "option_label": option_label,
                },
                client_message_id=client_message_id,
            )
            chat.status = "needs_clarification"
            await self._assistant(
                session,
                chat,
                "Type your answer in your own words. I'll apply it to the question above.",
                message_type="custom_answer_requested",
                payload={"awaiting_custom_answer": True},
            )
            return chat
        if (
            cleaned
            and awaiting_key
            and not option_key
            and awaiting_key != "monitor_name"
            and pending_model is not None
            and not (
                turn_classification is not None
                and turn_classification.intent == "clarification_answer"
            )
            and not _answer_satisfies_clarification(pending_model, cleaned)
        ):
            await self._append_message(
                session,
                chat,
                role="user",
                message_type="text",
                content=cleaned,
                payload={},
                client_message_id=client_message_id,
            )
            pending_model = _with_other_option(pending_model)
            if _clarification_question_limit_reached(context, pending_model):
                await self._assistant(
                    session,
                    chat,
                    (
                        "That did not resolve the condition, and I will not keep repeating the "
                        "same question. Give the missing measurable detail directly, revise the "
                        "condition, or remove it."
                    ),
                    message_type="clarification_limit",
                    payload={
                        "awaiting_answer": True,
                        "clarification_limit_reached": True,
                        "maximum_questions_per_condition": (
                            MAX_CLARIFICATION_QUESTIONS_PER_CONDITION
                        ),
                    },
                )
                return chat
            _record_clarification_question(context, pending_model)
            chat.context_json = dict(context)
            await self._assistant(
                session,
                chat,
                f"I still need this detail: {pending_model.question}",
                message_type="clarification",
                payload={
                    "clarifications": [pending_model.model_dump(mode="json")],
                    "beginner_note": pending_model.reason,
                },
            )
            return chat
        if option_key and option_value:
            # The active question owns the answer. This makes a stale or malformed option key
            # harmless instead of leaving the current clarification unresolved and looping it.
            answer_key = awaiting_key or option_key
            resolved[answer_key] = option_value
            answered_keys.add(answer_key)
            answered_fingerprints.add(awaiting_fingerprint or f"key:{answer_key}")
            cleaned = cleaned or option_label or option_value
            if answer_key.startswith("capability_meaning_"):
                capability_key = _selected_capability_key(option_value)
                if capability_key:
                    selections = dict(context.get("capability_selections") or {})
                    selections[answer_key] = capability_key
                    context["capability_selections"] = selections
            elif answer_key not in {
                "setup_mode",
                "monitor_name",
                "screened_universe_mode",
                "screened_watchlist",
                "screened_explicit_assets",
            }:
                fragments.append(
                    _option_strategy_fragment(
                        pending_model,
                        selected_option=selected_option,
                        option_value=option_value,
                        option_label=option_label,
                        setup_context="\n".join(fragments),
                    )
                )
        elif cleaned and awaiting_key:
            resolved[awaiting_key] = cleaned
            answered_keys.add(awaiting_key)
            answered_fingerprints.add(awaiting_fingerprint or f"key:{awaiting_key}")
            if awaiting_key not in {
                "monitor_name",
                "screened_universe_mode",
                "screened_watchlist",
                "screened_explicit_assets",
            }:
                fragments.append(
                    _canonical_clarification_answer(
                        _pending_clarification_model(pending_clarification),
                        cleaned,
                        setup_context="\n".join(fragments),
                    )
                )
        elif routed_technical_fragments:
            fragments.extend(routed_technical_fragments)
        elif turn_classification is not None and turn_classification.intent in {
            "setup_instruction",
            "setup_revision",
        }:
            fragments.append(cleaned)
        elif turn_classification is not None and turn_classification.intent == "mixed":
            pass
        elif cleaned:
            fragments.append(cleaned)
        if awaiting_key and cleaned:
            context["pending_training_evidence"] = {
                "key": awaiting_key,
                "source_fragment": (
                    _extension_source_fragment(context, awaiting_key)
                    or chat.original_idea
                    or cleaned
                ),
                "question": pending_model.question if pending_model else awaiting_key,
                "answer": cleaned,
            }
        context.pop("awaiting_clarification", None)
        context.pop("awaiting_clarification_key", None)
        if not chat.original_idea and routed_technical_fragments:
            chat.original_idea = " ".join(routed_technical_fragments)
            chat.title = _chat_title(chat.original_idea)
        elif (
            not chat.original_idea
            and turn_classification is not None
            and turn_classification.intent in {"setup_instruction", "setup_revision"}
            and cleaned
        ):
            chat.original_idea = cleaned
            chat.title = _chat_title(cleaned)
        context["setup_fragments"] = fragments[-30:]
        # The accumulated text keeps every superseded statement, so the typed state is
        # what makes a later correction win over the wording it replaced.
        context["strategy_state"] = _advance_strategy_state(
            context.get("strategy_state"),
            fragments[fragment_count_before:],
        ).to_dict()
        context["resolved_ambiguities"] = resolved
        context["answered_clarification_keys"] = sorted(answered_keys)
        context["answered_clarification_fingerprints"] = sorted(answered_fingerprints)
        chat.context_json = dict(context)
        user_message = await self._append_message(
            session,
            chat,
            role="user",
            message_type="option" if option_key else "text",
            content=cleaned,
            payload={
                "option_key": option_key,
                "option_value": option_value,
                "option_label": option_label,
                "turn_classification": (
                    turn_classification.model_dump(mode="json")
                    if turn_classification is not None
                    else None
                ),
            },
            client_message_id=client_message_id,
        )
        new_fragments = fragments[fragment_count_before:]
        if new_fragments:
            fragment_records = list(context.get("setup_fragment_records") or [])
            for fragment in new_fragments:
                fragment_records.append(
                    {
                        "fragment": str(fragment)[:500],
                        "user_message_id": str(user_message.id),
                        "user_message_sequence": user_message.sequence,
                        "user_text": cleaned[:1000],
                    }
                )
            context["setup_fragment_records"] = fragment_records[-100:]
        if turn_classification is not None and turn_classification.intent == "setup_revision":
            correction_history = list(context.get("correction_history") or [])
            correction_field = awaiting_key or "strategy_logic"
            correction = {
                "field": correction_field,
                "user_message_id": str(user_message.id),
                "user_message_sequence": user_message.sequence,
                "user_text": cleaned[:1000],
                "superseded_schema_hash": context.get("schema_hash"),
            }
            correction_history.append(correction)
            context["correction_history"] = correction_history[-50:]
            latest_corrections = dict(context.get("latest_correction_by_field") or {})
            latest_corrections[correction_field] = correction
            context["latest_correction_by_field"] = latest_corrections
        chat.context_json = dict(context)
        await CapabilityCoverageService(self.settings).record_clarification_choice(
            session,
            chat=chat,
            option_key=option_key,
            option_value=option_value,
        )

        if awaiting_key == "monitor_name" or option_key == "monitor_name":
            await self._apply_monitor_name(session, chat, cleaned)
            return chat

        screening_answer_key = awaiting_key or (
            option_key
            if option_key
            in {"screened_universe_mode", "screened_watchlist", "screened_explicit_assets"}
            else ""
        )
        if screening_answer_key in {
            "screened_universe_mode",
            "screened_watchlist",
            "screened_explicit_assets",
        }:
            await self._apply_screened_universe_answer(
                session,
                chat,
                key=screening_answer_key,
                value=option_value or cleaned,
            )
            return chat

        if option_key == "setup_mode":
            mode = (option_value or "").casefold().strip()
            if mode not in {"scanner", "monitor"}:
                raise SetupChatError("invalid_setup_mode", "Choose Scanner or Monitor.")
            context["setup_mode"] = mode
            context.pop("scanner_result", None)
            chat.context_json = dict(context)
            chat.status = "interviewing"
            chat.draft_schema_json = None
            chat.translation_sheet = {}
            chat.lint_warnings = []
            chat.rule_confidence = []
            chat.ambiguities = []
            chat.unsupported_conditions = []
            if self.settings.sharia_screening_enforced:
                setup_language = "\n".join(
                    [chat.original_idea or "", *(context.get("setup_fragments") or [])]
                )
                if _requests_favorites_universe(setup_language):
                    await self._apply_screened_universe_answer(
                        session,
                        chat,
                        key="screened_universe_mode",
                        value=ShariaUniverseMode.APPROVED_WATCHLIST.value,
                    )
                    return chat
                await self._ask_screened_universe(session, chat)
                return chat
            await self._assistant(
                session,
                chat,
                (
                    "Scanner is ready. Describe the conditions coins should match right now."
                    if mode == "scanner"
                    else "Let's build your monitor. First, describe the market event that "
                    "should trigger it; we'll clarify filters and timing next."
                ),
                message_type="mode_selected",
                payload={"setup_mode": mode},
            )
            return chat

        if turn_classification is not None and turn_classification.assistant_message.strip():
            await self._assistant(
                session,
                chat,
                turn_classification.assistant_message.strip(),
                message_type="turn_context",
                payload={"turn_classification": turn_classification.model_dump(mode="json")},
            )

        accumulated = "\n".join(fragments)
        strategy_state = StrategyDraftState.from_dict(
            context.get("strategy_state")
            if isinstance(context.get("strategy_state"), dict)
            else None
        )
        technical_setup = canonical_compiler_text(strategy_state, fallback=accumulated)
        unsupported = _unsupported_data_request(technical_setup)
        if unsupported:
            chat.status = "needs_clarification"
            chat.unsupported_conditions = unsupported
            chat.draft_schema_json = None
            chat.translation_sheet = {}
            await self._assistant(
                session,
                chat,
                (
                    f"I can't validate {unsupported[0]['label']} with the configured spot-data "
                    "providers. Remove it or replace it with an OHLCV-based rule such as price, "
                    "volume, RSI, EMA, or a candle condition."
                ),
                message_type="unsupported",
                payload={"unsupported_conditions": unsupported, "can_approve": False},
            )
            return chat

        # Produce the inactive draft before the question gates, so a fully specified
        # setup always returns a structured object even when clarifications remain.
        chat.context_json = dict(context)
        await self._ensure_inactive_draft(session, chat, technical_setup)
        context = dict(chat.context_json or {})

        deterministic_clarifications = _unanswered_clarifications(
            _unresolved_ambiguities(technical_setup, resolved),
            answered_keys,
            answered_fingerprints,
        )
        deterministic_clarifications = _apply_clarification_question_budget(
            context,
            deterministic_clarifications,
        )
        deterministic_clarifications = _cap_open_clarifications(
            context,
            deterministic_clarifications,
        )
        if deterministic_clarifications:
            deterministic_clarifications = [
                _with_other_option(item) for item in deterministic_clarifications
            ]
            clarification = deterministic_clarifications[0]
            resolved_ambiguity_count = sum(
                key
                in {
                    "breakout",
                    "strong_volume",
                    "near_support",
                    "momentum",
                    "clean_retest",
                    "fakeout",
                    "confirmation",
                }
                for key in resolved
            )
            total = max(
                int(context.get("clarification_total") or 0),
                len(deterministic_clarifications) + resolved_ambiguity_count,
            )
            current = max(1, total - len(deterministic_clarifications) + 1)
            context["clarification_total"] = total
            new_question_set = _begin_clarification_set(
                context, deterministic_clarifications, source="deterministic"
            )
            _record_clarification_question(context, clarification)
            _set_awaiting_clarification(context, clarification)
            chat.context_json = dict(context)
            chat.status = "needs_clarification"
            chat.ambiguities = [
                item.model_dump(mode="json") for item in deterministic_clarifications
            ]
            if new_question_set:
                await self._assistant(
                    session,
                    chat,
                    _clarification_checkpoint_message(total, len(deterministic_clarifications)),
                    message_type="process_state",
                    payload=_clarification_checkpoint_payload(
                        source="deterministic",
                        total=total,
                        current=current,
                        remaining=len(deterministic_clarifications),
                    ),
                )
            await self._assistant(
                session,
                chat,
                (f"Question {current} of {total}: {clarification.question}"),
                message_type="clarification",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "beginner_note": clarification.reason,
                    "question_progress": {"current": current, "total": total},
                    "jargon": _beginner_explanations(technical_setup),
                },
            )
            return chat

        pending_ai = _unanswered_clarifications(
            [
                SetupChatClarification.model_validate(item)
                for item in (context.get("pending_ai_clarifications") or [])
            ],
            answered_keys,
            answered_fingerprints,
        )
        pending_ai = _apply_clarification_question_budget(context, pending_ai)
        pending_ai = _cap_open_clarifications(context, pending_ai)
        if pending_ai:
            pending_ai = [_with_other_option(item) for item in pending_ai]
            clarification = pending_ai.pop(0)
            total = int(context.get("ai_clarification_total") or (len(pending_ai) + 1))
            current = total - len(pending_ai)
            new_question_set = _begin_clarification_set(
                context, [clarification, *pending_ai], source="interviewer"
            )
            context["pending_ai_clarifications"] = [
                item.model_dump(mode="json") for item in pending_ai
            ]
            context["ai_clarification_current"] = current
            _record_clarification_question(context, clarification)
            _set_awaiting_clarification(context, clarification)
            chat.context_json = dict(context)
            chat.status = "needs_clarification"
            if new_question_set:
                await self._assistant(
                    session,
                    chat,
                    _clarification_checkpoint_message(total, len(pending_ai) + 1),
                    message_type="process_state",
                    payload=_clarification_checkpoint_payload(
                        source="interviewer",
                        total=total,
                        current=current,
                        remaining=len(pending_ai) + 1,
                    ),
                )
            await self._assistant(
                session,
                chat,
                f"Question {current} of {total}: {clarification.question}",
                message_type="clarification",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "question_progress": {"current": current, "total": total},
                    "beginner_note": clarification.reason,
                    "jargon": _beginner_explanations(technical_setup),
                },
            )
            return chat

        capability_resolution = get_capability_index().resolver.resolve_prompt(technical_setup)
        hybrid_resolution = await HybridCapabilityResolutionService(self.settings).resolve(
            capability_resolution,
            history=history,
            default_timeframe=_guided_setup(
                technical_setup,
                setup_mode=_setup_mode(chat),
                resolved_state=_resolved_strategy_state(chat),
            ).timeframe,
            selections=dict(context.get("capability_selections") or {}),
        )
        capability_resolution = hybrid_resolution.report
        context["capability_resolution"] = capability_resolution.to_dict()
        context["capability_bindings"] = hybrid_resolution.bindings
        chat.context_json = dict(context)
        await CapabilityCoverageService(self.settings).record_usage(
            session,
            chat=chat,
            operation="capability_rerank",
            usage=hybrid_resolution.usage,
        )
        await CapabilityCoverageService(self.settings).record_resolution(
            session,
            chat=chat,
            report=capability_resolution,
        )
        evidence = dict(context.get("pending_training_evidence") or {})
        if evidence and str(evidence.get("key") or "").startswith("capability_meaning_"):
            source = str(evidence.get("source_fragment") or "")
            matched = next(
                (
                    item
                    for item in capability_resolution.fragments
                    if item.status == "matched"
                    and " ".join(item.fragment.casefold().split())
                    == " ".join(source.casefold().split())
                ),
                None,
            )
            if matched is not None:
                selected_key = matched.selected_capability_key or (
                    matched.candidates[0].capability_key if matched.candidates else None
                )
                confidence = matched.selection_confidence or (
                    matched.candidates[0].confidence if matched.candidates else None
                )
                await CapabilityCoverageService(self.settings).record_clarification_evidence(
                    session,
                    chat=chat,
                    source_fragment=source,
                    question=str(evidence.get("question") or ""),
                    answer=str(evidence.get("answer") or ""),
                    capability_key=selected_key,
                    confidence=confidence,
                )
                context.pop("pending_training_evidence", None)
                chat.context_json = dict(context)
        resolver_clarifications = _resolver_clarifications(
            capability_resolution,
            resolved,
            allow_mechanic_creation=(
                self.settings.capability_extension_enabled
                and self.settings.openai_api_key is not None
            ),
        )
        resolver_clarifications = _apply_clarification_question_budget(
            context,
            resolver_clarifications,
        )
        resolver_clarifications = _cap_open_clarifications(
            context,
            resolver_clarifications,
        )
        if resolver_clarifications:
            resolver_clarifications = [_with_other_option(item) for item in resolver_clarifications]
            clarification = resolver_clarifications[0]
            new_question_set = _begin_clarification_set(
                context,
                resolver_clarifications,
                source="capability_resolver",
            )
            _record_clarification_question(context, clarification)
            _set_awaiting_clarification(context, clarification)
            chat.context_json = dict(context)
            chat.status = "needs_clarification"
            chat.ambiguities = [
                {
                    "code": item.key,
                    "message": item.question,
                    "field": item.key,
                    "options": [option.value for option in item.options],
                    "blocking": True,
                    "source_fragment": chat.original_idea or cleaned,
                }
                for item in resolver_clarifications
            ]
            if new_question_set:
                await self._assistant(
                    session,
                    chat,
                    _clarification_checkpoint_message(
                        len(resolver_clarifications), len(resolver_clarifications)
                    ),
                    message_type="process_state",
                    payload=_clarification_checkpoint_payload(
                        source="capability_resolver",
                        total=len(resolver_clarifications),
                        current=1,
                        remaining=len(resolver_clarifications),
                    ),
                )
            await self._assistant(
                session,
                chat,
                f"Question 1 of {len(resolver_clarifications)}: {clarification.question}",
                message_type="clarification",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "beginner_note": clarification.reason,
                    "question_progress": {
                        "current": 1,
                        "total": len(resolver_clarifications),
                    },
                },
            )
            return chat

        if (
            _setup_is_complete(chat, technical_setup)
            and _can_interpret_deterministically(chat, technical_setup)
        ):
            interview = SetupChatInterviewResult(
                intent="setup",
                assistant_message="The measurable setup is ready for deterministic review.",
                ready_to_compile=True,
                setup_summary=technical_setup,
                clarifications=[],
                suggestions=[],
            )
        else:
            interview = await self.interviewer.respond(
                history=history,
                current_message=cleaned,
                accumulated_setup=technical_setup,
                capability_context={
                    **capability_resolution.ai_context(),
                    "setup_mode": _setup_mode(chat),
                    "screened_methodology": context.get("sharia_methodology_name"),
                    "clarification_question_counts": (
                        context.get("clarification_question_counts") or {}
                    ),
                    "maximum_questions_per_condition": (
                        MAX_CLARIFICATION_QUESTIONS_PER_CONDITION
                    ),
                },
            )
            await CapabilityCoverageService(self.settings).record_usage(
                session,
                chat=chat,
                operation="setup_interview",
                usage=getattr(self.interviewer, "last_usage", None),
            )
        if interview.intent in {"out_of_scope", "unsafe", "greeting"}:
            await self._assistant(
                session,
                chat,
                interview.assistant_message,
                message_type=("greeting" if interview.intent == "greeting" else "scope_refusal"),
            )
            return chat
        if interview.clarifications or not interview.ready_to_compile:
            clarifications = _unanswered_clarifications(
                interview.clarifications,
                answered_keys,
                answered_fingerprints,
            )
            clarifications = [
                item
                for item in clarifications
                if not _clarification_answered_by_prompt(item, technical_setup)
            ]
            clarifications = _apply_clarification_question_budget(context, clarifications)
            clarifications = _cap_open_clarifications(context, clarifications)
            clarifications = [_with_other_option(item) for item in clarifications]
            if not clarifications and (
                bool(interview.clarifications) or _setup_is_complete(chat, technical_setup)
            ):
                # Either every question the model raised was already answered, or the
                # description already specifies every field the compiler needs. Asking
                # again would loop, which is what run 20260725T122105Z did for 41 of 42
                # cases. Compile and present the draft; approval stays with the user.
                interview = interview.model_copy(update={"ready_to_compile": True})
            if interview.ready_to_compile:
                await self._finalize_translation(session, chat, technical_setup, interview)
                return chat
            if not clarifications:
                clarifications = [
                    _with_other_option(
                        SetupChatClarification(
                            key="compile_confirmation",
                            question="Should I compile this into measurable monitor rules now?",
                            reason="You approve the final deterministic rules only after review.",
                            options=[
                                SetupChatOption(
                                    key="compile_confirmation",
                                    label="Yes, compile it",
                                    value="Compile the current setup into monitor rules",
                                ),
                                SetupChatOption(
                                    key="compile_confirmation",
                                    label="Not yet",
                                    value="Keep refining the setup",
                                ),
                            ],
                        )
                    )
                ]
            clarification = clarifications[0]
            context = dict(chat.context_json or {})
            context["pending_ai_clarifications"] = [
                item.model_dump(mode="json") for item in clarifications[1:]
            ]
            context["ai_clarification_total"] = len(clarifications)
            context["ai_clarification_current"] = 1
            new_question_set = _begin_clarification_set(
                context, clarifications, source="interviewer"
            )
            _record_clarification_question(context, clarification)
            _set_awaiting_clarification(context, clarification)
            chat.context_json = dict(context)
            chat.status = "needs_clarification"
            chat.ambiguities = [
                {
                    "code": item.key,
                    "message": item.question,
                    "field": item.key,
                    "options": [option.value for option in item.options],
                    "blocking": True,
                    "source_fragment": chat.original_idea or cleaned,
                }
                for item in clarifications
            ]
            if new_question_set:
                await self._assistant(
                    session,
                    chat,
                    _clarification_checkpoint_message(len(clarifications), len(clarifications)),
                    message_type="process_state",
                    payload=_clarification_checkpoint_payload(
                        source="interviewer",
                        total=len(clarifications),
                        current=1,
                        remaining=len(clarifications),
                    ),
                )
            await self._assistant(
                session,
                chat,
                f"Question 1 of {len(clarifications)}: {clarification.question}",
                message_type="clarification",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "suggestions": interview.suggestions,
                    "question_progress": {"current": 1, "total": len(clarifications)},
                    "jargon": _beginner_explanations(technical_setup),
                },
            )
            return chat

        await self._finalize_translation(session, chat, technical_setup, interview)

        return chat

    async def _ask_screened_universe(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> None:
        screening = ShariaScreeningService(session, self.settings)
        methodology = await screening.default_methodology()
        context = dict(chat.context_json or {})
        if methodology is None:
            context["sharia_configuration_blocked"] = True
            chat.context_json = context
            chat.status = "needs_clarification"
            await self._assistant(
                session,
                chat,
                (
                    "Screened monitoring is not available yet because no real approved "
                    "methodology is active. The development/test methodology is not a "
                    "religious ruling and cannot be used for scans or Watchlists."
                ),
                message_type="screening_unavailable",
                payload={"can_approve": False, "can_scan": False},
            )
            return
        context.update(
            {
                "sharia_methodology_id": str(methodology.id),
                "sharia_methodology_code": methodology.code,
                "sharia_methodology_name": methodology.name,
                "sharia_methodology_version": methodology.version,
                "allowed_sharia_statuses": sorted(
                    status.value for status in DEFAULT_ALLOWED_STATUSES
                ),
                "compliance_change_behavior": ComplianceChangeBehavior.PAUSE_ASSET.value,
                "sharia_configuration_blocked": False,
            }
        )
        clarification = _with_other_option(
            SetupChatClarification(
                key="screened_universe_mode",
                question="Which screened assets should HilalMarkets watch?",
                reason=(
                    f"The selected methodology is {methodology.name}, version "
                    f"{methodology.version}. Assets without a current eligible assessment "
                    "will be excluded."
                ),
                options=[
                    SetupChatOption(
                        key="screened_universe_mode",
                        label="All eligible spot assets",
                        value=ShariaUniverseMode.ELIGIBLE_MARKET.value,
                        description="Use every currently eligible asset that meets market filters.",
                    ),
                    SetupChatOption(
                        key="screened_universe_mode",
                        label="My Favorites",
                        value=ShariaUniverseMode.APPROVED_WATCHLIST.value,
                        description=(
                            "Use only eligible assets you have favorited in Halal Assets."
                        ),
                    ),
                    SetupChatOption(
                        key="screened_universe_mode",
                        label="Specific eligible assets",
                        value=ShariaUniverseMode.EXPLICIT_ASSETS.value,
                        description="Type the individual screened spot assets to watch.",
                    ),
                ],
            )
        )
        _set_awaiting_clarification(context, clarification)
        chat.context_json = context
        chat.status = "needs_clarification"
        await self._assistant(
            session,
            chat,
            clarification.question,
            message_type="screened_universe_required",
            payload={
                "clarifications": [clarification.model_dump(mode="json")],
                "screening_methodology": {
                    "id": str(methodology.id),
                    "name": methodology.name,
                    "version": methodology.version,
                },
                "can_approve": False,
                "can_scan": False,
            },
        )

    async def _apply_screened_universe_answer(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        key: str,
        value: str,
    ) -> None:
        context = dict(chat.context_json or {})
        if key == "screened_universe_mode":
            normalized = value.casefold().strip().replace(" ", "_")
            aliases = {
                "all_eligible_spot_assets": ShariaUniverseMode.ELIGIBLE_MARKET.value,
                "my_approved_watchlist": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "my_favorites": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "my_favourites": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "favorites": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "favourites": ShariaUniverseMode.APPROVED_WATCHLIST.value,
                "specific_eligible_assets": ShariaUniverseMode.EXPLICIT_ASSETS.value,
            }
            normalized = aliases.get(normalized, normalized)
            try:
                mode = ShariaUniverseMode(normalized)
            except ValueError:
                await self._ask_screened_universe(session, chat)
                return
            context["screened_universe_mode"] = mode.value
            if mode == ShariaUniverseMode.APPROVED_WATCHLIST:
                watchlists = list(
                    (
                        await session.scalars(
                            select(ApprovedWatchlist)
                            .where(ApprovedWatchlist.user_id == chat.user_id)
                            .order_by(
                                ApprovedWatchlist.is_default.desc(),
                                ApprovedWatchlist.name.asc(),
                            )
                        )
                    ).all()
                )
                if not watchlists:
                    chat.context_json = context
                    chat.status = "needs_clarification"
                    await self._assistant(
                        session,
                        chat,
                        (
                            "You do not have Favorites yet. Add eligible assets from Screened "
                            "Market, or choose all eligible or specific assets."
                        ),
                        message_type="screened_watchlist_missing",
                        payload={"can_approve": False, "can_scan": False},
                    )
                    return
                if len(watchlists) == 1:
                    context["approved_watchlist_id"] = str(watchlists[0].id)
                    context["approved_watchlist_name"] = watchlists[0].name
                    chat.context_json = context
                    await self._complete_screened_universe_selection(session, chat)
                    return
                clarification = SetupChatClarification(
                    key="screened_watchlist",
                    question="Which Favorites list should HilalMarkets use?",
                    reason=(
                        "Every asset is rechecked against the selected methodology before scanning."
                    ),
                    options=[
                        SetupChatOption(
                            key="screened_watchlist",
                            label=row.name,
                            value=str(row.id),
                            description="Use these favorited eligible assets",
                        )
                        for row in watchlists[:8]
                    ],
                )
                _set_awaiting_clarification(context, clarification)
                chat.context_json = context
                chat.status = "needs_clarification"
                await self._assistant(
                    session,
                    chat,
                    clarification.question,
                    message_type="screened_watchlist_required",
                    payload={"clarifications": [clarification.model_dump(mode="json")]},
                )
                return
            if mode == ShariaUniverseMode.EXPLICIT_ASSETS:
                clarification = SetupChatClarification(
                    key="screened_explicit_assets",
                    question="Which eligible spot assets should HilalMarkets watch?",
                    reason="Type symbols such as BTC, ETH, SOL or BTC/USDT, ETH/USDT.",
                )
                _set_awaiting_clarification(context, clarification)
                chat.context_json = context
                chat.status = "needs_clarification"
                await self._assistant(
                    session,
                    chat,
                    clarification.question,
                    message_type="screened_assets_required",
                    payload={
                        "clarifications": [clarification.model_dump(mode="json")],
                        "awaiting_answer": True,
                    },
                )
                return
            chat.context_json = context
            await self._complete_screened_universe_selection(session, chat)
            return

        if key == "screened_watchlist":
            try:
                watchlist_id = UUID(value)
            except ValueError:
                await self._ask_screened_universe(session, chat)
                return
            watchlist = await session.get(ApprovedWatchlist, watchlist_id)
            if watchlist is None or watchlist.user_id != chat.user_id:
                raise SetupChatError(
                    "watchlist_not_found",
                    "That approved watchlist is unavailable.",
                    status_code=404,
                )
            context["approved_watchlist_id"] = str(watchlist.id)
            context["approved_watchlist_name"] = watchlist.name
            chat.context_json = context
            await self._complete_screened_universe_selection(session, chat)
            return

        symbols = _parse_screened_symbols(value)
        if not symbols:
            clarification = SetupChatClarification(
                key="screened_explicit_assets",
                question="Type at least one asset symbol, for example BTC, ETH or SOL/USDT.",
                reason="Only evidence-backed eligible assets can enter the scan.",
            )
            _set_awaiting_clarification(context, clarification)
            chat.context_json = context
            chat.status = "needs_clarification"
            await self._assistant(
                session,
                chat,
                clarification.question,
                message_type="screened_assets_invalid",
                payload={"clarifications": [clarification.model_dump(mode="json")]},
            )
            return
        context["screened_explicit_symbols"] = symbols
        chat.context_json = context
        await self._complete_screened_universe_selection(session, chat)

    async def _complete_screened_universe_selection(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> None:
        context = dict(chat.context_json or {})
        methodology_id = UUID(str(context["sharia_methodology_id"]))
        mode = ShariaUniverseMode(context["screened_universe_mode"])
        screening = ShariaScreeningService(session, self.settings)
        assessments = await screening.effective_assessments(methodology_id)
        safety_holds = await screening.safety_hold_assets(assets=set(assessments))
        eligible_assets = {
            asset
            for asset, assessment in assessments.items()
            if assessment.status in DEFAULT_ALLOWED_STATUSES and asset not in safety_holds
        }
        scope_label = "all eligible spot assets"
        if mode == ShariaUniverseMode.EXPLICIT_ASSETS:
            requested_symbols = list(context.get("screened_explicit_symbols") or [])
            requested_assets = {canonical_asset(symbol) for symbol in requested_symbols}
            excluded = sorted(requested_assets - eligible_assets)
            included = sorted(requested_assets & eligible_assets)
            if not included:
                context["screened_explicit_excluded"] = excluded
                chat.context_json = context
                chat.status = "needs_clarification"
                await self._assistant(
                    session,
                    chat,
                    (
                        "None of those assets currently has an eligible assessment under "
                        "the selected methodology. Choose another eligible asset; nothing "
                        "was silently included."
                    ),
                    message_type="screened_assets_not_eligible",
                    payload={"excluded_assets": excluded, "can_approve": False},
                )
                return
            context["screened_explicit_symbols"] = [f"{asset}/USDT" for asset in included]
            context["screened_explicit_excluded"] = excluded
            eligible_count = len(included)
            scope_label = ", ".join(included)
        elif mode == ShariaUniverseMode.APPROVED_WATCHLIST:
            watchlist_id = UUID(str(context["approved_watchlist_id"]))
            watchlist_assets = set(
                (
                    await session.scalars(
                        select(ApprovedWatchlistAsset.canonical_asset).where(
                            ApprovedWatchlistAsset.watchlist_id == watchlist_id
                        )
                    )
                ).all()
            )
            eligible_count = len(watchlist_assets & eligible_assets)
            scope_label = str(context.get("approved_watchlist_name") or "approved watchlist")
        else:
            eligible_count = len(eligible_assets)
        context["screened_eligible_count"] = eligible_count
        context.pop("awaiting_clarification", None)
        context.pop("awaiting_clarification_key", None)
        chat.context_json = context
        chat.status = "interviewing"
        mode_name = "Scanner" if _setup_mode(chat) == "scanner" else "Watchlist"
        await self._assistant(
            session,
            chat,
            (
                f"Halal Assets set: {scope_label}. {eligible_count} currently eligible "
                f"asset{'s' if eligible_count != 1 else ''} match this screening scope. "
                f"If a status changes, the affected asset will be paused. "
                + (
                    "Now describe the conditions coins should match right now."
                    if _setup_mode(chat) == "scanner"
                    else f"Now tell me the measurable market event this {mode_name} should find."
                )
            ),
            message_type="screened_universe_selected",
            payload={
                "screened_market": {
                    "mode": mode.value,
                    "methodology_id": str(methodology_id),
                    "methodology_name": context.get("sharia_methodology_name"),
                    "methodology_version": context.get("sharia_methodology_version"),
                    "allowed_statuses": context.get("allowed_sharia_statuses"),
                    "eligible_count": eligible_count,
                    "compliance_change_behavior": context.get("compliance_change_behavior"),
                }
            },
        )

    async def _finalize_translation(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        accumulated: str,
        interview: SetupChatInterviewResult,
    ) -> None:
        # AI prose is presentation-only. Compiling it would let an example or paraphrase add
        # rules the user never requested and can make the approval report contradict the chat.
        compile_text = accumulated
        await self._compile(session, chat, compile_text)
        context = dict(chat.context_json or {})
        if (
            context.get("requires_monitor_name")
            and context.get("awaiting_clarification_key") == "monitor_name"
        ):
            clarification = _pending_clarification_model(
                dict(context.get("awaiting_clarification") or {})
            )
            await self._assistant(
                session,
                chat,
                "What would you like to name this monitor?",
                message_type="monitor_name_required",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")]
                    if clarification
                    else [],
                    "name_required": True,
                    "can_approve": False,
                    "setup_mode": "monitor",
                },
            )
            return
        refusal_reasons = _refusal_reasons(
            chat.lint_warnings,
            chat.ambiguities,
            chat.unsupported_conditions,
            chat.translation_sheet.get("unsupported_conditions") or [],
        )
        blocking_reason_count = sum(item["blocking"] for item in refusal_reasons)
        summary = str(chat.translation_sheet.get("summary_paragraph") or "").strip()
        assistant_message = (
            (
                f"{summary} I found {blocking_reason_count} detail"
                f"{'s' if blocking_reason_count != 1 else ''} that need your input before "
                "this can continue. Open the Translation Sheet to review the fields and the "
                "plain-language fixes under What needs attention."
            )
            if blocking_reason_count
            else (
                f"{summary} Review the Translation Sheet, then run the one-time scan if every "
                "field matches what you meant."
                if _setup_mode(chat) == "scanner"
                else f"{summary} Review the Translation Sheet beside this chat, especially each "
                "required rule, then approve only if it matches your idea."
            )
        )
        await self._assistant(
            session,
            chat,
            assistant_message,
            message_type="translation",
            payload={
                "translation_sheet": chat.translation_sheet,
                "lint_warnings": chat.lint_warnings,
                "refusal_reasons": refusal_reasons,
                "rule_confidence": chat.rule_confidence,
                "suggestions": interview.suggestions
                or _improvement_suggestions(
                    StrategyDefinition.model_validate(chat.draft_schema_json),
                    setup_mode=_setup_mode(chat),
                ),
                "can_approve": chat.status == "ready_for_approval",
                "can_scan": chat.status == "ready_to_scan",
                "setup_mode": _setup_mode(chat),
                "jargon": _beginner_explanations(accumulated),
            },
        )

    async def screened_percentage_snapshot(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        draft: StrategyDraftV2,
        direction: Literal["up", "down"],
        threshold: float,
        timeframe: str = "24h",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run one durable, policy-bound read-only percentage Scanner request.

        Typed Scanner failures intentionally cross this boundary.  The launch service
        localises them while preserving their safe error codes; swallowing them here
        would make quota, policy and programming failures look like the same data miss.
        """

        response = await OnDemandScanService(
            session,
            self.market_provider,
            settings=self.settings,
        ).run_percentage_snapshot(
            chat.user_id,
            draft=draft,
            direction=direction,
            threshold=threshold,
            timeframe=timeframe,
            idempotency_key=(
                idempotency_key or f"setup-chat-percentage:{chat.id}:{uuid4().hex}"
            ),
        )
        return {
            **response.model_dump(mode="json"),
            "read_only": True,
            "strategy_mutated": False,
            "query": {
                "movement_direction": direction,
                "threshold_percent": threshold,
                "measurement_window": timeframe,
            },
        }

    async def market_snapshot(
        self,
        *,
        exchange: str | None = None,
        quote_currency: str = "USDT",
    ) -> MarketSnapshotResponse:
        selected_exchange = (exchange or self.settings.market_data_exchange or "binance").lower()
        captured_at = datetime.now(UTC)
        provider_name = type(self.market_provider).__name__
        unavailable_reason = ""
        try:
            try:
                symbols = await self.market_provider.list_symbols(
                    selected_exchange, [quote_currency]
                )
            except Exception as exc:
                unavailable_reason = "the eligible symbol universe could not be loaded"
                raise ValueError(unavailable_reason) from exc
            unique_symbols = sorted(set(symbols))
            prioritized = [
                symbol
                for symbol in (f"BTC/{quote_currency}", f"ETH/{quote_currency}")
                if symbol in unique_symbols
            ]
            symbols = (prioritized + [s for s in unique_symbols if s not in prioritized])[
                : self.settings.market_breadth_max_symbols
            ]
            if not symbols:
                unavailable_reason = "the provider returned no eligible spot symbols"
                raise ValueError(unavailable_reason)
            try:
                metadata = await self.market_provider.fetch_universe_metadata(
                    selected_exchange, symbols, include_listing_dates=False
                )
            except Exception as exc:
                unavailable_reason = "24-hour ticker metadata could not be loaded"
                raise ValueError(unavailable_reason) from exc
            changes = [
                (symbol, float(values["percentage_24h"]))
                for symbol, values in metadata.items()
                if values.get("percentage_24h") is not None
            ]
            if not changes:
                unavailable_reason = "24-hour percentage changes were unavailable"
                raise ValueError(unavailable_reason)
            ordered = sorted(changes, key=lambda item: item[1], reverse=True)
            values = [value for _, value in changes]
            advancing = sum(1 for value in values if value > 0.05)
            declining = sum(1 for value in values if value < -0.05)
            unchanged = len(values) - advancing - declining
            average = fmean(values)
            dispersion = pstdev(values) if len(values) > 1 else 0.0
            broad = "mixed"
            if advancing >= len(values) * 0.6:
                broad = "broadly positive"
            elif declining >= len(values) * 0.6:
                broad = "broadly negative"
            volatility = (
                "quiet" if dispersion < 2 else "elevated" if dispersion >= 5 else "moderate"
            )
            by_symbol = dict(changes)

            def asset_status(symbol: str) -> MarketSnapshotAssetStatus | None:
                value = by_symbol.get(symbol)
                if value is None:
                    return None
                direction: Literal["advancing", "declining", "unchanged"] = (
                    "advancing" if value > 0.05 else "declining" if value < -0.05 else "unchanged"
                )
                return MarketSnapshotAssetStatus(
                    symbol=symbol,
                    percentage_24h=round(value, 2),
                    direction=direction,
                )

            top = [
                MarketSnapshotMover(symbol=symbol, percentage_24h=round(value, 2))
                for symbol, value in ordered[:5]
            ]
            bottom = [
                MarketSnapshotMover(symbol=symbol, percentage_24h=round(value, 2))
                for symbol, value in list(reversed(ordered[-5:]))
            ]
            leader_text = ", ".join(
                f"{item.symbol} {item.percentage_24h:+.2f}%" for item in top[:3]
            )
            loser_text = ", ".join(
                f"{item.symbol} {item.percentage_24h:+.2f}%" for item in bottom[:3]
            )
            btc = asset_status(f"BTC/{quote_currency}")
            eth = asset_status(f"ETH/{quote_currency}")
            majors = (
                ", ".join(
                    f"{item.symbol} {item.percentage_24h:+.2f}% ({item.direction})"
                    for item in (btc, eth)
                    if item is not None
                )
                or "BTC/ETH unavailable"
            )
            return MarketSnapshotResponse(
                status="available",
                exchange=selected_exchange,
                quote_currency=quote_currency,
                captured_at=captured_at,
                provider_name=provider_name,
                symbols_checked=len(changes),
                advancing=advancing,
                declining=declining,
                unchanged=unchanged,
                average_change_24h=round(average, 2),
                volatility_label=volatility,
                dispersion_24h=round(dispersion, 2),
                btc_status=btc,
                eth_status=eth,
                top_movers=top,
                bottom_movers=bottom,
                data_source=provider_name,
                message=(
                    f"{captured_at:%Y-%m-%d %H:%M} UTC | {provider_name} | "
                    f"{len(changes)} {selected_exchange.title()} {quote_currency} spot pairs. "
                    f"BTC/ETH: {majors}. Gainers: {leader_text}. Losers: {loser_text}. "
                    f"Breadth: {advancing} advancing, {declining} declining, {unchanged} flat; "
                    f"average {average:+.2f}%; {broad}, {volatility} dispersion "
                    f"({dispersion:.2f}%). Market context only, not financial advice."
                ),
            )
        except Exception:
            return MarketSnapshotResponse(
                status="unavailable",
                exchange=selected_exchange,
                quote_currency=quote_currency,
                captured_at=captured_at,
                provider_name=provider_name,
                data_source=provider_name,
                unavailable_reason=(
                    unavailable_reason or "the configured provider did not respond"
                ),
                message=(
                    f"I couldn't build the snapshot because "
                    f"{unavailable_reason or 'the configured provider did not respond'}. "
                    "No values were invented. Please retry when the provider is available."
                ),
            )

    async def _interpret_setup(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        setup_text: str,
        *,
        operation: str,
    ) -> InterpretationPreview:
        """Interpret ``setup_text`` once per request, per text, per binding set.

        A single turn can need the same interpretation twice — once for the inactive
        draft returned while questions are still open, once for the final compile.
        Interpreting twice would bill the model twice for an identical result.
        """
        bindings = list((chat.context_json or {}).get("capability_bindings") or [])
        mode = _setup_mode(chat)
        resolved_state = _resolved_strategy_state(chat)
        compiler_text = canonical_compiler_text(resolved_state, fallback=setup_text)
        cache_key = (
            compiler_text,
            mode,
            json.dumps(bindings, sort_keys=True, default=str),
            json.dumps(resolved_state, sort_keys=True, default=str),
        )
        started = monotonic()
        cached = self._interpretations.get(cache_key)
        if cached is not None:
            _record_turn_runtime_stage(chat, operation, started, cache_hit=True)
            return cached
        guided = _guided_setup(
            compiler_text,
            capability_bindings=bindings,
            setup_mode=mode,
            resolved_state=resolved_state,
        )
        state_has_formula = bool(resolved_state.get("formula"))
        # A formula already accepted into canonical state is application-owned.
        # Surrounding conversational prose or an operator glossary must not send it
        # back through a model that can omit settled context, direction, or universe
        # fields. The rule interpreter still reports any genuinely unsupported
        # companion mechanic, so this does not turn unknown wording into executable
        # logic.
        deterministic = state_has_formula or (
            _can_interpret_deterministically(chat, compiler_text)
            and bool(
                getattr(
                    self.strategy_interpreter,
                    "deterministic_core_authority",
                    False,
                )
            )
        )
        if deterministic:
            preview = await RuleBasedStrategyInterpreter().interpret(guided)
            runtime = dict((chat.context_json or {}).get("turn_runtime") or {})
            runtime["deterministic_interpretations"] = (
                int(runtime.get("deterministic_interpretations") or 0) + 1
            )
            context = dict(chat.context_json or {})
            context["turn_runtime"] = runtime
            chat.context_json = context
        else:
            if self.settings.openai_api_key is None:
                raise SetupChatError(
                    "openai_not_configured",
                    "AI Setup Chat is unavailable because OPENAI_API_KEY is not configured.",
                    status_code=503,
                )
            preview = await self.strategy_interpreter.interpret(guided)
        await CapabilityCoverageService(self.settings).record_usage(
            session,
            chat=chat,
            operation=operation,
            usage=(preview.raw_metadata or {}).get("openai_usage"),
        )
        self._interpretations[cache_key] = preview
        _record_turn_runtime_stage(chat, operation, started, cache_hit=False)
        return preview

    async def _ensure_inactive_draft(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        accumulated: str,
    ) -> None:
        """Compile an inactive draft as soon as the description is complete.

        Structured output must not depend on the interviewer model choosing to
        finish. Run 20260725T122105Z captured a strategy object in 1 of 42 cases
        because ``ready_to_compile`` is model-owned: a session could already hold
        every field the compiler needs and still loop on questions forever.

        When the deterministic requirement check says every compiler-required field
        is present, a draft is produced and returned even though questions remain
        open. The draft is inactive — this method never touches ``status``, so
        ``can_approve`` stays False and the approval route still refuses it. It is a
        preview of the current reading, not a request to approve anything.
        """
        if not accumulated.strip():
            return
        if not _setup_is_complete(chat, accumulated):
            return
        context = dict(chat.context_json or {})
        source_digest = _text_digest(accumulated)
        if source_digest in {
            context.get("compiled_source_digest"),
            context.get("inactive_draft_source_digest"),
        }:
            return
        try:
            preview = await self._interpret_setup(
                session, chat, accumulated, operation="inactive_draft_compile"
            )
            definition = StrategyDefinition.model_validate(preview.strategy.model_dump(mode="json"))
        except (SetupChatError, ValidationError, ValueError, httpx.HTTPError) as exc:
            # A preview must never break the turn the user is having. Record why it
            # could not be produced so the gap is visible instead of silent.
            context["inactive_draft_source_digest"] = source_digest
            context["inactive_draft_error"] = type(exc).__name__
            chat.context_json = context
            return
        # Interpretation records cache/model/runtime telemetry in context. Refresh
        # before persisting draft metadata so those measurements are not overwritten
        # by the pre-call snapshot.
        context = dict(chat.context_json or {})
        if _setup_mode(chat) == "monitor" and not context.get("confirmed_monitor_name"):
            # An AI-chosen name must not look confirmed on an unapproved draft.
            definition = definition.model_copy(update={"name": "Untitled Monitor"})
        chat.draft_schema_json = definition.model_dump(mode="json")
        chat.translation_sheet = translation_sheet(
            chat.original_idea or accumulated,
            definition,
            preview,
            setup_mode=_setup_mode(chat),
        )
        chat.lint_warnings = lint_strategy(definition, preview, setup_mode=_setup_mode(chat))
        chat.rule_confidence = rule_confidence(definition)
        chat.assumptions = list(preview.assumptions)
        chat.unsupported_conditions = [
            item.model_dump(mode="json") for item in preview.unsupported_conditions
        ]
        context["inactive_draft_source_digest"] = source_digest
        context["inactive_draft"] = True
        context.pop("inactive_draft_error", None)
        context["schema_hash"] = definition.canonical_hash()
        context["interpreter"] = preview.interpreter
        chat.context_json = context
        await session.flush()

    async def _compile(
        self, session: AsyncSession, chat: AISetupChatSession, setup_text: str
    ) -> None:
        preview = await self._interpret_setup(
            session, chat, setup_text, operation="strategy_compile"
        )
        definition = StrategyDefinition.model_validate(preview.strategy.model_dump(mode="json"))
        context = dict(chat.context_json or {})
        if (
            _setup_mode(chat) == "scanner"
            and not _explicit_closed_candle_requested(setup_text)
            and definition.trigger_mode != TriggerMode.INTRABAR
        ):
            definition = definition.model_copy(update={"trigger_mode": TriggerMode.INTRABAR})
            preview = preview.model_copy(
                update={
                    "strategy": definition,
                    "assumptions": [
                        *preview.assumptions,
                        (
                            "Scanner evaluates the current market snapshot because no "
                            "closed-candle requirement was requested."
                        ),
                    ],
                }
            )
        screening_resolution = None
        screening_error: dict[str, str] | None = None
        if self.settings.sharia_screening_enforced:
            try:
                mode = ShariaUniverseMode(str(context["screened_universe_mode"]))
                methodology_id = UUID(str(context["sharia_methodology_id"]))
                policy = ShariaPolicyDefinition(
                    universe_mode=mode,
                    methodology_id=methodology_id,
                    allowed_statuses=[
                        ShariaAssetStatus(value)
                        for value in context.get("allowed_sharia_statuses")
                        or [
                            ShariaAssetStatus.ELIGIBLE.value,
                            ShariaAssetStatus.ELIGIBLE_WITH_QUALIFICATIONS.value,
                        ]
                    ],
                    qualification_policy="include_with_warning",
                    disputed_asset_policy="exclude",
                    compliance_change_behavior=ComplianceChangeBehavior(
                        context.get("compliance_change_behavior")
                        or ComplianceChangeBehavior.PAUSE_ASSET.value
                    ),
                    approved_watchlist_id=(
                        UUID(str(context["approved_watchlist_id"]))
                        if context.get("approved_watchlist_id")
                        else None
                    ),
                )
                universe_update: dict[str, Any] = {"sharia_policy": policy}
                if mode == ShariaUniverseMode.EXPLICIT_ASSETS:
                    universe_update["include_symbols"] = list(
                        context.get("screened_explicit_symbols") or []
                    )
                definition = definition.model_copy(
                    update={"universe": definition.universe.model_copy(update=universe_update)}
                )
                screening_resolution = await ShariaUniverseResolver(
                    session,
                    self.market_provider,
                    self.settings,
                ).resolve(
                    definition,
                    user_id=chat.user_id,
                    persist_snapshot=False,
                )
                if not screening_resolution.included_symbols:
                    screening_error = {
                        "code": "empty_screened_universe",
                        "message": (
                            "No asset currently meets both the selected screening policy "
                            "and the market filters."
                        ),
                    }
            except (KeyError, ValueError, ShariaUniverseError) as exc:
                screening_error = {
                    "code": getattr(exc, "code", "screened_universe_required"),
                    "message": str(exc) or "Choose and validate Halal Assets before approval.",
                }
        requires_monitor_name = context.get("setup_mode") == "monitor" and not context.get(
            "confirmed_monitor_name"
        )
        if requires_monitor_name:
            suggested_names = _monitor_name_suggestions(definition)
            context["suggested_monitor_names"] = suggested_names
            context["requires_monitor_name"] = True
            context["interpreter_name_suggestion"] = definition.name
            definition = definition.model_copy(update={"name": "Untitled Monitor"})
        lint = lint_strategy(definition, preview, setup_mode=_setup_mode(chat))
        if screening_error:
            lint.append(
                {
                    "code": screening_error["code"],
                    "severity": "critical",
                    "message": screening_error["message"],
                }
            )
        confidence = rule_confidence(definition)
        chat.draft_schema_json = definition.model_dump(mode="json")
        chat.translation_sheet = translation_sheet(
            chat.original_idea or setup_text,
            definition,
            preview,
            setup_mode=_setup_mode(chat),
        )
        if self.settings.sharia_screening_enforced:
            chat.translation_sheet["screened_market"] = {
                "universe_mode": context.get("screened_universe_mode"),
                "methodology_id": context.get("sharia_methodology_id"),
                "methodology_name": context.get("sharia_methodology_name"),
                "methodology_version": context.get("sharia_methodology_version"),
                "allowed_statuses": context.get("allowed_sharia_statuses") or [],
                "eligible_assets": (
                    screening_resolution.included_count if screening_resolution else 0
                ),
                "assets_excluded_by_policy": (
                    screening_resolution.excluded_by_policy_count if screening_resolution else 0
                ),
                "insufficient_information": (
                    screening_resolution.insufficient_information_count
                    if screening_resolution
                    else 0
                ),
                "compliance_change_behavior": context.get(
                    "compliance_change_behavior",
                    ComplianceChangeBehavior.PAUSE_ASSET.value,
                ),
                "policy_hash": (screening_resolution.policy_hash if screening_resolution else None),
                "snapshot_hash": (
                    screening_resolution.snapshot_hash if screening_resolution else None
                ),
            }
        chat.lint_warnings = lint
        chat.rule_confidence = confidence
        chat.assumptions = list(preview.assumptions)
        chat.ambiguities = [item.model_dump(mode="json") for item in preview.ambiguities]
        chat.unsupported_conditions = [
            item.model_dump(mode="json") for item in preview.unsupported_conditions
        ]
        blocking = any(item["severity"] == "critical" for item in lint)
        chat.status = (
            "needs_clarification"
            if blocking
            else "needs_clarification"
            if requires_monitor_name
            else "ready_to_scan"
            if _setup_mode(chat) == "scanner"
            else "ready_for_approval"
        )
        intent_state = _structured_intent_state(
            definition,
            context=context,
            preview=preview,
            lint=lint,
            setup_mode=_setup_mode(chat),
        )
        context["intent_state"] = intent_state
        context["conversation_state"] = intent_state
        for key in (
            "awaiting_clarification",
            "awaiting_clarification_key",
            "pending_ai_clarifications",
            "active_clarification_keys",
            "active_clarification_source",
        ):
            context.pop(key, None)
        if requires_monitor_name and not blocking:
            clarification = SetupChatClarification(
                key="monitor_name",
                question="What would you like to name this monitor?",
                reason=(
                    "The confirmed name identifies this monitor in Lifecycles, alerts, and "
                    "health reports."
                ),
                options=[
                    SetupChatOption(
                        key="monitor_name",
                        label=name,
                        value=name,
                        description="Use this concise name",
                    )
                    for name in context["suggested_monitor_names"]
                ],
            )
            _set_awaiting_clarification(context, clarification)
        context.pop("inactive_draft", None)
        context.pop("inactive_draft_source_digest", None)
        context.pop("inactive_draft_error", None)
        canonical_hash = definition.canonical_hash()
        strategy_state = StrategyDraftState.from_dict(
            context.get("strategy_state")
            if isinstance(context.get("strategy_state"), dict)
            else None
        )
        unresolved_definitions = tuple(
            str(item.get("code") or item.get("field") or "unresolved_definition")
            for item in (chat.ambiguities or [])
            if item.get("blocking", True)
        )
        unsupported_capabilities = tuple(
            str(item.get("code") or item.get("field") or "unsupported_capability")
            for item in (chat.unsupported_conditions or [])
            if item.get("blocking", True)
        )
        critical_lint = tuple(
            str(item.get("code") or "critical_lint")
            for item in lint
            if item.get("severity") == "critical"
        )
        provider_requirements = tuple(
            str(item.get("capability_key") or item.get("requirements") or "")
            for item in (
                (context.get("capability_resolution") or {}).get("provider_requirements") or []
            )
            if item
        )
        strategy_state = strategy_state.with_compilation(
            canonical_hash=canonical_hash,
            conversation_snapshot_hash=_text_digest(
                json.dumps(
                    context.get("setup_fragments") or [],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            unresolved_definitions=(*unresolved_definitions, *critical_lint),
            unsupported_capabilities=unsupported_capabilities,
            provider_requirements=provider_requirements,
        )
        if strategy_state.approval_state == "READY_FOR_CONFIRMATION":
            strategy_state = strategy_state.awaiting_approval()
        context["strategy_state"] = strategy_state.to_dict()
        chat.context_json = {
            **context,
            "interpreter": preview.interpreter,
            "schema_hash": canonical_hash,
            # Records which description this draft came from so the same text is never
            # interpreted twice and a changed description always recompiles.
            "compiled_source_digest": _text_digest(setup_text),
        }
        await session.flush()

    async def _apply_monitor_name(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        requested_name: str,
    ) -> None:
        name = " ".join(requested_name.split()).strip()
        context = dict(chat.context_json or {})
        suggestions = list(context.get("suggested_monitor_names") or [])
        if not _valid_monitor_name(name):
            clarification = SetupChatClarification(
                key="monitor_name",
                question="Choose a monitor name using 3-80 letters, numbers, spaces, &, /, - or _.",
                reason="A clear name keeps lifecycle and health records easy to identify.",
                options=[
                    SetupChatOption(key="monitor_name", label=item, value=item)
                    for item in suggestions[:3]
                ],
            )
            _set_awaiting_clarification(context, clarification)
            chat.context_json = context
            chat.status = "needs_clarification"
            await self._assistant(
                session,
                chat,
                clarification.question,
                message_type="monitor_name_invalid",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "name_required": True,
                    "can_approve": False,
                },
            )
            return
        duplicate = await session.scalar(
            select(Strategy.id).where(
                Strategy.user_id == chat.user_id,
                func.lower(Strategy.name) == name.casefold(),
                Strategy.archived_at.is_(None),
            )
        )
        if duplicate is not None:
            alternatives = _deduplicated_name_suggestions(name, suggestions)
            clarification = SetupChatClarification(
                key="monitor_name",
                question=f'You already have a monitor named "{name}". Choose a distinct name.',
                reason="Distinct names make filters and lifecycle evidence unambiguous.",
                options=[
                    SetupChatOption(key="monitor_name", label=item, value=item)
                    for item in alternatives
                ],
            )
            _set_awaiting_clarification(context, clarification)
            chat.context_json = context
            chat.status = "needs_clarification"
            await self._assistant(
                session,
                chat,
                clarification.question,
                message_type="monitor_name_duplicate",
                payload={
                    "clarifications": [clarification.model_dump(mode="json")],
                    "name_required": True,
                    "can_approve": False,
                },
            )
            return
        if not chat.draft_schema_json:
            raise SetupChatError(
                "monitor_name_out_of_sequence",
                "Define the monitor rules before naming the monitor.",
                status_code=409,
            )
        definition = StrategyDefinition.model_validate(chat.draft_schema_json).model_copy(
            update={"name": name}
        )
        chat.draft_schema_json = definition.model_dump(mode="json")
        sheet = dict(chat.translation_sheet or {})
        sheet["monitor_name"] = name
        sheet["fields"] = [
            {**field, "value": name} if field.get("label") == "Monitor name" else field
            for field in list(sheet.get("fields") or [])
        ]
        chat.translation_sheet = sheet
        chat.title = name
        context["confirmed_monitor_name"] = name
        context["requires_monitor_name"] = False
        context["schema_hash"] = definition.canonical_hash()
        context.pop("awaiting_clarification", None)
        context.pop("awaiting_clarification_key", None)
        chat.context_json = context
        chat.approved_at = None
        chat.approved_strategy_id = None
        chat.approved_strategy_version_id = None
        chat.status = (
            "needs_clarification"
            if any(item.get("severity") == "critical" for item in (chat.lint_warnings or []))
            else "ready_for_approval"
        )
        await self._assistant(
            session,
            chat,
            (
                f'I named this monitor "{name}". Review its fields and rules in the Translation '
                "Sheet, then approve only if they match your idea."
            ),
            message_type="translation",
            payload={
                "translation_sheet": chat.translation_sheet,
                "lint_warnings": chat.lint_warnings,
                "rule_confidence": chat.rule_confidence,
                "can_approve": chat.status == "ready_for_approval",
                "setup_mode": "monitor",
                "monitor_name": name,
            },
        )
        await session.flush()

    async def run_scanner(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        user_id: UUID,
        idempotency_key: str | None = None,
    ) -> AISetupChatSession:
        if _setup_mode(chat) != "scanner":
            raise SetupChatError(
                "scanner_mode_required",
                "Choose Scanner before running a one-time scan.",
                status_code=409,
            )
        context = dict(chat.context_json or {})
        v2_authority = context.get("strategy_state_authority") == "v2"
        required_status = "approved" if v2_authority else "ready_to_scan"
        if chat.status != required_status or not chat.draft_schema_json:
            raise SetupChatError(
                "scanner_not_ready",
                (
                    "Review and approve the exact Scanner draft before running it."
                    if v2_authority
                    else "Add and resolve at least one measurable current-match condition "
                    "before running Scanner."
                ),
                status_code=409,
            )
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        draft = load_strategy_draft_v2(chat)
        if v2_authority and not v2_scanner_approval_matches(
            chat,
            draft,
            definition,
            user_id=user_id,
        ):
            raise SetupChatError(
                "scanner_approval_mismatch",
                "The Scanner approval no longer matches the exact reviewed draft.",
                status_code=409,
            )
        has_executable_required_rule = any(
            rule.required
            and rule.key != "clarification_required"
            and not rule.provider_required
            and rule.availability == "available"
            for rule in _condition_rules(definition.conditions)
        )
        if not has_executable_required_rule:
            raise SetupChatError(
                "scanner_condition_required",
                "Scanner needs at least one measurable condition that coins must match now.",
                status_code=409,
            )
        try:
            response = await OnDemandScanService(
                session,
                self.market_provider,
                settings=self.settings,
            ).run(
                user_id,
                OnDemandScanRequest(
                    strategy=definition,
                    source_draft_id=draft.draft_id,
                    source_draft_version=draft.executable_version,
                    source_draft_hash=draft.executable_hash,
                    max_symbols=100000,
                    idempotency_key=(
                        idempotency_key or f"setup-chat-scanner:{chat.id}:{uuid4()}"
                    ),
                    light_scan=True,
                    include_non_confirmed=True,
                ),
            )
        except OnDemandScanError as exc:
            raise SetupChatError(exc.code, str(exc), status_code=409) from exc

        scanner_result = _scanner_result_payload(response)
        context = dict(chat.context_json or {})
        context["scanner_result"] = scanner_result
        chat.context_json = context
        await self._assistant(
            session,
            chat,
            _scanner_result_message(scanner_result),
            message_type="scanner_result",
            payload={"setup_mode": "scanner", "scanner_result": scanner_result},
        )
        await session.flush()
        return chat

    async def _route_turn(
        self,
        *,
        history: list[dict[str, str]],
        current_message: str,
        accumulated_setup: str,
        active_clarification: SetupChatClarification | None,
    ) -> SetupChatTurnClassification:
        fallback = _fallback_turn_classification(
            current_message,
            active_clarification=active_clarification,
        )
        classifier = getattr(self.interviewer, "classify_turn", None)
        if not callable(classifier):
            return fallback
        try:
            return await classifier(
                history=history,
                current_message=current_message,
                accumulated_setup=accumulated_setup,
                active_clarification=_active_clarification_for_ai(active_clarification),
                capability_context=_routing_capability_context(current_message),
            )
        except SetupChatError:
            # Turn routing has a conservative local fallback. Strategy compilation and
            # capability certification still fail closed if their required AI call is down.
            return fallback

    async def _run_bounded_agent(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        history: list[dict[str, str]],
    ) -> AgentTurnOutcome:
        tool_service = AgentToolService(
            self.settings,
            compile_draft=self._agent_compile_draft,
            market_snapshot=self._agent_market_snapshot,
            run_scanner=self._agent_run_scanner,
        )
        return await AgentControlService(
            self.settings,
            tool_service,
            client=self.agent_client,
        ).run_turn(
            session,
            chat,
            message=message,
            history=history,
            shadow_mode=self.settings.ai_agent_shadow_mode,
        )

    async def _agent_compile_draft(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        setup_text: str,
        bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prior_hash = (
            StrategyDefinition.model_validate(chat.draft_schema_json).canonical_hash()
            if chat.draft_schema_json
            else None
        )
        context = dict(chat.context_json or {})
        context["capability_bindings"] = bindings
        chat.context_json = context
        await self._compile(session, chat, setup_text)
        if not chat.draft_schema_json:
            raise SetupChatError("agent_compile_failed", "The compiler did not return a draft.")
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        canonical_hash = definition.canonical_hash()
        state_payload = (chat.context_json or {}).get("strategy_state")
        strategy_state = StrategyDraftState.from_dict(
            state_payload if isinstance(state_payload, dict) else None
        )
        return {
            "strategy": definition.model_dump(mode="json"),
            "canonical_hash": canonical_hash,
            "lint_warnings": chat.lint_warnings or [],
            "assumptions": chat.assumptions or [],
            "ambiguities": chat.ambiguities or [],
            "unsupported_conditions": chat.unsupported_conditions or [],
            "rule_confidence": chat.rule_confidence or [],
            "translation_sheet": chat.translation_sheet or {},
            "approval_eligible": chat.status == "ready_for_approval",
            "scan_eligible": chat.status == "ready_to_scan",
            "approval_is_external": True,
            "previous_hash_invalidated": bool(prior_hash and prior_hash != canonical_hash),
            "draft_version": strategy_state.draft_version,
            "approval_state": strategy_state.approval_state,
            "conversation_snapshot_hash": strategy_state.conversation_snapshot_hash,
        }

    async def _agent_market_snapshot(
        self,
        exchange: str | None,
        quote_currency: str,
    ) -> dict[str, Any]:
        snapshot = await self.market_snapshot(
            exchange=exchange,
            quote_currency=quote_currency,
        )
        return snapshot.model_dump(mode="json")

    async def _agent_run_scanner(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        expected_hash: str,
    ) -> dict[str, Any]:
        context = dict(chat.context_json or {})
        v2_authority = context.get("strategy_state_authority") == "v2"
        if (
            _setup_mode(chat) != "scanner"
            or not chat.draft_schema_json
            or (v2_authority and chat.status != "approved")
        ):
            raise SetupChatError(
                "scanner_not_ready",
                "Scanner needs the current validated and authenticated approved draft.",
                status_code=409,
            )
        definition = StrategyDefinition.model_validate(chat.draft_schema_json)
        draft = load_strategy_draft_v2(chat)
        if v2_authority and not v2_scanner_approval_matches(
            chat,
            draft,
            definition,
            user_id=chat.user_id,
        ):
            raise SetupChatError(
                "scanner_approval_mismatch",
                "The Scanner approval no longer matches the exact reviewed draft.",
                status_code=409,
            )
        if definition.canonical_hash() != expected_hash:
            raise SetupChatError(
                "strategy_hash_mismatch",
                "The Scanner draft changed before execution.",
                status_code=409,
            )
        try:
            response = await OnDemandScanService(
                session,
                self.market_provider,
                settings=self.settings,
            ).run(
                chat.user_id,
                OnDemandScanRequest(
                    strategy=definition,
                    source_draft_id=draft.draft_id,
                    source_draft_version=draft.executable_version,
                    source_draft_hash=draft.executable_hash,
                    max_symbols=100000,
                    idempotency_key=f"bounded-agent-scanner:{chat.id}:{expected_hash}",
                    light_scan=True,
                    include_non_confirmed=True,
                ),
            )
        except OnDemandScanError as exc:
            raise SetupChatError(exc.code, str(exc), status_code=409) from exc
        scanner_result = _scanner_result_payload(response)
        scanner_result["warnings"] = [
            _safe_agent_scanner_warning(item) for item in scanner_result.get("warnings", [])
        ]
        scanner_result["draft_hash"] = expected_hash
        scanner_result["evidence_refs"] = [f"on-demand-scan:{response.usage_record_id}"]
        context = dict(chat.context_json or {})
        context["scanner_result"] = scanner_result
        chat.context_json = context
        await session.flush()
        return scanner_result

    async def _apply_agent_outcome(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        client_message_id: str | None,
        outcome: AgentTurnOutcome,
    ) -> None:
        final = outcome.final_response
        if final is None:
            raise SetupChatError(
                "agent_response_missing",
                "The bounded agent did not produce a safe response.",
                status_code=502,
            )
        user_message = await self._append_message(
            session,
            chat,
            role="user",
            message_type="text",
            content=message,
            payload={"agent_run_id": str(outcome.run_id)},
            client_message_id=client_message_id,
        )
        context = dict(chat.context_json or {})
        context["last_agent_run_id"] = str(outcome.run_id)
        context["last_agent_evidence_refs"] = final.evidence_refs
        fragment_records = list(context.get("setup_fragment_records") or [])
        setup_fragments = list(context.get("setup_fragments") or [])
        if any(
            " ".join(str(fragment).casefold().split()) == " ".join(message.casefold().split())
            for fragment in setup_fragments
        ):
            fragment_records.append(
                {
                    "fragment": message[:500],
                    "user_message_id": str(user_message.id),
                    "user_message_sequence": user_message.sequence,
                    "user_text": message[:1000],
                }
            )
            context["setup_fragment_records"] = fragment_records[-100:]
        context["conversation_state"] = _refresh_conversation_state(
            chat,
            context=context,
            user_message=user_message,
            tool_results=outcome.tool_results,
        )
        chat.context_json = context
        result_by_name: dict[str, AgentToolResult] = {}
        for item in outcome.tool_results:
            if item.tool_name not in result_by_name or item.status == "success":
                result_by_name[item.tool_name] = item

        snapshot = result_by_name.get("get_market_snapshot")
        if snapshot and snapshot.status == "success" and final.intent != "market_snapshot":
            await self._assistant(
                session,
                chat,
                str(snapshot.data.get("message") or "Provider-backed market snapshot loaded."),
                message_type="market_snapshot",
                payload=snapshot.data | {"evidence_refs": snapshot.evidence_refs},
            )

        resolution = result_by_name.get("resolve_trading_capabilities")
        clarifications = list((resolution.data if resolution else {}).get("clarifications") or [])
        if clarifications:
            context = dict(chat.context_json or {})
            parsed_clarifications: list[SetupChatClarification] = []
            for item in clarifications:
                safe_item = dict(item)
                safe_item.pop("source_fragment", None)
                parsed_clarifications.append(
                    _with_other_option(SetupChatClarification.model_validate(safe_item))
                )
            parsed_clarifications = _apply_clarification_question_budget(
                context,
                parsed_clarifications,
            )
            clarifications = [item.model_dump(mode="json") for item in parsed_clarifications]
            chat.context_json = context
        if clarifications and "compile_strategy_draft" not in result_by_name:
            clarification = SetupChatClarification.model_validate(clarifications[0])
            context = dict(chat.context_json or {})
            _record_clarification_question(context, clarification)
            _set_awaiting_clarification(context, clarification)
            context["agent_pending_clarifications"] = clarifications[1:]
            chat.context_json = context
            chat.status = "needs_clarification"
            chat.ambiguities = [
                {
                    "code": item.get("key"),
                    "message": item.get("question"),
                    "field": item.get("key"),
                    "blocking": True,
                    "source_fragment": item.get("source_fragment") or message,
                }
                for item in clarifications
            ]
        elif (
            resolution
            and (resolution.data.get("clarifications") or [])
            and "compile_strategy_draft" not in result_by_name
        ):
            message = (
                "I still cannot make this condition deterministic without inventing details. "
                "I will not repeat the question; revise that condition directly or remove it."
            )
            final = final.model_copy(
                update={
                    "message": message,
                    "assistant_message": message,
                    "intent": "clarify",
                    "status": "needs_user_input",
                    "clarification": None,
                    "confidence": min(final.confidence, 0.5),
                }
            )

        if (chat.context_json or {}).get("requires_monitor_name"):
            pending_name_clarification = _pending_clarification_model(
                dict((chat.context_json or {}).get("awaiting_clarification") or {})
            )
            await self._assistant(
                session,
                chat,
                "What would you like to name this monitor?",
                message_type="monitor_name_required",
                payload={
                    "clarifications": [pending_name_clarification.model_dump(mode="json")]
                    if pending_name_clarification
                    else [],
                    "name_required": True,
                    "can_approve": False,
                    "agent_run_id": str(outcome.run_id),
                    "_traceedge_model": outcome.model,
                    "usage": outcome.usage,
                },
            )
            return

        message_type = {
            "clarify": "clarification",
            "draft_ready": "translation",
            "scan_result": "scanner_result",
            "market_snapshot": "market_snapshot",
            "monitor_status": "monitor_status",
            "refusal": "scope_refusal",
            "unavailable": "agent_unavailable",
            "error": "agent_error",
        }.get(final.intent, "conversation")
        payload: dict[str, Any] = {
            "agent_run_id": str(outcome.run_id),
            "_traceedge_model": outcome.model,
            "usage": outcome.usage,
            "agent_intent": final.intent,
            "agent_status": final.status,
            "evidence_refs": final.evidence_refs,
            "suggested_actions": [item.model_dump(mode="json") for item in final.suggested_actions],
            "requires_user_confirmation": final.requires_user_confirmation,
            "conversation_stage": final.stage,
            "user_intent": final.user_intent,
            "clarification": (
                final.clarification.model_dump(mode="json")
                if final.clarification is not None
                else None
            ),
            "assumptions": final.assumptions,
            "unresolved_conflicts": final.unresolved_conflicts,
            "referenced_user_message_ids": final.referenced_user_message_ids,
            "source_clause_coverage": [
                item.model_dump(mode="json") for item in final.source_clause_coverage
            ],
            "can_continue": final.can_continue,
            "refusal_reason": final.refusal_reason,
            "confidence": final.confidence,
        }
        compile_result = result_by_name.get("compile_strategy_draft")
        if compile_result and compile_result.status == "success":
            payload.update(
                {
                    "translation_sheet": chat.translation_sheet,
                    "lint_warnings": chat.lint_warnings,
                    "rule_confidence": chat.rule_confidence,
                    "can_approve": chat.status == "ready_for_approval",
                    "can_scan": chat.status == "ready_to_scan",
                    "setup_mode": _setup_mode(chat),
                }
            )
        if resolution and clarifications:
            pending = _pending_clarification_model(
                dict((chat.context_json or {}).get("awaiting_clarification") or {})
            )
            payload["clarifications"] = [pending.model_dump(mode="json")] if pending else []
        if snapshot and final.intent == "market_snapshot":
            payload.update(snapshot.data | {"evidence_refs": snapshot.evidence_refs})
        scan = result_by_name.get("run_one_time_scan")
        if scan and scan.status == "success":
            payload["scanner_result"] = scan.data
            payload["setup_mode"] = "scanner"
        monitor = result_by_name.get("get_monitor_status")
        if monitor and monitor.status == "success":
            payload["monitor_status"] = monitor.data
        await self._assistant(
            session,
            chat,
            final.message,
            message_type=message_type,
            payload=payload,
        )

    async def finalize_agent_shadow_comparison(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
    ) -> None:
        run_id = (chat.context_json or {}).get("agent_shadow_run_id")
        if not run_id:
            return
        from ai_market_monitor.db.models import AgentRun

        try:
            run = await session.get(AgentRun, UUID(str(run_id)))
        except ValueError:
            return
        if run is None or run.chat_session_id != chat.id or not run.shadow_mode:
            return
        comparison = dict(run.comparison or {})
        comparison.update(
            {
                "legacy_status_after": chat.status,
                "comparison_pending": False,
            }
        )
        after_hash = (
            StrategyDefinition.model_validate(chat.draft_schema_json).canonical_hash()
            if chat.draft_schema_json
            else None
        )
        comparison["legacy_draft_hash_after"] = after_hash
        turn_intent = str(
            ((chat.context_json or {}).get("last_turn_classification") or {}).get("intent") or ""
        )
        expected_first_tool = None
        if turn_intent == "market_snapshot":
            expected_first_tool = "get_market_snapshot"
        elif (
            comparison.get("legacy_draft_hash_before") != after_hash and after_hash
        ) or chat.status == "needs_clarification":
            expected_first_tool = "resolve_trading_capabilities"
        selected_tools = list(comparison.get("agent_selected_tools") or [])
        comparison["legacy_expected_first_tool"] = expected_first_tool
        comparison["agent_first_tool_correct"] = (
            selected_tools[0] == expected_first_tool
            if expected_first_tool and selected_tools
            else None
        )
        run.comparison = comparison
        run.status = "shadow_compared"

    async def record_turn_failure(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        envelope: SetupChatErrorEnvelope,
        client_message_id: str | None = None,
    ) -> None:
        """Close a failed turn with a real assistant message and no state change.

        A failed turn must never leave the conversation silent, and it must never
        advance or demote the last authoritative draft. The user can retry the
        instruction without losing the prior lifecycle state or preview.
        """
        current_hash = None
        has_authoritative_draft = bool(
            (chat.context_json or {}).get("strategy_draft_v2")
            or chat.draft_schema_json
        )
        if chat.draft_schema_json:
            try:
                current_hash = StrategyDefinition.model_validate(
                    chat.draft_schema_json
                ).canonical_hash()
            except ValidationError:
                current_hash = None
        user_safe_validation = not envelope.error_code.startswith(("TARGET_", "STRATEGY_"))
        assistant_content = (
            envelope.message
            if user_safe_validation
            else _TURN_FAILURE_MESSAGE.format(request_id=envelope.request_id)
        )
        stored_failure = (chat.context_json or {}).get("last_turn_failure")
        safe_failure_proof = None
        if isinstance(stored_failure, dict) and isinstance(stored_failure.get("proof"), dict):
            proof = stored_failure["proof"]
            # This allowlist is deliberately narrower than the internal record. It
            # contains only server classification and the user's own source excerpt.
            safe_failure_proof = {
                key: proof.get(key)
                for key in (
                    "failure_class",
                    "failure_owner",
                    "intent_ref",
                    "segment_ref",
                    "semantic_path",
                    "semantic_paths",
                    "source_excerpt",
                    "expected_value",
                    "expected_values",
                    "observed_value",
                    "observed_values",
                    "repair_eligible",
                    "repair_decision",
                    "support_reference",
                    "operator_alertable",
                )
            }
        assistant = await self._assistant(
            session,
            chat,
            assistant_content,
            message_type="turn_error",
            payload={
                "error": envelope.model_dump(mode="json"),
                "can_approve": chat.status == "ready_for_approval",
                "authoritative_draft_preserved": has_authoritative_draft,
                "authoritative_draft_hash": current_hash,
                "approval_state": _strategy_approval_state(chat),
                "failure_proof": safe_failure_proof,
            },
        )
        if client_message_id:
            turn = await session.scalar(
                select(SetupChatTurn)
                .where(
                    SetupChatTurn.chat_session_id == chat.id,
                    SetupChatTurn.client_message_id == client_message_id,
                )
                .with_for_update()
            )
            if (
                turn is not None
                and turn.status != "COMPLETED"
                and turn.execution_result_json is None
            ):
                draft = load_strategy_draft_v2(chat)
                turn.status = (
                    "RETRYABLE_FAILURE" if envelope.retryable else "COMPLETED"
                )
                turn.failure_code = envelope.error_code
                turn.failure_stage = envelope.stage
                turn.failure_retryable = envelope.retryable
                turn.assistant_message_id = assistant.id
                turn.reply_json = {
                    "message": assistant_content,
                    "execution_result": None,
                    "error": envelope.model_dump(mode="json"),
                    "failure_proof": safe_failure_proof,
                }
                turn.executable_version_after = draft.executable_version
                turn.workflow_revision_after = draft.workflow_revision
                if not envelope.retryable:
                    turn.completed_at = datetime.now(UTC)

    async def _assistant(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        content: str,
        *,
        message_type: str,
        payload: dict[str, Any] | None = None,
    ) -> AISetupChatMessage:
        return await self._append_message(
            session,
            chat,
            role="assistant",
            message_type=message_type,
            content=content,
            payload=payload or {},
        )

    async def _append_message(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        role: str,
        message_type: str,
        content: str,
        payload: dict[str, Any],
        client_message_id: str | None = None,
    ) -> AISetupChatMessage:
        sequence = await session.scalar(
            select(func.max(AISetupChatMessage.sequence)).where(
                AISetupChatMessage.session_id == chat.id
            )
        )
        item = AISetupChatMessage(
            session_id=chat.id,
            sequence=(sequence or 0) + 1,
            role=role,
            message_type=message_type,
            client_message_id=client_message_id,
            content=content,
            payload=payload,
            created_at=datetime.now(UTC),
        )
        session.add(item)
        await session.flush()
        return item

    @staticmethod
    def _classify(text: str) -> str:
        lowered = text.casefold().strip()
        if re.fullmatch(
            (
                r"(?:(?:hi|hello|hey)(?:,?\s+how are you)?|how are you|"
                r"good (?:morning|afternoon|evening))[!?. ]*"
            ),
            lowered,
        ):
            return "greeting"
        if any(
            phrase in lowered
            for phrase in (
                "how is the market",
                "market today",
                "market snapshot",
                "what is crypto doing",
                "how are coins doing",
            )
        ):
            return "market_snapshot"
        if any(
            phrase in lowered
            for phrase in (
                "place a trade",
                "execute a trade",
                "auto trade",
                "guaranteed profit",
                "guarantee returns",
                "exchange api key",
                "binance api",
                "exchange key",
                "seed phrase",
                "private key",
                "buy now",
                "sell now",
                "should i buy",
                "should i sell",
                "which coin will pump",
                "what coin will pump",
                "leverage advice",
                "how much leverage",
                "use leverage",
            )
        ):
            return "unsafe"
        setup_terms = (
            "crypto",
            "coin",
            "symbol",
            "pair",
            "spot",
            "rsi",
            "ema",
            "sma",
            "macd",
            "volume",
            "candle",
            "breakout",
            "support",
            "resistance",
            "momentum",
            "vwap",
            "bollinger",
            "liquidity",
            "sweep",
            "timeframe",
            "alert",
            "monitor",
            "price",
            "bullish",
            "bearish",
            "doji",
            "atr",
            "trend",
            "retest",
            "fakeout",
            "order book imbalance",
            "cvd",
            "cumulative volume delta",
            "liquidation heatmap",
            "whale wallet",
            "fear and greed",
            "news sentiment",
        )
        if any(term in lowered for term in setup_terms):
            return "setup"
        return "out_of_scope"


_NON_CONVERSATIONAL_HISTORY_TYPES = {
    "mechanic_build_status",
    "process_state",
    "scanner_result",
    "translation",
}


def _agent_rollout_enabled(user_id: UUID, rollout_percent: int) -> bool:
    if rollout_percent <= 0:
        return False
    if rollout_percent >= 100:
        return True
    bucket = int.from_bytes(sha256(user_id.bytes).digest()[:4], "big") % 10_000
    return bucket < rollout_percent * 100


def _conversation_history(
    messages: list[AISetupChatMessage],
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Return compact human dialogue without operational noise or repeated old text."""
    history: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in messages:
        if item.role not in {"user", "assistant"}:
            continue
        if item.message_type in _NON_CONVERSATIONAL_HISTORY_TYPES:
            continue
        content = " ".join(item.content.split())
        if not content:
            continue
        identity_text = re.sub(
            r"^question\s+\d+\s+of\s+\d+\s*:\s*",
            "",
            content.casefold(),
        )
        identity = (item.role, identity_text)
        if identity in seen:
            continue
        seen.add(identity)
        history.append(
            {
                "role": item.role,
                "content": content,
                "message_id": str(item.id),
                "sequence": str(item.sequence),
                "message_type": item.message_type,
            }
        )
    return history[-limit:]


def _active_clarification_for_ai(
    clarification: SetupChatClarification | None,
) -> dict[str, Any] | None:
    if clarification is None:
        return None
    return {
        "key": clarification.key,
        "question": clarification.question,
        "reason": clarification.reason,
        # Machine values are intentionally excluded. The router sees only language shown
        # to the user, so it cannot ask the user to define an internal identifier.
        "options": [
            {
                "label": option.label,
                "description": option.description,
                "action": option.action,
            }
            for option in clarification.options
        ],
    }


def _capability_query_text(value: str) -> str:
    cleaned = " ".join(value.split()).strip(" ?.!:")
    patterns = (
        (
            r"^(?:do|does)\s+"
            r"(?:you|hilalmarkets|traceedge|the\s+system)\s+"
            r"(?:have|support|recognize|understand|identify)\s+"
        ),
        (
            r"^(?:can|could)\s+"
            r"(?:you|hilalmarkets|traceedge|the\s+system)\s+"
            r"(?:support|recognize|identify|detect|explain)\s+"
        ),
        (
            r"^(?:is|are)\s+(.+?)\s+"
            r"(?:supported|available|registered|identified)"
            r"(?:\s+in\s+(?:hilalmarkets|traceedge|the\s+system))?$"
        ),
        (
            r"^(?:what|how)\s+(?:does|do)\s+"
            r"(?:hilalmarkets|traceedge|the\s+system)\s+"
            r"(?:call|detect|measure)\s+"
        ),
    )
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        if match.lastindex:
            return " ".join(match.group(1).split())
        cleaned = cleaned[match.end() :]
        break
    cleaned = re.sub(
        r"\b(?:in\s+the\s+system|in\s+hilalmarkets|in\s+traceedge|as\s+a\s+feature)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split()).strip(" ?.!:") or value


def _routing_capability_context(value: str) -> dict[str, Any]:
    index = get_capability_index()
    query = _capability_query_text(value)
    candidates = [
        candidate
        for candidate in index.resolver.broad_candidates(query, limit=10)
        if candidate.score >= 42
    ][:8]
    return {
        "registry_hash": index.registry_hash,
        "query": query,
        "candidates": [
            {
                "capability_key": candidate.capability_key,
                "label": candidate.label,
                "availability": candidate.availability,
                "confidence": candidate.confidence,
                "matched_on": list(candidate.matched_on),
                "direction_support": list(candidate.direction_support),
                "temporal_behavior": candidate.temporal_behavior,
            }
            for candidate in candidates
        ],
    }


def _looks_like_question(value: str) -> bool:
    normalized = value.casefold().strip()
    return (
        value.rstrip().endswith("?")
        or re.match(
            r"^(?:what|why|how|which|where|when|who|do|does|did|is|are|can|could|"
            r"would|will|have|has)\b",
            normalized,
        )
        is not None
    )


def _looks_like_product_question(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    has_product_context = bool(
        AISetupChatService._classify(value) == "setup"
        or re.search(
            r"\b(?:hilalmarkets|traceedge|the system|feature|capability|indicator|condition|"
            r"option|choice|mechanic)\b",
            normalized,
        )
        or re.search(r"\b[A-Z]{2,10}\b", value)
    )
    if not has_product_context:
        return False
    return bool(
        re.search(
            r"\b(?:do|does|can|could|is|are|have|has)\s+"
            r"(?:you|hilalmarkets|traceedge|the system)\b.*\b"
            r"(?:have|support|recognize|understand|identify|available|registered|work)\b",
            normalized,
        )
        or re.search(
            r"^(?:what (?:does|do|is|are)|how (?:does|do|is|are)|"
            r"(?:can|could) you explain|tell me about)\b",
            normalized,
        )
    )


def _fallback_turn_classification(
    value: str,
    *,
    active_clarification: SetupChatClarification | None,
) -> SetupChatTurnClassification:
    legacy_intent = AISetupChatService._classify(value)
    intent: Literal[
        "conversation",
        "product_question",
        "option_question",
        "clarification_answer",
        "setup_instruction",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    ]
    category: Literal[
        "human_conversation",
        "product_question",
        "option_question",
        "technical_instruction",
        "clarification_answer",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    ]
    if legacy_intent == "market_snapshot":
        intent = "market_snapshot"
        category = "market_snapshot"
        assistant_message = ""
        technical_fragments: list[str] = []
    elif legacy_intent == "unsafe":
        intent = "unsafe"
        category = "unsafe"
        assistant_message = (
            "I can help translate your own crypto spot monitoring idea, but I cannot give "
            "buy/sell advice, handle exchange keys, promise returns, or automate trades."
        )
        technical_fragments = []
    elif active_clarification is not None and _is_explanation_request(value):
        intent = "option_question"
        category = "option_question"
        assistant_message = ""
        technical_fragments = []
    elif _looks_like_product_question(value):
        intent = "product_question"
        category = "product_question"
        assistant_message = ""
        technical_fragments = []
    elif (
        active_clarification is not None
        and not _looks_like_question(value)
        and _answer_satisfies_clarification(active_clarification, value)
    ):
        intent = "clarification_answer"
        category = "clarification_answer"
        assistant_message = ""
        technical_fragments = []
    elif legacy_intent == "greeting" or re.fullmatch(
        r"(?:thanks?|thank you|okay|ok|got it|sounds good|great)[!?. ]*",
        value.casefold().strip(),
    ):
        intent = "conversation"
        category = "human_conversation"
        assistant_message = (
            "You're welcome. We can keep talking while we build this; your setup stays unchanged "
            "until you give or confirm a rule."
            if legacy_intent != "greeting"
            else "I'm well, thank you. Describe a crypto spot setup whenever you're ready."
        )
        technical_fragments = []
    elif legacy_intent == "setup":
        intent = "setup_instruction"
        category = "technical_instruction"
        assistant_message = ""
        technical_fragments = [value]
    elif active_clarification is not None:
        # Keep the active technical question open. Existing deterministic answer checks will
        # explain what is still needed instead of converting an unrelated word into a rule.
        intent = "setup_instruction"
        category = "human_conversation"
        assistant_message = ""
        technical_fragments = []
    else:
        intent = "out_of_scope"
        category = "out_of_scope"
        assistant_message = (
            "I'm focused on HilalMarkets crypto spot monitoring and product help. Tell me a setup "
            "condition or ask how a HilalMarkets feature works."
        )
        technical_fragments = []
    return SetupChatTurnClassification(
        intent=intent,
        assistant_message=assistant_message,
        technical_fragments=technical_fragments,
        clarification_answer=(value if intent == "clarification_answer" else None),
        segments=[
            SetupChatTurnSegment(text=setup_chat_source_excerpt(value), category=category)
        ],
        preserve_pending_question=intent
        in {"conversation", "product_question", "option_question", "out_of_scope", "unsafe"},
        confidence=0.72,
    )


def _clarification_answer_turn(value: str) -> SetupChatTurnClassification:
    return SetupChatTurnClassification(
        intent="clarification_answer",
        assistant_message="",
        technical_fragments=[],
        clarification_answer=value,
        segments=[
            SetupChatTurnSegment(
                text=setup_chat_source_excerpt(value),
                category="clarification_answer",
            )
        ],
        preserve_pending_question=False,
        confidence=1.0,
    )


def _normalize_turn_classification(
    classification: SetupChatTurnClassification,
    *,
    current_message: str,
    active_clarification: SetupChatClarification | None,
) -> SetupChatTurnClassification:
    deterministic = classify_strategy_turn(current_message)
    deterministic_categories = {fragment.category for fragment in deterministic.fragments}
    deterministic_dialogue = bool(deterministic.fragments) and all(
        fragment.kind in {"conversational", "conversation_control"}
        for fragment in deterministic.fragments
    )
    deterministic_dialogue = deterministic_dialogue and any(
        fragment.kind == "conversation_control" for fragment in deterministic.fragments
    )
    state_categories = {
        "SYMBOL",
        "INCLUDE",
        "EXCLUDE",
        "TIMEFRAME",
        "DIRECTION",
        "OPERATOR",
        "THRESHOLD",
        "FORMULA",
        "REVERSION",
        "TRADING_MECHANIC",
    }
    legacy_intent = AISetupChatService._classify(current_message)
    if legacy_intent in {"unsafe", "market_snapshot"}:
        fallback = _fallback_turn_classification(
            current_message, active_clarification=active_clarification
        )
        if fallback.intent == legacy_intent:
            return fallback
    if active_clarification is not None and _is_explanation_request(current_message):
        return classification.model_copy(
            update={
                "intent": "option_question",
                "technical_fragments": [],
                "clarification_answer": None,
                "preserve_pending_question": True,
            }
        )
    if _looks_like_product_question(current_message):
        return classification.model_copy(
            update={
                "intent": "product_question",
                "technical_fragments": [],
                "clarification_answer": None,
                "preserve_pending_question": True,
            }
        )
    if active_clarification is not None:
        if active_clarification.key == "monitor_name":
            return _clarification_answer_turn(current_message)
        if (
            not _looks_like_question(current_message)
            and _answer_satisfies_clarification(active_clarification, current_message)
        ):
            return _clarification_answer_turn(current_message)
    if (
        deterministic.fragments
        and not deterministic.is_approval
        and not deterministic_categories.intersection(state_categories)
        and (legacy_intent != "out_of_scope" or deterministic_dialogue)
    ):
        return classification.model_copy(
            update={
                "intent": "conversation",
                "technical_fragments": [],
                "clarification_answer": None,
                "preserve_pending_question": True,
            }
        )
    if (
        deterministic.fragments
        and deterministic_categories.intersection(state_categories)
        and "TRADING_MECHANIC" not in deterministic_categories
        and classification.intent not in {"clarification_answer", "setup_revision"}
    ):
        classification = classification.model_copy(
            update={
                "intent": "setup_instruction",
                "technical_fragments": [current_message],
                "clarification_answer": None,
            }
        )
    if classification.intent == "clarification_answer" and active_clarification is None:
        classification = classification.model_copy(
            update={
                "intent": "setup_instruction",
                "clarification_answer": None,
                "technical_fragments": classification.technical_fragments or [current_message],
            }
        )
    elif classification.intent == "clarification_answer" and _looks_like_question(current_message):
        classification = classification.model_copy(
            update={
                "intent": "option_question",
                "clarification_answer": None,
                "technical_fragments": [],
                "preserve_pending_question": True,
            }
        )
    if classification.intent in {
        "conversation",
        "product_question",
        "option_question",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    }:
        classification = classification.model_copy(
            update={
                "technical_fragments": [],
                "clarification_answer": None,
                "preserve_pending_question": True,
            }
        )
    valid_segments = [
        segment
        for segment in classification.segments
        if " ".join(segment.text.casefold().split()) in " ".join(current_message.casefold().split())
    ]
    if len(valid_segments) != len(classification.segments):
        classification = classification.model_copy(update={"segments": valid_segments})
    return classification


def _is_deterministic_state_update(report: Any) -> bool:
    """Return whether a turn can patch canonical state without model routing."""

    categories = {fragment.category for fragment in report.fragments}
    state_fields = {
        "SYMBOL",
        "INCLUDE",
        "EXCLUDE",
        "TIMEFRAME",
        "DIRECTION",
        "OPERATOR",
        "THRESHOLD",
        "FORMULA",
        "REVERSION",
    }
    permitted = {
        *state_fields,
        "CONVERSATIONAL_TEXT",
        "CONVERSATION_CONTROL",
        "APPROVAL_INSTRUCTION",
        "PRODUCT_POLICY",
    }
    return bool(categories.intersection(state_fields)) and categories.issubset(permitted)


def _turn_materially_updates_strategy(report: Any) -> bool:
    """Return whether the authenticated turn can create a new draft revision."""

    material_categories = {
        "SYMBOL",
        "INCLUDE",
        "EXCLUDE",
        "TIMEFRAME",
        "DIRECTION",
        "OPERATOR",
        "THRESHOLD",
        "FORMULA",
        "REVERSION",
        "TRADING_MECHANIC",
    }
    return any(fragment.category in material_categories for fragment in report.fragments)


def _requests_stale_approval_reuse(text: str, report: Any) -> bool:
    """Detect attempts to carry an old approval onto the current edited draft."""

    categories = {fragment.category for fragment in report.fragments}
    if "APPROVAL_INSTRUCTION" not in categories:
        return False
    lowered = " ".join(text.casefold().split())
    return bool(
        re.search(r"\b(?:previous|prior|old|existing|earlier)\s+approv\w*\b", lowered)
        and re.search(r"\b(?:reuse|re-use|carry|transfer|apply|keep)\b", lowered)
    )


def _validated_technical_fragments(
    current_message: str,
    candidates: list[str],
) -> list[str]:
    normalized_message = " ".join(current_message.casefold().split())
    results: list[str] = []
    for candidate in candidates:
        cleaned = " ".join(candidate.split())
        if not cleaned:
            continue
        if " ".join(cleaned.casefold().split()) not in normalized_message:
            continue
        report = classify_strategy_turn(cleaned)
        if not any(
            fragment.category
            in {
                "SYMBOL",
                "INCLUDE",
                "EXCLUDE",
                "TIMEFRAME",
                "DIRECTION",
                "OPERATOR",
                "THRESHOLD",
                "FORMULA",
                "REVERSION",
                "TRADING_MECHANIC",
            }
            for fragment in report.fragments
        ):
            continue
        if cleaned not in results:
            results.append(cleaned)
    return results


def _is_non_mutating_turn(
    classification: SetupChatTurnClassification,
    technical_fragments: list[str],
) -> bool:
    if classification.intent in {
        "conversation",
        "product_question",
        "option_question",
        "market_snapshot",
        "unsafe",
        "out_of_scope",
    }:
        return True
    return classification.intent == "mixed" and not technical_fragments


def _turn_response_fallback(
    classification: SetupChatTurnClassification,
    *,
    current_message: str,
    capability_context: dict[str, Any],
) -> str:
    if classification.intent == "product_question":
        candidates = [
            item
            for item in capability_context.get("candidates", [])
            if item.get("availability") == "available"
        ]
        if candidates:
            labels = ", ".join(dict.fromkeys(item["label"] for item in candidates[:3]))
            return (
                f"HilalMarkets has registered mechanics related to {labels}. I haven't added "
                "anything to your setup; ask me to compare them or tell me which meaning you want."
            )
        return (
            "I can't confirm a verified registered mechanic for that wording yet. I haven't "
            "changed your setup; describe the measurable behavior and I can check it safely."
        )
    if classification.intent == "option_question":
        return "I'll explain the current choices without treating your question as a rule."
    if classification.intent == "conversation":
        return (
            "Of course. We can talk normally; I only change the setup when you give or "
            "confirm a rule."
        )
    if classification.intent == "unsafe":
        return (
            "I can help with crypto spot monitoring, but not buy/sell advice, exchange keys, "
            "leverage, guaranteed returns, or automatic trading."
        )
    if classification.intent == "out_of_scope":
        return (
            "I'm focused on HilalMarkets crypto spot monitoring and product help. Ask me about a "
            "feature or describe what market behavior you want to monitor."
        )
    return "Tell me what you would like to monitor."


def _option_strategy_fragment(
    clarification: SetupChatClarification | None,
    *,
    selected_option: SetupChatOption | None,
    option_value: str,
    option_label: str | None,
    setup_context: str,
) -> str:
    # Option values are transport data owned by the assistant's active question, not
    # independent user-authored market mechanics. Always bind the value back to that
    # question before it enters accumulated setup text. Opaque values such as `0` or
    # `all_supported_spot_pairs` use the visible label so the interviewer keeps the
    # meaning it originally offered to the user.
    answer = option_value
    if _is_opaque_option_value(option_value):
        answer = (
            option_label
            or (selected_option.label if selected_option is not None else None)
            or option_value.replace("_", " ")
        )
    return _canonical_clarification_answer(
        clarification,
        answer,
        setup_context=setup_context,
    )


def _is_opaque_option_value(value: str) -> bool:
    normalized = value.casefold().strip()
    return bool(
        re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", normalized)
        or re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:%|x)?", normalized)
        or normalized in {"yes", "no", "none", "true", "false"}
    )


def _setup_text_limit() -> int:
    """The schema's own cap on ``setup_text``, read from the schema.

    Hard-coding it here would let the two drift apart, and the drift was invisible:
    a long conversation built a request the schema rejected, the ValidationError
    escaped as HTTP 500, and the trader was told to rephrase a message that was
    never the problem.
    """
    for constraint in GuidedSetupRequest.model_fields["setup_text"].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            return limit
    return 5000


def _bounded_setup_text(text: str) -> str:
    """Fit ``text`` inside the schema limit without cutting a line in half.

    The canonical strategy state — not this transcript — is the authority for every
    settled field (INV-01), so dropping the oldest raw lines loses no decision. Only
    whole lines are dropped, and always the oldest, so the wording that survives is
    still exactly what the trader typed.
    """
    limit = _setup_text_limit()
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        extra = len(line) + (1 if kept else 0)
        if size + extra > limit:
            break
        kept.append(line)
        size += extra
    if not kept:
        # A single line longer than the whole budget: keep its most recent end,
        # which is where the operative instruction sits.
        return text[-limit:]
    return "\n".join(reversed(kept))


def _guided_setup(
    text: str,
    *,
    capability_bindings: list[dict[str, Any]] | None = None,
    setup_mode: Literal["scanner", "monitor"] = "monitor",
    resolved_state: dict[str, Any] | None = None,
) -> GuidedSetupRequest:
    settled = resolved_state or {}
    timeframe_match = re.search(r"\b(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)\b", text.casefold())
    timeframe = timeframe_match.group(1) if timeframe_match else "15m"
    settled_timeframe = settled.get("base_timeframe")
    if isinstance(settled_timeframe, str) and settled_timeframe in SUPPORTED_TIMEFRAMES:
        timeframe = settled_timeframe
    exchange = "bybit" if "bybit" in text.casefold() else "binance"
    quote = "USDC" if "usdc" in text.casefold() else "USDT"
    symbols = sorted(
        {
            match.upper().replace("-", "/")
            for match in re.findall(r"\b[A-Z0-9]{2,12}[/\-](?:USDT|USDC)\b", text.upper())
        }
    )
    explicit_close = _explicit_closed_candle_requested(text)
    trigger_mode: Literal["candle_close", "intrabar"] = (
        "intrabar"
        if "intrabar" in text.casefold() or (setup_mode == "scanner" and not explicit_close)
        else "candle_close"
    )
    settled_symbols = [
        item for item in (settled.get("include_symbols") or ()) if isinstance(item, str)
    ]
    return GuidedSetupRequest(
        exchange=exchange,
        quote_currency=quote,
        timeframe=timeframe,
        symbols=sorted(set(settled_symbols)) or symbols,
        setup_mode="free_text",
        setup_text=_bounded_setup_text(text),
        trigger_mode=trigger_mode,
        forming_alerts="forming alerts" in text.casefold(),
        near_miss_threshold=70,
        delivery_channels=["telegram"],
        maximum_alerts_per_hour=50,
        capability_bindings=capability_bindings or [],
        resolved_state=dict(settled),
    )


def _explicit_closed_candle_requested(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:candle|bar)\s+(?:must\s+)?clos(?:e|es|ed)\b|"
            r"\b(?:closed|completed)\s+(?:candle|bar)\b|"
            r"\bon\s+(?:candle|bar)\s+close\b",
            text.casefold(),
        )
    )


def _condition_rules(node: ConditionRule | ConditionGroup) -> list[ConditionRule]:
    if isinstance(node, ConditionRule):
        return [node]
    rules: list[ConditionRule] = []
    for child in node.children:
        rules.extend(_condition_rules(child))
    return rules


def _requests_favorites_universe(value: str) -> bool:
    """Recognize the customer-facing Favorites label without treating it as a mechanic."""
    return bool(
        re.search(
            r"\b(?:my\s+)?favou?rites?\s*(?:assets?|coins?|list)?\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _setup_mode(chat: AISetupChatSession) -> Literal["scanner", "monitor"]:
    return "scanner" if (chat.context_json or {}).get("setup_mode") == "scanner" else "monitor"


def v2_scanner_approval_matches(
    chat: AISetupChatSession,
    draft: StrategyDraftV2,
    definition: StrategyDefinition,
    *,
    user_id: UUID,
) -> bool:
    """One read-only proof that Scanner may execute the reviewed V2 draft."""

    return bool(
        (chat.context_json or {}).get("strategy_state_authority") == "v2"
        and chat.status == "approved"
        and draft.mode == DraftMode.SCANNER
        and draft.approval.approved
        and draft.approval.user_id == user_id
        and draft.approval.executable_version == draft.executable_version
        and draft.approval.executable_hash == draft.executable_hash
        and draft.approval.schema_hash == definition.canonical_hash()
        and chat.approved_strategy_id is not None
        and chat.approved_strategy_version_id is not None
    )


def _parse_screened_symbols(value: str) -> list[str]:
    ignored = {
        "AND",
        "OR",
        "THE",
        "ASSET",
        "ASSETS",
        "COIN",
        "COINS",
        "SPOT",
        "PAIR",
        "PAIRS",
    }
    tokens = re.findall(
        r"\b[A-Za-z0-9]{2,15}(?:/[A-Za-z0-9]{2,12})?\b",
        value.upper(),
    )
    symbols: list[str] = []
    for token in tokens:
        if token in ignored:
            continue
        symbol = token if "/" in token else f"{token}/USDT"
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols[:1000]


def _rule_roles(
    definition: StrategyDefinition,
    *,
    setup_mode: Literal["scanner", "monitor"] = "monitor",
) -> dict[str, str]:
    rules = _condition_rules(definition.conditions)
    if setup_mode == "scanner":
        return {
            rule.key: "current_match_condition" if rule.required else "optional_suggestion"
            for rule in rules
        }
    required = [rule for rule in rules if rule.required]
    if not required:
        return {rule.key: "optional_suggestion" for rule in rules}

    trigger_terms = (
        "trigger",
        "alert when",
        "cross",
        "breakout",
        "breakdown",
        "sweep",
        "reclaim",
        "engulf",
        "close",
    )
    confirmation_terms = ("confirm", "candle close", "engulf", "reclaim")

    def trigger_score(rule: ConditionRule) -> tuple[int, int]:
        text = " ".join(
            value
            for value in (rule.label, rule.source_fragment, rule.explanation_template)
            if value
        ).casefold()
        return (
            sum(1 for term in trigger_terms if term in text),
            int(str(rule.timeframe) == str(definition.base_timeframe)),
        )

    primary = max(enumerate(required), key=lambda item: (*trigger_score(item[1]), -item[0]))[1]
    roles: dict[str, str] = {}
    for rule in rules:
        if not rule.required:
            roles[rule.key] = "optional_suggestion"
            continue
        if rule is primary:
            roles[rule.key] = "primary_trigger"
            continue
        text = " ".join(value for value in (rule.label, rule.source_fragment) if value).casefold()
        roles[rule.key] = (
            "required_confirmation"
            if any(term in text for term in confirmation_terms)
            else "required_filter"
        )
    return roles


def _unsupported_data_request(text: str) -> list[dict[str, Any]]:
    lowered = text.casefold()
    concepts = {
        "order_book_imbalance": ("order book imbalance", ("order book imbalance",)),
        "cumulative_volume_delta": ("CVD", (r"\bcvd\b", "cumulative volume delta")),
        "liquidation_heatmap": ("liquidation heatmap", ("liquidation heatmap",)),
        "whale_wallets": ("whale-wallet activity", ("whale wallet", "whale activity")),
        "fear_and_greed": ("Fear and Greed Index", ("fear and greed",)),
        "news_sentiment": ("news sentiment", ("news sentiment", "sentiment from news")),
    }
    findings: list[dict[str, Any]] = []
    for code, (label, patterns) in concepts.items():
        if not any(re.search(pattern, lowered) for pattern in patterns):
            continue
        findings.append(
            {
                "code": code,
                "label": label,
                "message": (
                    f"{label} is unavailable because no configured and verified provider "
                    "supplies that evidence."
                ),
                "blocking": True,
                "source_fragment": text[:500],
            }
        )
    return findings


def _beginner_explanations(text: str) -> list[dict[str, str]]:
    lowered = text.casefold()
    glossary = {
        "rvol": "Relative volume compares current volume with its recent average.",
        "htf": "Higher timeframe means a broader chart, such as 4h beside a 15m trigger.",
        "breakout": "A breakout is a measurable move beyond a defined recent price level.",
        "retest": "A retest is price returning to a broken level to check whether it holds.",
        "confirmation": "Confirmation is an extra measurable event used to support the trigger.",
        "invalidation": "Invalidation defines when the monitored idea is no longer valid.",
        "candle close": "Candle close waits for the timeframe bar to finish before evaluating it.",
    }
    aliases = {
        "rvol": (r"\brvol\b", "relative volume"),
        "htf": (r"\bhtf\b", "higher timeframe"),
        "breakout": (r"\bbreakouts?\b",),
        "retest": (r"\bretests?\b",),
        "confirmation": (r"\bconfirmation\b",),
        "invalidation": (r"\binvalidation\b",),
        "candle close": (r"\bcandle[- ]close\b", r"\bcandle closes?\b"),
    }
    return [
        {
            "term": term.upper() if term in {"rvol", "htf"} else term.title(),
            "explanation": glossary[term],
        }
        for term, patterns in aliases.items()
        if any(re.search(pattern, lowered) for pattern in patterns)
    ]


def rule_confidence(definition: StrategyDefinition) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for rule in _condition_rules(definition.conditions):
        score = rule.confidence if rule.confidence is not None else 0.75
        level = "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
        payload.append(
            {
                "rule_key": rule.key,
                "label": rule.label,
                "confidence": level,
                "score": round(score, 2),
                "requires_confirmation": level == "low",
                "source_fragment": rule.source_fragment,
            }
        )
    return payload


def lint_strategy(
    definition: StrategyDefinition,
    preview: InterpretationPreview,
    *,
    setup_mode: Literal["scanner", "monitor"] = "monitor",
) -> list[dict[str, Any]]:
    rules = _condition_rules(definition.conditions)
    warnings: list[dict[str, Any]] = []
    if not rules or all(rule.key == "clarification_required" for rule in rules):
        warnings.append(
            {
                "code": "setup_too_vague",
                "severity": "critical",
                "message": "No executable monitored condition was defined.",
            }
        )
    for issue in preview.ambiguities:
        warnings.append({"code": issue.code, "severity": "critical", "message": issue.message})
    for issue in preview.unsupported_conditions:
        warnings.append(
            {
                "code": issue.code,
                "severity": "critical" if issue.blocking else "warning",
                "message": issue.message,
            }
        )
    coverage = _prompt_coverage_report(preview)
    uncovered = [
        item
        for item in coverage.get("mapping_table", [])
        if isinstance(item, dict) and item.get("bucket") == "unclassified"
    ]
    if uncovered:
        warnings.append(
            {
                "code": "meaningful_clause_uncovered",
                "severity": "critical",
                "message": (
                    "One or more instructions are not represented in the rule set. "
                    "Clarify or remove them before approval."
                ),
            }
        )
    required = [rule for rule in rules if rule.required]
    if rules and not required:
        warnings.append(
            {
                "code": (
                    "scanner_condition_required"
                    if setup_mode == "scanner"
                    else "primary_trigger_required"
                ),
                "severity": "critical",
                "message": (
                    "Define at least one measurable condition that coins must match now."
                    if setup_mode == "scanner"
                    else "Define at least one measurable required trigger before approval."
                ),
            }
        )
    if len(required) > 8:
        warnings.append(
            {
                "code": "setup_may_be_too_strict",
                "severity": "warning",
                "message": (
                    f"This {'scan' if setup_mode == 'scanner' else 'monitor'} has "
                    f"{len(required)} required rules, so matches may be rare."
                ),
            }
        )
    if len(required) == 1:
        warnings.append(
            {
                "code": "setup_may_be_noisy",
                "severity": "info",
                "message": (
                    "A single required rule may create many matches. Consider another measurable "
                    "condition or a narrower screened universe."
                ),
            }
        )
    if (
        setup_mode == "monitor"
        and definition.trigger_mode.value == "intrabar"
        and definition.alerts.cooldown_seconds < 60
    ):
        warnings.append(
            {
                "code": "intrabar_noise_risk",
                "severity": "warning",
                "message": (
                    "Intrabar monitoring with a short cooldown may produce repetitive alerts."
                ),
            }
        )
    constraints: dict[tuple[str, str], dict[str, list[float]]] = {}
    for rule in required:
        name = rule.left.name or rule.left.field or rule.key
        right_value = getattr(rule.right, "value", None) if rule.right else None
        if not isinstance(right_value, int | float):
            continue
        bucket = constraints.setdefault((str(rule.timeframe), name), {"lower": [], "upper": []})
        if rule.comparator.value in {"gt", "gte"}:
            bucket["lower"].append(float(right_value))
        elif rule.comparator.value in {"lt", "lte"}:
            bucket["upper"].append(float(right_value))
    for (timeframe, name), bucket in constraints.items():
        if bucket["lower"] and bucket["upper"] and max(bucket["lower"]) >= min(bucket["upper"]):
            warnings.append(
                {
                    "code": "contradictory_thresholds",
                    "severity": "critical",
                    "message": f"{name} has incompatible required thresholds on {timeframe}.",
                }
            )
    return warnings


def translation_sheet(
    original_idea: str,
    definition: StrategyDefinition,
    preview: InterpretationPreview,
    *,
    setup_mode: Literal["scanner", "monitor"] = "monitor",
) -> dict[str, Any]:
    rules = _condition_rules(definition.conditions)
    roles = _rule_roles(definition, setup_mode=setup_mode)
    risk_invalidation = None
    if definition.risk.enabled:
        risk_invalidation = {
            "stop_method": definition.risk.stop_method,
            "maximum_stop_percent": definition.risk.maximum_stop_percent,
        }
    trigger = next(
        (rule for rule in rules if roles.get(rule.key) == "primary_trigger"),
        rules[0] if rules else None,
    )
    required_filters = sum(1 for role in roles.values() if role == "required_filter")
    required_confirmations = sum(1 for role in roles.values() if role == "required_confirmation")
    optional_count = sum(1 for role in roles.values() if role == "optional_suggestion")
    required_filter_rules = [
        rule.label for rule in rules if roles.get(rule.key) == "required_filter"
    ]
    required_confirmation_rules = [
        rule.label for rule in rules if roles.get(rule.key) == "required_confirmation"
    ]
    optional_rules = [rule.label for rule in rules if roles.get(rule.key) == "optional_suggestion"]
    current_match_rules = [
        rule.label for rule in rules if roles.get(rule.key) == "current_match_condition"
    ]
    context_clause = (
        f"Supporting context is read on {', '.join(definition.supporting_timeframes)}; "
        f"the trigger is evaluated on {definition.base_timeframe}. "
        if definition.supporting_timeframes
        else ""
    )
    summary = (
        (
            f"HilalMarkets will scan {definition.universe.exchange.title()} spot markets on "
            f"{definition.base_timeframe} and return coins where all "
            f"{len(current_match_rules)} required current condition"
            f"{'s' if len(current_match_rules) != 1 else ''} match. "
            f"{context_clause}"
            f"{optional_count} rule"
            f"{'s are' if optional_count != 1 else ' is'} optional."
        )
        if setup_mode == "scanner"
        else (
            f"HilalMarkets will watch {definition.universe.exchange.title()} spot markets on "
            f"{definition.base_timeframe}. The trigger is "
            f"{trigger.label if trigger else 'not yet defined'}"
            f" with {required_filters} required filter"
            f"{'s' if required_filters != 1 else ''} and "
            f"{required_confirmations} required confirmation"
            f"{'s' if required_confirmations != 1 else ''}. "
            f"{context_clause}"
            f"{optional_count} rule"
            f"{'s are' if optional_count != 1 else ' is'} optional suggestions."
        )
    )
    watchlist = (
        ", ".join(definition.universe.include_symbols)
        if definition.universe.include_symbols
        else f"All eligible {', '.join(definition.universe.quote_currencies)} spot pairs"
    )
    timeframes = [definition.base_timeframe, *definition.supporting_timeframes]
    invalidation = (
        f"{definition.risk.stop_method} (maximum {definition.risk.maximum_stop_percent}%)"
        if definition.risk.enabled and definition.risk.maximum_stop_percent is not None
        else definition.risk.stop_method
        if definition.risk.enabled
        else "Not provided; research monitoring only"
    )
    common_fields = [
        {"label": "Mode", "value": "One-time Scanner" if setup_mode == "scanner" else "Monitor"},
        {
            "label": "Market",
            "value": (
                f"{definition.universe.exchange.title()} {definition.universe.market_type.value}"
            ),
        },
        {"label": "Market universe", "value": watchlist},
        {"label": "Timeframes", "value": ", ".join(timeframes)},
        {"label": "Direction", "value": definition.direction.value.replace("_", " ").title()},
    ]
    scanner_fields = [
        {
            "label": "Current-match conditions",
            "value": ", ".join(current_match_rules) or "Not defined",
        },
        {
            "label": "Evaluation timing",
            "value": (
                "Current market snapshot"
                if definition.trigger_mode == TriggerMode.INTRABAR
                else "Latest closed candle"
            ),
        },
        {
            "label": "Rule logic",
            "value": definition.conditions.operator.value.upper(),
        },
        {"label": "Optional ideas", "value": ", ".join(optional_rules) or "None"},
    ]
    monitor_fields = [
        {"label": "Monitor name", "value": definition.name},
        {"label": "Primary trigger", "value": trigger.label if trigger else "Not defined"},
        {
            "label": "Required filters",
            "value": ", ".join(required_filter_rules) or "None",
        },
        {
            "label": "Required confirmations",
            "value": ", ".join(required_confirmation_rules) or "None",
        },
        {"label": "Optional ideas", "value": ", ".join(optional_rules) or "None"},
        {
            "label": "Rule logic",
            "value": definition.conditions.operator.value.upper(),
        },
        {
            "label": "Alert timing",
            "value": definition.trigger_mode.value.replace("_", " ").title(),
        },
        {
            "label": "Delivery",
            "value": ", ".join(definition.alerts.channels) or "Choose before activation",
        },
        {"label": "Invalidation", "value": invalidation},
    ]
    fields = [*common_fields, *(scanner_fields if setup_mode == "scanner" else monitor_fields)]
    coverage = _prompt_coverage_report(preview)
    return {
        "original_idea": original_idea,
        "setup_mode": setup_mode,
        "summary_paragraph": summary,
        "monitor_name": definition.name,
        "direction": definition.direction.value,
        "market_type": definition.universe.market_type.value,
        "exchange": definition.universe.exchange,
        "symbols_watchlist": definition.universe.include_symbols,
        "quote_currencies": definition.universe.quote_currencies,
        "timeframes": timeframes,
        "logic_operator": definition.conditions.operator.value,
        "conditions": [
            {
                "key": rule.key,
                "name": rule.label,
                "required": rule.required,
                "role": roles.get(rule.key, "optional_suggestion"),
                "timeframe": str(rule.timeframe),
                "operator": rule.comparator.value,
                "explanation": rule.explanation_template or rule.notes,
                "source_fragment": rule.source_fragment,
            }
            for rule in rules
        ],
        "invalidation": risk_invalidation,
        "alert_timing": {
            "trigger_mode": definition.trigger_mode.value,
            "cooldown_seconds": definition.alerts.cooldown_seconds,
            "forming_alerts": definition.alerts.forming_alerts,
        },
        "delivery_channels": definition.alerts.channels,
        "fields": fields,
        "assumptions": preview.assumptions,
        "unsupported_conditions": [
            issue.model_dump(mode="json") for issue in preview.unsupported_conditions
        ],
        "clause_coverage": [
            _beginner_clause_coverage(
                item,
                rules_by_key={rule.key: rule for rule in rules},
            )
            for item in coverage.get("mapping_table", [])
            if isinstance(item, dict)
        ],
        "approval_required": setup_mode == "monitor",
        "execution": "No automatic trade execution. Deterministic monitoring only.",
    }


def _prompt_coverage_report(preview: InterpretationPreview) -> dict[str, Any]:
    metadata = dict(preview.raw_metadata or {})
    report = metadata.get("prompt_coverage_report") or metadata.get("openai_prompt_coverage_report")
    return dict(report) if isinstance(report, dict) else {}


def _beginner_clause_coverage(
    item: dict[str, Any],
    *,
    rules_by_key: dict[str, ConditionRule] | None = None,
    fragment_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bucket = str(item.get("bucket") or "unclassified")
    status_map = {
        "executable_condition": (
            "covered",
            "COVERED",
            "Included as a required rule.",
            False,
        ),
        "optional_condition": (
            "intentionally_optional",
            "INTENTIONALLY_OPTIONAL",
            "Included as an optional confirmation.",
            False,
        ),
        "ambiguity": (
            "needs_clarification",
            "NEEDS_CLARIFICATION",
            "Needs one clear definition.",
            True,
        ),
        "unsupported": (
            "unsupported",
            "PROVIDER_UNAVAILABLE",
            "Cannot run with the configured data.",
            True,
        ),
        "assumption": (
            "covered_as_assumption",
            "NON_EXECUTABLE_CONTEXT",
            "Shown openly as an assumption.",
            False,
        ),
        "ignored_filler": (
            "not_executable",
            "NON_EXECUTABLE_CONTEXT",
            "Conversational wording only.",
            False,
        ),
        "unclassified": (
            "needs_clarification",
            "NEEDS_CLARIFICATION",
            "This instruction is not represented in the current rules.",
            True,
        ),
    }
    status, status_code, explanation, blocking = status_map.get(
        bucket,
        (
            "needs_clarification",
            "NEEDS_CLARIFICATION",
            "This instruction needs review.",
            True,
        ),
    )
    condition_key = item.get("condition_id")
    rule = (rules_by_key or {}).get(str(condition_key)) if condition_key else None
    source_fragment = str(item.get("fragment") or "")[:1000]
    source_message_id = None
    normalized = " ".join(source_fragment.casefold().split())
    for record in reversed(fragment_records or []):
        candidate = " ".join(
            str(record.get("fragment") or record.get("user_text") or "").casefold().split()
        )
        if normalized and candidate and (normalized in candidate or candidate in normalized):
            source_message_id = record.get("user_message_id")
            break
    return {
        "source_fragment": source_fragment,
        "source_message_id": source_message_id,
        "status": status,
        "status_code": status_code,
        "explanation": explanation,
        "condition_key": condition_key,
        "capability_key": rule.capability_key if rule else None,
        "capability_version": rule.capability_version if rule else None,
        "parameters": {},
        "required": rule.required if rule else None,
        "blocking": blocking,
    }


def _structured_intent_state(
    definition: StrategyDefinition,
    *,
    context: dict[str, Any],
    preview: InterpretationPreview,
    lint: list[dict[str, Any]],
    setup_mode: Literal["scanner", "monitor"] = "monitor",
) -> dict[str, Any]:
    rules = _condition_rules(definition.conditions)
    roles = _rule_roles(definition, setup_mode=setup_mode)
    fragment_records = [
        item for item in context.get("setup_fragment_records") or [] if isinstance(item, dict)
    ]

    def rule_record(rule: ConditionRule) -> dict[str, Any]:
        source = str(rule.source_fragment or "")
        source_normalized = " ".join(source.casefold().split())
        references = []
        for item in fragment_records:
            candidate = " ".join(
                str(item.get("fragment") or item.get("user_text") or "").casefold().split()
            )
            if (
                source_normalized
                and candidate
                and (source_normalized in candidate or candidate in source_normalized)
            ):
                references.append(
                    {
                        "message_id": item.get("user_message_id"),
                        "sequence": item.get("user_message_sequence"),
                    }
                )
        return {
            "rule_key": rule.key,
            "capability_key": rule.capability_key,
            "capability_version": rule.capability_version,
            "label": rule.label,
            "role": roles.get(rule.key, "required_rule" if rule.required else "optional"),
            "required": rule.required,
            "timeframe": str(rule.timeframe),
            "source_fragment": rule.source_fragment,
            "message_references": references[-5:],
        }

    rule_records = [rule_record(rule) for rule in rules]
    unresolved = [
        {
            "code": item.get("code"),
            "message": item.get("message"),
        }
        for item in lint
        if item.get("severity") == "critical"
    ]
    coverage_rows = [
        _beginner_clause_coverage(
            item,
            rules_by_key={rule.key: rule for rule in rules},
            fragment_records=fragment_records,
        )
        for item in _prompt_coverage_report(preview).get("mapping_table", [])
        if isinstance(item, dict)
    ]
    state = SetupChatConversationState(
        version=1,
        original_request=str(context.get("original_request") or ""),
        confirmed_requirements=[
            SetupConversationRequirement(
                field=str(item["rule_key"]),
                value=item,
                source_message_ids=[
                    str(reference["message_id"])
                    for reference in item.get("message_references") or []
                    if reference.get("message_id")
                ],
                confirmed=True,
            )
            for item in rule_records
        ],
        rejected_interpretations=list(context.get("correction_history") or [])[-50:],
        latest_correction_by_field=dict(context.get("latest_correction_by_field") or {}),
        required_conditions=[item for item in rule_records if item["required"]],
        optional_conditions=[item for item in rule_records if not item["required"]],
        exclusion_conditions=[
            item
            for item in rule_records
            if re.search(
                r"\b(?:avoid|exclude|without|not|no)\b",
                str(item["source_fragment"]),
                re.I,
            )
        ],
        logical_structure={
            "operator": definition.conditions.operator.value,
            "condition_keys": [item["rule_key"] for item in rule_records],
        },
        timeframes=list(
            dict.fromkeys(
                [str(definition.base_timeframe), *map(str, definition.supporting_timeframes)]
            )
        ),
        direction=definition.direction.value,
        universe={
            "exchange": definition.universe.exchange,
            "market_type": definition.universe.market_type.value,
            "symbols": list(definition.universe.include_symbols),
            "quote_currencies": list(definition.universe.quote_currencies),
            "screened_universe_mode": context.get("screened_universe_mode"),
        },
        selected_methodology={
            "id": context.get("sharia_methodology_id"),
            "code": context.get("sharia_methodology_code"),
            "name": context.get("sharia_methodology_name"),
            "version": context.get("sharia_methodology_version"),
        },
        alert_timing={
            "trigger_mode": definition.trigger_mode.value,
            "cooldown_seconds": definition.alerts.cooldown_seconds,
            "forming_alerts": definition.alerts.forming_alerts,
        },
        delivery_preferences=list(definition.alerts.channels),
        invalidation_rules=[
            {
                "enabled": definition.risk.enabled,
                "stop_method": definition.risk.stop_method,
                "maximum_stop_percent": definition.risk.maximum_stop_percent,
            }
        ]
        if definition.risk.enabled
        else [],
        setup_mode=("scanner" if context.get("setup_mode") == "scanner" else "monitor"),
        custom_terminology={
            str(key): str(value)
            for key, value in dict(context.get("resolved_ambiguities") or {}).items()
        },
        unresolved_contradictions=unresolved,
        capability_bindings=list(context.get("capability_bindings") or [])[-100:],
        user_message_ids=list(
            dict.fromkeys(
                str(item.get("user_message_id"))
                for item in fragment_records
                if item.get("user_message_id")
            )
        )[-200:],
        tool_results=list(dict(context.get("conversation_state") or {}).get("tool_results") or [])[
            -50:
        ],
        draft_version={
            "canonical_hash": definition.canonical_hash(),
            "schema_version": definition.schema_version,
            "monitor_name": definition.name,
        },
        clause_coverage=[
            AgentClauseCoverage.model_validate(
                {
                    "source_fragment": item["source_fragment"],
                    "source_message_id": item.get("source_message_id"),
                    "status": item["status_code"],
                    "compiled_field": item.get("condition_key"),
                    "capability_key": item.get("capability_key"),
                    "capability_version": item.get("capability_version"),
                    "parameters": item.get("parameters") or {},
                    "required": item.get("required"),
                    "explanation": item["explanation"],
                }
            )
            for item in coverage_rows
        ],
    )
    return state.model_dump(mode="json")


def _refresh_conversation_state(
    chat: AISetupChatSession,
    *,
    context: dict[str, Any],
    user_message: AISetupChatMessage | None,
    tool_results: list[AgentToolResult],
) -> dict[str, Any]:
    raw_state = context.get("conversation_state") or {}
    try:
        state = SetupChatConversationState.model_validate(raw_state)
    except ValidationError:
        state = SetupChatConversationState()
    message_ids = list(state.user_message_ids)
    if user_message is not None:
        message_ids.append(str(user_message.id))
    summaries = list(state.tool_results)
    summaries.extend(
        {
            "tool_name": result.tool_name,
            "status": result.status,
            "evidence_refs": list(result.evidence_refs),
            "data_fields": sorted(str(key) for key in result.data)[:40],
        }
        for result in tool_results
    )
    updates: dict[str, Any] = {
        "original_request": state.original_request or chat.original_idea or "",
        "setup_mode": "scanner" if context.get("setup_mode") == "scanner" else "monitor",
        "latest_correction_by_field": dict(context.get("latest_correction_by_field") or {}),
        "capability_bindings": list(context.get("capability_bindings") or [])[-100:],
        "custom_terminology": {
            str(key): str(value)
            for key, value in dict(context.get("resolved_ambiguities") or {}).items()
        },
        "user_message_ids": list(dict.fromkeys(message_ids))[-200:],
        "tool_results": summaries[-50:],
    }
    if context.get("schema_hash"):
        updates["draft_version"] = {
            **dict(state.draft_version),
            "canonical_hash": context.get("schema_hash"),
        }
    return state.model_copy(update=updates).model_dump(mode="json")


def _improvement_suggestions(
    definition: StrategyDefinition,
    *,
    setup_mode: Literal["scanner", "monitor"] = "monitor",
) -> list[str]:
    rules = _condition_rules(definition.conditions)
    text = " ".join(rule.label.casefold() for rule in rules)
    suggestions: list[str] = []
    if "volume" not in text:
        suggestions.append("Add a measurable volume threshold if volume quality matters to you.")
    if definition.trigger_mode.value == "intrabar":
        suggestions.append(
            "Consider candle-close confirmation to reduce temporary intrabar matches."
        )
    if setup_mode == "monitor" and not definition.risk.enabled:
        suggestions.append(
            "Optionally define an invalidation rule so HilalMarkets can explain when the idea "
            "is no longer valid."
        )
    if not definition.supporting_timeframes:
        suggestions.append("Optionally add a higher-timeframe alignment rule.")
    if not definition.universe.include_symbols:
        suggestions.append("Narrow the screened universe if you want fewer, more focused matches.")
    if setup_mode == "monitor" and definition.alerts.cooldown_seconds < 300:
        suggestions.append("Use a cooldown to reduce repeated alerts for the same symbol.")
    return suggestions[:6]


def _scanner_result_payload(response: Any) -> dict[str, Any]:
    results = list(response.results)
    outcome_counts = Counter(item.outcome for item in results)
    missing_counts: Counter[str] = Counter(
        condition.name for item in results for condition in item.missing_conditions
    )
    return {
        "status": response.status,
        "evaluated_at": response.evaluated_at.isoformat(),
        "symbols_requested": response.symbols_requested,
        "symbols_scanned": response.symbols_scanned,
        "confirmed_count": outcome_counts.get("confirmed", 0),
        "forming_count": outcome_counts.get("forming", 0) + outcome_counts.get("near_miss", 0),
        "failed_count": sum(
            count
            for outcome, count in outcome_counts.items()
            if outcome not in {"confirmed", "forming", "near_miss"}
        ),
        "common_missing_reasons": [
            {"condition": name, "count": count} for name, count in missing_counts.most_common(3)
        ],
        "results": [item.model_dump(mode="json") for item in results[:100]],
        "warnings": list(response.warnings),
        "disclaimer": "Scanner results are market research, not buy or sell advice.",
    }


def _safe_agent_scanner_warning(value: Any) -> str:
    parts = str(value).split(":", 2)
    if len(parts) >= 2 and re.fullmatch(r"[A-Z0-9._-]+/[A-Z0-9._-]+", parts[0].strip()):
        error_type = re.sub(r"[^A-Za-z0-9_]", "", parts[1])[:80]
        if error_type:
            return f"{parts[0].strip()}: {error_type}"
    return "One or more symbols could not be evaluated; no result was inferred."


def _scanner_result_message(result: dict[str, Any]) -> str:
    return (
        f"Scanner checked {result['symbols_scanned']} symbols: "
        f"{result['confirmed_count']} confirmed, {result['forming_count']} forming, and "
        f"{result['failed_count']} not matched. Review the evidence below; this is research only."
    )


def _unresolved_ambiguities(text: str, resolved: dict[str, str]) -> list[SetupChatClarification]:
    lowered = text.casefold()
    definitions = {
        "breakout": (
            "What measurable event should count as a breakout?",
            "A breakout needs a price level, lookback, and close/intrabar rule.",
            [
                ("20-candle close", "Candle closes above the previous 20-candle high"),
                ("50-candle close", "Candle closes above the previous 50-candle high"),
                ("Intrabar break", "Price trades above the previous 20-candle high intrabar"),
            ],
        ),
        "strong_volume": (
            "How strong should volume be?",
            "A numeric multiplier makes volume deterministic and testable.",
            [
                ("1.5x average", "Volume is at least 1.5x the 20-candle average"),
                ("2.0x average", "Volume is at least 2.0x the 20-candle average"),
                ("Above average", "Volume is above the 20-candle average"),
            ],
        ),
        "near_support": (
            "How close to support should price be?",
            "Near needs a percentage distance and a deterministic support definition.",
            [
                ("Within 1%", "Price is within 1% of the previous 20-candle low"),
                ("Within 0.5%", "Price is within 0.5% of the previous 20-candle low"),
                ("ATR distance", "Price is within 0.5 ATR of the previous 20-candle low"),
            ],
        ),
        "momentum": (
            "Which measurable indicator should define momentum?",
            "Momentum can mean several different deterministic calculations.",
            [
                ("RSI", "RSI 14 is above 55"),
                ("MACD", "MACD line is above its signal line"),
                ("Rate of change", "20-candle rate of change is above 0%"),
            ],
        ),
        "clean_retest": (
            "What should count as a clean retest?",
            "Retest quality needs a level, tolerance, and closing rule.",
            [
                (
                    "1% reclaim",
                    "Price retests within 1% of the breakout level and closes back above it",
                ),
                ("0.5 ATR reclaim", "Price retests within 0.5 ATR and closes back above the level"),
            ],
        ),
        "fakeout": (
            "What measurable sequence should count as a fakeout?",
            "A fakeout needs an exact break-and-reclaim sequence.",
            [
                (
                    "Same-candle reclaim",
                    "Price trades beyond the level and the same candle closes back inside",
                ),
                (
                    "Two-candle reclaim",
                    "Price breaks the level and closes back inside within two candles",
                ),
            ],
        ),
        "confirmation": (
            "What specific confirmation should HilalMarkets wait for?",
            "Confirmation must name a candle, indicator, volume, or close condition.",
            [
                ("Candle close", "Wait for the trigger candle to close"),
                ("Volume", "Require volume at least 1.5x the 20-candle average"),
                ("RSI", "Require RSI 14 above 50"),
            ],
        ),
        "reference_sweep_side": (
            "Which side of the previous period should the current candle sweep?",
            (
                "A candle cannot sweep an entire previous candle as one level; choose its "
                "high or low so the rule is measurable."
            ),
            [
                (
                    "Previous low",
                    "Sweep the previous period low",
                ),
                (
                    "Previous high",
                    "Sweep the previous period high",
                ),
            ],
        ),
    }
    triggers = {
        "breakout": re.search(r"\bbreakouts?\b", lowered),
        "strong_volume": re.search(r"\b(?:strong|high|heavy) volume\b", lowered),
        "near_support": re.search(r"\bnear (?:support|resistance)\b", lowered),
        "momentum": re.search(r"\bmomentum\b", lowered),
        "clean_retest": re.search(r"\bclean retest\b", lowered),
        "fakeout": re.search(r"\bfakeout\b|\bfake out\b", lowered),
        "confirmation": (
            None
            if re.search(r"\bcandle[- ]close confirmation\b", lowered)
            else re.search(r"\bconfirmation\b", lowered)
        ),
        "reference_sweep_side": (
            re.search(r"\b(?:swept|sweep(?:ed|s|ing)?)\b", lowered)
            if re.search(
                r"\b(?:previous|prior|last)\s+(?:day|daily|week|weekly|month|monthly)"
                r"(?:\s+candle)?\b",
                lowered,
            )
            and not re.search(r"\b(?:high|low|bullish|bearish)\b", lowered)
            else None
        ),
    }
    results: list[SetupChatClarification] = []
    for key, match in triggers.items():
        if not match or key in resolved:
            continue
        question, reason, options = definitions[key]
        results.append(
            _with_other_option(
                SetupChatClarification(
                    key=key,
                    question=question,
                    reason=reason,
                    options=[
                        SetupChatOption(key=key, label=label, value=value)
                        for label, value in options
                    ],
                )
            )
        )
    return results


def _clarification_identity(clarification: SetupChatClarification) -> str:
    """Return a stable semantic identity so rephrased AI questions cannot loop."""
    key = re.sub(r"[^a-z0-9]+", " ", clarification.key.casefold()).strip()
    question = re.sub(r"[^a-z0-9]+", " ", clarification.question.casefold()).strip()
    combined = f"{key} {question}"
    semantic_families = {
        "timeframe": ("timeframe", "time frame"),
        "universe": ("universe", "watchlist", "symbols", "pairs"),
        "exchange": ("exchange",),
        "direction": ("direction", "bullish", "bearish"),
        "breakout": ("breakout",),
        "strong_volume": ("strong volume", "volume multiplier", "volume"),
        "near_support": ("near support", "near resistance", "support distance"),
        "momentum": ("momentum",),
        "fvg_definition": ("fvg", "fair value gap"),
        "clean_retest": ("clean retest", "retest"),
        "fakeout": ("fakeout", "fake out"),
        "confirmation": ("confirmation", "candle close"),
        "tolerance": ("tolerance", "allowed margin", "price margin"),
        "reference_sweep_side": ("which side", "previous period", "previous candle"),
        "persistence": ("persist", "consecutive candles", "how many candles"),
        "alert_timing": ("alert timing", "when should", "candle close or intrabar"),
        "invalidation": ("invalidation", "invalidate", "invalidated"),
    }
    for identity, terms in semantic_families.items():
        if any(term in combined for term in terms):
            return f"semantic:{identity}"
    return f"question:{question or key}"


def _clarification_budget_subject(
    clarification: SetupChatClarification,
) -> str | None:
    key = clarification.key.casefold().strip()
    if key in {
        "setup_mode",
        "monitor_name",
        "screened_universe_mode",
        "screened_watchlist",
        "screened_explicit_assets",
        "compile_confirmation",
    }:
        return None
    combined = " ".join(
        (
            clarification.key,
            clarification.question,
            clarification.reason,
        )
    ).casefold()
    condition_families = {
        "rsi": ("rsi", "relative strength index"),
        "ema": ("ema", "exponential moving average"),
        "sma": ("sma", "simple moving average"),
        "macd": ("macd",),
        "vwap": ("vwap",),
        "fvg": ("fvg", "fair value gap"),
        "volume": ("volume", "rvol", "relative volume"),
        "liquidity_sweep": ("liquidity sweep", "swept", "sweep"),
        "head_and_shoulders": ("head and shoulders", "head & shoulders", "neckline"),
        "support_resistance": ("support", "resistance"),
        "candle_pattern": ("candle", "doji", "hammer", "engulf"),
    }
    for identity, terms in condition_families.items():
        if any(term in combined for term in terms):
            return f"condition:{identity}"
    return _clarification_identity(clarification)


def _apply_clarification_question_budget(
    context: dict[str, Any],
    clarifications: list[SetupChatClarification],
) -> list[SetupChatClarification]:
    counts = {
        str(key): max(0, int(value))
        for key, value in dict(context.get("clarification_question_counts") or {}).items()
    }
    planned: Counter[str] = Counter()
    allowed: list[SetupChatClarification] = []
    exhausted: list[dict[str, str]] = []
    for clarification in clarifications:
        subject = _clarification_budget_subject(clarification)
        if subject is None:
            allowed.append(clarification)
            continue
        if counts.get(subject, 0) + planned[subject] >= (MAX_CLARIFICATION_QUESTIONS_PER_CONDITION):
            exhausted.append(
                {
                    "subject": subject,
                    "key": clarification.key,
                    "question": clarification.question,
                }
            )
            continue
        planned[subject] += 1
        allowed.append(clarification)
    if exhausted:
        previous = [
            item
            for item in context.get("clarification_budget_exhausted") or []
            if isinstance(item, dict)
        ]
        identities = {(str(item.get("subject")), str(item.get("key"))) for item in previous}
        for item in exhausted:
            identity = (item["subject"], item["key"])
            if identity not in identities:
                previous.append(item)
                identities.add(identity)
        context["clarification_budget_exhausted"] = previous[-100:]
    return allowed


def _cap_open_clarifications(
    context: dict[str, Any],
    clarifications: list[SetupChatClarification],
) -> list[SetupChatClarification]:
    """Keep at most ``MAX_UNRESOLVED_CLARIFICATIONS`` questions open at once.

    Deferred questions are not silently dropped. Each one is recorded on the session
    so the draft can disclose that the field fell back to its documented default,
    and the user can still override any of them in a later turn.
    """
    if len(clarifications) <= MAX_UNRESOLVED_CLARIFICATIONS:
        return clarifications
    deferred = clarifications[MAX_UNRESOLVED_CLARIFICATIONS:]
    recorded = [
        item for item in context.get("deferred_clarifications") or [] if isinstance(item, dict)
    ]
    seen = {str(item.get("key")) for item in recorded}
    for item in deferred:
        if item.key in seen:
            continue
        recorded.append({"key": item.key, "question": item.question, "reason": item.reason})
        seen.add(item.key)
    context["deferred_clarifications"] = recorded[-50:]
    return clarifications[:MAX_UNRESOLVED_CLARIFICATIONS]


def _record_clarification_question(
    context: dict[str, Any],
    clarification: SetupChatClarification,
) -> None:
    subject = _clarification_budget_subject(clarification)
    if subject is None:
        return
    counts = {
        str(key): max(0, int(value))
        for key, value in dict(context.get("clarification_question_counts") or {}).items()
    }
    counts[subject] = counts.get(subject, 0) + 1
    context["clarification_question_counts"] = counts


def _clarification_question_limit_reached(
    context: dict[str, Any],
    clarification: SetupChatClarification,
) -> bool:
    subject = _clarification_budget_subject(clarification)
    if subject is None:
        return False
    counts = dict(context.get("clarification_question_counts") or {})
    return int(counts.get(subject) or 0) >= MAX_CLARIFICATION_QUESTIONS_PER_CONDITION


def _unanswered_clarifications(
    clarifications: list[SetupChatClarification],
    answered_keys: set[str],
    answered_fingerprints: set[str],
) -> list[SetupChatClarification]:
    return [
        item
        for item in clarifications
        if item.key not in answered_keys
        and _clarification_identity(item) not in answered_fingerprints
    ]


def _set_awaiting_clarification(
    context: dict[str, Any], clarification: SetupChatClarification
) -> None:
    context["awaiting_clarification_key"] = clarification.key
    context["awaiting_clarification"] = {
        "key": clarification.key,
        "fingerprint": _clarification_identity(clarification),
        "clarification": clarification.model_dump(mode="json"),
    }


def _begin_clarification_set(
    context: dict[str, Any],
    clarifications: list[SetupChatClarification],
    *,
    source: str,
) -> bool:
    identities = {_clarification_identity(item) for item in clarifications}
    active_identities = set(context.get("active_clarification_keys") or [])
    is_new_set = context.get("active_clarification_source") != source or not identities.issubset(
        active_identities
    )
    if is_new_set:
        context["active_clarification_source"] = source
        context["active_clarification_keys"] = sorted(identities)
    return is_new_set


def _clarification_checkpoint_message(total: int, remaining: int) -> str:
    return (
        f"Clarification checkpoint: {remaining} detail{'s' if remaining != 1 else ''} "
        f"remain in this {total}-question review. I'll ask one at a time before validation."
    )


def _clarification_checkpoint_payload(
    *, source: str, total: int, current: int, remaining: int
) -> dict[str, Any]:
    return {
        "state": "clarifying",
        "source": source,
        "current": current,
        "total": total,
        "remaining": remaining,
    }


def _refusal_reasons(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consolidate compiler output so a blocking cause appears once in the review UI."""
    reasons: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        for item in source:
            message = str(item.get("message") or "").strip()
            if not message:
                continue
            code = str(item.get("code") or "validation_issue").strip()
            identity = (code.casefold(), re.sub(r"\s+", " ", message.casefold()))
            if identity in seen:
                continue
            seen.add(identity)
            severity = str(item.get("severity") or "critical").casefold()
            blocking = bool(item.get("blocking")) or severity == "critical"
            title, plain_message, next_step, category = _plain_attention_item(
                code,
                message,
            )
            reasons.append(
                {
                    "code": code,
                    "title": title,
                    "message": plain_message,
                    "next_step": next_step,
                    "category": category,
                    "severity": severity,
                    "blocking": blocking,
                    "label": "Fix before approval" if blocking else "Review note",
                }
            )
    return reasons


def _plain_attention_item(code: str, message: str) -> tuple[str, str, str, str]:
    normalized = code.casefold()
    quoted = re.search(r"['\"]([^'\"]{2,300})['\"]", message)
    instruction = quoted.group(1) if quoted else "one part of your request"
    if normalized == "scanner_condition_required":
        return (
            "Add one condition to the scan",
            "Scanner needs a measurable condition that coins must match right now.",
            "Describe a condition such as RSI below 30 on 15m.",
            "Scan condition",
        )
    if normalized in {"prompt_fragment_unclassified", "instruction_not_converted"}:
        return (
            "One instruction was not translated",
            f'I could not turn "{instruction}" into a monitor rule yet.',
            "Explain that market condition in your own words, or choose Build and test this rule.",
            "Rule meaning",
        )
    if normalized in {"no_supported_monitor_condition", "no_required_condition"}:
        return (
            "A measurable trigger is still missing",
            "The monitor does not yet know the exact market event that should start a match.",
            "Describe one event with a timeframe, such as RSI above 50 on 15m.",
            "Trigger",
        )
    if "timeframe" in normalized or "timeframe" in message.casefold():
        return (
            "The timeframe needs your choice",
            "One rule does not yet have a timeframe the monitor can use.",
            "Choose the candle timeframe for that rule.",
            "Timing",
        )
    if "contradict" in normalized or "incompatible" in message.casefold():
        return (
            "Two rules conflict",
            "The current rules cannot all be true at the same time.",
            "Tell me which rule should take priority or remove one of them.",
            "Rule logic",
        )
    if any(term in normalized for term in ("provider", "external_data", "data_unavailable")):
        return (
            "The required data is not available",
            "The connected market source cannot provide the information needed for this rule.",
            (
                "Remove that rule or replace it with one based on available spot price and "
                "volume data."
            ),
            "Data",
        )
    if "ambig" in normalized or "clarification" in normalized:
        return (
            "One rule has more than one meaning",
            "I need one more choice before I can translate this part safely.",
            "Answer the open question in the chat.",
            "Rule meaning",
        )
    if any(term in normalized for term in ("unsupported", "not_executable", "binding_invalid")):
        return (
            "One rule is not ready to run",
            "This idea is understood, but it is not yet connected to a verified monitor rule.",
            "Choose a matching rule or ask HilalMarkets to build and test a candle-based version.",
            "Rule availability",
        )
    return (
        "One detail needs review",
        _plain_validation_message(message),
        "Answer or revise this detail in the chat, then review the Translation Sheet again.",
        "Review",
    )


def _plain_validation_message(message: str) -> str:
    replacements = {
        "capability": "rule",
        "executable": "usable",
        "deterministic": "measurable",
        "schema": "rule format",
        "provider": "data source",
        "prompt fragment": "instruction",
        "canonical hash": "approved version",
    }
    result = message
    for technical, plain in replacements.items():
        result = re.sub(technical, plain, result, flags=re.IGNORECASE)
    result = re.sub(
        r"\b[a-z]+(?:_[a-z0-9]+)+\b",
        lambda match: match.group(0).replace("_", " "),
        result,
    )
    return result


def _is_platform_screening_only_fragment(fragment: str) -> bool:
    lowered = " ".join(fragment.casefold().replace("-", " ").split())
    if not re.search(r"\b(?:halal|shariah?|screened)\b", lowered):
        return False
    remainder = re.sub(
        r"\b(?:all|only|the|our|platform|hilalmarkets|hilal markets|currently|"
        r"halal|shariah?|screened|approved|eligible|spot|market|markets|"
        r"coin|coins|asset|assets|token|tokens|pair|pairs|universe|"
        r"find|show|scan|bring|me|in|on|from)\b",
        " ",
        lowered,
    )
    return not " ".join(remainder.split())


def _resolver_clarifications(
    report: CapabilityResolutionReport,
    resolved: dict[str, str],
    *,
    allow_mechanic_creation: bool,
) -> list[SetupChatClarification]:
    clarifications: list[SetupChatClarification] = []
    for fragment in report.fragments:
        if fragment.status == "matched" or _is_platform_screening_only_fragment(fragment.fragment):
            continue
        lowered_fragment = fragment.fragment.casefold()
        known_ambiguities = {
            "breakout": ("breakout",),
            "strong_volume": ("strong volume",),
            "near_support": ("near support",),
            "momentum": ("momentum",),
            "clean_retest": ("clean retest",),
            "fakeout": ("fakeout", "fake out"),
            "confirmation": ("confirmation",),
        }
        if any(
            key in resolved and any(term in lowered_fragment for term in terms)
            for key, terms in known_ambiguities.items()
        ):
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", fragment.fragment.casefold()).strip("_")[:48]
        key = f"capability_meaning_{slug or 'unknown'}"
        if key in resolved:
            continue
        options = [
            SetupChatOption(
                key=key,
                label=candidate.label,
                value=(
                    f"Interpret '{fragment.fragment}' as {candidate.label} "
                    f"({candidate.capability_key})"
                ),
                description=(
                    f"{round(candidate.confidence * 100)}% registry match | "
                    f"{candidate.temporal_behavior.replace('_', ' ')}"
                ),
            )
            for candidate in fragment.candidates[:3]
        ]
        if allow_mechanic_creation and fragment.status in {"unknown", "ambiguous"}:
            create_option = SetupChatOption(
                key=key,
                label=(
                    "None match - build this rule"
                    if fragment.candidates
                    else "Build and test this rule"
                ),
                value="__build_mechanic__",
                description=(
                    "AI drafts the candle logic; deterministic checks and market tests must "
                    "certify it before review."
                ),
                action="build_mechanic",
            )
            options = (
                [create_option, *options] if not fragment.candidates else [*options, create_option]
            )
        unknown_creatable = (
            fragment.status == "unknown" and allow_mechanic_creation and not fragment.candidates
        )
        clarifications.append(
            _with_other_option(
                SetupChatClarification(
                    key=key,
                    question=(
                        (
                            "I do not have a verified candle-data rule for "
                            f"'{fragment.fragment}' yet. Should I build and test that exact rule?"
                        )
                        if unknown_creatable
                        else fragment.clarification_question
                        or f"How should HilalMarkets measure '{fragment.fragment}'?"
                    ),
                    reason=(
                        (
                            "This can be proposed from closed price and volume history, but it "
                            "must pass deterministic validation before it can become a rule."
                        )
                        if unknown_creatable
                        else "I found more than one plausible registered mechanic, so I need "
                        "your meaning."
                        if fragment.status == "ambiguous"
                        else "This wording is not linked to a verified capability yet, "
                        "so I will not guess."
                    ),
                    options=options,
                )
            )
        )
    return clarifications


def _clarification_answered_by_prompt(
    clarification: SetupChatClarification,
    prompt: str,
) -> bool:
    lowered = prompt.casefold()
    question_context = f"{clarification.key} {clarification.question}".casefold()
    if "timeframe" in question_context or "time frame" in question_context:
        return re.search(r"\b(?:1|3|5|15|30)m\b|\b(?:1|2|4|6|8|12)h\b|\b1d\b", lowered) is not None
    if any(term in question_context for term in ("universe", "watchlist", "symbols", "pairs")):
        return bool(
            re.search(r"\b(?:binance|bybit|usdt|usdc|all pairs|all symbols)\b", lowered)
            or re.search(r"\b[A-Z0-9]{2,12}[/\-](?:USDT|USDC)\b", prompt.upper())
        )
    if "direction" in question_context:
        return re.search(r"\b(?:bullish|bearish|long|short|both)\b", lowered) is not None
    if "which side" in question_context or "previous period" in question_context:
        return re.search(r"\b(?:high|low|bullish|bearish)\b", lowered) is not None
    if any(term in question_context for term in ("threshold", "how strong", "how close")):
        return re.search(r"\b\d+(?:\.\d+)?\s*(?:%|x|r)?\b", lowered) is not None
    for option in clarification.options:
        for value in (option.label, option.value):
            normalized = " ".join(value.casefold().split())
            if len(normalized) >= 3 and normalized in lowered:
                return True
    return False


def _with_other_option(clarification: SetupChatClarification) -> SetupChatClarification:
    if any(option.value == "__other__" for option in clarification.options):
        return clarification
    return clarification.model_copy(
        update={
            "options": [
                *clarification.options,
                SetupChatOption(
                    key=clarification.key,
                    label="Other (type in chat)",
                    value="__other__",
                    description="Answer this question in your own words.",
                    action="other",
                ),
            ]
        }
    )


def _extension_source_fragment(context: dict[str, Any], option_key: str) -> str | None:
    report = dict(context.get("capability_resolution") or {})
    for fragment in report.get("fragments") or []:
        source = str(fragment.get("fragment") or "")
        slug = re.sub(r"[^a-z0-9]+", "_", source.casefold()).strip("_")[:48]
        if option_key == f"capability_meaning_{slug or 'unknown'}":
            return source
    return None


def _pending_clarification_model(
    pending: dict[str, Any],
) -> SetupChatClarification | None:
    payload = pending.get("clarification")
    if not isinstance(payload, dict):
        return None
    try:
        return SetupChatClarification.model_validate(payload)
    except ValidationError:
        return None


def _selected_clarification_option(
    clarification: SetupChatClarification | None,
    option_value: str | None,
) -> SetupChatOption | None:
    if clarification is None or option_value is None:
        return None
    return next(
        (option for option in clarification.options if option.value == option_value),
        None,
    )


def _is_clarification_help_turn(
    *,
    cleaned: str,
    option_value: str | None,
    option_label: str | None,
    selected_option: SetupChatOption | None,
) -> bool:
    """Recognize a request to understand choices without treating it as rule input."""
    if selected_option is not None and selected_option.action == "explain":
        return True
    if option_value == "__explain_options__":
        return True
    text = " ".join(value for value in (cleaned, option_label, option_value) if value)
    return _is_explanation_request(text)


def _is_explanation_request(value: str) -> bool:
    normalized = " ".join(value.casefold().replace("’", "'").split())
    if not normalized:
        return False
    uncertainty = re.search(
        r"\b(?:i\s+(?:do\s+not|don't)\s+know|i(?:'m|\s+am)\s+not\s+sure|"
        r"not\s+sure|unsure|no\s+idea|help\s+me\s+choose)\b",
        normalized,
    )
    explanatory_request = re.search(
        r"^(?:(?:please|could\s+you|can\s+you|would\s+you)\s+)?"
        r"(?:explain|compare|clarify|describe|show\s+examples?|walk\s+me\s+through)\b",
        normalized,
    ) or re.search(
        r"\b(?:what(?:'s|\s+is|\s+are)\s+the\s+difference|"
        r"what\s+do\s+the(?:se)?\s+(?:choices|options|candidates)\s+mean)\b",
        normalized,
    )
    return bool(uncertainty or explanatory_request)


def _clarification_help(
    clarification: SetupChatClarification,
) -> tuple[str, list[dict[str, str]]]:
    answer_options = [
        option
        for option in clarification.options
        if option.action == "answer"
        and option.value not in {"__other__", "__build_mechanic__", "__explain_options__"}
    ]
    items = [
        {
            "label": option.label,
            "description": option.description or option.value,
            "value": option.value,
        }
        for option in answer_options
    ]
    if not items:
        return (
            "This is the technical detail I still need: "
            f"{clarification.question} Tell me the measurable rule you want to use.",
            [],
        )
    lines = ["Here is the practical difference:"]
    lines.extend(
        f"{index}. {item['label']}: {item['description']}"
        for index, item in enumerate(items, start=1)
    )
    lines.append("Choose the closest one, or type your own definition.")
    return "\n".join(lines), items


def _answer_satisfies_clarification(
    clarification: SetupChatClarification,
    answer: str,
) -> bool:
    lowered = answer.casefold().strip()
    key = clarification.key.casefold()
    if _is_explanation_request(answer):
        return False
    normalized = " ".join(lowered.split())
    if any(
        normalized
        in {
            " ".join(option.label.casefold().split()),
            " ".join(option.value.casefold().split()),
        }
        for option in clarification.options
        if option.action == "answer"
    ):
        return True
    if key == "reference_sweep_side":
        return re.search(r"\b(?:high|low|bullish|bearish)\b", lowered) is not None
    if "timeframe" in key:
        return re.search(r"\b(?:1|3|5|15|30)m\b|\b(?:1|2|4|6|8|12)h\b|\b1d\b", lowered) is not None
    if any(
        term in key for term in ("threshold", "distance", "volume", "persistence", "candle_count")
    ):
        return _contains_quantity(lowered) or "above average" in lowered
    if any(term in key for term in ("direction", "side")):
        return re.search(r"\b(?:bullish|bearish|long|short|both|high|low)\b", lowered) is not None
    if "exchange" in key:
        return re.search(r"\b(?:binance|bybit)\b", lowered) is not None
    if any(term in key for term in ("universe", "watchlist", "symbol", "pair")):
        return (
            re.search(r"\b(?:usdt|usdc|binance|bybit|all|symbols?|pairs?)\b", lowered) is not None
        )
    if key == "compile_confirmation":
        return re.fullmatch(r"(?:yes|no|ready|compile|not yet)[.! ]*", lowered) is not None
    return len(re.findall(r"[a-z0-9]+", lowered)) >= 2


def _contains_quantity(value: str) -> bool:
    if re.search(r"\d", value):
        return True
    return (
        re.search(
            r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|fifty|"
            r"hundred)\b",
            value,
        )
        is not None
    )


def _canonical_clarification_answer(
    clarification: SetupChatClarification | None,
    answer: str,
    *,
    setup_context: str = "",
) -> str:
    if clarification is None:
        return answer
    lowered = answer.casefold()
    if clarification.key == "reference_sweep_side":
        side = "high" if re.search(r"\b(?:high|bearish)\b", lowered) else "low"
        period = _reference_period_word(setup_context)
        return f"Sweep the previous {period or 'period'} {side}"
    if clarification.key.startswith("capability_meaning_"):
        return answer
    return f"Clarification answer for {clarification.key}: {answer}"


def _reference_period_word(value: str) -> str | None:
    match = re.search(
        r"\b(?:previous|prior|last)\s+"
        r"(day|daily|week|weekly|month|monthly)(?:\s+candle)?\b",
        value.casefold(),
    )
    if match is None:
        return None
    return {
        "day": "daily",
        "daily": "daily",
        "week": "weekly",
        "weekly": "weekly",
        "month": "monthly",
        "monthly": "monthly",
    }[match.group(1)]


def _selected_capability_key(option_value: str) -> str | None:
    match = re.search(r"\(([a-z0-9_]+)\)\s*$", option_value.casefold())
    return match.group(1) if match else None


def _friendly_key(value: str) -> str:
    return value.replace("_", " ").strip().capitalize()


def _chat_title(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:157] + "..." if len(cleaned) > 160 else cleaned


def _valid_monitor_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &/_-]{2,79}", value))


def _monitor_name_suggestions(definition: StrategyDefinition) -> list[str]:
    rules = _condition_rules(definition.conditions)
    primary = rules[0].label if rules else "Market Setup"
    words = re.findall(r"[A-Za-z0-9]+", primary)
    concise_rule = " ".join(words[:3]).title() or "Market Setup"
    symbol = (
        definition.universe.include_symbols[0].split("/", 1)[0]
        if definition.universe.include_symbols
        else "Altcoin"
    )
    candidates = [
        f"{symbol} {concise_rule}",
        f"{definition.base_timeframe} {concise_rule} Monitor",
        f"{concise_rule} Watch",
    ]
    unique: list[str] = []
    for candidate in candidates:
        normalized = " ".join(candidate.split())[:80].strip(" -_/&")
        if len(normalized) >= 3 and normalized.casefold() not in {
            item.casefold() for item in unique
        }:
            unique.append(normalized)
    return unique[:3]


def _deduplicated_name_suggestions(name: str, suggestions: list[str]) -> list[str]:
    candidates = [f"{name} 2", *suggestions, f"{name} Revised"]
    unique: list[str] = []
    for candidate in candidates:
        normalized = " ".join(candidate.split())[:80]
        if (
            _valid_monitor_name(normalized)
            and normalized.casefold() != name.casefold()
            and normalized.casefold() not in {item.casefold() for item in unique}
        ):
            unique.append(normalized)
    return unique[:3]


def _extract_responses_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise ValueError("OpenAI response did not contain output text")


def _turn_router_prompt() -> str:
    return (
        "You are the context-aware turn router for HilalMarkets AI Setup Chat. Classify only the "
        "user's current message after reading the curated conversation, accumulated setup, and "
        "active clarification. Users may greet you, think aloud, correct themselves, ask whether "
        "HilalMarkets supports a concept, or ask about an option before answering. Those turns "
        "must "
        "not mutate the strategy. A question such as 'Do you have FVG in the system?' is a "
        "product_question, not a technical instruction. A question about wording or choices you "
        "offered is option_question; answer it briefly and keep the active question open. If the "
        "current message directly answers the active question, use clarification_answer. Values "
        "like 'All supported spot pairs' are universe answers, never indicators or mechanics. "
        "Understand ordinary human glue language such as I want, bring me, then, about, yes, no, "
        "one, and two without asking the user to define it. Do not classify assistant-authored "
        "words, option labels, internal keys, or old messages as new user mechanics. Never quote "
        "an old message unless the current user explicitly asks for a quote. "
        "Treat halal coins, Shariah-screened coins, and screened assets as the platform's current "
        "screened universe, not as a market mechanic or a request for a religious definition. "
        "A methodology choice may be requested only when the server has not already selected one. "
        "For a real setup instruction or revision, put only exact verbatim user-authored spans "
        "from current_message in technical_fragments. Never paraphrase, invent, or copy a fragment "
        "from history. Use mixed only when the current message genuinely contains both a human or "
        "product question and a separate technical instruction. Unsupported trading concepts are "
        "still technical instructions; the deterministic registry handles them after routing. "
        "Use capability_context only to answer product capability questions and never claim a "
        "capability outside those candidates. For conversation, product_question, option_question, "
        "unsafe, and out_of_scope, technical_fragments must be empty and preserve_pending_question "
        "must be true. For a pure setup instruction or clarification answer, assistant_message "
        "should be empty. For a conversational/question/mixed turn, answer concisely, naturally, "
        "and without financial advice. Return only schema-valid JSON."
    )


def _turn_classification_schema() -> dict[str, Any]:
    segment = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "category": {
                "type": "string",
                "enum": [
                    "human_conversation",
                    "product_question",
                    "option_question",
                    "technical_instruction",
                    "clarification_answer",
                    "market_snapshot",
                    "unsafe",
                    "out_of_scope",
                ],
            },
        },
        "required": ["text", "category"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "conversation",
                    "product_question",
                    "option_question",
                    "clarification_answer",
                    "setup_instruction",
                    "setup_revision",
                    "mixed",
                    "market_snapshot",
                    "unsafe",
                    "out_of_scope",
                ],
            },
            "assistant_message": {"type": "string"},
            "technical_fragments": {"type": "array", "items": {"type": "string"}},
            "clarification_answer": {"type": ["string", "null"]},
            "segments": {"type": "array", "items": segment},
            "preserve_pending_question": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "intent",
            "assistant_message",
            "technical_fragments",
            "clarification_answer",
            "segments",
            "preserve_pending_question",
            "confidence",
        ],
    }


def _system_prompt() -> str:
    return (
        "You are HilalMarkets AI Setup Chat, a beginner-safe interviewer for crypto spot market "
        "monitoring. Stay inside setup clarification, deterministic monitoring-rule design, "
        "market-monitor explanations, and HilalMarkets product help. Never give financial advice, "
        "trade signals, buy/sell instructions, profit promises, automatic execution guidance, "
        "or request exchange keys. Be friendly, humble, concise, and use simple trader language. "
        "Do not hide assumptions. Keep replies short and direct. Ask necessary measurable "
        "questions "
        "one at a time; the server numbers multi-question interviews. Options must be concrete and "
        "clickable. In Monitor mode, identify one primary trigger and preserve every user-stated "
        "required filter and confirmation. In Scanner mode, do not require or invent a primary "
        "trigger: treat every required rule as a condition coins must match in the current scan. "
        "Use the current market snapshot in Scanner unless the user explicitly requires a closed "
        "candle. Mark only explicitly optional ideas as suggestions. "
        "Use capability_context as the registry authority. Ask only about an unresolved technical "
        "market mechanic, parameter, data dependency, timeframe, or logic relationship. Never ask "
        "the user to define ordinary conversational words, corrections, pronouns, numbers, or "
        "wording that you supplied in your own question or options. Use the full conversation "
        "to understand follow-up answers. Do not quote old messages or re-ask a resolved subject. "
        "A `Clarification answer for ...` record is an authoritative answer to a question you "
        "already asked. Treat its label and value in that question's context; never turn a "
        "numeric value, option label, or `none` answer into a new mechanic. "
        "Treat halal coins, Shariah-screened coins, and screened assets as the platform's current "
        "screened universe. Never ask what halal means. Ask which methodology only if the server "
        "has not already supplied one and the choice materially changes the universe. "
        "The current turn has already been classified, so never reinterpret human conversation "
        "or product questions as strategy mechanics. If you offer an option that asks to explain, "
        "compare, "
        "or show "
        "examples, set its action to explain; it is a help request and never a strategy rule. Set "
        "action to answer only for a concrete answer to the current technical question. If a "
        "technical concept has no confident registered meaning, ask for its measurable meaning or "
        "offer the resolver's candidates; never guess. Ask no more than two short, necessary "
        "questions about the same condition. Do not ask for a timeframe, universe, "
        "direction, threshold, or definition that is already stated clearly in the accumulated "
        "setup. Infer ordinary logical consequences and established platform defaults, disclose "
        "them as assumptions, and do not ask the user to confirm details that do not change the "
        "deterministic calculation. "
        "Mark ready_to_compile "
        "only when the accumulated setup names at least one deterministic condition plus usable "
        "timeframe and universe defaults or choices. Suggest improvements without silently adding "
        "them. For unrelated requests, refuse politely and redirect to a crypto spot monitor. For "
        "greetings, respond naturally and invite a setup. Market snapshots are handled by the "
        "server, so return intent market_snapshot and never invent prices or market conditions. "
        "Return only schema-valid JSON."
    )


def _interview_schema() -> dict[str, Any]:
    option = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string"},
            "label": {"type": "string"},
            "value": {"type": "string"},
            "description": {"type": ["string", "null"]},
            "action": {
                "type": "string",
                "enum": ["answer", "explain", "other", "build_mechanic"],
            },
        },
        "required": ["key", "label", "value", "description", "action"],
    }
    clarification = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string"},
            "question": {"type": "string"},
            "reason": {"type": "string"},
            "options": {"type": "array", "items": option},
        },
        "required": ["key", "question", "reason", "options"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["greeting", "setup", "market_snapshot", "out_of_scope", "unsafe"],
            },
            "assistant_message": {"type": "string"},
            "ready_to_compile": {"type": "boolean"},
            "setup_summary": {"type": ["string", "null"]},
            "clarifications": {"type": "array", "items": clarification},
            "suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "intent",
            "assistant_message",
            "ready_to_compile",
            "setup_summary",
            "clarifications",
            "suggestions",
        ],
    }
