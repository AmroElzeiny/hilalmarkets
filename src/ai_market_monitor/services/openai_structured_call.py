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
from ai_market_monitor.services.provider_reliability import ProviderCallError
from ai_market_monitor.services.provider_runtime import provider_request


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
        usage: dict[str, Any] | None = None,
        candidate: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.stage = stage
        self.details = details
        self.usage = dict(usage or {})
        # Sanitized structured candidate used only by the one bounded repair call.
        self.candidate = dict(candidate) if isinstance(candidate, dict) else None


def estimate_structured_call_cost(
    settings: Settings,
    *,
    schema_model: type[BaseModel],
    instructions: str,
    payload: dict[str, Any],
    model: str,
    max_output_tokens: int,
    service_tier: str = "default",
) -> float:
    """Pessimistic reservation used before a provider attempt, including retries."""

    pricing = (
        settings.openai_fast_model_pricing_usd_per_million.get(model)
        if service_tier in {"fast", "priority"}
        else settings.openai_model_pricing_usd_per_million.get(model)
    )
    if not pricing:
        raise StructuredCallError(
            "SETUP_AGENT_MODEL_PRICING_UNAVAILABLE",
            "The selected model has no configured cost contract.",
            stage="provider",
        )
    estimated_input_tokens = max(
        1,
        (
            len(instructions)
            + len(json.dumps(payload, ensure_ascii=False))
            + len(json.dumps(strict_json_schema(schema_model), ensure_ascii=False))
        )
        // 4,
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
    fragments: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if (
                part.get("type") in {"output_text", "text"}
                and isinstance(text, str)
                and text
            ):
                fragments.append(text)
    combined = "".join(fragments)
    if combined.strip():
        return combined
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
    service_tier: str | None = None,
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
            "TARGET_PROVIDER_NOT_CONFIGURED",
            "Setup interpretation is temporarily unavailable. Your draft is unchanged.",
            retryable=True,
            stage=stage,
        )
    pricing = (
        settings.openai_fast_model_pricing_usd_per_million.get(model)
        if service_tier in {"fast", "priority"}
        else settings.openai_model_pricing_usd_per_million.get(model)
    )
    if estimated_cost_limit is not None and not pricing:
        raise StructuredCallError(
            "SETUP_AGENT_MODEL_PRICING_UNAVAILABLE",
            "The selected model has no configured cost contract.",
            stage=stage,
        )
    estimated_cost = (
        estimate_structured_call_cost(
            settings,
            schema_model=schema_model,
            instructions=instructions,
            payload=payload,
            model=model,
            max_output_tokens=max_output_tokens,
            service_tier=service_tier or "default",
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
    if service_tier is not None:
        request["service_tier"] = service_tier
    api_key = (
        settings.openai_api_key.get_secret_value().strip()
        if settings.openai_api_key is not None
        else ""
    )
    if not api_key:
        raise StructuredCallError(
            "TARGET_PROVIDER_NOT_CONFIGURED",
            "Setup interpretation is temporarily unavailable. Your draft is unchanged.",
            retryable=True,
            stage=stage,
        )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    usage: dict[str, Any] = {}
    raw_output = ""
    try:
        response_payload = consume_evaluator_llm_fault()
        if response_payload is None:
            response = await provider_request(
                settings,
                "POST",
                f"{str(settings.openai_base_url).rstrip('/')}/responses",
                provider="openai",
                operation="responses",
                # One paid answer per turn: this call is not repeated.
                retry=False,
                model=model,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else settings.openai_timeout_seconds
                ),
                # The turn owns the clock. Retrying past the caller's own timeout would
                # buy a paid answer that arrives after the turn has already been
                # abandoned, so the whole guarded call fits inside the one bound.
                deadline_seconds=float(
                    timeout_seconds
                    if timeout_seconds is not None
                    else settings.openai_timeout_seconds
                ),
                # ``store=false`` above means the provider keeps nothing, so a repeat is
                # a fresh generation rather than a duplicated record. It still costs
                # money, which is why the deadline, not optimism, bounds it.
                mutation_committed=False,
                transport=transport,
                headers=headers,
                json=request,
            )
            response.raise_for_status()
            response_payload = response.json()
        usage = dict(response_payload.get("usage") or {})
        returned_service_tier = response_payload.get("service_tier")
        if isinstance(returned_service_tier, str) and returned_service_tier:
            usage["_setup_service_tier"] = returned_service_tier
        raw_output = response_output_text(response_payload)
        parsed = schema_model.model_validate_json(raw_output)
    except ProviderCallError as exc:
        # The shared circuit breaker refused before anything was sent. This used to be a
        # second breaker living inside the agent, with its own Redis coordination and its
        # own state — so one code path could believe the provider was down while another
        # kept calling it. There is one breaker now, and this is how its refusal reaches
        # the turn in the vocabulary the turn already understands.
        raise StructuredCallError(
            "SETUP_AGENT_CIRCUIT_OPEN",
            "Setup interpretation is temporarily unavailable. Your draft is unchanged.",
            retryable=True,
            stage=stage,
        ) from exc
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
        details: list[str] = []
        request_id = exc.response.headers.get("x-request-id", "").strip()
        if request_id:
            details.append(f"provider_request_id:{request_id[:120]}")
        try:
            error_payload = exc.response.json().get("error")
        except (ValueError, AttributeError):
            error_payload = None
        if isinstance(error_payload, dict):
            error_type = str(error_payload.get("type") or "").strip()
            error_code = str(error_payload.get("code") or "").strip()
            if error_type:
                details.append(f"provider_error_type:{error_type[:120]}")
            if error_code:
                details.append(f"provider_error_code:{error_code[:120]}")
        raise StructuredCallError(
            (
                "TARGET_HTTP_429"
                if status == 429
                else "TARGET_PROVIDER_AUTH_FAILURE"
                if status in {401, 403}
                else "TARGET_HTTP_409"
                if status == 409
                else "TARGET_HTTP_5XX"
                if status >= 500
                else "TARGET_PROVIDER_ERROR"
            ),
            "The interpreter could not complete this turn.",
            retryable=status in {401, 403, 429} or status >= 500,
            stage=stage,
            details=tuple(details),
        ) from exc
    except ValidationError as exc:
        error_types = {str(item.get("type") or "") for item in exc.errors()}
        try:
            candidate = json.loads(raw_output)
        except (TypeError, ValueError):
            candidate = None
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
                    + ":"
                    + str(item.get("msg") or "")
                )[:300]
                for item in exc.errors()[:12]
            ),
            usage=usage,
            candidate=candidate if isinstance(candidate, dict) else None,
        ) from exc
    except (KeyError, json.JSONDecodeError) as exc:
        raise StructuredCallError(
            "TARGET_INVALID_JSON",
            "The interpreter returned invalid JSON.",
            stage=stage,
            usage=usage,
            candidate=None,
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
            usage=usage,
        ) from exc
    return parsed, usage
