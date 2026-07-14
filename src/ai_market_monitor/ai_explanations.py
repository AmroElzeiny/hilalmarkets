from __future__ import annotations

import json
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings

PROHIBITED_CLAIMS = (
    "guaranteed profit",
    "guaranteed return",
    "win rate",
    "will make money",
    "risk free",
    "predict the market",
)


class OpenAISuggestionNarrator:
    """Rewords deterministic evidence without changing the proposed schema."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def narrate(
        self,
        *,
        action: str,
        deterministic_reason: str,
        diff: list[dict[str, Any]],
        bottleneck: dict[str, Any],
    ) -> str | None:
        if (
            not self.settings.openai_explanation_enabled
            or self.settings.ai_interpreter_provider != "openai"
            or self.settings.openai_api_key is None
        ):
            return None
        payload = {
            "model": self.settings.openai_model,
            "store": False,
            "max_output_tokens": 220,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "strategy_suggestion_wording",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "maxLength": 700},
                        },
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                }
            },
            "instructions": (
                "Rewrite the deterministic strategy-monitor suggestion in clear, cautious "
                "language. Do not add, remove, or change any rule, threshold, timeframe, "
                "market value, result, performance claim, or recommendation to trade. State "
                "that the change remains a draft requiring user approval. Return JSON only."
            ),
            "input": json.dumps(
                {
                    "action": action,
                    "deterministic_reason": deterministic_reason,
                    "validated_schema_diff": diff,
                    "bottleneck_evidence": bottleneck,
                },
                sort_keys=True,
                default=str,
            ),
        }
        headers = {
            "Authorization": (f"Bearer {self.settings.openai_api_key.get_secret_value()}"),
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=str(self.settings.openai_base_url).rstrip("/"),
                timeout=self.settings.openai_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post("/responses", headers=headers, json=payload)
            response.raise_for_status()
            message = str(json.loads(_output_text(response.json())).get("message") or "").strip()
        except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError):
            return None
        lowered = message.lower()
        if not message or any(claim in lowered for claim in PROHIBITED_CLAIMS):
            return None
        return message[:700]


def _output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise ValueError("OpenAI response did not contain output text")
