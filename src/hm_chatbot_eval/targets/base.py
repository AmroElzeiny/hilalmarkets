from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TargetReply:
    text: str
    latency_ms: float
    status_code: int | None = None
    structured: dict[str, Any] | None = None
    canonical_state: dict[str, Any] | None = None
    raw: Any = None
    raw_hash: str | None = None
    conversation_id: str | None = None
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)


class ChatTarget(ABC):
    kind = "base"

    @abstractmethod
    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None: ...

    @abstractmethod
    async def send(
        self, message: str, *, scenario_id: str, fault: str | None = None
    ) -> TargetReply: ...

    async def approve(self, *, scenario_id: str) -> TargetReply:
        """Execute the real authenticated approval action for the reviewed version."""

        raise NotImplementedError(f"{self.kind} target does not implement approval")

    @abstractmethod
    async def close(self) -> None: ...
