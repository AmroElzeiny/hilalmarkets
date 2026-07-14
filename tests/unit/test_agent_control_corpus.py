import json
from pathlib import Path
from uuid import uuid4

from ai_market_monitor.services.agent_policy import (
    FORBIDDEN_AGENT_TOOLS,
    AgentPolicyService,
    AgentRuntimePolicyState,
    AgentServerContext,
)

CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "agent_control_corpus.jsonl"


def _load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_agent_control_corpus_covers_required_request_families() -> None:
    cases = _load_cases()
    categories = {case["category"] for case in cases}

    assert len(cases) >= 12
    assert {
        "multi_intent",
        "vague_setup",
        "correction",
        "adversarial",
        "unsupported_data",
        "idempotency",
        "prompt_injection",
        "monitor_status",
        "market_unavailable",
    }.issubset(categories)


def test_agent_control_corpus_expected_tools_are_policy_reachable_and_bounded() -> None:
    policy = AgentPolicyService()
    for case in _load_cases():
        monitor_id = uuid4()
        draft_hash = "a" * 64 if case["has_draft"] else None
        context = AgentServerContext(
            user_id=uuid4(),
            chat_id=uuid4(),
            request_text=case["prompt"],
            chat_status="interviewing",
            setup_mode=case["setup_mode"],
            has_draft=case["has_draft"],
            draft_hash=draft_hash,
            has_pending_clarification=False,
            explicit_scan_request=case["explicit_scan_request"],
            explicit_revision_request=case["category"] in {"correction", "corrective_context"},
            market_question=case["market_question"],
            monitor_question=case["monitor_question"],
            setup_language=case["setup_language"],
            scan_entitled=case["scan_entitled"],
            owned_monitor_ids=frozenset({monitor_id}) if case.get("owned_monitor") else frozenset(),
        )
        offered = set(policy.allowed_tools(context, AgentRuntimePolicyState()))

        assert set(case["expected_tools"]).issubset(offered), case["prompt"]
        assert offered.isdisjoint(FORBIDDEN_AGENT_TOOLS), case["prompt"]
