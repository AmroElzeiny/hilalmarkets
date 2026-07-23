import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from ai_market_monitor.db.models import AIUsageEvent, User
from ai_market_monitor.db.models.enums import UserRole
from ai_market_monitor.schemas.system_brain import SystemBrainAssistantRequest
from ai_market_monitor.services.system_brain_assistant import (
    SystemBrainAssistantService,
    SystemBrainAssistantUnavailable,
)


class FakeResponsesClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.payloads: list[dict[str, Any]] = []

    async def create(
        self,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        assert timeout_seconds > 0
        self.payloads.append(payload)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def test_system_brain_assistant_uses_bounded_nano_low_contract(
    test_context,
    monkeypatch,
):
    settings = test_context["settings"]
    settings.openai_api_key = "test-openai-key"
    settings.system_brain_ai_enabled = True
    settings.system_brain_ai_model = "gpt-5.4-nano"
    settings.system_brain_ai_reasoning_effort = "low"
    response = {
        "output_text": json.dumps(
            {
                "answer": "One retained error needs technical review.",
                "findings": [
                    {
                        "title": "Retained failure",
                        "detail": "The stored run failed validation.",
                        "severity": "attention",
                        "evidence_ref": "run:test",
                    }
                ],
                "suggested_actions": [
                    {
                        "label": "Inspect the run",
                        "rationale": "Use the retained safe error detail.",
                    }
                ],
                "evidence_refs": ["run:test"],
                "limitations": ["No raw provider payload was supplied."],
                "model": "gpt-5.4-nano",
                "reasoning_effort": "low",
            }
        ),
        "usage": {
            "input_tokens": 120,
            "output_tokens": 70,
            "output_tokens_details": {"reasoning_tokens": 12},
        },
    }
    client = FakeResponsesClient(response)
    service = SystemBrainAssistantService(settings, client=client)

    async def bounded_context(_session, _question):
        return {
            "operational_errors": {
                "failed_runs": [{"ref": "run:test", "code": "validation"}]
            }
        }

    monkeypatch.setattr(service, "_context", bounded_context)
    async with test_context["session_factory"]() as session:
        admin = User(display_name="System Brain reviewer", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        result = await service.answer(
            session,
            admin_user_id=admin.id,
            request=SystemBrainAssistantRequest(
                message="What failed?",
                history=[],
            ),
        )
        await session.commit()
        usage = await session.scalar(
            select(AIUsageEvent).where(
                AIUsageEvent.operation == "system_brain_assistant"
            )
        )

    assert result.answer == "One retained error needs technical review."
    assert result.model == "gpt-5.4-nano"
    assert result.reasoning_effort == "low"
    payload = client.payloads[0]
    assert payload["model"] == "gpt-5.4-nano"
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert usage is not None
    assert usage.model == "gpt-5.4-nano"
    assert usage.reasoning_effort == "low"


async def test_system_brain_assistant_fails_closed_on_provider_error(
    test_context,
    monkeypatch,
):
    settings = test_context["settings"]
    settings.openai_api_key = "test-openai-key"
    settings.system_brain_ai_enabled = True
    client = FakeResponsesClient(httpx.ConnectError("provider unavailable"))
    service = SystemBrainAssistantService(settings, client=client)

    async def bounded_context(_session, _question):
        return {"cases": []}

    monkeypatch.setattr(service, "_context", bounded_context)
    async with test_context["session_factory"]() as session:
        admin = User(display_name="System Brain reviewer", role=UserRole.ADMIN)
        session.add(admin)
        await session.flush()
        with pytest.raises(
            SystemBrainAssistantUnavailable,
            match="validated grounded answer",
        ):
            await service.answer(
                session,
                admin_user_id=admin.id,
                request=SystemBrainAssistantRequest(
                    message="Diagnose the provider",
                    history=[],
                ),
            )
