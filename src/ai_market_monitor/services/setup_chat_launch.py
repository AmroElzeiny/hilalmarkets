from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_market_monitor.core.config import Settings
from ai_market_monitor.db.models import AISetupChatMessage, AISetupChatSession
from ai_market_monitor.db.models.enums import (
    ComplianceChangeBehavior,
    ShariaAssetStatus,
    ShariaUniverseMode,
)
from ai_market_monitor.engine.setup_intent import decide_setup_intent
from ai_market_monitor.engine.setup_turn_execution import ProviderGate, ScreeningGate
from ai_market_monitor.engine.strategy_compiler_v2 import (
    StrategyV2CompileError,
    compile_strategy_draft_v2,
)
from ai_market_monitor.engine.strategy_draft_migration import migrate_legacy_draft
from ai_market_monitor.engine.strategy_draft_v2 import (
    DraftPatchError,
    apply_strategy_patch,
    validate_draft_semantics,
)
from ai_market_monitor.schemas.setup_agent import (
    DIALOGUE_WINDOW_MAX,
    SetupConversationContext,
    SetupTurnExecutionResult,
)
from ai_market_monitor.schemas.strategy import ShariaPolicyDefinition, StrategyDefinition
from ai_market_monitor.schemas.strategy_draft_v2 import (
    DraftMode,
    ProviderRequirementV2,
    StrategyDraftV2,
    UnresolvedFieldV2,
)
from ai_market_monitor.services.setup_chat_agent import (
    SetupAgentError,
    SetupAgentTurnInput,
    SetupChatAgent,
)
from ai_market_monitor.services.sharia_universe import (
    ShariaUniverseError,
    ShariaUniverseResolver,
)
from ai_market_monitor.services.strategy_patch_extractor import (
    LaunchStrategyPatchExtractor,
    StrategyPatchExtractionError,
    StrategyPatchExtractor,
    StrategyPatchNonMutation,
)
from ai_market_monitor.services.system_brain import CapabilityCoverageService

#: How many questions one draft may ask before it must show the remaining fields
#: instead. Asking is capped; *exposing* what is still missing is not.
MAX_CLARIFICATIONS_PER_DRAFT = 3

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


class SetupChatLaunchService:
    """The only writable Setup Chat strategy path outside test compatibility mode."""

    def __init__(
        self,
        settings: Settings,
        owner: Any,
        *,
        extractor: StrategyPatchExtractor | None = None,
        agent: SetupChatAgent | None = None,
    ) -> None:
        self.settings = settings
        self.owner = owner
        #: Free text goes to the agent. The extractor now serves only explicit
        #: server-offered field answers, which need no model call at all.
        self.agent = agent or SetupChatAgent(settings)
        self.extractor = extractor or LaunchStrategyPatchExtractor(settings)

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
        if client_message_id:
            replay = await self._replayed_turn(session, chat, client_message_id)
            if replay is not None:
                _set_runtime(chat, started, model_calls=0, cache_hits=1)
                return replay

        # The raw text, exactly as typed. Collapsing whitespace here destroyed the line
        # breaks and list structure that tell three numbered rules apart from one
        # sentence, and made the stored provenance disagree with what the user wrote.
        raw = message or option_label or option_value or ""
        cleaned = " ".join(raw.split())
        if option_key == "setup_mode":
            return await self._select_mode(
                session,
                chat,
                value=option_value or cleaned,
                label=option_label,
                client_message_id=client_message_id,
                started=started,
            )
        if option_key in {
            "screened_universe_mode",
            "screened_watchlist",
            "screened_explicit_assets",
        }:
            await self.owner._append_message(
                session,
                chat,
                role="user",
                message_type="option",
                content=option_label or option_value or cleaned,
                payload={
                    "option_key": option_key,
                    "option_value": option_value,
                    "launch_pipeline": "strategy_draft_v2",
                },
                client_message_id=client_message_id,
            )
            await self.owner._apply_screened_universe_answer(
                session,
                chat,
                key=option_key,
                value=option_value or cleaned,
            )
            _set_runtime(chat, started, model_calls=0, cache_hits=0)
            return chat
        if option_key == "monitor_name":
            cleaned = option_value or cleaned

        # The old deterministic gate is kept only as a hint recorded on the turn. It no
        # longer decides whether a message reaches the agent, and it no longer picks a
        # reply: a sentence its regular expressions did not recognise used to be
        # answered with "describe the market behavior you want", even when the user had
        # just written three lines of exact market logic.
        lexical_hint = decide_setup_intent(cleaned)
        offered_option = bool(option_key and option_value and option_key not in {"monitor_name"})
        if offered_option:
            # An explicit UI answer resolves one typed field. It stays deterministic and
            # costs no model call; it can still never grant approval.
            cleaned = f"{option_key}: {option_value}"
            raw = cleaned

        user_message = await self.owner._append_message(
            session,
            chat,
            role="user",
            message_type="option" if option_key else "text",
            # Stored as typed, so provenance quotes what the user can see they wrote.
            content=raw if not offered_option else cleaned,
            payload={
                "lexical_hint": lexical_hint.intent.value,
                "lexical_hint_confidence": lexical_hint.confidence,
                "lexical_hint_reason": lexical_hint.reason,
                "launch_pipeline": "setup_agent_v3",
                "option_key": option_key,
                "option_value": option_value,
            },
            client_message_id=client_message_id,
        )

        if not offered_option:
            return await self._run_agent_turn(
                session,
                chat,
                message=raw,
                source_turn_id=str(user_message.id),
                started=started,
                client_message_id=client_message_id,
            )

        draft = load_strategy_draft_v2(chat)
        context = dict(chat.context_json or {})
        input_fingerprint = hashlib.sha256(cleaned.casefold().encode()).hexdigest()
        if (
            context.get("last_v2_patch_input_hash") == input_fingerprint
            and context.get("last_v2_patch_result_hash") == draft.semantic_hash
        ):
            await self.owner._assistant(
                session,
                chat,
                "That exact update is already reflected in the current AI Sheet.",
                message_type="patch_already_applied",
                payload={
                    "strategy_mutated": False,
                    "draft_id": str(draft.draft_id),
                    "draft_version": draft.version,
                    "semantic_hash": draft.semantic_hash,
                    "cache_hit": True,
                },
            )
            _set_runtime(chat, started, model_calls=0, cache_hits=1)
            return chat
        try:
            patch = await self.extractor.extract(
                current_draft=draft,
                message=cleaned,
                source_turn_id=str(user_message.id),
            )
            history = list(context.get("strategy_draft_v2_history") or [])
            result = apply_strategy_patch(draft, patch, history=history)
            await CapabilityCoverageService(self.settings).record_usage(
                session,
                chat=chat,
                operation="strategy_patch_v2",
                usage=getattr(self.extractor, "last_usage", None),
            )
        except StrategyPatchNonMutation as exc:
            # A typed field answer that changed nothing. The answer text the extractor
            # produced is used instead of being discarded, which is what left a user
            # staring at a generic sentence after answering a question correctly.
            await CapabilityCoverageService(self.settings).record_usage(
                session,
                chat=chat,
                operation="strategy_patch_v2",
                usage=getattr(self.extractor, "last_usage", None),
            )
            await self.owner._assistant(
                session,
                chat,
                exc.answer or _no_change_summary(draft),
                message_type="option_no_change",
                payload={
                    "strategy_mutated": False,
                    "draft_id": str(draft.draft_id),
                    "draft_version": draft.version,
                    "semantic_hash": draft.semantic_hash,
                },
            )
            _set_runtime(
                chat,
                started,
                model_calls=int(getattr(self.extractor, "model_call_count", 0)),
                cache_hits=0,
            )
            return chat
        except StrategyPatchExtractionError as exc:
            raise SetupLaunchError(
                exc.code,
                str(exc),
                stage="extract",
                retryable=exc.retryable,
                status_code=503 if exc.retryable else 422,
            ) from exc
        except DraftPatchError as exc:
            raise SetupLaunchError(
                "STRATEGY_PATCH_REJECTED",
                str(exc),
                stage="patch",
                status_code=422,
            ) from exc

        if result.material_change:
            history.append(draft.model_dump(mode="json"))
            context["strategy_draft_v2_history"] = history[-100:]
            if chat.status == "approved":
                _archive_approval(chat, context, cleaned)
        context["strategy_draft_v2"] = result.draft.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        context["launch_pipeline_version"] = "2.0"
        context["last_semantic_diff"] = list(result.changed_fields)
        context["last_intent"] = f"offered_option:{option_key}"
        context["last_patch_source_turn_id"] = str(user_message.id)
        context["last_v2_patch_input_hash"] = input_fingerprint
        context["last_v2_patch_result_hash"] = result.draft.semantic_hash
        chat.context_json = context
        if not chat.original_idea:
            chat.original_idea = cleaned
            chat.title = _title(cleaned)

        await self._render_current_draft(session, chat, result.draft)
        model_calls = int(getattr(self.extractor, "model_call_count", 0))
        _set_runtime(chat, started, model_calls=model_calls, cache_hits=0)
        return chat

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
        record = _turn_record(chat, client_message_id)
        if record is None:
            existing = await session.scalar(
                select(AISetupChatMessage.id).where(
                    AISetupChatMessage.session_id == chat.id,
                    AISetupChatMessage.client_message_id == client_message_id,
                )
            )
            # A message row with no record predates this bookkeeping. Treat it as done.
            return chat if existing is not None else None
        status = str(record.get("status") or "")
        if status == TurnStatus.COMPLETED:
            return chat
        if status in {TurnStatus.PLANNING, TurnStatus.EXECUTING}:
            raise SetupLaunchError(
                "TURN_IN_PROGRESS",
                "That message is still being processed. Try again in a moment.",
                stage="interpret",
                retryable=True,
                status_code=409,
            )
        if status == TurnStatus.PERMANENT_FAILURE:
            raise SetupLaunchError(
                str(record.get("code") or "TURN_FAILED"),
                str(record.get("message") or "That message could not be processed."),
                stage="interpret",
                status_code=422,
            )
        # RECEIVED or RETRYABLE_FAILURE: nothing was applied, so reprocessing is safe.
        return None

    async def _run_agent_turn(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        message: str,
        source_turn_id: str,
        started: float,
        client_message_id: str | None = None,
    ) -> AISetupChatSession:
        """One free-text turn: plan, execute once, then answer from what happened."""

        draft = load_strategy_draft_v2(chat)
        context = dict(chat.context_json or {})
        _record_turn(context, client_message_id, status=TurnStatus.PLANNING)
        chat.context_json = context
        conversation = _load_conversation_context(context)
        history = list(context.get("strategy_draft_v2_history") or [])
        turn = SetupAgentTurnInput(
            message=message,
            source_turn_id=source_turn_id,
            draft=draft,
            dialogue=tuple(
                await self._recent_dialogue(session, chat, exclude_message_id=source_turn_id)
            ),
            conversation=conversation,
            history=tuple(history),
            setup_mode=draft.mode,
            previous_turn_failed=bool(context.get("last_turn_failed")),
            # Screening and provider availability are gates, so they run inside
            # execution and their outcome reaches the composer. Running them after the
            # reply let a message announce a draft the platform then blocked.
            screening=self._screening_gate(session, chat),
            providers=self._provider_gate(),
        )
        try:
            outcome = await self.agent.run_turn(turn)
        except SetupAgentError as exc:
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
            _record_turn(
                context,
                client_message_id,
                status=(
                    TurnStatus.RETRYABLE_FAILURE
                    if exc.retryable
                    else TurnStatus.PERMANENT_FAILURE
                ),
                code=exc.code,
                message=str(exc),
            )
            chat.context_json = context
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

        # Only a material change archives an approval and clears the approved strategy
        # ids. A turn that answered a question or asked one leaves all of that alone.
        if outcome.material_change and outcome.history_snapshot is not None:
            history.append(outcome.history_snapshot)
            context["strategy_draft_v2_history"] = history[-100:]
            if chat.status == "approved":
                _archive_approval(chat, context, message)

        context["strategy_draft_v2"] = outcome.draft.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        context["launch_pipeline_version"] = "3.0"
        context["setup_conversation_context"] = outcome.conversation.model_dump(mode="json")
        context["last_turn_trace"] = outcome.trace.to_dict()
        context["last_turn_failed"] = False
        context.pop("last_turn_failure", None)
        if outcome.execution is not None:
            context["last_semantic_diff"] = list(outcome.execution.semantic_diff)
            context["last_execution_result"] = outcome.execution.model_dump(mode="json")
        context["last_patch_source_turn_id"] = source_turn_id
        # Marked complete in the same context write that stores the new draft, so a
        # crash cannot leave a turn applied but recorded as unfinished — or the reverse.
        _record_turn(
            context,
            client_message_id,
            status=TurnStatus.COMPLETED,
            message=outcome.reply.message,
        )
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
        state = await self._persist_draft_state(
            session,
            chat,
            outcome.draft,
            definition=outcome.definition,
            execution=outcome.execution,
        )
        await self.owner._assistant(
            session,
            chat,
            outcome.reply.message,
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
        _set_runtime(chat, started, model_calls=outcome.trace.model_calls, cache_hits=0)
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
        ) -> tuple[StrategyDefinition | None, str | None]:
            try:
                return await self._apply_screening_policy(session, chat, definition), None
            except (KeyError, ValueError, ShariaUniverseError) as exc:
                return None, str(exc) or "Choose and validate a Halal Market first."

        return gate

    def _provider_gate(self) -> ProviderGate:
        """Mark each provider requirement available or not, from configuration.

        A requirement the platform has no adapter for stays unavailable, which blocks
        approval transparently instead of compiling a rule that cannot be evaluated.
        """

        async def gate(
            requirements: list[ProviderRequirementV2],
        ) -> list[ProviderRequirementV2]:
            return [
                item.model_copy(
                    update={"available": self._provider_available(item.provider)}
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
        return name in {"", "ohlcv", "market_data", "candles", "ccxt"}

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

    async def _select_mode(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        *,
        value: str,
        label: str | None,
        client_message_id: str | None,
        started: float,
    ) -> AISetupChatSession:
        mode_text = value.casefold().strip()
        if mode_text not in {"scanner", "monitor"}:
            raise SetupLaunchError(
                "INVALID_SETUP_MODE",
                "Choose Scanner or Monitor.",
                stage="intent",
                status_code=422,
            )
        await self.owner._append_message(
            session,
            chat,
            role="user",
            message_type="option",
            content=label or mode_text.title(),
            payload={
                "option_key": "setup_mode",
                "option_value": mode_text,
                "launch_pipeline": "strategy_draft_v2",
            },
            client_message_id=client_message_id,
        )
        context = dict(chat.context_json or {})
        current = load_strategy_draft_v2(chat)
        selected = DraftMode(mode_text)
        if current.mode != selected:
            current = StrategyDraftV2.model_validate(
                current.model_copy(
                    update={
                        "mode": selected,
                        "name": (
                            "Untitled Scanner"
                            if selected == DraftMode.SCANNER
                            else "Untitled Monitor"
                        ),
                        "version": current.version + 1,
                        "approval": {"approved": False},
                        "semantic_hash": "",
                    }
                ).model_dump(mode="json")
            )
        context["setup_mode"] = mode_text
        context["strategy_draft_v2"] = current.model_dump(mode="json")
        context["strategy_state_authority"] = "v2"
        chat.context_json = context
        chat.status = "interviewing"
        chat.draft_schema_json = None
        if self.settings.sharia_screening_enforced:
            await self.owner._ask_screened_universe(session, chat)
        else:
            await self.owner._assistant(
                session,
                chat,
                (
                    "Scanner is ready. Describe the exact conditions assets should match now."
                    if selected == DraftMode.SCANNER
                    else "Monitor is ready. Describe the exact conditions to follow continuously."
                ),
                message_type="mode_selected",
                payload={"setup_mode": mode_text, "launch_pipeline": "strategy_draft_v2"},
            )
        _set_runtime(chat, started, model_calls=0, cache_hits=0)
        return chat

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
        violations = list(execution.semantic_violations) if execution else (
            validate_draft_semantics(draft)
        )
        compile_error: str | None = None
        screening_error: str | None = None
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
                    definition = await self._apply_screening_policy(session, chat, definition)
                except (KeyError, ValueError, ShariaUniverseError) as exc:
                    screening_error = str(exc) or "Choose and validate a Halal Market."
                    definition = None
                    blocking = True

        chat.draft_schema_json = (
            definition.model_dump(mode="json") if definition is not None else None
        )
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
                else "ready_to_scan"
                if draft.mode == DraftMode.SCANNER
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

    async def _render_current_draft(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        draft: StrategyDraftV2,
    ) -> None:
        """Templated feedback for an explicit server-offered UI answer.

        Free-text turns never reach this. They are answered by the agent from the
        execution result, which is why the generic readiness sentence is gone.
        """
        state = await self._persist_draft_state(session, chat, draft)
        blocking = state.blocking
        violations = list(state.violations)
        unresolved = list(state.unresolved)
        if blocking:
            context = dict(chat.context_json or {})
            asked = list(context.get("v2_clarification_keys_asked") or [])
            next_item = next(
                (item for item in unresolved if item.key not in asked),
                None,
            )
            if next_item is not None and len(asked) < MAX_CLARIFICATIONS_PER_DRAFT:
                content = next_item.question
                asked.append(next_item.key)
                context["v2_clarification_keys_asked"] = asked[-MAX_CLARIFICATIONS_PER_DRAFT:]
                chat.context_json = context
                message_type = "clarification"
            else:
                content = (
                    "The remaining exact fields are listed in What needs attention. "
                    "Update one of those fields to continue."
                    if unresolved
                    else (
                        "This draft is blocked because an exact requested mechanic is "
                        "not available. Review the item in What needs attention."
                    )
                )
                message_type = "draft_blocked"
        else:
            content = (
                "The inactive Scanner preview is ready to run."
                if draft.mode == DraftMode.SCANNER
                else (
                    "The inactive Watchlist preview is ready. Review the AI Sheet, "
                    "then use Review and approve when it matches your intent."
                )
            )
            message_type = "draft_ready"
        await self.owner._assistant(
            session,
            chat,
            content,
            message_type=message_type,
            payload={
                "draft_v2": draft.model_dump(mode="json"),
                "semantic_violations": violations,
                "clarifications": [
                    {
                        "key": item.key,
                        "question": item.question,
                        "reason": "This exact field is required before compilation.",
                        "options": [],
                    }
                    for item in unresolved
                ],
                "can_approve": chat.status == "ready_for_approval",
                "model_call_count": int(getattr(self.extractor, "model_call_count", 0)),
            },
        )

    async def _apply_screening_policy(
        self,
        session: AsyncSession,
        chat: AISetupChatSession,
        definition: StrategyDefinition,
    ) -> StrategyDefinition:
        context = chat.context_json or {}
        mode = ShariaUniverseMode(str(context["screened_universe_mode"]))
        methodology_id = context.get("sharia_methodology_id")
        if not methodology_id:
            raise ValueError("Choose a screening methodology before compiling.")
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
            approved_watchlist_id=context.get("approved_watchlist_id"),
        )
        universe_update: dict[str, Any] = {"sharia_policy": policy}
        if mode == ShariaUniverseMode.EXPLICIT_ASSETS:
            universe_update["include_symbols"] = list(
                context.get("screened_explicit_symbols") or []
            )
        secured = definition.model_copy(
            update={
                "universe": definition.universe.model_copy(update=universe_update)
            }
        )
        resolution = await ShariaUniverseResolver(
            session,
            self.owner.market_provider,
            self.settings,
        ).resolve(
            secured,
            user_id=chat.user_id,
            persist_snapshot=False,
        )
        if not resolution.included_symbols:
            raise ValueError(
                "No asset currently meets both the screening policy and market scope."
            )
        return secured


def load_strategy_draft_v2(chat: AISetupChatSession) -> StrategyDraftV2:
    context = chat.context_json or {}
    payload = context.get("strategy_draft_v2")
    if isinstance(payload, dict):
        return StrategyDraftV2.model_validate(payload)
    return migrate_legacy_draft(
        chat.draft_schema_json,
        setup_mode=str(context.get("setup_mode") or "monitor"),
        unsupported=chat.unsupported_conditions or [],
    )


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


#: How many keyed turns one session remembers. Enough to make retries reliable without
#: growing the session document without bound.
_TURN_RECORD_LIMIT = 50


def _turn_record(
    chat: AISetupChatSession,
    client_message_id: str,
) -> dict[str, Any] | None:
    records = (chat.context_json or {}).get("turn_records")
    if isinstance(records, dict):
        record = records.get(client_message_id)
        if isinstance(record, dict):
            return record
    return None


def _record_turn(
    context: dict[str, Any],
    client_message_id: str | None,
    *,
    status: TurnStatus,
    code: str | None = None,
    message: str | None = None,
) -> None:
    """Write the turn's state into the same context the draft is written to.

    One document, one write: the state change and its completion status commit together,
    so no crash can leave a patch applied but the turn marked unfinished.
    """
    if not client_message_id:
        return
    records = dict(context.get("turn_records") or {})
    records[client_message_id] = {
        "status": status.value,
        "code": code,
        "message": (message or "")[:2000] or None,
    }
    if len(records) > _TURN_RECORD_LIMIT:
        for key in list(records)[: len(records) - _TURN_RECORD_LIMIT]:
            records.pop(key, None)
    context["turn_records"] = records


def _draft_is_approved(draft: StrategyDraftV2) -> bool:
    """True when the approval binding names this exact version and hash."""

    return (
        draft.approval.approved
        and draft.approval.draft_version == draft.version
        and draft.approval.semantic_hash == draft.semantic_hash
    )


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
    return f"That did not change the draft. It stays at version {draft.version}."


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
                        "source_fragment": node.source_fragment,
                    }
                )
    return {
        "schema_version": "2.0",
        "monitor_name": draft.name,
        "exchange": draft.market_scope.exchange,
        "market_type": draft.market_scope.market_type,
        "quote_currencies": [draft.market_scope.quote_asset],
        "symbols_watchlist": draft.universe.included_symbols,
        "excluded_symbols": draft.universe.excluded_symbols,
        "conditions": conditions,
        "timeframes": list(
            dict.fromkeys(
                item["timeframe"]
                for item in conditions
                if item["timeframe"] != "Not provided"
            )
        ),
        "fields": [
            {"label": "Mode", "value": draft.mode.value.title()},
            {"label": "Name", "value": draft.name},
            {"label": "Exchange", "value": draft.market_scope.exchange.title()},
            {"label": "Quote asset", "value": draft.market_scope.quote_asset},
            {
                "label": "Included assets",
                "value": ", ".join(draft.universe.included_symbols) or "Selected Halal Market",
            },
            {
                "label": "Excluded assets",
                "value": ", ".join(draft.universe.excluded_symbols) or "None",
            },
        ],
        "unresolved_fields": [
            item.model_dump(mode="json") for item in draft.unresolved_fields
        ],
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


def _set_runtime(
    chat: AISetupChatSession,
    started: float,
    *,
    model_calls: int,
    cache_hits: int,
) -> None:
    context = dict(chat.context_json or {})
    context["turn_runtime"] = {
        "attach": True,
        "cache_hits": cache_hits,
        "model_call_count": model_calls,
        "stages": [
            {
                "stage": "launch_pipeline_total",
                "duration_ms": round((monotonic() - started) * 1000),
            }
        ],
    }
    chat.context_json = context
