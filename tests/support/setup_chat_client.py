"""Send a Setup Chat message the way the official client sends one.

While a question is open the server requires every message to say which question it was
written under — typed answers included. A test that omits that identity is not testing
the product; it is testing a client that no longer exists.

One helper, shared by every suite, so the rule has a single owner here too. It reads the
identity from the stored contract at *send* time, exactly as the browser reads it from
the last assistant payload, which is what makes a control created under an older step
carry the current step rather than the one it was drawn under.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ai_market_monitor.schemas.setup_agent import SetupConversationContext

__all__ = [
    "active_question_identity",
    "build",
    "new_client_message_id",
    "say",
    "stored_conversation",
]


def new_client_message_id() -> str:
    """A fresh idempotency key, in the same shape the browser generates.

    The official client makes one of these before every send and reuses it for network
    retries only. A test that omits it is testing a client that cannot exist, because
    the server refuses a message without one.
    """

    return f"cm-{uuid4().hex}"


def stored_conversation(chat: Any) -> SetupConversationContext:
    """The conversation record as it is really persisted on the chat session."""

    stored = (chat.context_json or {}).get("setup_conversation_context") or {}
    return SetupConversationContext.model_validate(stored)


def active_question_identity(chat: Any) -> dict[str, Any]:
    """The identity the client attaches, or nothing when no question is open."""

    contract = stored_conversation(chat).active_question
    if contract is None:
        return {}
    return {
        "answered_question_id": contract.question_id,
        "answered_step_revision": contract.step_revision,
    }


async def say(
    service: Any,
    session: Any,
    chat: Any,
    message: str = "",
    *,
    client_message_id: str | None = None,
    **extra: Any,
) -> Any:
    """One message from the official client, carrying the current question identity.

    A key is generated when the caller does not pin one, because that is what the
    browser does. Tests that need to prove replay behaviour pass the same key twice on
    purpose.
    """

    return await service.handle_message(
        session,
        chat,
        message=message,
        client_message_id=client_message_id or new_client_message_id(),
        **{**active_question_identity(chat), **extra},
    )


async def build(
    service: Any,
    session: Any,
    chat: Any,
    action: str,
    *,
    client_message_id: str | None = None,
    **extra: Any,
) -> Any:
    """One guided Builder change, the way the real Builder sends one.

    Deliberately separate from :func:`say`: this path never reaches a model, and a test
    that goes through it is proving the product still works with the assistant off.
    """

    return await service.handle_builder_action(
        session,
        chat,
        action=action,
        client_message_id=client_message_id or new_client_message_id(),
        **extra,
    )
