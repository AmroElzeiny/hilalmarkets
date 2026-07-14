from __future__ import annotations

import math
from string import Formatter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MechanicParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    type: Literal["number", "integer", "boolean"]
    description: str = Field(min_length=1, max_length=300)
    required: bool = True
    default: int | float | bool | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> MechanicParameterSpec:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum cannot exceed maximum")
        if self.required and self.default is None:
            raise ValueError("required mechanic parameters need a default resolved value")
        if self.default is not None:
            self._validate_value(self.default, label="default")
        return self

    def _validate_value(self, value: int | float | bool, *, label: str) -> None:
        if self.type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"parameter {self.name} {label} must be boolean")
            return
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"parameter {self.name} {label} must be numeric")
        if self.type == "integer" and not isinstance(value, int):
            raise ValueError(f"parameter {self.name} {label} must be an integer")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"parameter {self.name} {label} must be finite")
        if self.minimum is not None and numeric < self.minimum:
            raise ValueError(f"parameter {self.name} {label} is below its minimum")
        if self.maximum is not None and numeric > self.maximum:
            raise ValueError(f"parameter {self.name} {label} exceeds its maximum")


class MechanicDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=3, max_length=160)
    deterministic_definition: str = Field(min_length=20, max_length=1200)
    timeframe: str = Field(pattern=r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^1d$")
    parameters: list[MechanicParameterSpec] = Field(default_factory=list, max_length=30)
    resolved_parameters: dict[str, int | float | bool] = Field(default_factory=dict)
    expression: dict[str, Any]
    proof_template: str = Field(min_length=10, max_length=500)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    expected_frequency: Literal["rare", "occasional", "frequent", "unknown"] = "unknown"
    logic_fidelity_statement: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def validate_resolved_parameters(self) -> MechanicDraft:
        specs = {parameter.name: parameter for parameter in self.parameters}
        if len(specs) != len(self.parameters):
            raise ValueError("mechanic parameter names must be unique")
        unknown = sorted(set(self.resolved_parameters) - set(specs))
        if unknown:
            raise ValueError(f"resolved parameters are undeclared: {', '.join(unknown)}")
        missing = sorted(
            name
            for name, parameter in specs.items()
            if parameter.required and name not in self.resolved_parameters
        )
        if missing:
            raise ValueError(f"required parameters are unresolved: {', '.join(missing)}")
        for name, value in self.resolved_parameters.items():
            specs[name]._validate_value(value, label="resolved value")
        for _literal, field_name, format_spec, conversion in Formatter().parse(
            self.proof_template
        ):
            if field_name and field_name not in {"actual", "required", "state"}:
                raise ValueError(f"unsupported proof placeholder: {field_name}")
            if format_spec or conversion:
                raise ValueError("proof placeholders cannot use format specs or conversions")
        return self


class MechanicReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "repair", "needs_user_clarification", "reject"]
    failure_source: Literal[
        "none",
        "user_logic",
        "implementation",
        "market_data",
        "delivery",
        "ambiguous",
    ]
    preserves_user_logic: bool
    confidence: float = Field(ge=0, le=1)
    candidate_quality: Literal["balanced", "too_strict", "too_permissive", "unknown"]
    issues: list[str] = Field(default_factory=list, max_length=30)
    recommended_changes: list[str] = Field(default_factory=list, max_length=30)
    explanation: str = Field(min_length=10, max_length=1200)


class MechanicRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revised_draft: MechanicDraft
    changed_implementation_only: bool
    user_logic_changed: bool
    applied_changes: list[str] = Field(default_factory=list, max_length=30)
    deferred_changes: list[str] = Field(default_factory=list, max_length=30)


class MechanicCertificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)
    classification: Literal[
        "balanced",
        "too_strict",
        "too_permissive",
        "insufficient_data",
        "invalid",
    ]
