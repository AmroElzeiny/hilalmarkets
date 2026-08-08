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


def _settings(*, waitlist_mode: bool = False) -> Settings:
    # The corpus was reviewed against the open product, where "how do I create an
    # account?" is answered from the account-entry fact. Pre-launch that fact is
    # replaced by the waitlist one, so the two modes are asserted separately rather than
    # by loosening what either of them has to satisfy.
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        sharia_default_methodology_code=None,
        public_waitlist_mode=waitlist_mode,
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


def test_pre_launch_corpus_answers_account_and_price_questions_from_the_home_facts() -> None:
    """Every reviewed question still has an answer while the site is pre-launch.

    The two routes that only exist for an open product disappear, so the questions that
    pointed at them must land on the waitlist fact instead of falling through to a
    knowledge gap. A visitor asking how to sign up must never be told nothing is known.
    """

    service = PublicKnowledgeService(_settings(waitlist_mode=True))
    routes = {entry.route_id for entry in service.entries}
    assert "dashboard_entry" not in routes
    assert "pricing" not in routes

    for group in _groups():
        if group["category"] not in {"account_and_beta", "pricing_and_billing"}:
            continue
        for question in group["questions"]:
            # "refused" is a correct outcome for the questions that ask about private
            # account data; the boundary is unchanged, only the next step it offers.
            _status, message, _score, _sources, routes, _gap = service.answer(question)
            for forbidden in ("sign in", "sign up", "create an account", "log in"):
                assert forbidden not in message.casefold(), (question, forbidden)
            assert "dashboard_entry" not in routes, question


#: The words that name a place an anonymous visitor cannot reach. The assistant may
#: describe the dashboard as a product feature; it must not tell this reader to open one.
_FORBIDDEN_INSTRUCTIONS = (
    "sign in",
    "sign up",
    "log in",
    "create an account",
    "open plan & billing",
    "plan & billing",
    "pricing page",
    "checkout",
)


def test_pre_launch_assistant_never_sends_a_visitor_to_an_account_page() -> None:
    """The regression: the assistant read the raw Help Center, not the visible one.

    The Help Center page had already stopped saying "Open Plan & Billing in the
    dashboard". The assistant kept saying it, because it built its facts from
    HELP_CATEGORIES directly. The page and the assistant were describing two different
    products. Both now read public_help_categories, so they cannot drift apart again.

    Parametrised over the whole family of ways a person asks this, not the one example.
    """

    service = PublicKnowledgeService(_settings(waitlist_mode=True))

    # The launched answer is gone from the assistant's facts, not merely outranked.
    answers = {entry.title: entry.answer for entry in service.entries}
    assert "Where do I manage my plan?" not in answers
    assert "What does Hilal Markets cost?" in answers

    questions = (
        "Where do I manage my plan?",
        "How do I sign up?",
        "Where do I sign in?",
        "How much does Hilal Markets cost?",
        "What are your prices?",
        "How do I create an account?",
        "How do I get access to Hilal Markets?",
        "Can I start a free trial?",
        "Where is the pricing page?",
        "How do I pay for Hilal Markets?",
    )

    for question in questions:
        _status, message, _score, _sources, routes, _gap = service.answer(question)
        lowered = message.casefold()
        for forbidden in _FORBIDDEN_INSTRUCTIONS:
            assert forbidden not in lowered, (question, forbidden, message)
        assert "dashboard_entry" not in routes, question
        assert "pricing" not in routes, question


def test_launched_assistant_still_answers_the_plan_question_from_the_dashboard() -> None:
    """Turning the switch off gives the launched answer back, unchanged."""

    service = PublicKnowledgeService(_settings())
    answers = {entry.title: entry.answer for entry in service.entries}

    assert "Where do I manage my plan?" in answers
    assert "Open Plan & Billing in the dashboard" in answers["Where do I manage my plan?"]
    assert "What does Hilal Markets cost?" not in answers


def test_public_support_safety_corpus_is_enforced_before_ai_generation() -> None:
    service = PublicKnowledgeService(_settings())
    refusal_group = next(
        group for group in _groups() if group["category"] == "safety_refusals"
    )

    for question in refusal_group["questions"]:
        result = service.boundary_answer(question, authenticated=False)
        assert result is not None, question
        assert result[0] == "refused", question
