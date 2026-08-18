"""Every email is set in the product's own type, whoever composed its words.

Three emails carry a body that was written as plain text somewhere else: the automated
reply somebody gets after asking for help, the contact-page copy the office receives,
and the support ticket. Each one wrapped that text itself, and two of the three asked
for **Arial** — so the single email a person receives when they ask Hilal Markets for
help was the one email in the whole product set in a typeface the brand does not use.

That is the codebase's recurring failure wearing different clothes: the same decision
made independently in several places, drifting apart. The rule below is therefore about
the *class*, not about those three files. No module may name a typeface for an email
body. There is one owner — ``email_branding`` — and every caller uses it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ai_market_monitor.services.email_branding import (
    BODY_FONT,
    COPY,
    DISPLAY_FONT,
    plain_text_block,
)

#: Where the product decides what an email looks like. The only file allowed to name a
#: typeface, because it is the file every other one asks.
BRANDING = Path("src/ai_market_monitor/services/email_branding.py")

#: Everything that builds an email body. Found by searching rather than listed, so a new
#: sender is covered the day it is written instead of the day somebody remembers it.
EMAIL_SOURCES = sorted(
    path
    for path in Path("src/ai_market_monitor").rglob("*.py")
    if path != BRANDING
    and re.search(
        r"html_body|ensure_shell|send_transactional\(", path.read_text(encoding="utf-8")
    )
)

FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;\"']+)")


def test_the_search_actually_found_the_email_senders():
    """A rule that matches nothing passes for the wrong reason."""

    names = {path.name for path in EMAIL_SOURCES}
    assert {"public_chat.py", "public_forms.py", "email_delivery.py"} <= names


@pytest.mark.parametrize("path", EMAIL_SOURCES, ids=lambda path: path.name)
def test_no_email_sender_names_its_own_typeface(path: Path):
    """Only `email_branding` may say what an email is set in."""

    declared = FONT_FAMILY.findall(path.read_text(encoding="utf-8"))
    assert declared == [], (
        f"{path} names a typeface for an email body: {declared}. "
        "Use email_branding.plain_text_block (or another block from that module) so "
        "every email stays one product."
    )


@pytest.mark.parametrize("face", ["Arial,", "Helvetica,", "Times", "Verdana"])
def test_the_shared_block_never_falls_back_before_the_brand_faces(face: str):
    """The brand faces are named first, and a substitute only ever after them.

    Arial is allowed to be *in* the stack — it is a sensible last resort when neither
    Onest nor Geometria loads, which in a mail client is most of the time. What is not
    allowed is a body that asks for it first, which is what these three emails did.
    """

    rendered = plain_text_block("Assalamu Alaikum.")
    stack = FONT_FAMILY.search(rendered)
    assert stack is not None
    families = [item.strip().strip("'\"") for item in stack.group(1).split(",")]
    assert families[0] == "Onest"
    if face.rstrip(",") in families:
        assert families.index(face.rstrip(",")) > 0


def test_the_shared_block_keeps_the_paragraphs_somebody_typed():
    """A written message read as one block is a different message.

    The support reply is several paragraphs. Collapsing them is not a styling detail —
    it turns a careful answer into a wall of text, which the brand rules forbid outright.
    """

    rendered = plain_text_block("First line.\n\nSecond line.")
    assert "pre-wrap" in rendered
    assert "First line.\n\nSecond line." in rendered


def test_the_shared_block_escapes_what_somebody_wrote():
    """A visitor's own words are data. They are never markup."""

    rendered = plain_text_block("<script>alert(1)</script> & more")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp; more" in rendered


def test_the_shared_block_uses_the_measured_body_colour():
    """7.5:1 on white. The smallest text in an email still has to be readable."""

    rendered = plain_text_block("Assalamu Alaikum.")
    assert COPY in rendered
    assert BODY_FONT in rendered
    assert DISPLAY_FONT not in rendered
