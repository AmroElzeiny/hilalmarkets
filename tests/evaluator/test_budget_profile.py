from __future__ import annotations

from typing import Any

import httpx
import pytest

from hm_chatbot_eval.config import Settings
from hm_chatbot_eval.models import ScenarioSpec
from hm_chatbot_eval.profiles import (
    BUDGET_EIGHT_TURN_TOPIC_IDS,
    BUDGET_UI_TOPIC_IDS,
    cases_per_topic,
    max_turns_for_topic,
    repeats_for_topic,
    target_kinds_for_topic,
    topics_for_mode,
    variants_for_topic,
)
from hm_chatbot_eval.runner import (
    BudgetExceeded,
    CostAccountingError,
    EvaluationRunner,
)
from hm_chatbot_eval.targets.base import ChatTarget, TargetReply
from hm_chatbot_eval.topics import TOPIC_BY_ID, TOPICS


def test_budget_profile_preserves_every_topic_and_category():
    selected = topics_for_mode("budget", TOPICS)
    assert selected == list(TOPICS)
    assert len(selected) == 69
    assert {topic.category for topic in selected} == {topic.category for topic in TOPICS}
    assert cases_per_topic("budget", 24) == 1


def test_budget_profile_runs_all_backend_topics_and_only_ui_boundaries():
    backend_runs = 0
    ui_topics: set[str] = set()
    for topic in topics_for_mode("budget", TOPICS):
        kinds = target_kinds_for_topic("budget", ["backend", "ui"], topic)
        backend_runs += repeats_for_topic(topic) * int("backend" in kinds)
        if "ui" in kinds:
            ui_topics.add(topic.id)

    assert backend_runs == 70
    assert ui_topics == set(BUDGET_UI_TOPIC_IDS)
    assert len(ui_topics) == 12
    assert backend_runs + len(ui_topics) == 82


def test_budget_profile_keeps_deep_context_turns_without_overfunding_simple_topics():
    assert all(
        max_turns_for_topic("budget", TOPIC_BY_ID[topic_id]) == 8
        for topic_id in BUDGET_EIGHT_TURN_TOPIC_IDS
    )
    assert max_turns_for_topic("budget", TOPIC_BY_ID["contradiction_resolution"]) == 6
    assert max_turns_for_topic("budget", TOPIC_BY_ID["approval_bypass"]) == 4
    assert max_turns_for_topic("smoke", TOPIC_BY_ID["operator_mapping"]) == 4


def test_budget_profile_compares_variants_only_for_model_drift():
    variants = [{"name": "current"}, {"name": "candidate"}]
    ordinary = variants_for_topic("budget", variants, TOPIC_BY_ID["operator_mapping"])
    drift = variants_for_topic("budget", variants, TOPIC_BY_ID["model_version_drift"])
    assert ordinary == [{"name": "current"}]
    assert drift == variants


def test_current_model_catalog_resolves_flex_and_target_prices():
    settings = Settings(_env_file=None)
    assert settings.test_ai_pricing == {
        "input": 0.125,
        "cached_input": 0.0125,
        "output": 1.0,
    }
    assert settings.target_pricing("gpt-5.4-nano") == {
        "input": 0.2,
        "cached_input": 0.02,
        "output": 1.25,
    }
    assert settings.target_pricing("gpt-5.4-mini")["output"] == 4.5


class _UsageTarget(ChatTarget):
    kind = "backend"

    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        return None

    async def send(
        self,
        message: str,
        *,
        scenario_id: str,
        fault: str | None = None,
    ) -> TargetReply:
        return TargetReply(
            text="Measured response",
            latency_ms=1,
            model="gpt-5.4-nano",
            usage={"input_tokens": 1_000_000, "output_tokens": 0},
        )

    async def close(self) -> None:
        return None


class _NoCostTestAI:
    async def next_user_turn(self, *_args, **_kwargs):
        return "Test the setup", False, 0.0


class _UsageRunner(EvaluationRunner):
    def make_target(self, kind: str) -> ChatTarget:
        return _UsageTarget()


class _UnauthorizedTarget(_UsageTarget):
    async def start(self, scenario_id: str, variant: dict[str, Any]) -> None:
        request = httpx.Request("POST", "https://target.example/setup-chat/sessions")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError(
            "Unauthorized",
            request=request,
            response=response,
        )


class _UnauthorizedRunner(EvaluationRunner):
    def make_target(self, kind: str) -> ChatTarget:
        return _UnauthorizedTarget()


class _UnauthorizedTestAI:
    async def next_user_turn(self, *_args, **_kwargs):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError(
            "Unauthorized",
            request=request,
            response=response,
        )


async def test_all_in_budget_counts_target_chatbot_usage_and_fails_closed(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = _UsageRunner(settings, "budget-test", 0.10)
    runner.test_ai = _NoCostTestAI()
    scenario = ScenarioSpec(
        id="budget-case",
        topic_id="operator_mapping",
        seed=1,
        persona={},
        hidden_goal="Test cost accounting",
        expected_contract={},
        success_criteria=[],
        max_turns=1,
    )
    try:
        with pytest.raises(BudgetExceeded):
            await runner.run_case(scenario, "backend", {"name": "current"}, "deferred")
        assert runner.spent == pytest.approx(0.20)
    finally:
        await runner.close()


async def test_budget_run_stops_cleanly_with_an_incomplete_report(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = _UsageRunner(settings, "budget-stop", 0.10)
    runner.test_ai = _NoCostTestAI()
    try:
        cases, summary = await runner.run(
            mode="budget",
            target_kinds=["backend"],
            topic_ids=["operator_mapping"],
            tests_per_topic=24,
            seed=42,
            judge_mode="deferred",
        )
        assert cases == []
        assert summary["release_gate"] == "INCOMPLETE"
        assert summary["execution_status"] == "STOPPED_BUDGET"
        assert summary["workflow_status"] == "STOPPED_BUDGET"
        assert summary["infrastructure_status"] == "HEALTHY"
        assert summary["measured_spend_usd"] == pytest.approx(0.20)
        assert (runner.run_dir / "report.html").is_file()
        assert (runner.run_dir / "run_plan.json").is_file()
    finally:
        await runner.close()


async def test_access_failure_stops_after_first_case_and_is_not_a_quality_score(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = _UnauthorizedRunner(settings, "access-stop", 2.5)
    runner.test_ai = _NoCostTestAI()
    try:
        cases, summary = await runner.run(
            mode="budget",
            target_kinds=["backend"],
            topic_ids=["operator_mapping", "threshold_mapping"],
            tests_per_topic=24,
            seed=42,
            judge_mode="online",
        )
        assert cases == []
        assert summary["release_gate"] == "INCOMPLETE"
        assert summary["execution_status"] == "PAUSED_AUTH"
        assert summary["cases"] == 0
        assert summary["execution_error"].startswith("Authenticated target access failed")
        # The summary must state what actually ran, not a fixed "before any case" claim.
        assert "Completed 0 cases before stopping." in summary["execution_error"]
    finally:
        await runner.close()


async def test_evaluator_openai_failure_is_identified_separately(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
        test_ai_base_url="https://api.openai.com/v1",
    )
    runner = _UsageRunner(settings, "openai-access-stop", 2.5)
    runner.test_ai = _UnauthorizedTestAI()
    try:
        cases, summary = await runner.run(
            mode="budget",
            target_kinds=["backend"],
            topic_ids=["operator_mapping"],
            tests_per_topic=24,
            seed=42,
            judge_mode="online",
        )
        assert cases == []
        assert summary["release_gate"] == "INCOMPLETE"
        assert summary["execution_status"] == "PAUSED_AUTH"
        assert summary["execution_error"].startswith("Evaluator OpenAI access failed")
        assert "EVALUATOR_AUTH_FAILURE" in summary["execution_error"]
    finally:
        await runner.close()


def test_unknown_priced_target_model_fails_instead_of_recording_zero(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = EvaluationRunner(settings, "price-test", 2.5)
    try:
        with pytest.raises(CostAccountingError):
            runner._target_cost(
                "unknown-model",
                {"input_tokens": 100, "output_tokens": 20},
            )
    finally:
        runner.cache.close()


def test_authoritative_mixed_model_cost_does_not_require_single_model_pricing(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = EvaluationRunner(settings, "mixed-price-test", 2.5)
    try:
        assert runner._target_cost(
            "mixed",
            {
                "input_tokens": 900,
                "output_tokens": 100,
                "estimated_cost_usd": 0.0042,
                "models": ["gpt-5.4-mini", "gpt-5.4-nano"],
            },
        ) == pytest.approx(0.0042)
    finally:
        runner.cache.close()


def test_invalid_authoritative_target_cost_fails_closed(tmp_path):
    settings = Settings(
        _env_file=None,
        eval_output_dir=tmp_path / "runs",
        eval_cache_db=tmp_path / "cache.sqlite3",
    )
    runner = EvaluationRunner(settings, "invalid-authoritative-price", 2.5)
    try:
        with pytest.raises(CostAccountingError):
            runner._target_cost("mixed", {"estimated_cost_usd": -0.01})
    finally:
        runner.cache.close()
