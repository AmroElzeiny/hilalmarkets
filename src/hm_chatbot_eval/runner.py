from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Settings, process_openai_key_overrides_dotenv
from .evaluate import deterministic_metrics, validate_schema
from .models import CaseResult, JudgeVerdict, ScenarioSpec, TurnRecord
from .openai_client import OpenAIResponsesClient
from .profiles import (
    cases_per_topic,
    max_turns_for_topic,
    repeats_for_topic,
    target_kinds_for_topic,
    topics_for_mode,
    variants_for_topic,
)
from .report import write_reports
from .scenarios import build_scenario
from .targets.backend import GenericHTTPBackendTarget, HilalMarketsBackendTarget
from .targets.base import ChatTarget
from .targets.ui import UITarget
from .test_ai import TestAI
from .topics import TOPIC_BY_ID, TOPICS
from .util import ensure_dir, stable_hash, utc_now


class BudgetExceeded(RuntimeError):
    pass


class CostAccountingError(RuntimeError):
    pass


class EvaluationInfrastructureError(RuntimeError):
    pass


class EvaluationRunner:
    def __init__(self, settings: Settings, run_id: str, budget_usd: float):
        self.settings = settings
        self.run_id = run_id
        self.run_dir = ensure_dir(settings.eval_output_dir / run_id)
        self.evidence_dir = ensure_dir(self.run_dir / "evidence")
        self.cache = ResponseCache(settings.eval_cache_db)
        self.ai_client = OpenAIResponsesClient(settings, self.cache)
        self.test_ai = TestAI(settings, self.ai_client)
        self.budget = budget_usd
        self.spent = 0.0
        self.schema = settings.load_schema()

    def _charge(self, amount: float) -> None:
        self.spent += amount
        if self.budget > 0 and self.spent > self.budget:
            raise BudgetExceeded(
                f"Hard evaluator budget exceeded: ${self.spent:.4f} > ${self.budget:.4f}"
            )

    def _target_cost(self, model: str | None, usage: dict[str, Any]) -> float:
        recorded_cost = usage.get("estimated_cost_usd")
        if recorded_cost is not None:
            try:
                authoritative_cost = float(recorded_cost)
            except (TypeError, ValueError) as exc:
                raise CostAccountingError(
                    "Target returned an invalid authoritative cost."
                ) from exc
            if not math.isfinite(authoritative_cost) or authoritative_cost < 0:
                raise CostAccountingError(
                    "Target returned an invalid authoritative cost."
                )
            return authoritative_cost
        inp = float(usage.get("input_tokens", 0))
        cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
        out = float(usage.get("output_tokens", 0))
        if inp <= 0 and out <= 0:
            return 0.0
        try:
            pricing = self.settings.target_pricing(model)
        except ValueError as exc:
            raise CostAccountingError(str(exc)) from exc
        return (
            max(0.0, inp - cached) * pricing["input"]
            + cached * pricing["cached_input"]
            + out * pricing["output"]
        ) / 1_000_000

    def make_target(self, kind: str) -> ChatTarget:
        if kind == "backend":
            if self.settings.target_backend_adapter == "hilalmarkets":
                return HilalMarketsBackendTarget(self.settings)
            if self.settings.target_backend_adapter == "generic_http":
                return GenericHTTPBackendTarget(self.settings)
            raise ValueError(
                f"Unknown TARGET_BACKEND_ADAPTER: {self.settings.target_backend_adapter}"
            )
        if kind == "ui":
            return UITarget(self.settings, self.evidence_dir)
        raise ValueError(f"Unknown target kind: {kind}")

    async def run_case(
        self, scenario: ScenarioSpec, kind: str, variant: dict[str, Any], judge_mode: str
    ) -> CaseResult:
        started_at = utc_now()
        target = self.make_target(kind)
        turns: list[TurnRecord] = []
        artifacts: list[str] = []
        structured = None
        error = None
        test_cost = 0.0
        target_cost = 0.0
        fault_used = False
        try:
            await target.start(scenario.id, variant)
            for turn_number in range(1, scenario.max_turns + 1):
                user_text, done, cost = await self.test_ai.next_user_turn(
                    scenario, turns, turn_number
                )
                self._charge(cost)
                test_cost += cost
                turns.append(
                    TurnRecord(
                        turn_id=f"u{turn_number}", role="user", text=user_text, timestamp=utc_now()
                    )
                )
                fault = scenario.fault if scenario.fault and not fault_used else None
                reply = await target.send(user_text, scenario_id=scenario.id, fault=fault)
                fault_used = fault_used or bool(fault)
                artifacts.extend(reply.artifacts)
                if reply.structured is not None:
                    structured = reply.structured
                usage = reply.usage or {}
                turn_target_cost = self._target_cost(reply.model, usage)
                self._charge(turn_target_cost)
                target_cost += turn_target_cost
                turns.append(
                    TurnRecord(
                        turn_id=f"a{turn_number}",
                        role="assistant",
                        text=reply.text,
                        timestamp=utc_now(),
                        latency_ms=reply.latency_ms,
                        status_code=reply.status_code,
                        raw_hash=reply.raw_hash,
                        structured=reply.structured,
                        model=reply.model,
                        usage=reply.usage,
                        error=reply.error,
                    )
                )
                raw_path = self.evidence_dir / f"{scenario.id}-{kind}-{turn_number}.json"
                raw_path.write_text(
                    json.dumps(
                        {"user": user_text, "reply": reply.raw, "error": reply.error},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                artifacts.append(str(raw_path))
                if done and turn_number >= 2:
                    break
            schema_errors = validate_schema(structured, self.schema)
            deterministic = deterministic_metrics(
                scenario, turns, structured, schema_errors, self.settings.target_field_map
            )
            judge: JudgeVerdict | None = None
            if judge_mode == "online":
                judge, cost = await self.test_ai.judge(
                    scenario, turns, deterministic, schema_errors, structured
                )
                self._charge(cost)
                test_cost += cost
            elif judge_mode == "deferred":
                pending_dir = ensure_dir(self.run_dir / "batch_pending")
                (
                    pending_dir / f"{scenario.id}-{kind}-{variant.get('name', 'current')}.json"
                ).write_text(
                    json.dumps(
                        {
                            "scenario": scenario.__dict__,
                            "turns": [t.__dict__ for t in turns],
                            "deterministic": deterministic,
                            "schema_errors": schema_errors,
                            "structured_output": structured,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            passed = (
                bool(judge.passed)
                if judge
                else bool(deterministic.get("schema_valid")) and not error
            )
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics=deterministic,
                judge=judge,
                structured_output=structured,
                structured_hash=stable_hash(structured) if structured else None,
                schema_errors=schema_errors,
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=passed,
                error=error,
                artifacts=artifacts,
            )
        except (BudgetExceeded, CostAccountingError, EvaluationInfrastructureError):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403, 429}:
                evaluator_request = str(exc.request.url).startswith(
                    self.settings.test_ai_base_url.rstrip("/")
                )
                if evaluator_request:
                    source_hint = (
                        " A process-level OPENAI_API_KEY is overriding a different "
                        "project .env value."
                        if process_openai_key_overrides_dotenv()
                        else ""
                    )
                    raise EvaluationInfrastructureError(
                        "Evaluator OpenAI access failed before a quality case could run "
                        f"(HTTP {exc.response.status_code}).{source_hint}"
                    ) from exc
                raise EvaluationInfrastructureError(
                    "Authenticated target access failed before a quality case could run "
                    f"(HTTP {exc.response.status_code})."
                ) from exc
            error = f"{type(exc).__name__}: {exc}"
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics={},
                judge=None,
                structured_output=structured,
                structured_hash=stable_hash(structured) if structured else None,
                schema_errors=[],
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=False,
                error=error,
                artifacts=artifacts,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return CaseResult(
                run_id=self.run_id,
                scenario=scenario,
                target_kind=kind,
                target_variant=str(variant.get("name", "current")),
                started_at=started_at,
                finished_at=utc_now(),
                turns=turns,
                deterministic_metrics={},
                judge=None,
                structured_output=structured,
                structured_hash=stable_hash(structured) if structured else None,
                schema_errors=[],
                total_latency_ms=sum(t.latency_ms or 0 for t in turns),
                target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost,
                passed=False,
                error=error,
                artifacts=artifacts,
            )
        finally:
            await target.close()

    async def run(
        self,
        *,
        mode: str,
        target_kinds: list[str],
        topic_ids: list[str] | None,
        tests_per_topic: int,
        seed: int,
        judge_mode: str,
        only_scenario: str | None = None,
    ) -> tuple[list[CaseResult], dict[str, Any]]:
        configured = [TOPIC_BY_ID[x] for x in topic_ids] if topic_ids else list(TOPICS)
        selected = topics_for_mode(mode, configured)
        count = cases_per_topic(mode, tests_per_topic)
        scenarios = [
            build_scenario(
                topic,
                i,
                seed,
                max_turns=max_turns_for_topic(mode, topic),
            )
            for topic in selected
            for i in range(1, count + 1)
        ]
        if only_scenario:
            scenarios = [s for s in scenarios if s.id == only_scenario]
            if not scenarios:
                raise ValueError(
                    "Scenario ID not found in selected deterministic plan; use same seed/mode/topics"
                )
        work = []
        for scenario in scenarios:
            topic = TOPIC_BY_ID[scenario.topic_id]
            repeats = repeats_for_topic(topic)
            for _ in range(repeats):
                for kind in target_kinds_for_topic(mode, target_kinds, topic):
                    for variant in variants_for_topic(
                        mode,
                        self.settings.target_variants,
                        topic,
                    ):
                        work.append((scenario, kind, variant))
        concurrency = 1 if mode == "budget" else self.settings.eval_max_concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def one(item):
            async with semaphore:
                return await self.run_case(item[0], item[1], item[2], judge_mode)

        cases: list[CaseResult] = []
        execution_status = None
        execution_error = None
        chunk_width = 1 if mode == "budget" else max(1, concurrency * 2)
        for chunk_start in range(0, len(work), chunk_width):
            chunk = work[chunk_start : chunk_start + chunk_width]
            try:
                cases.extend(await asyncio.gather(*(one(item) for item in chunk)))
            except BudgetExceeded as exc:
                execution_status = "budget_exhausted"
                execution_error = str(exc)
                break
            except CostAccountingError as exc:
                execution_status = "cost_accounting_failed"
                execution_error = str(exc)
                break
            except EvaluationInfrastructureError as exc:
                execution_status = "infrastructure_unavailable"
                execution_error = str(exc)
                break
            write_reports(
                self.run_dir,
                cases,
                budget_usd=self.budget,
                measured_spend_usd=self.spent,
            )
        summary = write_reports(
            self.run_dir,
            cases,
            budget_usd=self.budget,
            measured_spend_usd=self.spent,
            execution_status=execution_status,
            execution_error=execution_error,
        )
        return cases, summary

    async def close(self) -> None:
        await self.ai_client.close()
        self.cache.close()
