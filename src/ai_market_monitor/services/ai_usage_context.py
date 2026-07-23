from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_AI_USAGE_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "ai_usage_correlation_id",
    default=None,
)


def current_ai_usage_correlation_id() -> str | None:
    return _AI_USAGE_CORRELATION_ID.get()


@contextmanager
def ai_usage_correlation(correlation_id: str) -> Iterator[None]:
    token = _AI_USAGE_CORRELATION_ID.set(correlation_id)
    try:
        yield
    finally:
        _AI_USAGE_CORRELATION_ID.reset(token)
