from __future__ import annotations

import httpx

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.targets.backend import HilalMarketsBackendTarget


async def test_structured_409_remains_measurable_instead_of_becoming_transport_failure(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "chat-1"})
        return httpx.Response(
            409,
            json={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "Review the latest translation before approval.",
                        "payload": {},
                    }
                ],
                "evaluation_contract": {
                    "schema_version": "1",
                    "symbols": ["BTC/USDT"],
                },
                "error": {
                    "error_code": "setup_changed",
                    "request_id": "request-1",
                    "stage": "compile",
                    "retryable": False,
                    "message": "Review the latest translation before approval.",
                },
            },
        )

    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
        target_backend_base_url="https://target.test",
        target_backend_email="",
        target_backend_password="",
        target_session_cookie=None,
    )
    client = httpx.AsyncClient(
        base_url="https://target.test",
        transport=httpx.MockTransport(handler),
    )
    target = HilalMarketsBackendTarget(settings, client=client)
    try:
        await target.start("scenario-1", {})
        reply = await target.send("I approve", scenario_id="scenario-1")
    finally:
        await client.aclose()

    assert reply.status_code == 409
    assert reply.error is None
    assert reply.structured is not None
    assert reply.text == "Review the latest translation before approval."
