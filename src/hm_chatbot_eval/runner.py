from __future__ import annotations

import asyncio
import json
import math
from collections import Counter, defaultdict
from contextlib import suppress
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Settings, process_openai_key_overrides_dotenv
from .evaluate import deterministic_metrics, validate_schema
from .failures import (
    ExecutionState,
    FailureClass,
    FailureRecord,
    Role,
    backoff_delay,
    classify_exception,
    classify_http_status,
    is_retryable,
    parse_retry_after,
    pause_state_for,
    rate_limit_headers,
    sanitize_excerpt,
)
from .models import (
    ApprovalMode,
    CaseResult,
    JudgeVerdict,
    ScenarioContract,
    ScenarioSpec,
    TurnRecord,
)
from .openai_client import MalformedAIResponse, OpenAIResponsesClient
from .profiles import (
    cases_per_topic,
    max_turns_for_topic,
    repeats_for_topic,
    target_kinds_for_topic,
    topics_for_mode,
    variants_for_topic,
)
from .report import write_reports
from .scenarios import build_randomized_scenario_plan, build_scenario
from .targets.backend import GenericHTTPBackendTarget, HilalMarketsBackendTarget
from .targets.base import ChatTarget, TargetReply
from .targets.ui import UITarget
from .test_ai import TestAI
from .topics import TOPIC_BY_ID, TOPICS
from .util import ensure_dir, semantic_contract_hash, stable_hash, utc_now

_EXPECTED_EVALUATOR_FAULT_ERRORS: dict[str, frozenset[str]] = {
    "empty_once": frozenset({"TARGET_EMPTY_RESPONSE"}),
    "invalid_json_once": frozenset({"TARGET_INVALID_JSON"}),
    "partial_json_once": frozenset({"TARGET_INVALID_JSON"}),
    "timeout_once": frozenset({"TARGET_TOTAL_TIMEOUT"}),
    "429_once": frozenset({"TARGET_HTTP_429"}),
    "stream_disconnect_once": frozenset({"TARGET_PARTIAL_STREAM"}),
}

_TARGET_PRODUCT_FAILURE_CODES = frozenset(
    {
        "TARGET_EMPTY_RESPONSE",
        "TARGET_INVALID_JSON",
        "TARGET_SCHEMA_VALIDATION",
        "VALUE_NOT_GROUNDED",
        "SPAN_NOT_GROUNDED",
        "SEMANTIC_VALIDATION_FAILED",
        "PLANNER_REPAIR_DROPPED_ACTION",
        "UNRESOLVED_TARGET_UNCHANGED",
    }
)


def _product_failure_code(reply: TargetReply) -> str | None:
    raw_error = reply.raw.get("error") if isinstance(reply.raw, dict) else None
    code = str(raw_error.get("error_code") or "") if isinstance(raw_error, dict) else ""
    if code in _TARGET_PRODUCT_FAILURE_CODES:
        return code
    if reply.status_code == 422:
        return code or "TARGET_HTTP_422"
    if reply.status_code is not None and reply.status_code < 400 and not reply.text.strip():
        return "TARGET_EMPTY_RESPONSE"
    return None


def _approval_lifecycle_from_structured(structured: dict[str, Any] | None) -> str:
    approval = structured.get("approval") if isinstance(structured, dict) else None
    return str(approval.get("lifecycle_state") or "") if isinstance(approval, dict) else ""


def _needs_stale_approval_probe(scenario: ScenarioSpec, turns: list[TurnRecord]) -> bool:
    """Let approval-rebind cases test stale intent once before the real UI action.

    The first ready draft is approved immediately through the authenticated control.
    After the material edit, the scenario emits one stale-approval-intent turn and
    verifies that the target remains at the gate; only the following step invokes the
    real approval action. Text intent is never treated as approval by this function.
    """

    if (
        ScenarioContract.from_value(scenario.expected_contract).workflow().get("kind")
        != "approval_rebind"
    ):
        return False
    compiled_indexes = [
        index
        for index, turn in enumerate(turns)
        if turn.role == "assistant"
        and _approval_lifecycle_from_structured(turn.structured)
        in {"approved", "compiled", "activated"}
    ]
    if not compiled_indexes:
        return False
    user_turns_after = [
        turn for turn in turns[compiled_indexes[-1] + 1 :] if turn.role == "user"
    ]
    # The only user turn after the first approval is the material edit. The next
    # evaluator turn is therefore the deliberate stale-intent probe.
    return len(user_turns_after) == 1


def _role_for_kind(kind: str) -> Role:
    """The browser harness has its own failure vocabulary; keep the roles distinct."""
    return "ui" if kind == "ui" else "target"


class BudgetExceeded(RuntimeError):
    pass


class CostAccountingError(RuntimeError):
    pass


class EvaluationInfrastructureError(RuntimeError):
    """An infrastructure condition that stops the run.

    Carries the taxonomy class so the summary can name what actually happened
    (rate limit, flex capacity, quota, auth) instead of collapsing every cause into
    one "infrastructure unavailable" string.
    """

    def __init__(self, message: str, *, failure_class: FailureClass) -> None:
        super().__init__(message)
        self.failure_class = failure_class


class EvaluationRunner:
    def __init__(self, settings: Settings, run_id: str, budget_usd: float):
        self.settings = settings
        self.run_id = run_id
        self.run_dir = ensure_dir(settings.eval_output_dir / run_id)
        self.evidence_dir = ensure_dir(self.run_dir / "evidence")
        self.cache = ResponseCache(settings.eval_cache_db)
        self.ai_client = OpenAIResponsesClient(settings, self.cache)
        self.test_ai = TestAI(settings, self.ai_client)
        self.budget = budget_usd
        self.spent = 0.0
        self.readiness_target_cost = 0.0
        #: Observed cost of each completed case, used to project the next one.
        self._case_costs: list[float] = []
        self.schema = settings.load_schema()

    async def _readiness_gate(
        self,
        work: list[tuple[ScenarioSpec, str, dict[str, Any]]],
    ) -> tuple[bool, list[dict[str, Any]], FailureRecord | None]:
        """Verify auth, session creation, and UI boundaries before paid model work."""

        probes: dict[tuple[str, str], tuple[str, dict[str, Any], bool]] = {}
        for scenario, kind, variant in work:
            key = (kind, stable_hash(variant))
            previous = probes.get(key)
            requires_fault_control = scenario.fault is not None
            if previous is None:
                probes[key] = (kind, variant, requires_fault_control)
            elif requires_fault_control and not previous[2]:
                probes[key] = (kind, variant, True)

        records: list[dict[str, Any]] = []
        last_failure: FailureRecord | None = None
        for kind, variant, requires_fault_control in probes.values():
            probe_name = f"{kind}:{variant.get('name', 'current')}"
            passed = False
            for attempt in range(1, self.settings.eval_readiness_attempts + 1):
                target = self.make_target(kind)
                started = asyncio.get_running_loop().time()
                try:
                    await target.start(f"readiness-{self.run_id}", variant)
                    probe_fault = (
                        "empty_once"
                        if requires_fault_control
                        and self.settings.target_backend_adapter == "hilalmarkets"
                        else None
                    )
                    readiness_checks = [
                        "authenticated_session",
                        "ai_first_non_mutating_turn",
                        (
                            "fault_control"
                            if requires_fault_control
                            else "fault_control_not_required"
                        ),
                        "complete_turn",
                    ]
                    reply = await target.send(
                        self._readiness_message(),
                        scenario_id=f"readiness-{self.run_id}",
                        fault=probe_fault,
                    )
                    readiness_cost = self._target_cost(
                        reply.model,
                        dict(reply.usage or {}),
                    )
                    self._charge(readiness_cost)
                    self.readiness_target_cost += readiness_cost
                    if probe_fault and self._expected_evaluator_fault_response(
                        reply,
                        expected_fault=probe_fault,
                    ):
                        records.append(
                            {
                                "target": probe_name,
                                "attempt": attempt,
                                "status": "PASS",
                                "checks": [*readiness_checks, "fault_control_observed"],
                                "elapsed_ms": (
                                    asyncio.get_running_loop().time() - started
                                )
                                * 1000,
                                "target_cost_usd": readiness_cost,
                            }
                        )
                        passed = True
                        break
                    if probe_fault:
                        # A successful ordinary reply is not a successful fault
                        # probe.  The target must prove that the exact one-shot
                        # injected fault reached its LLM boundary, otherwise a
                        # deterministic branch or ignored header would let a broken
                        # resilience test look ready.
                        last_failure = FailureRecord(
                            failure_class=FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE,
                            role=_role_for_kind(kind),
                            stage="readiness",
                            retryable=False,
                            attempt=attempt,
                            elapsed_ms=(
                                asyncio.get_running_loop().time() - started
                            )
                            * 1000,
                            http_status=reply.status_code,
                            error_type="EvaluatorFaultNotObserved",
                            error_message=(
                                "The target did not return the exact evidence-bound "
                                f"response for injected fault {probe_fault!r}."
                            ),
                        )
                        records.append(
                            {
                                "target": probe_name,
                                "attempt": attempt,
                                "status": "FAIL",
                                "checks": readiness_checks,
                                "failure": last_failure.to_dict(),
                                "target_cost_usd": readiness_cost,
                            }
                        )
                        break
                    probe_failure = self._reply_failure(
                        reply,
                        kind=kind,
                        scenario_id=f"readiness-{self.run_id}",
                        turn_id="readiness-turn",
                    )
                    if probe_failure is not None:
                        last_failure = probe_failure
                        records.append(
                            {
                                "target": probe_name,
                                "attempt": attempt,
                                "status": "FAIL",
                                "checks": readiness_checks,
                                "failure": probe_failure.to_dict(),
                                "target_cost_usd": readiness_cost,
                            }
                        )
                        if not probe_failure.retryable:
                            break
                        if attempt < self.settings.eval_readiness_attempts:
                            await asyncio.sleep(
                                backoff_delay(
                                    attempt,
                                    retry_after=probe_failure.retry_after_seconds,
                                    max_seconds=2,
                                )
                            )
                        continue
                    records.append(
                        {
                            "target": probe_name,
                            "attempt": attempt,
                            "status": "PASS",
                            "checks": readiness_checks,
                            "elapsed_ms": (
                                asyncio.get_running_loop().time() - started
                            )
                            * 1000,
                            "target_cost_usd": readiness_cost,
                        }
                    )
                    passed = True
                    break
                except Exception as exc:
                    role = _role_for_kind(kind)
                    elapsed_ms = (
                        asyncio.get_running_loop().time() - started
                    ) * 1000
                    if isinstance(exc, httpx.HTTPStatusError):
                        body = sanitize_excerpt(exc.response.text) or ""
                        failure_class = classify_http_status(
                            exc.response.status_code,
                            role=role,
                            body=body,
                        )
                        headers = rate_limit_headers(exc.response.headers)
                        last_failure = FailureRecord(
                            failure_class=failure_class,
                            role=role,
                            stage="readiness",
                            retryable=is_retryable(failure_class),
                            attempt=attempt,
                            elapsed_ms=elapsed_ms,
                            http_status=exc.response.status_code,
                            error_type=type(exc).__name__,
                            request_id=headers.get("x-request-id"),
                            retry_after_seconds=parse_retry_after(
                                headers.get("retry-after")
                            ),
                            rate_limit_headers=headers,
                            response_excerpt=body or None,
                        )
                    else:
                        failure_class = classify_exception(
                            exc,
                            role=role,
                            stage="navigate" if kind == "ui" else "readiness",
                        )
                        last_failure = FailureRecord(
                            failure_class=failure_class,
                            role=role,
                            stage="readiness",
                            retryable=is_retryable(failure_class),
                            attempt=attempt,
                            elapsed_ms=elapsed_ms,
                            error_type=type(exc).__name__,
                            error_message=sanitize_excerpt(str(exc), limit=300),
                        )
                    records.append(
                        {
                            "target": probe_name,
                            "attempt": attempt,
                            "status": "FAIL",
                            "failure": last_failure.to_dict(),
                        }
                    )
                    if not last_failure.retryable:
                        break
                    if attempt < self.settings.eval_readiness_attempts:
                        await asyncio.sleep(
                            backoff_delay(
                                attempt,
                                retry_after=last_failure.retry_after_seconds,
                                max_seconds=2,
                            )
                        )
                finally:
                    with suppress(Exception):
                        await target.close()
            if not passed:
                return False, records, last_failure
        return True, records, None

    @staticmethod
    def _expected_evaluator_fault_response(
        reply: TargetReply,
        *,
        expected_fault: str,
    ) -> bool:
        """Recognize only the integrated target's explicit expected fault response.

        ``empty_once`` is deliberately not a valid assistant reply.  A normal
        reply would mean the header was ignored; an unmarked empty reply remains a
        target outage.  The test-only response marker is emitted only after the
        target accepted evaluator control in its test environment.
        """

        raw: dict[str, Any] = reply.raw if isinstance(reply.raw, dict) else {}
        error_candidate = raw.get("error")
        error: dict[str, Any] = (
            error_candidate if isinstance(error_candidate, dict) else {}
        )
        expected_codes = _EXPECTED_EVALUATOR_FAULT_ERRORS.get(expected_fault, frozenset())
        return (
            reply.status_code is not None
            and reply.status_code >= 400
            and raw.get("_evaluator_fault_applied") == expected_fault
            and str(error.get("error_code") or "") in expected_codes
        )

    @staticmethod
    def _readiness_message() -> str:
        """A cheap, non-mutating AI-first turn for authenticated readiness.

        Long-input behavior belongs to its own measured evaluator topics. Running a
        repeated 1,000-character strategy mutation before every case routed readiness
        through the expensive complex planner, could spend real target tokens, and
        occasionally exhausted the provider before a quality case began.
        """

        return "Can you hear me? Reply briefly."

    def _reply_failure(
        self,
        reply: TargetReply,
        *,
        kind: str,
        scenario_id: str,
        turn_id: str,
    ) -> FailureRecord | None:
        """Classify target failures before any simulated follow-up or judge call."""

        role = _role_for_kind(kind)
        failure_class: FailureClass | None = None
        raw_error = reply.raw.get("error") if isinstance(reply.raw, dict) else None
        raw_error_code = (
            str(raw_error.get("error_code") or "")
            if isinstance(raw_error, dict)
            else ""
        )
        structured_failure: FailureClass | None = None
        if raw_error_code:
            try:
                structured_failure = FailureClass(raw_error_code)
            except ValueError:
                structured_failure = None
        deterministic_application_failure = bool(
            raw_error_code
            and structured_failure is None
            and isinstance(raw_error, dict)
            and raw_error.get("retryable") is False
        )
        if raw_error_code in _TARGET_PRODUCT_FAILURE_CODES or reply.status_code == 422:
            # The target handled the request and exposed a product defect.  It remains
            # a measured reliability signal and may recover on a later user turn.
            failure_class = None
        elif structured_failure is not None:
            failure_class = structured_failure
        elif (
            reply.status_code in {401, 403, 429}
            or (reply.status_code is not None and reply.status_code >= 500)
        ) and not deterministic_application_failure:
            failure_class = classify_http_status(
                int(reply.status_code),
                role=role,
                body=sanitize_excerpt(reply.raw) or "",
            )
        elif reply.error and not deterministic_application_failure:
            failure_class = classify_exception(
                RuntimeError(reply.error),
                role=role,
                stage="turn",
            )
        elif not reply.text.strip() and reply.status_code is None:
            failure_class = (
                FailureClass.UI_RESPONSE_TIMEOUT
                if kind == "ui"
                else FailureClass.TARGET_EMPTY_RESPONSE
            )
        if failure_class is None:
            return None
        request_id = (
            str(raw_error.get("request_id"))
            if isinstance(raw_error, dict) and raw_error.get("request_id")
            else None
        )
        return FailureRecord(
            failure_class=failure_class,
            role=role,
            stage=(
                str(raw_error.get("stage") or "turn")
                if isinstance(raw_error, dict)
                else "turn"
            ),
            retryable=(
                bool(raw_error.get("retryable"))
                if isinstance(raw_error, dict)
                and isinstance(raw_error.get("retryable"), bool)
                else is_retryable(failure_class)
            ),
            case_id=scenario_id,
            turn_id=turn_id,
            elapsed_ms=reply.latency_ms,
            http_status=reply.status_code,
            error_type="TargetReplyError" if reply.error else None,
            error_message=sanitize_excerpt(reply.error, limit=300),
            request_id=request_id,
            response_excerpt=sanitize_excerpt(reply.raw),
        )

    def _failed_case(
        self,
        *,
        scenario: ScenarioSpec,
        kind: str,
        variant: dict[str, Any],
        started_at: str,
        turns: list[TurnRecord],
        structured: dict[str, Any] | None,
        canonical_state: dict[str, Any] | None,
        target_cost: float,
        test_cost: float,
        artifacts: list[str],
        failure: FailureRecord,
    ) -> CaseResult:
        return CaseResult(
            run_id=self.run_id,
            scenario=scenario,
            target_kind=kind,
            target_variant=str(variant.get("name", "current")),
            started_at=started_at,
            finished_at=utc_now(),
            turns=turns,
            deterministic_metrics={},
            judge=None,
            structured_output=structured,
            structured_hash=semantic_contract_hash(structured) if structured else None,
            schema_errors=[],
            total_latency_ms=sum(turn.latency_ms or 0 for turn in turns),
            target_cost_usd=target_cost,
            test_ai_cost_usd=test_cost,
            passed=False,
            error=f"{failure.failure_class} at {failure.stage}",
            artifacts=artifacts,
            failure=failure.to_dict(),
            measurement_status="NOT_MEASURED",
            measurement_issues=[str(failure.failure_class)],
            canonical_state=canonical_state,
        )

    def _charge(self, amount: float) -> None:
        self.spent += amount
        if self.budget > 0 and self.spent > self.budget:
            raise BudgetExceeded(
                f"Hard evaluator budget exceeded: ${self.spent:.4f} > ${self.budget:.4f}"
            )

    @property
    def remaining_budget(self) -> float:
        """Budget left. Unlimited budgets report infinity so callers need no special case."""
        if self.budget <= 0:
            return math.inf
        return max(0.0, self.budget - self.spent)

    def projected_case_cost(self) -> float:
        """What the next case is expected to cost, from what cases have actually cost.

        The mean of completed cases is used rather than a fixed guess so the estimate
        tracks the suite actually running. Before any case completes there is nothing
        to project from, so scheduling is gated on the hard limit alone.
        """
        if not self._case_costs:
            return 0.0
        return sum(self._case_costs) / len(self._case_costs)

    def stop_reason(self) -> str | None:
        """Why no further case may be scheduled, or ``None`` to continue.

        Checked *before* starting a case, not while charging one. Charging was the
        only gate before, so the run always discovered it was over budget after
        already spending past it. Every recorded run overshot its limit and reported
        `STOPPED_BUDGET` only once the whole suite had finished.
        """
        if self.budget <= 0:
            return None
        if self.spent >= self.budget:
            return f"Budget ${self.budget:.4f} reached (${self.spent:.4f} spent)."
        projected = self.projected_case_cost()
        if projected > 0 and self.remaining_budget < projected:
            return (
                f"Remaining ${self.remaining_budget:.4f} cannot cover another case "
                f"(cases have averaged ${projected:.4f})."
            )
        return None

    def _target_cost(self, model: str | None, usage: dict[str, Any]) -> float:
        recorded_cost = usage.get("estimated_cost_usd")
        if recorded_cost is not None:
            try:
                authoritative_cost = float(recorded_cost)
            except (TypeError, ValueError) as exc:
                raise CostAccountingError("Target returned an invalid authoritative cost.") from exc
            if not math.isfinite(authoritative_cost) or authoritative_cost < 0:
                raise CostAccountingError("Target returned an invalid authoritative cost.")
            return authoritative_cost
        inp = float(usage.get("input_tokens", 0))
        cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        out = float(usage.get("output_tokens", 0))
        if inp <= 0 and out <= 0:
            return 0.0
        try:
            pricing = self.settings.target_pricing(model)
        except ValueError as exc:
            raise CostAccountingError(str(exc)) from exc
        return (
            max(0.0, inp - cached) * pricing["input"]
            + cached * pricing["cached_input"]
            + out * pricing["output"]
        ) / 1_000_000

    def make_target(self, kind: str) -> ChatTarget:
        if kind == "backend":
            if self.settings.target_backend_adapter == "hilalmarkets":
                return HilalMarketsBackendTarget(self.settings)
            if self.settings.target_backend_adapter == "generic_http":
                return GenericHTTPBackendTarget(self.settings)
            raise ValueError(
                f"Unknown TARGET_BACKEND_ADAPTER: {self.settings.target_backend_adapter}"
            )
        if kind == "ui":
            return UITarget(self.settings, self.evidence_dir)
        raise ValueError(f"Unknown target kind: {kind}")

    async def run_case(
        self, scenario: ScenarioSpec, kind: str, variant: dict[str, Any], judge_mode: str
    ) -> CaseResult:
        started_at = utc_now()
        target = self.make_target(kind)
        turns: list[TurnRecord] = []
        artifacts: list[str] = []
        structured = None
        canonical_state = None
        error = None
        test_cost = 0.0
        target_cost = 0.0
        fault_used = False
        clean_turn_success = True
        product_failures: list[str] = []
        try:
            await target.start(scenario.id, variant)
            for turn_number in range(1, scenario.max_turns + 1):
                user_text, done, cost = await self.test_ai.next_user_turn(
                    scenario, turns, turn_number
                )
                self._charge(cost)
                test_cost += cost
                turns.append(
                    TurnRecord(
                        turn_id=f"u{turn_number}", role="user", text=user_text, timestamp=utc_now()
                    )
                )
                fault = scenario.fault if scenario.fault and not fault_used else None
                reply = await target.send(user_text, scenario_id=scenario.id, fault=fault)
                fault_used = fault_used or bool(fault)
                artifacts.extend(reply.artifacts)
                if reply.structured is not None:
                    structured = reply.structured
                if reply.canonical_state is not None:
                    canonical_state = reply.canonical_state
                usage = reply.usage or {}
                turn_target_cost = self._target_cost(reply.model, usage)
                self._charge(turn_target_cost)
                target_cost += turn_target_cost
                turns.append(
                    TurnRecord(
                        turn_id=f"a{turn_number}",
                        role="assistant",
                        text=reply.text,
                        timestamp=utc_now(),
                        latency_ms=reply.latency_ms,
                        status_code=reply.status_code,
                        raw_hash=reply.raw_hash,
                        structured=reply.structured,
                        canonical_state=reply.canonical_state,
                        model=reply.model,
                        usage=reply.usage,
                        error=reply.error,
                    )
                )
                raw_path = self.evidence_dir / f"{scenario.id}-{kind}-{turn_number}.json"
                raw_path.write_text(
                    json.dumps(
                        {"user": user_text, "reply": reply.raw, "error": reply.error},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                artifacts.append(str(raw_path))
                if fault and self._expected_evaluator_fault_response(
                    reply,
                    expected_fault=fault,
                ):
                    # The test-only target proved the injected one-shot fault reached
                    # the real LLM boundary.  Preserve that failed turn as evidence,
                    # then let the simulated trader perform the recovery turn.
                    continue
                product_failure = _product_failure_code(reply)
                if product_failure is not None:
                    clean_turn_success = False
                    product_failures.append(product_failure)
                    # A handled target-side 422/schema/grounding failure is product
                    # reliability, not evaluator infrastructure.  Both routes get the
                    # same remaining-turn recovery policy.
                    continue
                reply_failure = self._reply_failure(
                    reply,
                    kind=kind,
                    scenario_id=scenario.id,
                    turn_id=f"a{turn_number}",
                )
                if reply_failure is not None:
                    return self._failed_case(
                        scenario=scenario,
                        kind=kind,
                        variant=variant,
                        started_at=started_at,
                        turns=turns,
                        structured=structured,
                        canonical_state=canonical_state,
                        target_cost=target_cost,
                        test_cost=test_cost,
                        artifacts=artifacts,
                        failure=reply_failure,
                    )
                lifecycle = _approval_lifecycle_from_structured(structured)
                if lifecycle == "awaiting_approval":
                    if scenario.approval_mode == "preserve_gate":
                        break
                    if _needs_stale_approval_probe(scenario, turns):
                        continue
                    turns.append(
                        TurnRecord(
                            turn_id=f"approval-action-{turn_number}",
                            role="user",
                            text="[authenticated Review and approve control]",
                            timestamp=utc_now(),
                        )
                    )
                    approval_reply = await target.approve(scenario_id=scenario.id)
                    artifacts.extend(approval_reply.artifacts)
                    if approval_reply.structured is not None:
                        structured = approval_reply.structured
                    if approval_reply.canonical_state is not None:
                        canonical_state = approval_reply.canonical_state
                    approval_cost = self._target_cost(
                        approval_reply.model,
                        approval_reply.usage or {},
                    )
                    self._charge(approval_cost)
                    target_cost += approval_cost
                    turns.append(
                        TurnRecord(
                            turn_id=f"approval-result-{turn_number}",
                            role="assistant",
                            text=approval_reply.text,
                            timestamp=utc_now(),
                            latency_ms=approval_reply.latency_ms,
                            status_code=approval_reply.status_code,
                            raw_hash=approval_reply.raw_hash,
                            structured=approval_reply.structured,
                            canonical_state=approval_reply.canonical_state,
                            model=approval_reply.model,
                            usage=approval_reply.usage,
                            error=approval_reply.error,
                        )
                    )
                    approval_failure = self._reply_failure(
                        approval_reply,
                        kind=kind,
                        scenario_id=scenario.id,
                        turn_id=f"approval-result-{turn_number}",
                    )
                    if approval_failure is not None:
                        return self._failed_case(
                            scenario=scenario,
                            kind=kind,
                            variant=variant,
                            started_at=started_at,
                            turns=turns,
                            structured=structured,
                            canonical_state=canonical_state,
                            target_cost=target_cost,
                            test_cost=test_cost,
                            artifacts=artifacts,
                            failure=approval_failure,
                        )
                    approved_lifecycle = _approval_lifecycle_from_structured(structured)
                    if approved_lifecycle not in {"approved", "compiled", "activated"}:
                        clean_turn_success = False
                        product_failures.append("AUTHENTICATED_APPROVAL_NOT_BOUND")
                    if ScenarioContract.from_value(scenario.expected_contract).workflow().get(
                        "kind"
                    ) != "approval_rebind":
                        break
                    continue
                if done and turn_number >= 2:
                    break
                if TestAI.workflow_complete(scenario, turns):
                    break
            if structured is None:
                clean_turn_success = False
                product_failures.append("TARGET_NO_STRUCTURED_STRATEGY")
            schema_errors = validate_schema(structured, self.schema)
            deterministic = deterministic_metrics(
                scenario, turns, structured, schema_errors, self.settings.target_field_map
            )
            paired_only = scenario.topic_id == "ui_backend_parity"
            judge_eligible = not paired_only and structured is not None and any(
                turn.role == "assistant" and turn.text.strip() for turn in turns
            )
            deterministic["judge_eligible"] = float(judge_eligible)
            judge: JudgeVerdict | None = None
            if judge_mode == "online" and judge_eligible:
                judge, cost = await self.test_ai.judge(
                    scenario, turns, deterministic, schema_errors, structured
                )
                self._charge(cost)
                test_cost += cost
            elif judge_mode == "deferred" and judge_eligible:
                pending_dir = ensure_dir(self.run_dir / "batch_pending")
                (
                    pending_dir / f"{scenario.id}-{kind}-{variant.get('name', 'current')}.json"
                ).write_text(
                    json.dumps(
                        {
                            "scenario": scenario.__dict__,
                            "turns": [t.__dict__ for t in turns],
                            "deterministic": deterministic,
                            "schema_errors": schema_errors,
                            "structured_output": structured,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            semantic_pass = bool(deterministic.get("semantic_contract_pass"))
            eventual_success = semantic_pass and structured is not None
            passed = (
                bool(judge.passed) and eventual_success
                if judge
                else eventual_success and not error
            )
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics=deterministic,
                judge=judge,
                structured_output=structured,
                structured_hash=semantic_contract_hash(structured) if structured else None,
                schema_errors=schema_errors,
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=passed,
                error=error,
                artifacts=artifacts,
                measurement_status="NOT_MEASURED" if paired_only else "MEASURED",
                measurement_issues=(
                    ["awaiting_paired_backend_ui_capture"] if paired_only else []
                ),
                clean_turn_success=clean_turn_success,
                eventual_case_success=eventual_success,
                product_failure_classes=list(dict.fromkeys(product_failures)),
                canonical_state=canonical_state,
            )
        except (BudgetExceeded, CostAccountingError, EvaluationInfrastructureError):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 429}:
                evaluator_request = str(exc.request.url).startswith(
                    self.settings.test_ai_base_url.rstrip("/")
                )
                body = sanitize_excerpt(exc.response.text) or ""
                failure_class = classify_http_status(
                    exc.response.status_code,
                    role="judge" if evaluator_request else "target",
                    body=body,
                )
                if evaluator_request:
                    # .env now outranks the environment, so a mismatch is no longer
                    # the cause of the rejection — the key in .env is the one that
                    # was refused. Say so, and flag the ignored duplicate separately.
                    source_hint = (
                        " The key in .env was used and rejected; a different, now "
                        "ignored, OPENAI_API_KEY is also set in the environment."
                        if process_openai_key_overrides_dotenv()
                        else " The key in .env was used and rejected."
                    )
                    raise EvaluationInfrastructureError(
                        f"Evaluator OpenAI access failed ({failure_class}, HTTP "
                        f"{exc.response.status_code}).{source_hint}",
                        failure_class=failure_class,
                    ) from exc
                raise EvaluationInfrastructureError(
                    f"Authenticated target access failed ({failure_class}, HTTP "
                    f"{exc.response.status_code}).",
                    failure_class=failure_class,
                ) from exc
            error = f"{type(exc).__name__}: {exc}"
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics={},
                judge=None,
                structured_output=structured,
                structured_hash=semantic_contract_hash(structured) if structured else None,
                schema_errors=[],
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=False,
                error=error,
                artifacts=artifacts,
                failure=self._http_failure_record(
                    exc,
                    kind=kind,
                    scenario_id=scenario.id,
                    turns=turns,
                    elapsed_ms=sum(t.latency_ms or 0 for t in turns),
                ).to_dict(),
                measurement_status="NOT_MEASURED",
                measurement_issues=["target_http_failure"],
                canonical_state=canonical_state,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            role = _role_for_kind(kind)
            failure_class = classify_exception(exc, role=role, stage="turn")
            if isinstance(exc, MalformedAIResponse):
                # The evaluator's own model produced the bad body, not the chatbot.
                # Without this the same `TARGET_INVALID_JSON` was recorded either way
                # and there was no way to tell which side to fix.
                role = "judge" if exc.namespace == "judge" else "simulated_trader"
                failure_class = FailureClass.EVALUATOR_INVALID_JSON
                artifacts.append(
                    self._write_malformed_body(scenario.id, kind, exc)
                )
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics={},
                judge=None,
                structured_output=structured,
                structured_hash=semantic_contract_hash(structured) if structured else None,
                schema_errors=[],
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=False,
                error=error,
                artifacts=artifacts,
                failure=FailureRecord(
                    failure_class=failure_class,
                    role=role,
                    stage="turn",
                    retryable=is_retryable(failure_class),
                    case_id=scenario.id,
                    turn_id=turns[-1].turn_id if turns else None,
                    elapsed_ms=sum(t.latency_ms or 0 for t in turns),
                    error_type=type(exc).__name__,
                    error_message=sanitize_excerpt(str(exc), limit=300),
                ).to_dict(),
                measurement_status="NOT_MEASURED",
                measurement_issues=[str(failure_class)],
                canonical_state=canonical_state,
            )
        finally:
            await target.close()

    def _write_malformed_body(
        self,
        scenario_id: str,
        kind: str,
        exc: MalformedAIResponse,
    ) -> str:
        """Persist the unparseable body so the cause is diagnosable after the run."""
        name = f"malformed-{exc.namespace or 'response'}-{scenario_id}-{kind}.txt"
        path = self.evidence_dir / name
        path.write_text(
            f"namespace: {exc.namespace}\n"
            f"total_length: {exc.body_length}\n"
            f"error: {exc}\n"
            f"--- sanitized excerpt ---\n{exc.body_excerpt}\n",
            encoding="utf-8",
        )
        return str(path)

    def _http_failure_record(
        self,
        exc: httpx.HTTPStatusError,
        *,
        kind: str,
        scenario_id: str,
        turns: list[TurnRecord],
        elapsed_ms: float,
    ) -> FailureRecord:
        """Describe a non-2xx response as infrastructure telemetry, not a bad answer."""
        evaluator_request = str(exc.request.url).startswith(
            self.settings.test_ai_base_url.rstrip("/")
        )
        role: Role = "judge" if evaluator_request else _role_for_kind(kind)
        body = sanitize_excerpt(exc.response.text) or ""
        failure_class = classify_http_status(exc.response.status_code, role=role, body=body)
        headers = rate_limit_headers(exc.response.headers)
        return FailureRecord(
            failure_class=failure_class,
            role=role,
            stage="turn",
            retryable=is_retryable(failure_class),
            case_id=scenario_id,
            turn_id=turns[-1].turn_id if turns else None,
            elapsed_ms=elapsed_ms,
            http_status=exc.response.status_code,
            error_type=type(exc).__name__,
            request_id=headers.get("x-request-id"),
            retry_after_seconds=parse_retry_after(headers.get("retry-after")),
            rate_limit_headers=headers,
            response_excerpt=body or None,
        )

    async def run(
        self,
        *,
        mode: str,
        target_kinds: list[str],
        topic_ids: list[str] | None,
        tests_per_topic: int,
        seed: int,
        judge_mode: str,
        only_scenario: str | None = None,
        selection_seed: int | None = None,
        approval_mode: ApprovalMode = "preserve_gate",
    ) -> tuple[list[CaseResult], dict[str, Any]]:
        configured = [TOPIC_BY_ID[x] for x in topic_ids] if topic_ids else list(TOPICS)
        selected = topics_for_mode(mode, configured)
        count = cases_per_topic(mode, tests_per_topic)
        effective_selection_seed = (
            selection_seed
            if selection_seed is not None
            else int(stable_hash({"run_id": self.run_id, "scenario_seed": seed})[:16], 16)
        )
        max_turns = {topic.id: max_turns_for_topic(mode, topic) for topic in selected}
        scenarios = build_randomized_scenario_plan(
            selected,
            count_per_topic=count,
            global_seed=seed,
            selection_seed=effective_selection_seed,
            max_turns_by_topic=max_turns,
        )
        if only_scenario:
            # Explicit scenarios stay replayable even when random sampling did not
            # select them for this run.
            scenarios = [
                candidate
                for topic in selected
                for index in range(1, topic.max_cases + 1)
                if (
                    candidate := build_scenario(
                        topic,
                        index,
                        seed,
                        max_turns=max_turns[topic.id],
                    )
                ).id
                == only_scenario
            ]
            if not scenarios:
                raise ValueError(
                    "Scenario ID not found; use the same scenario seed and topic selection"
                )
        for scenario in scenarios:
            scenario.approval_mode = approval_mode
        work = []
        for scenario in scenarios:
            topic = TOPIC_BY_ID[scenario.topic_id]
            repeats = repeats_for_topic(topic)
            for _ in range(repeats):
                for kind in target_kinds_for_topic(mode, target_kinds, topic):
                    for variant in variants_for_topic(
                        mode,
                        self.settings.target_variants,
                        topic,
                    ):
                        work.append((scenario, kind, variant))
        run_metadata = {
            "scenario_seed": seed,
            "selection_seed": effective_selection_seed,
            "selection_strategy": "stratified_random_without_replacement",
            "planned_scenarios": len(scenarios),
            "planned_cases": len(work),
        }
        (self.run_dir / "run_plan.json").write_text(
            json.dumps(
                {
                    **run_metadata,
                    "work": [
                        {
                            "scenario_id": scenario.id,
                            "topic_id": scenario.topic_id,
                            "target": kind,
                            "variant": str(variant.get("name", "current")),
                        }
                        for scenario, kind, variant in work
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        readiness_ok, readiness_records, readiness_failure = await self._readiness_gate(work)
        readiness_payload = {
            "status": "PASS" if readiness_ok else "FAIL",
            "records": readiness_records,
            "failure": readiness_failure.to_dict() if readiness_failure else None,
        }
        (self.run_dir / "readiness.json").write_text(
            json.dumps(readiness_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_metadata["readiness_status"] = readiness_payload["status"]
        run_metadata["readiness_target_cost_usd"] = self.readiness_target_cost
        execution_status: str | None = None
        execution_error: str | None = None
        if not readiness_ok:
            failure_class = (
                readiness_failure.failure_class
                if readiness_failure
                else FailureClass.UNKNOWN_INFRASTRUCTURE_FAILURE
            )
            execution_status = str(
                pause_state_for(failure_class) or ExecutionState.FAILED_CONFIGURATION
            )
            execution_error = (
                f"Authenticated target access failed during readiness "
                f"({failure_class}) before quality evaluation. "
                "Completed 0 cases before stopping."
            )
            if failure_class is FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE:
                execution_error += (
                    " Fault-injection topics must use an isolated APP_ENV=test "
                    "target with AI_SETUP_EVALUATOR_ENABLED=true and "
                    "AI_SETUP_EVALUATOR_FAULTS_ENABLED=true; do not enable those "
                    "controls on the development or production app."
                )
            summary = write_reports(
                self.run_dir,
                [],
                budget_usd=self.budget,
                measured_spend_usd=self.spent,
                execution_status=execution_status,
                execution_error=execution_error,
                run_metadata=run_metadata,
            )
            return [], summary
        concurrency = 1 if mode == "budget" else self.settings.eval_max_concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def one(item):
            async with semaphore:
                return await self.run_case(item[0], item[1], item[2], judge_mode)

        cases: list[CaseResult] = []
        repeated_failure_classes: Counter[str] = Counter()
        circuit_classes = {
            str(FailureClass.TARGET_DNS_RESOLUTION_FAILURE),
            str(FailureClass.TARGET_CONNECT_TIMEOUT),
            str(FailureClass.TARGET_READ_TIMEOUT),
            str(FailureClass.TARGET_TOTAL_TIMEOUT),
            str(FailureClass.TARGET_HTTP_429),
            str(FailureClass.TARGET_HTTP_5XX),
            str(FailureClass.TARGET_EMPTY_RESPONSE),
            str(FailureClass.TARGET_PARTIAL_STREAM),
            str(FailureClass.TARGET_COMPILE_TIMEOUT),
            str(FailureClass.UI_NAVIGATION_TIMEOUT),
            str(FailureClass.UI_RESPONSE_TIMEOUT),
        }
        chunk_width = 1 if mode == "budget" else max(1, concurrency * 2)
        for chunk_start in range(0, len(work), chunk_width):
            # Gate *before* scheduling. The cases already in flight finish and are
            # kept; nothing new starts once the budget cannot cover it.
            reason = self.stop_reason()
            if reason is not None:
                execution_status = str(ExecutionState.STOPPED_BUDGET)
                execution_error = f"{reason} Completed {len(cases)} cases before stopping."
                break
            chunk = work[chunk_start : chunk_start + chunk_width]
            # `return_exceptions` keeps the cases that did complete. Letting the
            # exception escape `gather` discarded every result in the chunk, so work
            # that was already paid for was thrown away with it.
            results = await asyncio.gather(*(one(item) for item in chunk), return_exceptions=True)
            fatal: BaseException | None = None
            for result in results:
                if isinstance(result, CaseResult):
                    cases.append(result)
                    self._case_costs.append(
                        float(result.target_cost_usd or 0.0) + float(result.test_ai_cost_usd or 0.0)
                    )
                    case_failure_class = str(
                        (result.failure or {}).get("failure_class") or ""
                    )
                    if case_failure_class in circuit_classes:
                        repeated_failure_classes[case_failure_class] += 1
                elif isinstance(result, BaseException) and fatal is None:
                    fatal = result
            if isinstance(fatal, BudgetExceeded):
                execution_status = str(ExecutionState.STOPPED_BUDGET)
                execution_error = f"{fatal} Completed {len(cases)} cases before stopping."
                break
            if isinstance(fatal, CostAccountingError):
                execution_status = str(ExecutionState.FAILED_CONFIGURATION)
                execution_error = f"{fatal} Completed {len(cases)} cases before stopping."
                break
            if isinstance(fatal, EvaluationInfrastructureError):
                pause_state = pause_state_for(fatal.failure_class)
                execution_status = str(pause_state or ExecutionState.FAILED_CONFIGURATION)
                # The summary must describe what actually ran. The previous message was
                # a fixed "before a quality case could run" string, which reported zero
                # completed cases in a run that had already finished nine of them.
                completed = len(cases)
                execution_error = (
                    f"{fatal} Completed {completed} case{'s' if completed != 1 else ''} "
                    f"before stopping."
                )
                break
            if fatal is not None:
                raise fatal
            repeated_class = next(
                (
                    name
                    for name, count in repeated_failure_classes.items()
                    if count >= self.settings.eval_circuit_breaker_failures
                ),
                None,
            )
            if repeated_class is not None:
                execution_status = str(ExecutionState.PAUSED_TARGET_UNAVAILABLE)
                execution_error = (
                    "Target circuit breaker opened after "
                    f"{repeated_failure_classes[repeated_class]} repeated "
                    f"{repeated_class} failures. Completed {len(cases)} cases "
                    "before stopping."
                )
                break
            self._apply_paired_measurements(cases)
            write_reports(
                self.run_dir,
                cases,
                budget_usd=self.budget,
                measured_spend_usd=self.spent,
                run_metadata=run_metadata,
            )
        self._apply_paired_measurements(cases)
        summary = write_reports(
            self.run_dir,
            cases,
            budget_usd=self.budget,
            measured_spend_usd=self.spent,
            execution_status=execution_status,
            execution_error=execution_error,
            run_metadata=run_metadata,
        )
        return cases, summary

    @staticmethod
    def _apply_paired_measurements(cases: list[CaseResult]) -> None:
        """Measure UI parity only from usable backend/UI captures of one scenario."""

        groups: dict[tuple[str, str], dict[str, CaseResult]] = defaultdict(dict)
        for case in cases:
            if case.scenario.topic_id != "ui_backend_parity":
                continue
            groups[(case.scenario.id, case.target_variant)][case.target_kind] = case
        for pair in groups.values():
            backend = pair.get("backend")
            ui = pair.get("ui")
            if (
                backend is None
                or ui is None
                or backend.failure is not None
                or ui.failure is not None
                or not backend.structured_hash
                or not ui.structured_hash
            ):
                continue
            parity = float(backend.structured_hash == ui.structured_hash)
            for case in (backend, ui):
                case.deterministic_metrics["ui_backend_parity"] = parity
                case.measurement_status = "MEASURED"
                case.measurement_issues = []
                case.passed = bool(
                    parity
                    and case.deterministic_metrics.get("semantic_contract_pass")
                )

    async def close(self) -> None:
        await self.ai_client.close()
        self.cache.close()
