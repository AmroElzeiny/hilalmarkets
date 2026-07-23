from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SystemBrainAssistantHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class SystemBrainAssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=2, max_length=2000)
    history: list[SystemBrainAssistantHistoryItem] = Field(
        default_factory=list,
        max_length=10,
    )


class SystemBrainAssistantFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=800)
    severity: Literal["information", "attention", "critical"]
    evidence_ref: str = Field(min_length=1, max_length=240)


class SystemBrainAssistantAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=500)


class SystemBrainAssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=6000)
    findings: list[SystemBrainAssistantFinding] = Field(
        default_factory=list,
        max_length=8,
    )
    suggested_actions: list[SystemBrainAssistantAction] = Field(
        default_factory=list,
        max_length=6,
    )
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    model: str
    reasoning_effort: str
