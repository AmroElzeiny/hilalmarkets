import json
from pathlib import Path

import pytest

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter

CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "setup_chat_language_quality_corpus.jsonl"
)
LANGUAGES = {
    "english",
    "arabic",
    "egyptian_arabic",
    "arabizi",
    "mixed_arabic_english",
    "common_misspelling",
}


def _cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        ai_setup_simple_model="configured-simple",
        ai_setup_complex_model="configured-complex",
        ai_setup_simple_reasoning_effort="low",
        ai_setup_complex_reasoning_effort="medium",
    )


def _guided(prompt: str) -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


def _rules(group):
    for child in group.children:
        if child.node_type == "condition":
            yield child
        else:
            yield from _rules(child)


def _rule_for_capability(rules, capability_key: str):
    operand_names = {
        "rsi_threshold": "rsi",
        "volume_ratio": "volume_ratio",
        "previous_daily_low_sweep": "daily_low_swept",
    }
    for rule in rules:
        if rule.capability_key == capability_key:
            return rule
        if rule.left.name == operand_names.get(capability_key):
            return rule
    raise AssertionError(f"No compiled rule represented {capability_key}")


def test_language_quality_corpus_is_reviewed_and_dimension_complete() -> None:
    cases = _cases()

    assert len(cases) >= 18
    assert {case["language"] for case in cases} == LANGUAGES
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["prompt"].strip()
        assert case["review_note"].strip()
        assert isinstance(case["expected_capability_keys"], list)
        assert isinstance(case["expected_parameters"], dict)
        assert "expected_timeframe" in case
        assert "expected_direction" in case
        assert isinstance(case["expected_required"], dict)
        assert "expected_logic" in case
        assert isinstance(case["max_corrections_before_approval"], int)
        assert case["evaluation_mode"] in {"deterministic", "shadow"}


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["case_id"])
def test_language_quality_corpus_uses_configured_routing(case: dict) -> None:
    report = CapabilityResolver().resolve_prompt(case["prompt"])
    route = select_setup_model(
        _settings(),
        current_message=case["prompt"],
        history=case["history"],
        capability_context=report.to_dict(),
    )

    assert route.tier == case["expected_route"]
    assert route.model == f"configured-{case['expected_route']}"


@pytest.mark.parametrize(
    "case",
    [case for case in _cases() if case["evaluation_mode"] == "deterministic"],
    ids=lambda case: case["case_id"],
)
async def test_reviewed_deterministic_language_cases_compile_all_labeled_dimensions(
    case: dict,
) -> None:
    preview = await RuleBasedStrategyInterpreter().interpret(_guided(case["prompt"]))
    rules = list(_rules(preview.strategy.conditions))
    report = CapabilityResolver().resolve_prompt(case["prompt"])

    assert preview.activation_blocked is False
    assert set(case["expected_capability_keys"]).issubset(report.candidate_keys)
    assert preview.strategy.direction.value == case["expected_direction"]
    assert preview.strategy.conditions.operator.value == case["expected_logic"]
    for capability_key in case["expected_capability_keys"]:
        rule = _rule_for_capability(rules, capability_key)
        assert rule.timeframe == case["expected_timeframe"]
        assert rule.required is case["expected_required"][capability_key]
        actual_parameters = dict(rule.resolved_parameters)
        actual_parameters.update(rule.left.parameters)
        if rule.right is not None and rule.right.value is not None:
            actual_parameters.setdefault("threshold", rule.right.value)
        for key, value in case["expected_parameters"].get(capability_key, {}).items():
            assert actual_parameters.get(key) == value
