from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CriterionOutcome = Literal[
    "pass",
    "qualification",
    "fail",
    "not_applicable",
    "needs_evidence",
]
UseCoverageDecision = Literal[
    "covered",
    "qualified",
    "not_covered",
    "not_applicable",
    "under_review",
    "excluded",
]


class MethodologyCriterionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    label: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=10, max_length=1200)
    required: bool = True
    allowed_outcomes: list[CriterionOutcome] = Field(min_length=1)
    evidence_categories: list[str] = Field(min_length=1)
    qualification_rules: dict[str, Any] = Field(default_factory=dict)
    blocking_outcomes: list[CriterionOutcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome_contract(self) -> MethodologyCriterionDefinition:
        allowed = set(self.allowed_outcomes)
        blocked = set(self.blocking_outcomes)
        if len(allowed) != len(self.allowed_outcomes):
            raise ValueError("allowed_outcomes must not contain duplicates")
        if not blocked.issubset(allowed):
            raise ValueError("blocking_outcomes must be a subset of allowed_outcomes")
        return self


class MethodologyUseDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    label: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=10, max_length=1200)
    required: bool = True
    allowed_decisions: list[UseCoverageDecision] = Field(min_length=1)
    criterion_keys: list[str] = Field(default_factory=list)
    evidence_categories: list[str] = Field(default_factory=list)
    default_scope: str = Field(min_length=3, max_length=500)
    execution_blocking_decisions: list[UseCoverageDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_contract(self) -> MethodologyUseDefinition:
        allowed = set(self.allowed_decisions)
        blocked = set(self.execution_blocking_decisions)
        if len(allowed) != len(self.allowed_decisions):
            raise ValueError("allowed_decisions must not contain duplicates")
        if not blocked.issubset(allowed):
            raise ValueError(
                "execution_blocking_decisions must be a subset of allowed_decisions"
            )
        return self


class MethodologyRulesDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = Field(min_length=1, max_length=40)
    criteria_version: str = Field(min_length=1, max_length=80)
    source_family: str = Field(min_length=2, max_length=80)
    source_adapter: str = Field(min_length=2, max_length=80)
    executable: bool
    required_criteria: list[MethodologyCriterionDefinition] = Field(min_length=1)
    use_cases: list[MethodologyUseDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_keys_and_references(self) -> MethodologyRulesDefinition:
        criterion_keys = [item.key for item in self.required_criteria]
        if len(criterion_keys) != len(set(criterion_keys)):
            raise ValueError("required_criteria keys must be unique")
        use_keys = [item.key for item in self.use_cases]
        if len(use_keys) != len(set(use_keys)):
            raise ValueError("use_cases keys must be unique")
        known = set(criterion_keys)
        unknown = {
            key for item in self.use_cases for key in item.criterion_keys if key not in known
        }
        if unknown:
            raise ValueError(f"use_cases reference unknown criteria: {sorted(unknown)}")
        return self

    @property
    def criteria_hash(self) -> str:
        payload = [
            item.model_dump(mode="json")
            for item in self.required_criteria
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class MethodologyEvidenceRequirements(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = Field(min_length=1, max_length=40)
    mandatory_source_categories: list[str] = Field(min_length=1)
    minimum_evidence_completeness: float = Field(ge=0, le=1)
    maximum_source_age_days: int = Field(ge=1, le=3650)
    critical_missing_fields: list[str] = Field(min_length=1)
    contradiction_policy: Literal[
        "block_any_unresolved",
        "allow_acknowledged_noncritical",
    ]
    review_cadence_days: int = Field(ge=1, le=3650)


class CriterionDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    outcome: CriterionOutcome
    reviewer_explanation: str = Field(default="", max_length=5000)


class UseCoverageDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    decision: UseCoverageDecision
    reason: str = Field(min_length=10, max_length=5000)
    scope: str | None = Field(default=None, max_length=1000)
