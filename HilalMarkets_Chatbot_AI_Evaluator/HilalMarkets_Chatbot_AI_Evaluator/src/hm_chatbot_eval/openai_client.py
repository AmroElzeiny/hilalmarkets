from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Settings


@dataclass
class AIResult:
    data: dict[str, Any]
    raw: dict[str, Any]
    usage: dict[str, Any]
    cost_usd: float
    cached: bool = False


class OpenAIResponsesClient:
    def __init__(self, settings: Settings, cache: ResponseCache):
        self.settings = settings
        self.cache = cache
        self.http = httpx.AsyncClient(
            base_url=settings.test_ai_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=settings.test_ai_timeout_seconds,
        )

    def _cost(self, usage: dict[str, Any]) -> float:
        inp = float(usage.get("input_tokens", 0))
        cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        out = float(usage.get("output_tokens", 0))
        uncached = max(0.0, inp - cached)
        return (
            uncached * self.settings.test_ai_input_usd_per_1m
            + cached * self.settings.test_ai_cached_input_usd_per_1m
            + out * self.settings.test_ai_output_usd_per_1m
        ) / 1_000_000

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        if isinstance(raw.get("output_text"), str):
            return raw["output_text"]
        texts: list[str] = []
        for item in raw.get("output", []):
            for content in item.get("content", []) if isinstance(item, dict) else []:
                text = content.get("text") if isinstance(content, dict) else None
                if text:
                    texts.append(text)
        return "\n".join(texts)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object from test AI")
        return value

    async def structured(
        self,
        *,
        namespace: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str,
        reasoning: str,
        service_tier: str,
        max_output_tokens: int,
        cacheable: bool = True,
    ) -> AIResult:
        cache_payload = {
            "instructions": instructions,
            "input": input_text,
            "schema": schema,
            "model": model,
            "reasoning": reasoning,
        }
        key = self.cache.key(namespace, cache_payload)
        if cacheable and (cached := self.cache.get(key)):
            return AIResult(cached["data"], cached["raw"], cached["usage"], cached["cost_usd"], True)
        body = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "reasoning": {"effort": reasoning},
            "service_tier": service_tier,
            "max_output_tokens": max_output_tokens,
            "prompt_cache_key": f"hm-chatbot-eval-{namespace}-v1",
            "store": False,
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }
        response = await self.http.post("responses", json=body)
        if response.status_code == 400:
            # Compatibility fallback for models/accounts that reject JSON Schema formatting.
            body.pop("text", None)
            body["instructions"] += "\nReturn one strict JSON object only, matching this schema: " + json.dumps(schema)
            response = await self.http.post("responses", json=body)
        response.raise_for_status()
        raw = response.json()
        data = self._parse_json(self._extract_text(raw))
        usage = raw.get("usage") or {}
        result = AIResult(data, raw, usage, self._cost(usage))
        if cacheable:
            self.cache.put(key, {"data": data, "raw": raw, "usage": usage, "cost_usd": result.cost_usd})
        return result

    async def close(self) -> None:
        await self.http.aclose()


async def bounded_retry(coro_factory, attempts: int = 3):
    last_error = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(2**attempt)
    raise last_error or RuntimeError("Unknown retry failure")
