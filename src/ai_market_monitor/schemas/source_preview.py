"""One preview card under an assistant answer. Built by `services/source_previews.py`."""

from __future__ import annotations

from pydantic import BaseModel


class SourcePreview(BaseModel):
    """One preview card, ready to draw. Every field is built by the server, never by a model."""

    key: str
    title: str
    description: str
    #: Where the card leads. Opened in a new tab.
    url: str
    #: The screenshot, or ``None`` when it has not been taken yet.
    image_url: str | None = None
    #: The address as a person reads it, such as ``hilalmarkets.com/markets``.
    address: str
