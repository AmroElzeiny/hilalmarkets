import httpx
import pytest
from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.schemas.strategy_draft_v2 import (
    SetupIntent,
    StrategyDraftV2,
    StrategyPatchExtraction,
)
from ai_market_monitor.services.ai_setup_chat import SetupChatError, setup_chat_error_envelope
from ai_market_monitor.services.strategy_patch_extractor import (
    LaunchStrategyPatchExtractor,
    StrategyPatchExtractionError,
    StrategyPatchNonMutation,
    deterministic_strategy_patch,
)


def _settings() -> Settings:
    return Settings().model_copy(
        update={
            "openai_api_key": SecretStr("test-key"),
            "openai_base_url": "https://provider.invalid/v1",
        }
    )


@pytest.mark.parametrize(
    ("response_status", "expected_code", "retryable"),
    [
        (409, "TARGET_HTTP_409", False),
        (429, "TARGET_HTTP_429", True),
        (500, "TARGET_HTTP_5XX", True),
        (502, "TARGET_HTTP_5XX", True),
    ],
)
async def test_structured_extractor_classifies_http_failures(
    response_status,
    expected_code,
    retryable,
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(response_status, request=request)
    )
    extractor = LaunchStrategyPatchExtractor(_settings(), transport=transport)

    with pytest.raises(StrategyPatchExtractionError) as captured:
        await extractor.extract(
            current_draft=StrategyDraftV2(),
            message="Add RSI below 30 on 15m",
            source_turn_id="turn-12345678",
        )

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    assert extractor.model_call_count == 1


async def test_structured_extractor_distinguishes_dns_and_disconnect():
    def dns_failure(request):
        raise httpx.ConnectError("getaddrinfo failed", request=request)

    dns_extractor = LaunchStrategyPatchExtractor(
        _settings(),
        transport=httpx.MockTransport(dns_failure),
    )
    with pytest.raises(StrategyPatchExtractionError) as dns_error:
        await dns_extractor.extract(
            current_draft=StrategyDraftV2(),
            message="Add RSI below 30 on 15m",
            source_turn_id="turn-12345678",
        )
    assert dns_error.value.code == "TARGET_DNS_RESOLUTION_FAILURE"

    def disconnect(request):
        raise httpx.RemoteProtocolError(
            "Server disconnected without sending a response.",
            request=request,
        )

    disconnect_extractor = LaunchStrategyPatchExtractor(
        _settings(),
        transport=httpx.MockTransport(disconnect),
    )
    with pytest.raises(StrategyPatchExtractionError) as disconnect_error:
        await disconnect_extractor.extract(
            current_draft=StrategyDraftV2(),
            message="Add RSI below 30 on 15m",
            source_turn_id="turn-12345678",
        )
    assert disconnect_error.value.code == "TARGET_PARTIAL_STREAM"


async def test_model_call_limit_is_per_turn_not_per_conversation():
    calls = 0

    def unavailable(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    extractor = LaunchStrategyPatchExtractor(
        _settings(),
        transport=httpx.MockTransport(unavailable),
    )
    for source_turn_id in ("turn-12345678", "turn-87654321"):
        with pytest.raises(StrategyPatchExtractionError) as captured:
            await extractor.extract(
                current_draft=StrategyDraftV2(),
                message="Add RSI below 30 on 15m",
                source_turn_id=source_turn_id,
            )
        assert captured.value.code == "TARGET_HTTP_5XX"
        assert extractor.model_call_count == 1

    assert calls == 2


def test_setup_error_envelope_preserves_launch_stage_and_retryability():
    envelope = setup_chat_error_envelope(
        SetupChatError(
            "TARGET_READ_TIMEOUT",
            "The provider timed out.",
            stage="extract",
            retryable=True,
            status_code=503,
        )
    )

    assert envelope.error_code == "TARGET_READ_TIMEOUT"
    assert envelope.stage == "extract"
    assert envelope.retryable


def test_option_lists_are_not_compiled_as_selected_strategy_conditions():
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        (
            "ETHUSDT only and exclude BTCUSDT. A bearish move of at least 5% "
            "from what reference: 1m close-to-close, 1h swing high to low, or "
            "current price? Which exact option should define the trigger?"
        ),
        source_turn_id="turn-options-1234",
    )

    assert patch is None


def test_numbered_multi_condition_turn_uses_one_structured_extraction():
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        (
            "1) On 1h require the close below EMA20. "
            "2) On 1m require a fall from the last 1h close of at least 5%. "
            "3) Then require the 1m close below the lowest close of 20 candles."
        ),
        source_turn_id="turn-multi-1234",
    )

    assert patch is None


def test_mixed_vague_mechanics_are_not_partially_compiled_as_percentage_only():
    patch = deterministic_strategy_patch(
        StrategyDraftV2(),
        (
            "Monitor ETHUSDT on 1m after a strong bearish move of at least 5% "
            "with clean liquidity, good displacement, and near 1h support."
        ),
        source_turn_id="turn-vague-mixed-1234",
    )

    assert patch is None


async def test_valid_non_patch_model_result_is_not_reported_as_invalid_output():
    extraction = StrategyPatchExtraction(
        intent=SetupIntent.EXPLANATION_REQUEST,
        answer="Choose the exact reference you want the monitor to use.",
    )

    def response(request):
        return httpx.Response(
            200,
            request=request,
            json={"output_text": extraction.model_dump_json(), "usage": {}},
        )

    extractor = LaunchStrategyPatchExtractor(
        _settings(),
        transport=httpx.MockTransport(response),
    )

    with pytest.raises(StrategyPatchNonMutation) as captured:
        await extractor.extract(
            current_draft=StrategyDraftV2(),
            message=(
                "Should the move use close-to-close or the last swing high? "
                "Choose one for me."
            ),
            source_turn_id="turn-choice-1234",
        )

    assert captured.value.intent is SetupIntent.EXPLANATION_REQUEST
    assert extractor.model_call_count == 1
