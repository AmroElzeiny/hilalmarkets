from __future__ import annotations

import httpx
import pytest

from hm_chatbot_eval.openai_client import bounded_retry


def _http_error(status: int, body: str, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(status, text=body, headers=headers, request=request)
    return httpx.HTTPStatusError("request failed", request=request, response=response)


@pytest.mark.asyncio
async def test_transient_evaluator_rate_limit_retries_with_retry_after():
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(429, "rate limit reached", retry_after="0")
        return "recovered"

    assert await bounded_retry(operation, attempts=3) == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_quota_429_does_not_retry():
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise _http_error(429, "insufficient_quota")

    with pytest.raises(httpx.HTTPStatusError):
        await bounded_retry(operation, attempts=3)
    assert calls == 1
