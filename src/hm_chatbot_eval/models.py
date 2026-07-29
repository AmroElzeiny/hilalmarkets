from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["critical", "high", "medium", "low"]


class ScenarioContract(dict[str, Any]):
    """Canonical scenario expectations shared by checks and the judge.

    Keeping the contract dict-compatible preserves the JSONL report format while
    giving scenario construction, deterministic checks, and judge prompts one
    explicit type instead of separate loosely related expectation payloads.
    """

    @classmethod
    def from_value(cls, value: dict[str, Any] | ScenarioContract) -> ScenarioContract:
        return value if isinstance(value, cls) else cls(value)

    def semantic_fields(self, *, final: bool = False) -> dict[str, Any]:
        """Return only target fields, with the final workflow patch when requested."""

        values = {key: value for key, value in self.items() if key != "workflow"}
        workflow = self.get("workflow")
        if final and isinstance(workflow, dict):
            final_fields = workflow.get("final_expected")
            if isinstance(final_fields, dict):
                values.update(final_fields)
        return values

    def workflow(self) -> dict[str, Any]:
        value = self.get("workflow")
        return dict(value) if isinstance(value, dict) else {}


@dataclass(frozen=True)
class SuccessCriterion:
    metric: str
    operator: Literal[">=", "<=", "=="]
    threshold: float
    description: str
    critical: bool = False


@dataclass(frozen=True)
class TopicSpec:
    id: str
    title: str
    category: str
    severity: Severity
    objective: str
    scenario_guidance: str
    criteria: tuple[SuccessCriterion, ...]
    default_cases: int = 24
    min_cases: int = 20
    max_cases: int = 30
    weight: float = 1.0
    max_turns: int = 8
    fault: str | None = None


@dataclass
class ScenarioSpec:
    id: str
    topic_id: str
    seed: int
    persona: dict[str, Any]
    hidden_goal: str
    expected_contract: ScenarioContract | dict[str, Any]
    success_criteria: list[dict[str, Any]]
    max_turns: int
    fault: str | None = None

    def __post_init__(self) -> None:
        self.expected_contract = ScenarioContract.from_value(self.expected_contract)


@dataclass
class TurnRecord:
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    timestamp: str
    latency_ms: float | None = None
    status_code: int | None = None
    raw_hash: str | None = None
    structured: dict[str, Any] | None = None
    model: str | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class EvidenceItem:
    kind: str
    reference: str
    detail: str
    path: str | None = None


@dataclass
class JudgeVerdict:
    passed: bool
    score: float
    confidence: float
    dimension_scores: dict[str, float]
    failures: list[dict[str, Any]]
    strengths: list[str]
    fixes: list[dict[str, Any]]
    evidence: list[EvidenceItem]
    unsupported_claims: list[str] = field(default_factory=list)


@dataclass
class CaseResult:
    run_id: str
    scenario: ScenarioSpec
    target_kind: str
    target_variant: str
    started_at: str
    finished_at: str
    turns: list[TurnRecord]
    deterministic_metrics: dict[str, float]
    judge: JudgeVerdict | None
    structured_output: dict[str, Any] | None
    structured_hash: str | None
    schema_errors: list[str]
    total_latency_ms: float
    target_cost_usd: float | None
    test_ai_cost_usd: float | None
    passed: bool
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)
    #: Serialized :class:`hm_chatbot_eval.failures.FailureRecord` when the case ended
    #: on an infrastructure condition rather than on a chatbot answer. Present means
    #: the failure is not a quality signal and must not be scored as one.
    failure: dict[str, Any] | None = None
    measurement_status: Literal["MEASURED", "NOT_MEASURED"] = "MEASURED"
    measurement_issues: list[str] = field(default_factory=list)

    @property
    def is_infrastructure_failure(self) -> bool:
        return self.failure is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def case_result_from_dict(data: dict[str, Any]) -> CaseResult:
    scenario_data = data["scenario"]
    scenario = ScenarioSpec(**scenario_data)
    turns = [TurnRecord(**x) for x in data.get("turns", [])]
    judge_data = data.get("judge")
    judge = None
    if judge_data:
        judge = JudgeVerdict(
            passed=judge_data["passed"],
            score=judge_data["score"],
            confidence=judge_data["confidence"],
            dimension_scores=judge_data.get("dimension_scores", {}),
            failures=judge_data.get("failures", []),
            strengths=judge_data.get("strengths", []),
            fixes=judge_data.get("fixes", []),
            evidence=[EvidenceItem(**x) for x in judge_data.get("evidence", [])],
            unsupported_claims=judge_data.get("unsupported_claims", []),
        )
    return CaseResult(
        run_id=data["run_id"],
        scenario=scenario,
        target_kind=data["target_kind"],
        target_variant=data["target_variant"],
        started_at=data["started_at"],
        finished_at=data["finished_at"],
        turns=turns,
        deterministic_metrics=data.get("deterministic_metrics", {}),
        judge=judge,
        structured_output=data.get("structured_output"),
        structured_hash=data.get("structured_hash"),
        schema_errors=data.get("schema_errors", []),
        total_latency_ms=data.get("total_latency_ms", 0),
        target_cost_usd=data.get("target_cost_usd"),
        test_ai_cost_usd=data.get("test_ai_cost_usd"),
        passed=data.get("passed", False),
        error=data.get("error"),
        artifacts=data.get("artifacts", []),
        failure=data.get("failure"),
        measurement_status=data.get("measurement_status", "MEASURED"),
        measurement_issues=data.get("measurement_issues", []),
    )
