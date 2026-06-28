from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.prompt_semantics import analyze_prompt_semantics
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.ai_semantic_fallback import (
    AISemanticFallbackService,
    AISemanticFallbackStrategyInterpreter,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


def _guided(prompt: str, timeframe: str = "15m") -> GuidedSetupRequest:
    return GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe=timeframe,
        setup_mode="free_text",
        setup_text=prompt,
        trigger_mode="candle_close",
        delivery_channels=["web"],
    )


def _operand_names(preview) -> set[str]:
    names: set[str] = set()
    for condition in preview.strategy.conditions.children:
        if condition.left.name:
            names.add(condition.left.name)
        if condition.right and condition.right.name:
            names.add(condition.right.name)
    return names


async def test_semantic_vocabulary_maps_candle_direction_words_safely():
    prompts = {
        "green candle": "green_candle",
        "bullish candle": "green_candle",
        "positive candle": "green_candle",
        "up candle": "green_candle",
        "red candle": "red_candle",
        "bearish candle": "red_candle",
        "negative candle": "red_candle",
        "down candle": "red_candle",
    }

    for prompt, expected_name in prompts.items():
        preview = await RuleBasedStrategyInterpreter().interpret(_guided(prompt))
        assert preview.activation_blocked is False, prompt
        assert expected_name in _operand_names(preview), prompt
        condition = preview.strategy.conditions.children[0]
        assert condition.source_fragment
        assert condition.confidence is not None


async def test_semantic_vocabulary_maps_percent_moves_without_entry_or_rr():
    candle_up = await RuleBasedStrategyInterpreter().interpret(
        _guided("candle grew at least 0.01%")
    )
    assert candle_up.activation_blocked is False
    assert "candle_change_percent" in _operand_names(candle_up)
    assert candle_up.strategy.risk.enabled is False

    candle_down = await RuleBasedStrategyInterpreter().interpret(_guided("candle dropped 0.01%"))
    assert candle_down.activation_blocked is False
    assert "candle_change_percent" in _operand_names(candle_down)

    coin_up = await RuleBasedStrategyInterpreter().interpret(_guided("coin up 5% today"))
    assert coin_up.activation_blocked is False
    assert "percent_change_up" in _operand_names(coin_up)

    coin_down = await RuleBasedStrategyInterpreter().interpret(_guided("coin dropped 5% today"))
    assert coin_down.activation_blocked is False
    assert "percent_change_down" in _operand_names(coin_down)


async def test_semantic_vocabulary_handles_volume_negation_and_requiredness():
    not_dead = await RuleBasedStrategyInterpreter().interpret(_guided("volume not dead"))
    assert not_dead.activation_blocked is False
    assert "volume_ratio" in _operand_names(not_dead)
    assert any("volume not dead" in assumption.casefold() for assumption in not_dead.assumptions)

    avoid_doji = await RuleBasedStrategyInterpreter().interpret(_guided("avoid doji"))
    assert avoid_doji.activation_blocked is False
    assert avoid_doji.strategy.conditions.children[0].left.name == "doji"
    assert avoid_doji.strategy.conditions.children[0].comparator.value == "is_false"

    no_engulfing = await RuleBasedStrategyInterpreter().interpret(_guided("no bearish engulfing"))
    assert no_engulfing.activation_blocked is False
    assert no_engulfing.strategy.conditions.children[0].left.name == "bearish_engulfing"
    assert no_engulfing.strategy.conditions.children[0].comparator.value == "is_false"

    optional = await RuleBasedStrategyInterpreter().interpret(_guided("optional volume spike"))
    assert optional.activation_blocked is False
    assert optional.strategy.conditions.children[0].required is False

    required = await RuleBasedStrategyInterpreter().interpret(_guided("must have volume spike"))
    assert required.activation_blocked is False
    assert required.strategy.conditions.children[0].required is True


async def test_semantic_vocabulary_blocks_false_positive_contexts():
    positive_news = await RuleBasedStrategyInterpreter().interpret(_guided("positive news"))
    assert positive_news.activation_blocked is True
    assert "green_candle" not in _operand_names(positive_news)
    assert any(issue.code == "provider_required" for issue in positive_news.unsupported_conditions)

    green_project = await RuleBasedStrategyInterpreter().interpret(_guided("green project"))
    assert green_project.activation_blocked is True
    assert "green_candle" not in _operand_names(green_project)

    bullish = await RuleBasedStrategyInterpreter().interpret(_guided("bullish"))
    assert bullish.activation_blocked is True
    assert "green_candle" not in _operand_names(bullish)

    looks_strong = await RuleBasedStrategyInterpreter().interpret(_guided("looks strong"))
    assert looks_strong.activation_blocked is True
    assert any(
        issue.code == "ambiguous_discretionary_language"
        for issue in looks_strong.unsupported_conditions
    )

    ready = await RuleBasedStrategyInterpreter().interpret(_guided("ready to pump"))
    assert ready.activation_blocked is True


async def test_existing_candle_patterns_still_map():
    bullish = await RuleBasedStrategyInterpreter().interpret(_guided("bullish engulfing"))
    bearish = await RuleBasedStrategyInterpreter().interpret(_guided("bearish engulfing"))

    assert "bullish_engulfing" in _operand_names(bullish)
    assert "bearish_engulfing" in _operand_names(bearish)


def test_semantic_module_exposes_data_driven_matches():
    result = analyze_prompt_semantics("positive candle with volume not dead", "15m")

    assert [condition.left.name for condition in result.conditions] == [
        "green_candle",
        "volume_ratio",
    ]
    assert all(condition.source_fragment for condition in result.conditions)
    assert result.metadata()["vocabulary_version"]


class CountingAIClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def classify_fragment(self, payload: dict):
        self.calls += 1
        return self.payload


def _settings() -> Settings:
    return Settings(
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        ai_interpreter_provider="rules",
        openai_api_key=SecretStr("test-key"),
        ai_semantic_fallback_enabled=True,
    )


async def test_ai_semantic_fallback_validates_registry_and_caches_results():
    client = CountingAIClient(
        {
            "fragment": "bright candle",
            "semantic_type": "condition",
            "plain_english_meaning": "bullish candle",
            "canonical_intent": "candle close above open",
            "candidate_capability_keys": ["green_candle"],
            "direction": "bullish",
            "comparator": None,
            "threshold": None,
            "timeframe": "15m",
            "required": True,
            "negated": False,
            "confidence": 0.95,
            "provider_required": False,
            "needs_clarification": False,
            "clarification_question": None,
            "reason": "User described a candle color concept.",
            "safe_to_convert": True,
        }
    )
    service = AISemanticFallbackService(_settings(), client=client)

    first = await service.resolve_fragment(
        original_prompt="bright candle",
        unresolved_fragment="bright candle",
        parsed_conditions=[],
        default_timeframe="15m",
    )
    second = await service.resolve_fragment(
        original_prompt="bright candle",
        unresolved_fragment="bright candle",
        parsed_conditions=[],
        default_timeframe="15m",
    )

    assert first.status == "converted"
    assert first.condition is not None
    assert first.condition.left.name == "green_candle"
    assert first.condition.source_fragment == "bright candle"
    assert second.from_cache is True
    assert client.calls == 1


async def test_ai_semantic_fallback_rejects_unsafe_and_provider_candidates():
    unsafe = AISemanticFallbackService(
        _settings(),
        client=CountingAIClient(
            {
                "fragment": "positive news",
                "semantic_type": "provider_required",
                "plain_english_meaning": "news context",
                "canonical_intent": "news event",
                "candidate_capability_keys": ["high_impact_market_news"],
                "direction": "bullish",
                "comparator": None,
                "threshold": None,
                "timeframe": None,
                "required": True,
                "negated": False,
                "confidence": 0.95,
                "provider_required": True,
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "News needs an external event feed.",
                "safe_to_convert": False,
            }
        ),
    )
    provider = await unsafe.resolve_fragment(
        original_prompt="positive news",
        unresolved_fragment="positive news",
        parsed_conditions=[],
        default_timeframe="15m",
    )
    assert provider.status == "provider_required"
    assert provider.issue is not None
    assert provider.issue.blocking is True

    unknown = AISemanticFallbackService(
        _settings(),
        client=CountingAIClient(
            {
                "fragment": "magic alpha",
                "semantic_type": "condition",
                "plain_english_meaning": "unknown",
                "canonical_intent": "unsupported",
                "candidate_capability_keys": ["magic_alpha"],
                "direction": None,
                "comparator": None,
                "threshold": None,
                "timeframe": None,
                "required": True,
                "negated": False,
                "confidence": 0.99,
                "provider_required": False,
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "Not in registry.",
                "safe_to_convert": True,
            }
        ),
    )
    rejected = await unknown.resolve_fragment(
        original_prompt="magic alpha",
        unresolved_fragment="magic alpha",
        parsed_conditions=[],
        default_timeframe="15m",
    )
    assert rejected.status == "rejected"
    assert rejected.issue is not None
    assert rejected.issue.code == "ai_semantic_unknown_capability"


async def test_ai_semantic_strategy_interpreter_uses_deterministic_parser_first():
    class ShouldNotCall:
        async def classify_fragment(self, payload: dict):
            raise AssertionError("AI fallback should not be called")

    service = AISemanticFallbackService(_settings(), client=ShouldNotCall())
    preview = await AISemanticFallbackStrategyInterpreter(
        _settings(),
        service=service,
    ).interpret(_guided("green candle"))

    assert preview.activation_blocked is False
    assert "green_candle" in _operand_names(preview)
    assert "ai_semantic_fallback" not in preview.raw_metadata
