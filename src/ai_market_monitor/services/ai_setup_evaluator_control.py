from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx

from ai_market_monitor.core.config import AISetupEvaluatorTargetVersion, Settings

EVALUATOR_FAULTS = frozenset(
    {
        "timeout_once",
        "429_once",
        "empty_once",
        "invalid_json_once",
        "partial_json_once",
        "stream_disconnect_once",
    }
)

_PROMPT_APPENDICES = {
    "current": "",
    "context_guard_v1": (
        "\nEvaluator prompt variant context_guard_v1: retain every still-current "
        "user-authored condition across corrections, and never infer a missing measurable value."
    ),
}


class AISetupEvaluatorControlError(ValueError):
    pass


def evaluator_fault_control_available(settings: Settings) -> bool:
    """Whether this process may accept a test-only fault-injection request."""

    return bool(
        settings.app_env == "test"
        and settings.ai_setup_evaluator_enabled
        and settings.ai_setup_evaluator_faults_enabled
    )


@dataclass(frozen=True, slots=True)
class AISetupEvaluatorTurn:
    fault: str | None
    target: AISetupEvaluatorTargetVersion | None


_turn: ContextVar[AISetupEvaluatorTurn | None] = ContextVar(
    "ai_setup_evaluator_turn",
    default=None,
)
_fault_consumed: ContextVar[bool] = ContextVar(
    "ai_setup_evaluator_fault_consumed",
    default=False,
)


@contextmanager
def evaluator_turn(
    settings: Settings,
    *,
    fault: str | None,
    target_version: str | None,
) -> Iterator[None]:
    """Install evaluator controls for one authenticated message request."""

    normalized_fault = (fault or "").strip() or None
    normalized_version = (target_version or "").strip() or None
    if normalized_fault is None and normalized_version is None:
        yield
        return
    # Each refusal names the setting that caused it. One shared message for three
    # different causes cost a whole evaluator run: both evaluator flags were already
    # true, the server was simply started with APP_ENV=development, and nothing in
    # "Evaluator controls are unavailable" could tell anyone that.
    if settings.app_env != "test":
        raise AISetupEvaluatorControlError(
            "Evaluator controls require APP_ENV=test; this server runs "
            f"APP_ENV={settings.app_env}"
        )
    if not settings.ai_setup_evaluator_enabled:
        raise AISetupEvaluatorControlError(
            "Evaluator controls require AI_SETUP_EVALUATOR_ENABLED=true"
        )
    if normalized_fault is not None:
        if not settings.ai_setup_evaluator_faults_enabled:
            raise AISetupEvaluatorControlError(
                "Evaluator fault injection requires AI_SETUP_EVALUATOR_FAULTS_ENABLED=true"
            )
        if normalized_fault not in EVALUATOR_FAULTS:
            raise AISetupEvaluatorControlError(
                f"Unknown evaluator fault {normalized_fault!r}; supported faults are "
                + ", ".join(sorted(EVALUATOR_FAULTS))
            )
    target = None
    if normalized_version is not None:
        target = settings.ai_setup_evaluator_target_versions.get(normalized_version)
        if target is None:
            known = ", ".join(sorted(settings.ai_setup_evaluator_target_versions)) or "none"
            raise AISetupEvaluatorControlError(
                f"Unknown evaluator target version {normalized_version!r}; "
                f"AI_SETUP_EVALUATOR_TARGET_VERSIONS defines {known}"
            )
    turn_token: Token[AISetupEvaluatorTurn | None] = _turn.set(
        AISetupEvaluatorTurn(fault=normalized_fault, target=target)
    )
    consumed_token: Token[bool] = _fault_consumed.set(False)
    try:
        yield
    finally:
        _fault_consumed.reset(consumed_token)
        _turn.reset(turn_token)


def current_evaluator_target() -> AISetupEvaluatorTargetVersion | None:
    active = _turn.get()
    return active.target if active is not None else None


def evaluator_prompt_appendix() -> str:
    target = current_evaluator_target()
    if target is None:
        return ""
    return _PROMPT_APPENDICES[target.prompt_version]


def evaluator_fault_was_consumed() -> str | None:
    """Return the injected fault only after it reached the LLM boundary.

    Admission to :func:`evaluator_turn` proves only that a test-only request was
    authorized.  The evaluator response marker must additionally prove that the
    one-shot fault was actually consumed by the routed model client; otherwise a
    deterministic/no-model branch could look like a working fault probe.
    """

    active = _turn.get()
    if active is None or active.fault is None or not _fault_consumed.get():
        return None
    return active.fault


def consume_evaluator_llm_fault() -> dict[str, Any] | None:
    """Return or raise the configured one-shot fault at the real LLM boundary."""

    active = _turn.get()
    if active is None or active.fault is None or _fault_consumed.get():
        return None
    _fault_consumed.set(True)
    fault = active.fault
    request = httpx.Request("POST", "https://api.openai.invalid/v1/responses")
    if fault == "timeout_once":
        raise httpx.ReadTimeout("Evaluator-injected LLM timeout", request=request)
    if fault == "429_once":
        response = httpx.Response(429, request=request, json={"error": "rate_limited"})
        raise httpx.HTTPStatusError(
            "Evaluator-injected LLM rate limit",
            request=request,
            response=response,
        )
    if fault == "stream_disconnect_once":
        raise httpx.RemoteProtocolError(
            "Evaluator-injected LLM stream disconnect",
            request=request,
        )
    if fault == "empty_once":
        return {}
    output_text = (
        '{"intent":"setup_instruction"' if fault == "partial_json_once" else "this is not JSON"
    )
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ]
    }
