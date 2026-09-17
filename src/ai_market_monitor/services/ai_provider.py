"""One owner for which AI provider a model id means, and how to call it.

Every assistant used to decide that itself: read ``openai_base_url``, build an
``Authorization`` header, post to ``/responses``. Five copies of the same three
lines means a second provider has to be added five times, and the sixth caller
writes a sixth copy that disagrees. This module holds the closed table, the
request building, the configured gate, and the single Responses-API text
reader. Callers ask. They do not rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import SecretStr

from ai_market_monitor.core.config import (
    Settings,
    is_placeholder_value,
)

__all__ = [
    "AIProviderConfigError",
    "MUSE_SPARK_MODEL",
    "OPENAI_MODELS",
    "OPENCODE_GO_MODELS",
    "ProviderResponsesRequest",
    "build_responses_request",
    "extract_response_text",
    "is_configured",
    "provider_base_url",
    "provider_credential_name",
    "provider_honours_service_tier",
    "resolve_feature_model",
    "resolve_provider",
]

#: The OpenCode Go model the active features use.
MUSE_SPARK_MODEL = "muse-spark-1.3-contributor"

#: Every model id the product ever configured on the OpenAI path: the ids the
#: stale Setup Chat family uses today, the embedding model, and the ids stored
#: in the environment examples. A closed table on purpose: a new id is added
#: here, once, rather than guessed at in each caller.
OPENAI_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.4-nano",
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.6-luna",
        "text-embedding-3-small",
    }
)

#: Model ids served through OpenCode Go.
OPENCODE_GO_MODELS: frozenset[str] = frozenset({MUSE_SPARK_MODEL})

ProviderName = Literal["openai", "opencode_go"]

#: Header naming one conversation to the OpenCode Go provider. A random id,
#: never a user id, never an email: the provider needs to group a
#: conversation's turns, and it must not be given who is having it.
SESSION_HEADER = "x-opencode-session"


class AIProviderConfigError(ValueError):
    """A model id or provider key the product cannot call with. Fail closed."""


def resolve_provider(model: str | None) -> ProviderName:
    """Which provider serves ``model``. Unknown ids are refused, never guessed."""

    candidate = (model or "").strip()
    if candidate in OPENAI_MODELS:
        return "openai"
    if candidate in OPENCODE_GO_MODELS:
        return "opencode_go"
    raise AIProviderConfigError(
        f"Unknown AI model {model!r}. Add it to services/ai_provider.py "
        "before any feature may use it."
    )


def resolve_feature_model(model: str | None, *, setting_name: str) -> str:
    """The configured model for one feature, or a configuration error.

    This is what removes the ``or settings.openai_model`` fallbacks: an unset
    model is refused here instead of silently inheriting the stale default.
    """

    candidate = (model or "").strip()
    if not candidate:
        raise AIProviderConfigError(
            f"{setting_name} is not set. Configure the feature's own model."
        )
    return resolve_provider(candidate) and candidate


def provider_credential_name(provider: ProviderName) -> str:
    """The setting holding this provider's key, for messages, never the value."""

    return "OPENAI_API_KEY" if provider == "openai" else "OPENCODE_GO_API_KEY"


def provider_base_url(settings: Settings, provider: ProviderName) -> str:
    if provider == "openai":
        return str(settings.openai_base_url).rstrip("/")
    return str(settings.opencode_go_base_url).rstrip("/")


def provider_honours_service_tier(provider: ProviderName) -> bool:
    """Whether the provider accepts ``service_tier``. OpenCode Go does not."""

    return provider == "openai"


def _provider_key(settings: Settings, provider: ProviderName) -> str | None:
    holder = settings.openai_api_key if provider == "openai" else settings.opencode_go_api_key
    if holder is None:
        return None
    # Plain strings reach here when tests assign a key straight onto the
    # settings object, bypassing pydantic validation. The old ``is None``
    # guards accepted those; this gate must too.
    if isinstance(holder, SecretStr):
        return holder.get_secret_value()
    return str(holder)


def is_configured(settings: Settings, model: str | None) -> bool:
    """Whether the provider behind ``model`` holds a usable key.

    The active Muse features need the OpenCode Go key, not the OpenAI key; an
    OpenAI key alone leaves them unconfigured. Blank and placeholder keys
    count as missing. An unknown model id raises instead of answering False:
    answering False would read as "not switched on" and hide a misspelling.
    """

    provider = resolve_provider(model)
    key = _provider_key(settings, provider)
    if key is None or not key.strip():
        return False
    return not is_placeholder_value(key)


@dataclass(frozen=True, slots=True)
class ProviderResponsesRequest:
    """Everything one Responses-API call needs: where, as whom, under what name."""

    url: str
    headers: dict[str, str]
    provider: ProviderName


def _session_value(session_key: str | None) -> str:
    if session_key is None:
        return uuid4().hex
    # Stable per conversation, random-looking, and derived without storing
    # anything: the same conversation reuses one id, and no identity travels.
    # Callers must pass a conversation or run id here. Never a user id or email.
    return uuid5(NAMESPACE_URL, f"hilalmarkets-ai-session:{session_key}").hex


def build_responses_request(
    settings: Settings,
    model: str | None,
    *,
    session_key: str | None = None,
) -> ProviderResponsesRequest:
    """URL, auth headers and metrics label for one Responses-API call."""

    provider = resolve_provider(model)
    if not is_configured(settings, model):
        raise AIProviderConfigError(
            f"{provider_credential_name(provider)} is not configured for model {model!r}."
        )
    key = (_provider_key(settings, provider) or "").strip()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if provider == "opencode_go":
        headers[SESSION_HEADER] = _session_value(session_key)
    return ProviderResponsesRequest(
        url=f"{provider_base_url(settings, provider)}/responses",
        headers=headers,
        provider=provider,
    )


def extract_response_text(payload: dict[str, Any]) -> str:
    """The single answer text out of a Responses-API payload.

    Read from ``output[type=message].content[].text``: the authoritative
    location. Responses also carry ``reasoning`` items, which are skipped, and
    message fragments are joined, because one answer may arrive in pieces.

    The top-level ``output_text`` shortcut is kept only as a fallback for
    recorded and fake payloads that predate this reader. Live traffic always
    carries ``output`` items, so production never depends on the shortcut.
    """

    fragments: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"output_text", "text"}:
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                fragments.append(text)
    combined = "".join(fragments)
    if combined.strip():
        return combined
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    raise ValueError("The provider response carried no answer text.")
