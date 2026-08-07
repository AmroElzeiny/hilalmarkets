"""What makes two Setup Chat requests the same request.

A turn costs money and changes the user's setup, so "have I already done this?" has to
have one answer. Two facts decide it:

* the **client message id** — the client's own name for this attempt, reused by every
  automatic retry of it and never by a new one
* the **request fingerprint** — what was actually asked, normalized

The id alone is not enough. A client that reuses an id for different content would get
back the first answer as though it had asked the second question, and the user would
see a reply to something they did not send. The fingerprint catches that and refuses,
rather than guessing which of the two the user meant.

The fingerprint deliberately excludes ``option_label``. A label is presentation only —
it is translated and reworded — while ``option_value`` is the canonical choice. Putting
the label in would make the same click from an English and an Arabic screen look like
two different requests.

This lives under ``schemas`` and imports nothing from the package. It describes the
shape of a request, and the public request model needs it, so it has to be reachable
without pulling in the strategy engine.
"""

from __future__ import annotations

import hashlib
import json
import re

#: The shape a client message id must have. Long enough to be unguessable, plain enough
#: to travel in a URL, a log line and a database key without escaping.
CLIENT_MESSAGE_ID_PATTERN = r"^[A-Za-z0-9_-]{8,80}$"
CLIENT_MESSAGE_ID_MIN_LENGTH = 8
CLIENT_MESSAGE_ID_MAX_LENGTH = 80

_CLIENT_MESSAGE_ID = re.compile(CLIENT_MESSAGE_ID_PATTERN)


def is_valid_client_message_id(value: str | None) -> bool:
    """True when this id is one the server will store and match on."""

    return bool(value) and bool(_CLIENT_MESSAGE_ID.match(str(value)))


def normalized_message(value: str | None) -> str:
    """The message text, compared the way a person would compare it.

    Leading and trailing space, and repeated inner space, are not part of what was
    asked. A retry that added a trailing newline is the same request, not a new one.
    The stored provenance still keeps the text exactly as typed; only this comparison
    is normalized.
    """

    return " ".join(str(value or "").split())


def request_fingerprint(
    *,
    message: str | None,
    option_key: str | None,
    option_value: str | None,
    question_id: str | None = None,
    step_revision: int | None = None,
) -> str:
    """One stable identity for what this request asks for.

    Same content, same fingerprint — on any machine, in any process, in any order of
    fields. Different content, different fingerprint, which is what makes a reused id
    with new content detectable instead of silently answered from the old record.
    """

    payload = {
        "message": normalized_message(message),
        "option_key": (option_key or "").strip() or None,
        "option_value": (option_value or "").strip() or None,
        "question_id": (question_id or "").strip() or None,
        "step_revision": step_revision,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
