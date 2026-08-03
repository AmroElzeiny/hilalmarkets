from ai_market_monitor.core.config import Settings
from ai_market_monitor.services.ai_model_routing import select_setup_model


def _settings() -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="test-secret-key-with-at-least-thirty-two-characters",
        database_url="sqlite+aiosqlite://",
        ai_setup_simple_model="configured-simple",
        ai_setup_complex_model="configured-complex",
        ai_setup_simple_reasoning_effort="low",
        ai_setup_complex_reasoning_effort="medium",
        openai_fast_model_pricing_usd_per_million={
            "configured-complex": {
                "input": 1.0,
                "cached_input": 0.1,
                "output": 5.0,
            }
        },
    )


def test_clear_single_condition_uses_configured_simple_tier() -> None:
    route = select_setup_model(
        _settings(),
        current_message="RSI below 30 on 15m",
    )

    assert route.model == "configured-simple"
    assert route.reasoning_effort == "low"
    assert route.service_tier == "default"
    assert route.tier == "simple"
    assert route.reasons == ("simple_clear_request",)
    assert route.correction_count == 0


def test_luna_minimal_effort_is_normalized_to_its_first_supported_level() -> None:
    settings = _settings()
    settings.ai_setup_simple_model = "gpt-5.6-luna"
    settings.ai_setup_simple_reasoning_effort = "minimal"

    route = select_setup_model(settings, current_message="hi there")

    assert route.model == "gpt-5.6-luna"
    assert route.reasoning_effort == "low"
    assert "model_reasoning_effort_normalized" in route.reasons


def test_complex_logic_and_multiple_timeframes_use_stable_complex_tier() -> None:
    route = select_setup_model(
        _settings(),
        current_message=(
            "RSI below 30 on 15m and volume above 1.5x, or price above EMA 200 on 1h, "
            "but not during the weekend"
        ),
    )

    assert route.model == "configured-complex"
    assert route.reasoning_effort == "medium"
    assert route.service_tier == "default"
    assert route.tier == "complex"
    assert "mixed_boolean_logic" in route.reasons
    assert "multiple_timeframes" in route.reasons


def test_repeated_corrections_and_low_confidence_escalate_without_changing_authority() -> None:
    route = select_setup_model(
        _settings(),
        current_message="No, I mean the previous weekly low sweep",
        history=[
            {"role": "user", "content": "I mean the weekly low, not daily"},
            {"role": "assistant", "content": "Which low should be used?"},
        ],
        capability_context={"candidates": [{"confidence": 0.42}]},
    )

    assert route.tier == "complex"
    assert "repeated_corrections" in route.reasons
    assert "low_capability_confidence" in route.reasons
    assert route.correction_count == 2
    assert route.usage_metadata()["_traceedge_correction_count"] == 2


def test_exact_selected_capability_is_not_downgraded_by_weak_alternates() -> None:
    route = select_setup_model(
        _settings(),
        current_message="Sweep the previous daily low on 5m",
        capability_context={
            "fragments": [
                {
                    "selection_confidence": 0.99,
                    "candidates": [
                        {"capability_key": "previous_daily_low_sweep", "confidence": 0.99},
                        {"capability_key": "reference_period_sweep", "confidence": 0.72},
                        {"capability_key": "previous_low_swept", "confidence": 0.31},
                    ],
                }
            ]
        },
    )

    assert route.tier == "simple"
    assert "low_capability_confidence" not in route.reasons


def test_multilingual_turn_uses_complex_tier_without_hard_coding_a_trade_rule() -> None:
    route = select_setup_model(
        _settings(),
        current_message="عايز أراقب كسر المقاومة على 15m",
    )

    assert route.tier == "complex"
    assert route.reasons == ("multilingual_or_mixed_language",)
