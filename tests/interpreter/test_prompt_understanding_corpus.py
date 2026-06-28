import json
from collections import Counter
from pathlib import Path

import pytest

from ai_market_monitor.schemas.onboarding import GuidedSetupRequest
from ai_market_monitor.services.interpreter import RuleBasedStrategyInterpreter

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_understanding_corpus.jsonl"


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


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _operand_names(preview) -> set[str]:
    names: set[str] = set()
    for condition in preview.strategy.conditions.children:
        if condition.left.name:
            names.add(condition.left.name)
        if condition.right and condition.right.name:
            names.add(condition.right.name)
    return names


def test_prompt_understanding_corpus_has_required_size_and_families():
    cases = _load_cases()
    families = {case["source_family"] for case in cases}
    counts = Counter(case["source_family"] for case in cases)

    assert len(cases) >= 1000
    assert {
        "candle_direction",
        "price_percent_move",
        "volume",
        "ma_vwap",
        "rsi_momentum",
        "negation",
        "required_optional",
        "timeframe_window",
        "mixed_multi_condition",
        "vague_ambiguous",
    }.issubset(families)
    assert counts["candle_direction"] >= 150
    assert counts["price_percent_move"] >= 150
    for family in {
        "volume",
        "ma_vwap",
        "rsi_momentum",
        "negation",
        "required_optional",
        "timeframe_window",
        "mixed_multi_condition",
        "vague_ambiguous",
    }:
        assert counts[family] >= 100


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["prompt"][:80])
async def test_prompt_understanding_corpus_interprets_generated_cases(case: dict):
    preview = await RuleBasedStrategyInterpreter().interpret(_guided(case["prompt"]))
    names = _operand_names(preview)
    report = preview.raw_metadata.get("prompt_coverage_report") or {}

    assert preview.activation_blocked is bool(case["should_block"]), case["prompt"]
    assert preview.strategy.canonical_hash()

    for condition in preview.strategy.conditions.children:
        assert condition.source_fragment, (case["prompt"], condition.key)
        assert condition.confidence is not None, (case["prompt"], condition.key)

    if not case["should_block"]:
        expected_names = set(case["expected_condition_names"])
        assert expected_names.intersection(names), (case["prompt"], expected_names, names)
        if case["expected_timeframe"]:
            assert any(
                condition.timeframe == case["expected_timeframe"]
                for condition in preview.strategy.conditions.children
            ), case["prompt"]
        unclassified = [
            row for row in report.get("mapping_table", []) if row.get("bucket") == "unclassified"
        ]
        assert not unclassified, case["prompt"]
    else:
        assert preview.unsupported_conditions or preview.ambiguities, case["prompt"]

    assert not any(
        condition.provider_required or condition.availability == "provider_required"
        for condition in preview.strategy.conditions.children
    ), case["prompt"]
