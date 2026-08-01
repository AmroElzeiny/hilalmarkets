from pathlib import Path

import pytest

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.targets.ui import UITarget


class FakeLocator:
    def __init__(self, count: int):
        self._count = count

    async def count(self) -> int:
        return self._count


class FakePage:
    def __init__(self, counts: dict[str, int]):
        self.counts = counts

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.counts.get(selector, 0))


class FakeResponse:
    def __init__(self, *, headers: dict[str, str]):
        self.url = "http://testserver/api/v1/dashboard/setup-chat/sessions/chat/messages"
        self.status = 503
        self.headers = headers

    async def json(self) -> dict[str, object]:
        return {"error": {"error_code": "TARGET_EMPTY_RESPONSE"}}


async def test_ui_adapter_accepts_only_the_authenticated_setup_chat_marker(tmp_path: Path):
    settings = Settings(_env_file=None)
    target = UITarget(settings, tmp_path)
    target.page = FakePage({settings.target_ui_expected_marker: 1})  # type: ignore[assignment]
    await target._verify_authenticated_setup_chat()


async def test_ui_adapter_refuses_public_support_pages(tmp_path: Path):
    settings = Settings(_env_file=None)
    support_marker = '[data-evaluator-target="public-support-chat"]'
    target = UITarget(settings, tmp_path)
    target.page = FakePage(  # type: ignore[assignment]
        {
            settings.target_ui_expected_marker: 1,
            support_marker: 1,
        }
    )
    with pytest.raises(RuntimeError, match="public support-agent page"):
        await target._verify_authenticated_setup_chat()


async def test_ui_adapter_reads_lowercase_fault_evidence_header(tmp_path: Path):
    settings = Settings(_env_file=None)
    target = UITarget(settings, tmp_path)

    await target._capture_response(
        FakeResponse(headers={"x-hm-eval-fault-applied": "empty_once"})
    )

    assert target.last_status == 503
    assert target.last_evaluator_fault_applied == "empty_once"
    assert target.last_api_json == {"error": {"error_code": "TARGET_EMPTY_RESPONSE"}}


def test_ui_fault_route_pattern_matches_playwright_nested_urls() -> None:
    settings = Settings(_env_file=None)

    assert settings.target_ui_chat_api_pattern == (
        "**/api/v1/dashboard/setup-chat/sessions/*/messages"
    )
