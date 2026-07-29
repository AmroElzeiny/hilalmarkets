from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Settings
from .failures import (
    backoff_delay,
    classify_http_status,
    is_retryable,
    parse_retry_after,
)

#: How much of an unparseable body to keep. Enough to see where it was cut off and
#: what produced it, short enough that a run's artifacts stay readable.
MALFORMED_BODY_EXCERPT_CHARS = 2000


class MalformedAIResponse(ValueError):
    """A response that could not be parsed, carrying the body that failed.

    The body is the only evidence of *who* produced the bad output. Discarding it left
    `TARGET_INVALID_JSON` unattributable: the same error is raised whether the chatbot
    under test or the grading model returned the truncated text.
    """

    def __init__(self, message: str, *, namespace: str, body: str) -> None:
        super().__init__(message)
        self.namespace = namespace
        self.body_excerpt = _sanitize_body(body)
        self.body_length = len(body or "")


def _sanitize_body(body: str) -> str:
    """A bounded excerpt with credentials removed, safe to write into a report."""
    text = (body or "").strip()
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}", "sk-***", text)
    text = re.sub(r"(?i)(authorization|api[_-]?key)\s*[:=]\s*\S+", r"\1=***", text)
    if len(text) <= MALFORMED_BODY_EXCERPT_CHARS:
        return text
    half = MALFORMED_BODY_EXCERPT_CHARS // 2
    return f"{text[:half]}\n…[{len(text) - MALFORMED_BODY_EXCERPT_CHARS} chars omitted]…\n{text[-half:]}"


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

    def _cost(
        self,
        usage: dict[str, Any],
        *,
        model: str,
        service_tier: str,
    ) -> float:
        inp = float(usage.get("input_tokens", 0))
        cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        out = float(usage.get("output_tokens", 0))
        uncached = max(0.0, inp - cached)
        pricing = self.settings.evaluator_pricing(model, service_tier)
        return (
            uncached * pricing["input"] + cached * pricing["cached_input"] + out * pricing["output"]
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
    def _parse_json(text: str, *, namespace: str = "") -> dict[str, Any]:
        original = text
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            # Keep the body. `model_version_drift-001` failed with
            # "Unterminated string … char 6422" and the text was discarded, so there
            # was no way to tell whether the chatbot or the grader had truncated it.
            raise MalformedAIResponse(
                f"{namespace or 'response'} was not valid JSON: {exc}",
                namespace=namespace,
                body=original,
            ) from exc
        if not isinstance(value, dict):
            raise MalformedAIResponse(
                f"{namespace or 'response'} was not a JSON object",
                namespace=namespace,
                body=original,
            )
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
            return AIResult(cached["data"], cached["raw"], cached["usage"], 0.0, True)
        body: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "reasoning": {"effort": reasoning},
            "service_tier": service_tier,
            "max_output_tokens": max_output_tokens,
            "prompt_cache_key": f"hm-chatbot-eval-{namespace}-v1",
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        response = await self.http.post("responses", json=body)
        if response.status_code == 400:
            # Compatibility fallback for models/accounts that reject JSON Schema formatting.
            body.pop("text", None)
            body["instructions"] += (
                "\nReturn one strict JSON object only, matching this schema: " + json.dumps(schema)
            )
            response = await self.http.post("responses", json=body)
        response.raise_for_status()
        raw = response.json()
        data = self._parse_json(self._extract_text(raw), namespace=namespace)
        usage = raw.get("usage") or {}
        result = AIResult(
            data,
            raw,
            usage,
            self._cost(usage, model=model, service_tier=service_tier),
        )
        if cacheable:
            self.cache.put(
                key, {"data": data, "raw": raw, "usage": usage, "cost_usd": result.cost_usd}
            )
        return result

    async def close(self) -> None:
        await self.http.aclose()


#: Attempts used when the caller does not say. Three was too few for a provider that
#: rate-limits under load: run `20260726T164155Z` exhausted its retries and finished
#: with zero cases, having spent the setup cost for nothing.
DEFAULT_RETRY_ATTEMPTS = 5


async def bounded_retry(coro_factory, attempts: int = DEFAULT_RETRY_ATTEMPTS):
    """Retry transient provider failures with exponential backoff and full jitter.

    Standing conditions — bad key, exhausted quota, malformed request — are raised on
    the first attempt. Retrying those only burns budget and delays the real answer.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text if exc.response is not None else ""
            failure_class = classify_http_status(
                exc.response.status_code,
                role="judge",
                body=body,
            )
            if not is_retryable(failure_class):
                raise
            last_error = exc
            if attempt + 1 < attempts:
                retry_after = parse_retry_after(exc.response.headers.get("retry-after"))
                await asyncio.sleep(backoff_delay(attempt + 1, retry_after=retry_after))
        except (
            httpx.TimeoutException,
            httpx.TransportError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                # The same jittered schedule as the HTTP path. A bare `2 ** attempt`
                # makes every concurrent worker retry on the same tick, which is how
                # a brief provider wobble turns into a synchronised stampede.
                await asyncio.sleep(backoff_delay(attempt + 1))
    raise last_error or RuntimeError("Unknown retry failure")
