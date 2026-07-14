from pydantic import SecretStr

from ai_market_monitor.core.config import Settings
from ai_market_monitor.engine.capability_resolver import CapabilityResolver
from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.hybrid_capability_resolution import (
    CapabilityRerankDecision,
    CapabilityRerankResponse,
    HybridCapabilityResolutionService,
)
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter


class WeeklySweepReranker:
    last_usage = {"input_tokens": 100, "output_tokens": 30}

    async def rerank(self, payload):
        candidate_keys = {item["capability_key"] for item in payload["fragments"][0]["candidates"]}
        assert "reference_period_sweep" in candidate_keys
        return CapabilityRerankResponse(
            decisions=[
                CapabilityRerankDecision(
                    fragment_index=0,
                    capability_key="reference_period_sweep",
                    parameters={
                        "reference_period": "week",
                        "side": "low",
                        "timezone": "UTC",
                    },
                    confidence=0.94,
                    needs_clarification=False,
                    reason="Weekly low sweep is explicit in conversation context.",
                )
            ]
        )


class InventingReranker:
    last_usage = {}

    async def rerank(self, payload):
        return CapabilityRerankResponse(
            decisions=[
                CapabilityRerankDecision(
                    fragment_index=0,
                    capability_key="invented_whale_alpha",
                    parameters={},
                    confidence=0.99,
                    needs_clarification=False,
                    reason="Invented key should be rejected.",
                )
            ]
        )


class NoSelectionReranker:
    last_usage = {}

    async def rerank(self, payload):
        return CapabilityRerankResponse(decisions=[])


def _settings() -> Settings:
    return Settings(
        app_env="development",
        app_secret_key="hybrid-test-secret-at-least-thirty-two-characters",
        openai_api_key=SecretStr("test-key"),
        ai_capability_reranker_enabled=True,
    )


async def test_ai_reranks_only_registry_candidates_and_builds_valid_binding():
    report = CapabilityResolver().resolve_prompt("weekly floor raid")
    result = await HybridCapabilityResolutionService(
        _settings(), reranker=WeeklySweepReranker()
    ).resolve(
        report,
        history=[
            {"role": "user", "content": "I mean last week's low."},
        ],
        default_timeframe="15m",
    )
    assert result.report.fragments[0].status == "matched"
    assert result.bindings[0]["capability_key"] == "reference_period_sweep"
    assert result.bindings[0]["parameters"]["side"] == "low"
    assert result.usage["input_tokens"] == 100


async def test_ai_invented_capability_key_is_never_accepted():
    report = CapabilityResolver().resolve_prompt("weekly floor raid")
    result = await HybridCapabilityResolutionService(
        _settings(), reranker=InventingReranker()
    ).resolve(report, history=[], default_timeframe="15m")
    assert result.report.fragments[0].status != "matched"
    assert result.bindings == []


async def test_candidate_retrieval_uses_current_fragment_without_old_chat_text(monkeypatch):
    report = CapabilityResolver().resolve_prompt("moon wobble pattern")
    service = HybridCapabilityResolutionService(
        _settings().model_copy(update={"capability_embeddings_enabled": False}),
        reranker=NoSelectionReranker(),
    )
    retrieved = []
    original = service.resolver.broad_candidates

    def record(fragment, *, limit=12):
        retrieved.append(fragment)
        return original(fragment, limit=limit)

    monkeypatch.setattr(service.resolver, "broad_candidates", record)
    await service.resolve(
        report,
        history=[{"role": "user", "content": "Use a range breakout like I said before."}],
        default_timeframe="15m",
    )
    assert retrieved == ["moon wobble pattern"]


async def test_verified_binding_compiles_unknown_wording_without_raw_ai_execution():
    guided = GuidedSetupRequest(
        exchange="binance",
        quote_currency="USDT",
        timeframe="15m",
        symbols=[],
        setup_mode="free_text",
        setup_text="weekly floor raid",
        trigger_mode="candle_close",
        delivery_channels=["telegram"],
        capability_bindings=[
            {
                "capability_key": "reference_period_sweep",
                "parameters": {
                    "reference_period": "week",
                    "side": "low",
                    "timezone": "UTC",
                },
                "timeframe": "15m",
                "required": True,
                "source_fragment": "weekly floor raid",
                "confidence": 0.94,
            }
        ],
    )
    preview = await RuleBasedStrategyInterpreter().interpret(guided)
    rule = preview.strategy.conditions.children[0]
    assert rule.capability_key == "reference_period_sweep"
    assert rule.left.parameters["side"] == "low"
    assert preview.unsupported_conditions == []
    assert preview.raw_metadata["prompt_coverage_report"]["activation_blocked"] is False
