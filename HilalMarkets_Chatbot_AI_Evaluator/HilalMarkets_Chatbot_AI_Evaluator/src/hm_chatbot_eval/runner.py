from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .cache import ResponseCache
from .config import Settings
from .evaluate import deterministic_metrics, validate_schema
from .models import CaseResult, JudgeVerdict, ScenarioSpec, TurnRecord
from .openai_client import OpenAIResponsesClient
from .report import write_reports
from .scenarios import build_scenario
from .targets.backend import BackendTarget
from .targets.base import ChatTarget
from .targets.ui import UITarget
from .test_ai import TestAI
from .topics import TOPIC_BY_ID, TOPICS
from .util import ensure_dir, stable_hash, utc_now


class BudgetExceeded(RuntimeError):
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
            raise BudgetExceeded(f"Hard evaluator budget exceeded: ${self.spent:.4f} > ${self.budget:.4f}")

    def make_target(self, kind: str) -> ChatTarget:
        if kind == "backend":
            return BackendTarget(self.settings)
        if kind == "ui":
            return UITarget(self.settings, self.evidence_dir)
        raise ValueError(f"Unknown target kind: {kind}")

    async def run_case(self, scenario: ScenarioSpec, kind: str, variant: dict[str, Any], judge_mode: str) -> CaseResult:
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
                user_text, done, cost = await self.test_ai.next_user_turn(scenario, turns, turn_number)
                self._charge(cost)
                test_cost += cost
                turns.append(TurnRecord(turn_id=f"u{turn_number}", role="user", text=user_text, timestamp=utc_now()))
                fault = scenario.fault if scenario.fault and not fault_used else None
                reply = await target.send(user_text, scenario_id=scenario.id, fault=fault)
                fault_used = fault_used or bool(fault)
                artifacts.extend(reply.artifacts)
                if reply.structured is not None:
                    structured = reply.structured
                usage = reply.usage or {}
                inp = float(usage.get("input_tokens", 0))
                cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
                out = float(usage.get("output_tokens", 0))
                target_cost += ((max(0.0, inp-cached) * self.settings.target_input_usd_per_1m) + (cached * self.settings.target_cached_input_usd_per_1m) + (out * self.settings.target_output_usd_per_1m)) / 1_000_000
                turns.append(TurnRecord(
                    turn_id=f"a{turn_number}", role="assistant", text=reply.text, timestamp=utc_now(),
                    latency_ms=reply.latency_ms, status_code=reply.status_code, raw_hash=reply.raw_hash,
                    structured=reply.structured, model=reply.model, usage=reply.usage, error=reply.error,
                ))
                raw_path = self.evidence_dir / f"{scenario.id}-{kind}-{turn_number}.json"
                raw_path.write_text(json.dumps({"user": user_text, "reply": reply.raw, "error": reply.error}, ensure_ascii=False, indent=2), encoding="utf-8")
                artifacts.append(str(raw_path))
                if done and turn_number >= 2:
                    break
            schema_errors = validate_schema(structured, self.schema)
            deterministic = deterministic_metrics(scenario, turns, structured, schema_errors, self.settings.target_field_map)
            judge: JudgeVerdict | None = None
            if judge_mode == "online":
                judge, cost = await self.test_ai.judge(scenario, turns, deterministic, schema_errors, structured)
                self._charge(cost)
                test_cost += cost
            elif judge_mode == "deferred":
                pending_dir = ensure_dir(self.run_dir / "batch_pending")
                (pending_dir / f"{scenario.id}-{kind}-{variant.get('name','current')}.json").write_text(json.dumps({"scenario": scenario.__dict__, "turns": [t.__dict__ for t in turns], "deterministic": deterministic, "schema_errors": schema_errors, "structured_output": structured}, ensure_ascii=False, indent=2), encoding="utf-8")
            passed = bool(judge.passed) if judge else bool(deterministic.get("schema_valid")) and not error
            return CaseResult(
                run_id=self.run_id, scenario=scenario, target_kind=kind, target_variant=str(variant.get("name", "current")),
                started_at=started_at, finished_at=utc_now(), turns=turns, deterministic_metrics=deterministic,
                judge=judge, structured_output=structured, structured_hash=stable_hash(structured) if structured else None,
                schema_errors=schema_errors, total_latency_ms=sum(t.latency_ms or 0 for t in turns), target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost, passed=passed, error=error, artifacts=artifacts,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            return CaseResult(
                run_id=self.run_id, scenario=scenario, target_kind=kind, target_variant=str(variant.get("name", "current")),
                started_at=started_at, finished_at=utc_now(), turns=turns, deterministic_metrics={}, judge=None,
                structured_output=structured, structured_hash=stable_hash(structured) if structured else None,
                schema_errors=[], total_latency_ms=sum(t.latency_ms or 0 for t in turns), target_cost_usd=target_cost,
                test_ai_cost_usd=test_cost, passed=False, error=error, artifacts=artifacts,
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
        selected = [TOPIC_BY_ID[x] for x in topic_ids] if topic_ids else list(TOPICS)
        if mode == "smoke":
            selected = [t for t in selected if t.severity == "critical"][:12]
            count = 1
        elif mode == "standard":
            count = min(5, tests_per_topic)
        elif mode == "full":
            if not 20 <= tests_per_topic <= 30:
                raise ValueError("Full mode requires 20–30 tests per topic")
            count = tests_per_topic
        else:
            raise ValueError("mode must be smoke, standard or full")
        scenarios = [build_scenario(topic, i, seed) for topic in selected for i in range(1, count + 1)]
        if only_scenario:
            scenarios = [s for s in scenarios if s.id == only_scenario]
            if not scenarios:
                raise ValueError("Scenario ID not found in selected deterministic plan; use same seed/mode/topics")
        work = []
        for scenario in scenarios:
            repeats = 2 if scenario.topic_id == "reproducibility" else 1
            for _ in range(repeats):
                for kind in target_kinds:
                    for variant in self.settings.target_variants:
                        work.append((scenario, kind, variant))
        semaphore = asyncio.Semaphore(self.settings.eval_max_concurrency)

        async def one(item):
            async with semaphore:
                return await self.run_case(item[0], item[1], item[2], judge_mode)

        cases: list[CaseResult] = []
        for chunk_start in range(0, len(work), max(1, self.settings.eval_max_concurrency * 2)):
            chunk = work[chunk_start:chunk_start + self.settings.eval_max_concurrency * 2]
            cases.extend(await asyncio.gather(*(one(item) for item in chunk)))
            write_reports(self.run_dir, cases)
        summary = write_reports(self.run_dir, cases)
        return cases, summary

    async def close(self) -> None:
        await self.ai_client.close()
        self.cache.close()
