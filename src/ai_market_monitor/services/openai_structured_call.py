"""One bounded, structured call to the Responses API, and one error vocabulary.

Both the Setup Agent and the legacy patch extractor need the same three things: a
strict-schema request, a hard cap of one call, and a failure classified as transport
or schema rather than as a strategy defect. Writing that twice is how the two drifted
apart before — one knew about dropped streams, the other reported them as compile
failures.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.agent_tools import strict_json_schema
from ai_market_monitor.services.ai_setup_evaluator_control import consume_evaluator_llm_fault


class StructuredCallError(ValueError):
    """A bounded call that could not produce a valid structured answer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        stage: str = "provider",
        details: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.stage = stage
        self.details = details


def estimate_structured_call_cost(
    settings: Settings,
    *,
    instructions: str,
    payload: dict[str, Any],
    model: str,
    max_output_tokens: int,
) -> float:
    """Pessimistic reservation used before a provider attempt, including retries."""

    pricing = settings.openai_model_pricing_usd_per_million.get(model)
    if not pricing:
        raise StructuredCallError(
            "SETUP_AGENT_MODEL_PRICING_UNAVAILABLE",
            "The selected model has no configured cost contract.",
            stage="provider",
        )
    estimated_input_tokens = max(
        1,
        (len(instructions) + len(json.dumps(payload, ensure_ascii=False))) // 4,
    )
    return (
        estimated_input_tokens * float(pricing.get("input", 0))
        + max_output_tokens * float(pricing.get("output", 0))
    ) / 1_000_000


def is_dns_failure(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__}: {current}".casefold()
        if any(
            marker in text
            for marker in (
                "getaddrinfo failed",
                "name or service not known",
                "temporary failure in name resolution",
                "name resolution",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def response_output_text(payload: dict[str, Any]) -> str:
    """The single structured answer out of a Responses payload."""

    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            text = part.get("text")
            if (
                part.get("type") in {"output_text", "text"}
                and isinstance(text, str)
                and text.strip()
            ):
                return text
    raise ValueError("the response carried no structured output")


async def structured_call[ModelT: BaseModel](
    settings: Settings,
    *,
    schema_model: type[ModelT],
    schema_name: str,
    instructions: str,
    payload: dict[str, Any],
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    timeout_seconds: int | float | None = None,
    estimated_cost_limit: float | None = None,
    stage: str = "provider",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[ModelT, dict[str, Any]]:
    """Make exactly one structured call and return ``(parsed, usage)``.

    ``store=false`` and non-streaming on purpose: a stored transcript is not needed,
    and a partially streamed body is a failure mode with no benefit here.
    """

    if settings.openai_api_key is None:
        raise StructuredCallError(
            "OPENAI_NOT_CONFIGURED",
            "This setup needs interpretation, but the AI provider is unavailable.",
            stage=stage,
        )
    pricing = settings.openai_model_pricing_usd_per_million.get(model)
    if estimated_cost_limit is not None and not pricing:
        raise StructuredCallError(
            "SETUP_AGENT_MODEL_PRICING_UNAVAILABLE",
            "The selected model has no configured cost contract.",
            stage=stage,
        )
    estimated_cost = (
        estimate_structured_call_cost(
            settings,
            instructions=instructions,
            payload=payload,
            model=model,
            max_output_tokens=max_output_tokens,
        )
        if pricing
        else 0
    )
    if estimated_cost_limit is not None and estimated_cost > estimated_cost_limit:
        raise StructuredCallError(
            "SETUP_AGENT_COST_LIMIT",
            "This turn would exceed the configured AI cost limit.",
            stage=stage,
        )
    request = {
        "model": model,
        "store": False,
        "stream": False,
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": strict_json_schema(schema_model),
            }
        },
        "instructions": instructions,
        "input": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    try:
        response_payload = consume_evaluator_llm_fault()
        if response_payload is None:
            async with httpx.AsyncClient(
                base_url=str(settings.openai_base_url).rstrip("/"),
                timeout=httpx.Timeout(
                    timeout_seconds
                    if timeout_seconds is not None
                    else settings.openai_timeout_seconds
                ),
                transport=transport,
            ) as client:
                response = await client.post("/responses", headers=headers, json=request)
            response.raise_for_status()
            response_payload = response.json()
        usage = dict(response_payload.get("usage") or {})
        parsed = schema_model.model_validate_json(response_output_text(response_payload))
    except httpx.ConnectTimeout as exc:
        raise StructuredCallError(
            "TARGET_CONNECT_TIMEOUT",
            "The interpreter could not be reached in time.",
            retryable=True,
            stage=stage,
        ) from exc
    except httpx.ReadTimeout as exc:
        raise StructuredCallError(
            "TARGET_READ_TIMEOUT",
            "The interpreter timed out.",
            retryable=True,
            stage=stage,
        ) from exc
    except httpx.RemoteProtocolError as exc:
        raise StructuredCallError(
            "TARGET_PARTIAL_STREAM",
            "The interpreter disconnected before completing its response.",
            retryable=True,
            stage=stage,
        ) from exc
    except httpx.ConnectError as exc:
        raise StructuredCallError(
            (
                "TARGET_DNS_RESOLUTION_FAILURE"
                if is_dns_failure(exc)
                else "TARGET_CONNECTION_REFUSED"
            ),
            "The interpreter could not be reached.",
            retryable=True,
            stage=stage,
        ) from exc
    except httpx.TimeoutException as exc:
        raise StructuredCallError(
            "TARGET_TOTAL_TIMEOUT",
            "The interpreter exceeded its bounded turn time.",
            retryable=True,
            stage=stage,
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise StructuredCallError(
            (
                "TARGET_HTTP_429"
                if status == 429
                else "TARGET_HTTP_409"
                if status == 409
                else "TARGET_HTTP_5XX"
                if status >= 500
                else "TARGET_PROVIDER_ERROR"
            ),
            "The interpreter could not complete this turn.",
            retryable=status == 429 or status >= 500,
            stage=stage,
        ) from exc
    except ValidationError as exc:
        error_types = {str(item.get("type") or "") for item in exc.errors()}
        raise StructuredCallError(
            (
                "TARGET_INVALID_JSON"
                if "json_invalid" in error_types
                else "TARGET_SCHEMA_VALIDATION"
            ),
            "The interpreter returned an answer that did not match its schema.",
            stage=stage,
            details=tuple(
                (
                    ".".join(map(str, item.get("loc") or ("root",)))
                    + ":"
                    + str(item.get("type") or "validation_error")
                )[:300]
                for item in exc.errors()[:12]
            ),
        ) from exc
    except (KeyError, json.JSONDecodeError) as exc:
        raise StructuredCallError(
            "TARGET_INVALID_JSON",
            "The interpreter returned invalid JSON.",
            stage=stage,
        ) from exc
    except ValueError as exc:
        raise StructuredCallError(
            (
                "TARGET_EMPTY_RESPONSE"
                if "no structured output" in str(exc).casefold()
                else "TARGET_INVALID_JSON"
            ),
            "The interpreter did not return a usable structured answer.",
            stage=stage,
        ) from exc
    return parsed, usage
