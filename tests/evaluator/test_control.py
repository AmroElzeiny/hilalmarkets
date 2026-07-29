import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.core.startup import (
    RuntimeConfigurationError,
    validate_runtime_configuration,
)
from ai_market_monitor.services.agent_control import OpenAIAgentResponsesClient
from ai_market_monitor.services.ai_model_routing import select_setup_model
from ai_market_monitor.services.ai_setup_evaluator_control import (
    AISetupEvaluatorControlError,
    consume_evaluator_llm_fault,
    evaluator_prompt_appendix,
    evaluator_turn,
)


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "app_secret_key": "evaluator-test-secret-with-at-least-32-characters",
        "openai_api_key": SecretStr("evaluator-test-key"),
        "ai_setup_evaluator_enabled": True,
        "ai_setup_evaluator_faults_enabled": True,
        "ai_setup_evaluator_target_versions": {
            "golden-v2": {
                "model": "gpt-test-golden",
                "reasoning_effort": "high",
                "prompt_version": "context_guard_v1",
            }
        },
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("fault", "exception_type"),
    [
        ("timeout_once", httpx.ReadTimeout),
        ("429_once", httpx.HTTPStatusError),
        ("stream_disconnect_once", httpx.RemoteProtocolError),
    ],
)
def test_exception_faults_are_one_shot(fault, exception_type):
    settings = _settings()
    with evaluator_turn(settings, fault=fault, target_version=None):
        with pytest.raises(exception_type):
            consume_evaluator_llm_fault()
        assert consume_evaluator_llm_fault() is None


@pytest.mark.parametrize(
    "fault",
    ["empty_once", "invalid_json_once", "partial_json_once"],
)
def test_payload_faults_are_one_shot(fault):
    settings = _settings()
    with evaluator_turn(settings, fault=fault, target_version=None):
        assert consume_evaluator_llm_fault() is not None
        assert consume_evaluator_llm_fault() is None


async def test_fault_is_applied_at_the_real_responses_client_boundary():
    settings = _settings()
    client = OpenAIAgentResponsesClient(settings)
    with evaluator_turn(settings, fault="empty_once", target_version=None):
        assert await client.create({}, timeout_seconds=1) == {}


def test_target_version_selects_only_server_configured_model_and_prompt():
    settings = _settings()
    with evaluator_turn(settings, fault=None, target_version="golden-v2"):
        route = select_setup_model(
            settings,
            current_message="Monitor BTC above 100 on 15m",
        )
        assert route.model == "gpt-test-golden"
        assert route.reasoning_effort == "high"
        assert "evaluator_target_version" in route.reasons
        assert "retain every still-current" in evaluator_prompt_appendix()

    with (
        pytest.raises(AISetupEvaluatorControlError),
        evaluator_turn(settings, fault=None, target_version="customer-model-name"),
    ):
        pass


def test_fault_controls_are_unavailable_outside_test_environment():
    settings = _settings(app_env="development")
    with (
        pytest.raises(AISetupEvaluatorControlError),
        evaluator_turn(settings, fault="timeout_once", target_version=None),
    ):
        pass


@pytest.mark.parametrize(
    ("overrides", "expected_setting"),
    [
        ({"app_env": "development"}, "APP_ENV"),
        ({"app_env": "staging"}, "APP_ENV"),
        ({"ai_setup_evaluator_enabled": False}, "AI_SETUP_EVALUATOR_ENABLED"),
        (
            {"ai_setup_evaluator_faults_enabled": False},
            "AI_SETUP_EVALUATOR_FAULTS_ENABLED",
        ),
    ],
)
def test_every_refusal_names_the_setting_that_caused_it(overrides, expected_setting):
    """One shared message for several causes cost a whole evaluator run.

    Both evaluator flags were already true; the target was simply started with
    `APP_ENV=development`. "Evaluator controls are unavailable" could not say so, and
    the run stopped at `EVALUATOR_FAULT_CONTROL_UNAVAILABLE` with nothing to act on.
    """
    settings = _settings(**overrides)
    with (
        pytest.raises(AISetupEvaluatorControlError) as error,
        evaluator_turn(settings, fault="empty_once", target_version=None),
    ):
        pass
    assert expected_setting in str(error.value), str(error.value)


def test_an_unknown_fault_names_the_supported_ones():
    settings = _settings()
    with (
        pytest.raises(AISetupEvaluatorControlError) as error,
        evaluator_turn(settings, fault="not_a_real_fault", target_version=None),
    ):
        pass
    message = str(error.value)
    assert "not_a_real_fault" in message
    assert "empty_once" in message


def test_an_unknown_target_version_names_the_configured_ones():
    settings = _settings()
    with (
        pytest.raises(AISetupEvaluatorControlError) as error,
        evaluator_turn(settings, fault=None, target_version="customer-model-name"),
    ):
        pass
    message = str(error.value)
    assert "customer-model-name" in message
    assert "golden-v2" in message


def test_a_turn_without_evaluator_headers_never_consults_these_settings():
    """Ordinary traffic must be unaffected, whatever the environment."""
    settings = _settings(
        app_env="production",
        ai_setup_evaluator_enabled=False,
        ai_setup_evaluator_faults_enabled=False,
    )
    with evaluator_turn(settings, fault=None, target_version=None):
        assert consume_evaluator_llm_fault() is None


def test_production_startup_rejects_evaluator_fault_mode():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret-with-at-least-32-characters",
        ai_setup_evaluator_enabled=True,
        ai_setup_evaluator_faults_enabled=True,
    )
    with pytest.raises(RuntimeConfigurationError) as error:
        validate_runtime_configuration(settings)
    assert "evaluator controls and fault injection are forbidden" in str(error.value)
