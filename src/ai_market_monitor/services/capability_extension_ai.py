from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.capability_extensions import (
    MechanicDraft,
    MechanicRepair,
    MechanicReview,
)


class CapabilityExtensionAIError(RuntimeError):
    pass


class CapabilityExtensionAI:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.last_usage: dict[str, Any] = {}

    async def draft(
        self,
        *,
        prompt: str,
        history: list[dict[str, str]],
        timeframe: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str = "default",
    ) -> MechanicDraft:
        payload = {
            "user_request": prompt,
            "conversation_history": history[-30:],
            "requested_timeframe": timeframe,
            "allowed_language": _dsl_reference(),
        }
        result = await self._structured_call(
            model=model or self.settings.capability_extension_draft_model,
            reasoning_effort=(
                reasoning_effort or self.settings.capability_extension_draft_reasoning_effort
            ),
            service_tier=service_tier,
            schema_name="traceedge_mechanic_draft",
            schema=_draft_schema(),
            instructions=_draft_instructions(),
            payload=payload,
        )
        return MechanicDraft.model_validate(result)

    async def review(
        self,
        *,
        prompt: str,
        history: list[dict[str, str]],
        draft: MechanicDraft,
        build_log: list[dict[str, Any]],
        market_report: dict[str, Any],
        reasoning_effort: str,
        service_tier: str,
        model: str | None = None,
    ) -> MechanicReview:
        result = await self._structured_call(
            model=model or self.settings.capability_extension_review_model,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
            schema_name="traceedge_mechanic_review",
            schema=_review_schema(),
            instructions=_review_instructions(),
            payload={
                "original_user_request": prompt,
                "conversation_history": history[-30:],
                "generated_mechanic": draft.model_dump(mode="json"),
                "build_log": build_log[-50:],
                "market_report": market_report,
                "allowed_language": _dsl_reference(),
            },
        )
        return MechanicReview.model_validate(result)

    async def repair(
        self,
        *,
        prompt: str,
        history: list[dict[str, str]],
        draft: MechanicDraft,
        review: MechanicReview,
        build_log: list[dict[str, Any]],
        reasoning_effort: str = "low",
    ) -> MechanicRepair:
        result = await self._structured_call(
            model=self.settings.capability_extension_implementation_model,
            reasoning_effort=reasoning_effort,
            service_tier=self.settings.capability_extension_repair_service_tier,
            schema_name="traceedge_mechanic_repair",
            schema=_repair_schema(),
            instructions=_repair_instructions(),
            payload={
                "original_user_request": prompt,
                "conversation_history": history[-30:],
                "current_mechanic": draft.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "build_log": build_log[-50:],
                "allowed_language": _dsl_reference(),
            },
        )
        return MechanicRepair.model_validate(result)

    async def _structured_call(
        self,
        *,
        model: str,
        reasoning_effort: str,
        service_tier: str,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.settings.openai_api_key is None:
            raise CapabilityExtensionAIError("OPENAI_API_KEY is not configured")
        request: dict[str, Any] = {
            "model": model,
            "store": False,
            "max_output_tokens": 6000,
            "reasoning": {"effort": reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    # The recursive AST is validated by the stricter local allowlist after output.
                    "strict": False,
                    "schema": schema,
                }
            },
            "instructions": instructions,
            "input": json.dumps(payload, sort_keys=True),
        }
        if service_tier == "flex":
            request["service_tier"] = "flex"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        timeout = (
            self.settings.capability_extension_flex_timeout_seconds
            if service_tier == "flex"
            else max(120, self.settings.openai_timeout_seconds)
        )
        last_error: Exception | None = None
        for attempt in range(1, self.settings.capability_extension_ai_max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=str(self.settings.openai_base_url).rstrip("/"),
                    timeout=timeout,
                    transport=self.transport,
                ) as client:
                    response = await client.post("/responses", headers=headers, json=request)
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                body = response.json()
                self.last_usage = dict(body.get("usage") or {})
                return json.loads(_output_text(body))
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if (
                    exc.response.status_code not in {408, 429, 500, 502, 503, 504}
                    or attempt >= self.settings.capability_extension_ai_max_attempts
                ):
                    break
                retry_after = exc.response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else 2 ** (attempt - 1)
                )
                await asyncio.sleep(min(10, delay))
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                break
        raise CapabilityExtensionAIError(
            str(last_error or "OpenAI mechanic request failed")
        ) from last_error


def _draft_instructions() -> str:
    return (
        "You design one deterministic crypto spot monitoring mechanic from the user's stated idea. "
        "Return only the strict schema. Preserve the user's logic exactly; do not add trade "
        "advice, "
        "profit assumptions, entry, stop, target, or extra filters. Use only the supplied bounded "
        "HilalMarkets JSON expression language and OHLCV data. Never emit Python, JavaScript, SQL, "
        "network calls, file access, loops, imports, randomness, current market values, or "
        "provider claims. If a threshold is stated, preserve it. If a required threshold is "
        "absent, expose a parameter with a conservative visible default and list the assumption. "
        "The expression must "
        "return a boolean. Explain proof in beginner-safe monitoring language. Proof templates "
        "may use only {state}; the exact AST and resolved parameters are attached separately."
    )


def _review_instructions() -> str:
    return (
        "Act as an independent HilalMarkets mechanic reviewer. Compare the original conversation, "
        "generated JSON AST, deterministic build log, and real market-test statistics. Decide "
        "whether failure comes from user logic, implementation, market data, or ambiguity. Do not "
        "change the user's initial logic to manufacture candidates. Flag always-true, "
        "always-false, look-ahead, missing-data, threshold-direction, timeframe, and proof "
        "mismatches. If candidates exist but no delivery was queued, classify the issue as "
        "delivery rather than changing the mechanic. A rare user idea can legitimately have "
        "zero candidates; classify it as "
        "user_logic rather than relaxing "
        "it. Recommend implementation-only corrections when possible."
    )


def _repair_instructions() -> str:
    return (
        "Implement only the independent review's implementation corrections in the bounded JSON "
        "AST. Preserve the original user logic, thresholds, direction, timeframe, and required "
        "conditions. Do not relax or tighten the strategy merely to create candidates. Never emit "
        "runtime code or unsupported operations. Set user_logic_changed=true if any requested fix "
        "would alter meaning and place that change in deferred_changes instead of applying it."
    )


def _dsl_reference() -> dict[str, Any]:
    return {
        "boolean_ops": [
            "and",
            "or",
            "not",
            "gt",
            "gte",
            "lt",
            "lte",
            "eq",
            "crosses_above",
            "crosses_below",
        ],
        "value_ops": [
            "constant",
            "parameter",
            "field",
            "indicator",
            "aggregate",
            "previous_period",
            "candle_metric",
            "add",
            "subtract",
            "multiply",
            "divide",
            "abs",
            "min",
            "max",
        ],
        "fields": ["open", "high", "low", "close", "volume", "quote_volume"],
        "indicators": ["sma", "ema", "rsi", "atr", "vwap", "average_volume", "bollinger"],
        "aggregates": ["highest", "lowest", "mean", "sum"],
        "previous_periods": ["day", "week", "month"],
        "candle_metrics": [
            "body_percent",
            "range_percent",
            "upper_wick_percent",
            "lower_wick_percent",
            "bullish",
            "bearish",
            "doji",
        ],
        "candle_metric_options": {
            "offset": "0 to 2000 closed candles ago",
            "doji_threshold_percent": "Use threshold_percent from 0 to 100 only with doji",
        },
    }


def _output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for item in payload.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if content.get("type") in {"output_text", "text"}:
                return str(content.get("text") or "")
    raise CapabilityExtensionAIError("OpenAI response did not contain structured output")


def _draft_schema() -> dict[str, Any]:
    return MechanicDraft.model_json_schema()


def _review_schema() -> dict[str, Any]:
    return MechanicReview.model_json_schema()


def _repair_schema() -> dict[str, Any]:
    return MechanicRepair.model_json_schema()
