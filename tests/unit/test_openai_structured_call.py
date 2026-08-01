from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.openai_structured_call import (
    StructuredCallError,
    response_output_text,
    structured_call,
)


class _StructuredAnswer(BaseModel):
    answer: str


def test_response_output_text_joins_all_non_streaming_message_fragments() -> None:
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"answer":"split'},
                    {"type": "output_text", "text": ' response"}'},
                ],
            }
        ]
    }

    assert response_output_text(payload) == '{"answer":"split response"}'


@pytest.mark.asyncio
async def test_structured_call_pins_and_records_requested_service_tier() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "service_tier": "default",
                "output_text": json.dumps({"answer": "ok"}),
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    result, usage = await structured_call(
        Settings(_env_file=None, openai_api_key="test-key"),
        schema_model=_StructuredAnswer,
        schema_name="test_structured_answer",
        instructions="Return the grounded answer.",
        payload={"request": "answer"},
        model="gpt-5.4-nano",
        reasoning_effort="low",
        max_output_tokens=512,
        service_tier="default",
        transport=httpx.MockTransport(handler),
    )

    assert result.answer == "ok"
    assert len(requests) == 1
    assert requests[0]["service_tier"] == "default"
    assert usage["_setup_service_tier"] == "default"


@pytest.mark.asyncio
async def test_schema_failure_preserves_the_provider_usage() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "service_tier": "priority",
                "output_text": json.dumps({"wrong_field": "not the schema"}),
                "usage": {"input_tokens": 21, "output_tokens": 3},
            },
        )

    with pytest.raises(StructuredCallError) as failure:
        await structured_call(
            Settings(_env_file=None, openai_api_key="test-key"),
            schema_model=_StructuredAnswer,
            schema_name="test_structured_answer",
            instructions="Return the grounded answer.",
            payload={"request": "answer"},
            model="gpt-5.4-nano",
            reasoning_effort="low",
            max_output_tokens=512,
            service_tier="fast",
            transport=httpx.MockTransport(handler),
        )

    assert failure.value.code == "TARGET_SCHEMA_VALIDATION"
    assert failure.value.usage == {
        "input_tokens": 21,
        "output_tokens": 3,
        "_setup_service_tier": "priority",
    }


@pytest.mark.asyncio
async def test_provider_failure_records_safe_identifiers_without_raw_message() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            headers={"x-request-id": "req-safe-123"},
            json={
                "error": {
                    "type": "server_error",
                    "code": "upstream_unavailable",
                    "message": "raw provider text must not be persisted",
                }
            },
        )

    with pytest.raises(StructuredCallError) as failure:
        await structured_call(
            Settings(_env_file=None, openai_api_key="test-key"),
            schema_model=_StructuredAnswer,
            schema_name="test_structured_answer",
            instructions="Return the grounded answer.",
            payload={"request": "answer"},
            model="gpt-5-mini",
            reasoning_effort="low",
            max_output_tokens=512,
            service_tier="default",
            transport=httpx.MockTransport(handler),
        )

    assert failure.value.code == "TARGET_HTTP_5XX"
    assert failure.value.details == (
        "provider_request_id:req-safe-123",
        "provider_error_type:server_error",
        "provider_error_code:upstream_unavailable",
    )
    assert "raw provider text" not in repr(failure.value.details)
