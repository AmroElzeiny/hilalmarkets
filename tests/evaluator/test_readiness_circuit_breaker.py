from __future__ import annotations

from typing import Any

import httpx

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.failures import FailureClass
from hm_chatbot_eval.models import ScenarioSpec
from hm_chatbot_eval.runner import EvaluationRunner
from hm_chatbot_eval.targets.base import ChatTarget, TargetReply


class _CountingTestAI:
    def __init__(self) -> None:
        self.user_calls = 0
        self.judge_calls = 0

    async def next_user_turn(self, *_args, **_kwargs):
        self.user_calls += 1
        return "Build the stated setup.", False, 0.0

    async def judge(self, *_args, **_kwargs):
        self.judge_calls += 1
        raise AssertionError("an unusable or infrastructure response must not be judged")


class _Target(ChatTarget):
    kind = "backend"

    def __init__(self, *, start_error: BaseException | None = None, status: int = 200):
        self.start_error = start_error
        self.status = status

    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        if self.start_error is not None:
            raise self.start_error

    async def send(
        self,
        message: str,
        *,
        scenario_id: str,
        fault: str | None = None,
    ) -> TargetReply:
        return TargetReply(
            text="" if self.status >= 500 else "I need more information.",
            latency_ms=4,
            status_code=self.status,
            structured=None,
            raw={"detail": "temporary failure"} if self.status >= 500 else {},
            error=f"HTTP {self.status}" if self.status >= 500 else None,
        )

    async def close(self) -> None:
        return None


class _Runner(EvaluationRunner):
    def __init__(self, *args, target_factory, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_factory = target_factory

    def make_target(self, kind: str) -> ChatTarget:
        return self.target_factory()


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
        **overrides,
    )


def _scenario(*, max_turns: int = 1, fault: str | None = None) -> ScenarioSpec:
    return ScenarioSpec(
        id="readiness-case",
        topic_id="operator_mapping",
        seed=7,
        persona={},
        hidden_goal="Exercise evaluator failure handling.",
        expected_contract={"operator": "gte"},
        success_criteria=[],
        max_turns=max_turns,
        fault=fault,
    )


async def test_readiness_failure_aborts_before_simulator_or_judge_spend(tmp_path):
    runner = _Runner(
        _settings(tmp_path, eval_readiness_attempts=2),
        "readiness-stop",
        2.5,
        target_factory=lambda: _Target(
            start_error=httpx.ConnectError("getaddrinfo failed")
        ),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        cases, summary = await runner.run(
            mode="budget",
            target_kinds=["backend"],
            topic_ids=["operator_mapping"],
            tests_per_topic=24,
            seed=42,
            judge_mode="online",
        )
        assert cases == []
        assert test_ai.user_calls == 0
        assert test_ai.judge_calls == 0
        assert summary["execution_status"] == "PAUSED_TARGET_UNAVAILABLE"
        assert summary["measured_spend_usd"] == 0
        assert (runner.run_dir / "readiness.json").is_file()
    finally:
        await runner.close()


async def test_target_5xx_stops_the_case_before_follow_up_or_judge(tmp_path):
    runner = _Runner(
        _settings(tmp_path),
        "single-5xx",
        2.5,
        target_factory=lambda: _Target(status=500),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        result = await runner.run_case(
            _scenario(max_turns=4),
            "backend",
            {"name": "current"},
            "online",
        )
        assert result.failure["failure_class"] == "TARGET_HTTP_5XX"
        assert test_ai.user_calls == 1
        assert test_ai.judge_calls == 0
        assert len(result.turns) == 2
    finally:
        await runner.close()


async def test_absent_structured_output_is_quality_failure_without_judge_spend(tmp_path):
    runner = _Runner(
        _settings(tmp_path),
        "absent-output",
        2.5,
        target_factory=lambda: _Target(status=200),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        result = await runner.run_case(
            _scenario(),
            "backend",
            {"name": "current"},
            "online",
        )
        assert result.failure is None
        assert result.passed is False
        assert result.deterministic_metrics["judge_eligible"] == 0
        assert test_ai.judge_calls == 0
    finally:
        await runner.close()


async def test_repeated_target_5xx_opens_the_run_circuit(tmp_path):
    statuses = iter([200, 500, 500, 500])
    runner = _Runner(
        _settings(tmp_path, eval_circuit_breaker_failures=2),
        "circuit-open",
        2.5,
        target_factory=lambda: _Target(status=next(statuses)),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        cases, summary = await runner.run(
            mode="budget",
            target_kinds=["backend"],
            topic_ids=["operator_mapping", "threshold_mapping", "timeframe_mapping"],
            tests_per_topic=24,
            seed=42,
            judge_mode="online",
        )
        assert len(cases) == 2
        assert test_ai.user_calls == 2
        assert test_ai.judge_calls == 0
        assert summary["execution_status"] == "PAUSED_TARGET_UNAVAILABLE"
        assert "circuit breaker opened" in summary["execution_error"]
    finally:
        await runner.close()


async def test_readiness_probes_non_mutating_turn_and_fault_control_before_test_ai(
    tmp_path,
):
    observed: list[tuple[str, str | None]] = []

    class _ProbeTarget(_Target):
        async def send(self, message, *, scenario_id, fault=None):
            observed.append((message, fault))
            if fault:
                return TargetReply(
                    text="",
                    latency_ms=4,
                    status_code=422,
                    raw={
                        "_evaluator_fault_applied": fault,
                        "error": {"error_code": "TARGET_EMPTY_RESPONSE"},
                    },
                    error="HTTP 422",
                )
            return await super().send(message, scenario_id=scenario_id, fault=fault)

    runner = _Runner(
        _settings(tmp_path),
        "readiness-contract",
        2.5,
        target_factory=lambda: _ProbeTarget(status=200),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        ok, records, failure = await runner._readiness_gate(
            [(_scenario(fault="invalid_json_once"), "backend", {"name": "current"})]
        )
        assert ok is True
        assert failure is None
        assert records[0]["status"] == "PASS"
        assert observed and len(observed[0][0]) < 200
        assert observed[0][0] == "Can you hear me? Reply briefly."
        assert observed[0][1] == "empty_once"
        assert test_ai.user_calls == 0
        assert test_ai.judge_calls == 0
    finally:
        await runner.close()


async def test_readiness_rejects_an_unobserved_fault_header(tmp_path):
    runner = _Runner(
        _settings(tmp_path),
        "readiness-fault-not-observed",
        2.5,
        target_factory=lambda: _Target(status=200),
    )
    test_ai = _CountingTestAI()
    runner.test_ai = test_ai
    try:
        ok, records, failure = await runner._readiness_gate(
            [(_scenario(fault="invalid_json_once"), "backend", {"name": "current"})]
        )
        assert ok is False
        assert failure is not None
        assert failure.failure_class is FailureClass.EVALUATOR_FAULT_CONTROL_UNAVAILABLE
        assert failure.error_type == "EvaluatorFaultNotObserved"
        assert records[0]["status"] == "FAIL"
        assert test_ai.user_calls == 0
        assert test_ai.judge_calls == 0
    finally:
        await runner.close()


async def test_readiness_accepts_only_the_explicit_integrated_fault_probe(tmp_path):
    class _FaultProbeTarget(_Target):
        async def send(self, message, *, scenario_id, fault=None):
            return TargetReply(
                text="",
                latency_ms=4,
                status_code=422,
                raw={
                    "_evaluator_fault_applied": fault,
                    "error": {"error_code": "TARGET_EMPTY_RESPONSE"},
                },
                error="HTTP 422",
            )

    runner = _Runner(
        _settings(tmp_path),
        "readiness-fault-observed",
        2.5,
        target_factory=_FaultProbeTarget,
    )
    try:
        ok, records, failure = await runner._readiness_gate(
            [(_scenario(fault="invalid_json_once"), "backend", {"name": "current"})]
        )
        assert ok is True
        assert failure is None
        assert records[0]["status"] == "PASS"
        assert "fault_control_observed" in records[0]["checks"]
    finally:
        await runner.close()


async def test_expected_fault_response_is_preserved_then_recovery_turn_runs(tmp_path):
    class _RecoveryAI:
        def __init__(self) -> None:
            self.calls = 0

        async def next_user_turn(self, *_args, **_kwargs):
            self.calls += 1
            return ("Retry the same instruction.", self.calls >= 2, 0.0)

    class _RecoveryTarget(_Target):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def send(self, message, *, scenario_id, fault=None):
            self.calls += 1
            if self.calls == 1:
                return TargetReply(
                    text="",
                    latency_ms=4,
                    status_code=422,
                    raw={
                        "_evaluator_fault_applied": fault,
                        "error": {"error_code": "TARGET_INVALID_JSON"},
                    },
                    error="HTTP 422",
                )
            return TargetReply(
                text="Please clarify the threshold.",
                latency_ms=4,
                status_code=200,
                raw={},
            )

    target = _RecoveryTarget()
    runner = _Runner(
        _settings(tmp_path),
        "fault-recovery-turn",
        2.5,
        target_factory=lambda: target,
    )
    runner.test_ai = _RecoveryAI()
    try:
        result = await runner.run_case(
            _scenario(max_turns=2, fault="invalid_json_once"),
            "backend",
            {"name": "current"},
            "deferred",
        )
        assert result.failure is None
        assert target.calls == 2
        assert len(result.turns) == 4
        assert result.turns[1].error == "HTTP 422"
    finally:
        await runner.close()


async def test_readiness_does_not_require_fault_control_for_ordinary_scenarios(tmp_path):
    observed: list[str | None] = []

    class _ProbeTarget(_Target):
        async def send(self, message, *, scenario_id, fault=None):
            observed.append(fault)
            return await super().send(message, scenario_id=scenario_id, fault=fault)

    runner = _Runner(
        _settings(tmp_path),
        "readiness-without-faults",
        2.5,
        target_factory=lambda: _ProbeTarget(status=200),
    )
    try:
        ok, records, failure = await runner._readiness_gate(
            [(_scenario(), "backend", {"name": "current"})]
        )
        assert ok is True
        assert failure is None
        assert observed == [None]
        assert "fault_control_not_required" in records[0]["checks"]
    finally:
        await runner.close()


def test_structured_runtime_taxonomy_overrides_generic_http_status(tmp_path) -> None:
    runner = _Runner(
        _settings(tmp_path),
        "structured-timeout-taxonomy",
        2.5,
        target_factory=lambda: _Target(),
    )
    try:
        failure = runner._reply_failure(
            TargetReply(
                text="The bounded setup turn exceeded its total time limit.",
                latency_ms=12_000,
                status_code=503,
                error="HTTP 503",
                raw={
                    "error": {
                        "error_code": "TARGET_TOTAL_TIMEOUT",
                        "request_id": "request-timeout-1",
                        "stage": "provider",
                        "retryable": True,
                    }
                },
            ),
            kind="backend",
            scenario_id="scenario-1",
            turn_id="a1",
        )
    finally:
        runner.cache.close()

    assert failure is not None
    assert failure.failure_class is FailureClass.TARGET_TOTAL_TIMEOUT
    assert failure.stage == "provider"
    assert failure.request_id == "request-timeout-1"


def test_deterministic_compiler_error_is_not_reported_as_infrastructure(tmp_path) -> None:
    runner = _Runner(
        _settings(tmp_path),
        "structured-compiler-validation",
        2.5,
        target_factory=lambda: _Target(),
    )
    try:
        failure = runner._reply_failure(
            TargetReply(
                text="The current draft cannot be compiled until its rule is corrected.",
                latency_ms=40,
                status_code=500,
                error="HTTP 500",
                structured={"schema_version": "1"},
                raw={
                    "error": {
                        "error_code": "strategy_compile_failed",
                        "request_id": "request-compile-1",
                        "stage": "compile",
                        "retryable": False,
                    }
                },
            ),
            kind="backend",
            scenario_id="scenario-compile",
            turn_id="a1",
        )
    finally:
        runner.cache.close()

    assert failure is None


def test_rendered_ui_grounding_rejection_is_not_a_response_timeout(tmp_path) -> None:
    runner = _Runner(
        _settings(tmp_path),
        "ui-grounding-rejection",
        2.5,
        target_factory=lambda: _Target(),
    )
    try:
        failure = runner._reply_failure(
            TargetReply(
                text="A value in that change does not appear in the words that asked for it.",
                latency_ms=40,
                status_code=422,
                error="HTTP 422",
                raw={
                    "error": {
                        "error_code": "VALUE_NOT_GROUNDED",
                        "request_id": "request-grounding-1",
                        "stage": "patch",
                        "retryable": False,
                    }
                },
            ),
            kind="ui",
            scenario_id="scenario-grounding",
            turn_id="a2",
        )
    finally:
        runner.cache.close()

    assert failure is None
