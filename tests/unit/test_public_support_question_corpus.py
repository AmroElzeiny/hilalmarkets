import json
from pathlib import Path

from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.public_chat import PUBLIC_ROUTE_PATHS, PublicKnowledgeService

CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "public_support_question_corpus.json"
)
AUTHENTICATED_READ_TOOLS = {
    "account_state",
    "telegram_status",
    "watch_plan_summary",
    "recent_alerts",
    "entitlement_usage",
    "screened_watchlist",
    "public_passport",
}


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_default_methodology_code=None,
    )


def _groups() -> list[dict]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_public_support_corpus_records_at_least_two_hundred_reviewed_expectations() -> None:
    groups = _groups()
    cases = [
        {**group, "question": question}
        for group in groups
        for question in group["questions"]
    ]

    assert len(cases) >= 200
    assert len({case["question"].casefold() for case in cases}) == len(cases)
    assert len({case["category"] for case in cases}) >= 20
    assert {case["expected_behavior"] for case in cases} == {
        "answer",
        "authenticated_tool",
        "refusal",
        "escalate",
    }
    for case in cases:
        assert case["question"].strip()
        assert case["expected_route"] in PUBLIC_ROUTE_PATHS
        if case["expected_behavior"] == "authenticated_tool":
            assert case["expected_tool"] in AUTHENTICATED_READ_TOOLS
        else:
            assert case["expected_tool"] is None

    languages = {group.get("language", "en") for group in groups}
    assert {"en", "ar", "ar-EG", "arabizi", "mixed", "en-typo"} <= languages
    multilingual_cases = [
        question
        for group in groups
        if group.get("language", "en") != "en"
        for question in group["questions"]
    ]
    assert len(multilingual_cases) >= 50


def test_public_support_corpus_routes_have_grounded_server_content() -> None:
    service = PublicKnowledgeService(_settings())
    supported_routes = {entry.route_id for entry in service.entries} | {"contact"}

    for group in _groups():
        if group["expected_behavior"] == "answer":
            assert group["expected_route"] in supported_routes
        elif group["expected_behavior"] == "authenticated_tool":
            assert group["expected_route"] in PUBLIC_ROUTE_PATHS


def test_public_support_safety_corpus_is_enforced_before_ai_generation() -> None:
    service = PublicKnowledgeService(_settings())
    refusal_group = next(
        group for group in _groups() if group["category"] == "safety_refusals"
    )

    for question in refusal_group["questions"]:
        result = service.boundary_answer(question, authenticated=False)
        assert result is not None, question
        assert result[0] == "refused", question
